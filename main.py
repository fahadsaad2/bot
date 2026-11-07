# -*- coding: utf-8 -*-
# بوت سكويز - نسخة عربية معدلة + شرط الانفجار والنقطة الدافعة
# الفكرة: ينبه وقت الكسر على شموع مكتملة فقط
# الجمعة: تقرير أسبوعي بعد الإغلاق، باقي الأيام: تنبيه فوري لأقوى شركة
# جديد: تنبيه "انفجار + نقطة دافعة + فاصل + غاما" (راجع قسم الانفجار أسفل دالة format_gex_line)

from __future__ import annotations
import html, json, logging, math, os, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from flask import Flask, jsonify
from alpaca.data.enums import DataFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

# ============ الإعدادات ============
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").strip().lower() != "false"

TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]  # الكبار - 1%
TIER_MID = ["AAPL", "AMZN", "AMD", "AVGO", "MU"]  # الوسط - 2%
TIER_VOL = ["SMCI", "MSTR", "COIN", "PLTR", "APP", "ARM", "LITE", "RDDT", "SNDK"]  # المتقلبة - 5%
SYMBOLS = TIER1 + TIER_MID + TIER_VOL

TIMEFRAME_5M = TimeFrame(5, TimeFrameUnit.Minute)
TIMEFRAME_15M = TimeFrame(15, TimeFrameUnit.Minute)
TIMEFRAME_1D = TimeFrame(1, TimeFrameUnit.Day)
TIMEFRAME_1W = TimeFrame(1, TimeFrameUnit.Week)

HISTORY_DAYS = 10
HISTORY_DAYS_DAILY = 150
HISTORY_DAYS_WEEKLY = 365
SLOW_CACHE_TTL = 1800  # اليومي والأسبوعي يتحدثون كل 30 دقيقة فقط

STATE_FILE = Path("squeeze_state.json")
SIGNALS_FILE = Path("signals_log.json")
SEND_GAP_SECONDS = 3.5
RESEND_BLOCK_SECONDS = 7200  # لا يعيد التنبيه لنفس الشركة قبل ساعتين
STATE_RETENTION_HOURS = 30
MIN_SCORE = 50

_feed_env = os.getenv("STOCK_FEED", "").strip().lower()
STOCK_FEED = DataFeed.SIP if _feed_env == "sip" else DataFeed.IEX if _feed_env == "iex" else None

MAX_SPREAD_DAILY = 15
MAX_SPREAD_WEEKLY = 12
MAX_SPREAD_MONTHLY = 10
MIN_OI = 100
MIN_MID = 0.5
MAX_MID = 15.0
MAX_DIFF_PCT = 8
MAX_BE_RATIO = 1.2
TARGET_DELTA = 0.40

US_EASTERN = ZoneInfo("America/New_York")
GAP_MIN_PCT = 2.0
GAP_MAX_PCT = 8.0
BIG_GAP_PCT = 5.0
GAP_RULE_WINDOW_MIN = 60

STOP_ATR_MULT = 1.5
R_MULTS = (1.5, 3.0, 5.0)

GEX_MAX_DTE = 30
GEX_CACHE_TTL = 600
RISK_FREE = 0.04

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("squeeze_bot")
stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)

# ============ أدوات عامة ============
def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
def _as_float(v, d=None):
    try:
        r = float(v)
        return r if math.isfinite(r) else d
    except Exception:
        return d
def _as_date(v):
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    try: return date.fromisoformat(str(v)[:10])
    except Exception: return None

# --- حالة السوق من Alpaca (تعرف الإجازات والإغلاق المبكر) مع كاش دقيقة ---
_clock_cache = {"ts": 0.0, "is_open": None, "next_open": None}
def _get_clock():
    if time.time() - _clock_cache["ts"] < 60 and _clock_cache["is_open"] is not None:
        return _clock_cache
    try:
        c = trading_client.get_clock()
        _clock_cache.update(ts=time.time(), is_open=bool(c.is_open), next_open=c.next_open)
    except Exception as e:
        logger.warning(f"فشل جلب ساعة السوق: {e}")
        _clock_cache.update(ts=time.time(), is_open=None, next_open=None)
    return _clock_cache
def _time_based_open() -> bool:
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5: return False
    return now.replace(hour=9, minute=30, second=0, microsecond=0) <= now < now.replace(hour=16, minute=0, second=0, microsecond=0)
def is_market_open() -> bool:
    c = _get_clock()
    return c["is_open"] if c["is_open"] is not None else _time_based_open()
def is_premarket_trading_day() -> bool:
    # يوم تداول اليوم ولسا ما فتح السوق
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5: return False
    c = _get_clock()
    if c["is_open"] is None: return True
    if c["is_open"]: return False
    no = c.get("next_open")
    try: return no is not None and no.astimezone(US_EASTERN).date() == now.date()
    except Exception: return True
def minutes_since_open():
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5: return None
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < open_t: return None
    return (now - open_t).total_seconds() / 60

def _bar_frame_multi(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.index, pd.MultiIndex):
        try: frame = frame.xs(symbol, level=0)
        except Exception: return pd.DataFrame()
    rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    frame = frame.rename(columns=rename)
    for c in ["Open", "High", "Low", "Close"]: frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    try:
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame.index = frame.index.tz_convert("UTC")
    except Exception: pass
    return frame
def _fetch_frames(symbols, timeframe, days):
    now = datetime.now(timezone.utc)
    kwargs = {"symbol_or_symbols": symbols, "timeframe": timeframe, "start": now - timedelta(days=days)}
    if STOCK_FEED is not None: kwargs["feed"] = STOCK_FEED
    raw = stock_client.get_stock_bars(StockBarsRequest(**kwargs)).df
    out = {}
    for s in symbols:
        f = _bar_frame_multi(raw, s)
        if not f.empty: out[s] = f
    return out

def _closed_only(frames, minutes):
    """يحذف الشمعة الجارية - نستخدم الشموع المكتملة فقط (إصلاح الإشارات الكاذبة)"""
    now = pd.Timestamp.now(tz="UTC")
    delta = pd.Timedelta(minutes=minutes)
    out = {}
    for s, f in frames.items():
        g = f[f.index + delta <= now]
        if len(g): out[s] = g
    return out

_slow_cache = {"ts": 0.0, "symbols": None, "hD": {}, "hW": {}}
def _get_slow_frames(symbols, force=False):
    key = tuple(symbols)
    if (not force and _slow_cache["symbols"] == key and time.time() - _slow_cache["ts"] < SLOW_CACHE_TTL
            and _slow_cache["hD"]):
        return _slow_cache["hD"], _slow_cache["hW"]
    hD = _fetch_frames(symbols, TIMEFRAME_1D, HISTORY_DAYS_DAILY)
    hW = _fetch_frames(symbols, TIMEFRAME_1W, HISTORY_DAYS_WEEKLY)
    _slow_cache.update(ts=time.time(), symbols=key, hD=hD, hW=hW)
    return hD, hW

def fetch_market_data(symbols, force_slow=False):
    h5 = _closed_only(_fetch_frames(symbols, TIMEFRAME_5M, HISTORY_DAYS), 5)
    h15 = _closed_only(_fetch_frames(symbols, TIMEFRAME_15M, HISTORY_DAYS), 15)
    hD, hW = _get_slow_frames(symbols, force_slow)
    prices = {}
    try:
        kwargs = {"symbol_or_symbols": symbols}
        if STOCK_FEED is not None: kwargs["feed"] = STOCK_FEED
        latest = stock_client.get_stock_latest_trade(StockLatestTradeRequest(**kwargs))
        for s in symbols:
            t = latest.get(s) if isinstance(latest, dict) else None
            p = _as_float(_field(t, "price"))
            if p and p > 0: prices[s] = p
    except Exception as e: logger.warning(f"فشل جلب آخر الأسعار: {e}")
    for s, f in h5.items():
        if s not in prices and len(f): prices[s] = float(f["Close"].iloc[-1])
    return h5, h15, hD, hW, prices

# ============ المؤشرات ============
def calc_rsi(hist, period=14):
    if len(hist) < period + 1: return None
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    al = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    if al == 0: return 100.0 if ag > 0 else 50.0
    return float(100 - (100 / (1 + ag / al)))
def calc_atr(hist, period=14):
    if hist is None or len(hist) < period + 1: return None
    pc = hist["Close"].shift(1)
    tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - pc).abs(), (hist["Low"] - pc).abs()], axis=1).max(axis=1)
    v = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else None
def calc_levels(atr, entry, is_put=False):
    if not atr or entry <= 0: return None
    risk = STOP_ATR_MULT * atr
    if is_put:
        vals = (entry + risk, *[entry - m * risk for m in R_MULTS])
    else:
        vals = (entry - risk, *[entry + m * risk for m in R_MULTS])
    return tuple(round(x, 2) for x in vals)

def min_move_for_symbol(symbol):
    if symbol in TIER1: return 1.0
    if symbol in TIER_MID: return 2.0
    return 5.0

def is_marubozu(row):
    body = abs(row["Close"] - row["Open"])
    rng = row["High"] - row["Low"]
    if rng <= 0: return False
    return body / rng > 0.70

def aligned_marubozu(row, direction):
    """ماروبوزو باتجاه الإشارة (صاعدة لـ UP وهابطة لـ DOWN)"""
    if not is_marubozu(row): return False
    return row["Close"] > row["Open"] if direction == "UP" else row["Close"] < row["Open"]

def weekly_score(hW_sym):
    """يرجع (القوة، الوصف، اتجاه الشمعة). الابتلاعية تتطلب أن السابقة عكسها"""
    if hW_sym is None or len(hW_sym) < 3: return 0, "", None
    last, prev = hW_sym.iloc[-1], hW_sym.iloc[-2]
    last_up = last["Close"] > last["Open"]
    last_down = last["Close"] < last["Open"]
    prev_up = prev["Close"] > prev["Open"]
    prev_down = prev["Close"] < prev["Open"]
    w_dir = "UP" if last_up else "DOWN" if last_down else None
    score = 0; txt = []
    if is_marubozu(last):
        score += 40; txt.append("ماروبوزو أسبوعي ✅")
    if last_up and prev_down and last["Close"] >= prev["Open"] and last["Open"] <= prev["Close"]:
        score += 30; txt.append("ابتلاعية صاعدة ✅")
    if last_down and prev_up and last["Close"] <= prev["Open"] and last["Open"] >= prev["Close"]:
        score += 30; txt.append("ابتلاعية هابطة ✅")
    return score, " | ".join(txt), w_dir

def calc_gap_metrics(symbol, hD, current_price, atr_daily):
    try:
        f = hD.get(symbol)
        if f is None or len(f) < 2: return None
        today = datetime.now(US_EASTERN).date()
        has_today = f.index[-1].date() == today
        prev_close = float(f["Close"].iloc[-2] if has_today else f["Close"].iloc[-1])
        if prev_close <= 0: return None
        if has_today:
            ref = float(f["Open"].iloc[-1]); source = "فتح"
        else:
            ref = float(current_price); source = "بري ماركت"
        gap_points = ref - prev_close
        gap_pct = gap_points / prev_close * 100
        gap_atr = abs(gap_points) / atr_daily if atr_daily else 0
        is_valid = GAP_MIN_PCT <= abs(gap_pct) <= GAP_MAX_PCT
        m = minutes_since_open()
        in_window = True if m is None else (m <= GAP_RULE_WINDOW_MIN)
        gap_type = "أب ⬆️" if gap_points > 0 else "داون ⬇️" if gap_points < 0 else "بدون"
        return {"prev_close": prev_close, "gap_points": gap_points, "gap_pct": gap_pct, "gap_atr": gap_atr,
                "gap_type": gap_type, "is_valid": is_valid, "is_up": gap_points > 0, "source": source,
                "in_window": in_window, "active": bool(is_valid and in_window),
                "is_big": abs(gap_pct) >= BIG_GAP_PCT}
    except Exception as e:
        logger.warning(f"خطأ حساب القاب {symbol}: {e}")
        return None

def squeeze_metrics(hist, n=20):
    if hist is None or len(hist) < n + 30: return None
    close = hist["Close"]
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    pc = close.shift(1)
    tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - pc).abs(), (hist["Low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    ub, lb = mid + 2 * std, mid - 2 * std
    uk, lk = mid + 1.5 * atr, mid - 1.5 * atr
    in_sq = (lb > lk) & (ub < uk)
    width = (ub - lb) / mid * 100
    kcw = (uk - lk) / mid * 100
    valid = width.dropna()
    if valid.empty or pd.isna(kcw.iloc[-1]) or kcw.iloc[-1] <= 0: return None
    cur = float(valid.iloc[-1])
    pct = float((valid <= cur).mean())
    bars = 0
    for v in reversed(in_sq.values):
        if v: bars += 1
        else: break
    ratio = float(width.iloc[-1] / kcw.iloc[-1])
    score = (0.4 * (1 - pct) * 100 + 0.4 * min(bars / 15, 1) * 100 + 0.2 * max(0.0, min(1.0, (1 - ratio) / 0.3)) * 100)
    return {"in_squeeze": bool(in_sq.iloc[-1]), "bars": bars, "tight_pct": (1 - pct) * 100, "score": int(round(score))}

def breakout_metrics(hist, direction):
    # ملاحظة: المستوى هنا = أعلى/أدنى آخر 20 شمعة 5 دقايق (مو 20 يوم)
    if hist is None or len(hist) < 25 or "Volume" not in hist.columns: return None
    last = hist.iloc[-1]
    prior = hist.iloc[-21:-1]
    atr = calc_atr(hist)
    avg_vol = float(prior["Volume"].mean())
    rec_vol = float(hist["Volume"].tail(3).mean())
    rvol = rec_vol / avg_vol if avg_vol > 0 else 0.0
    hi, lo = float(prior["High"].max()), float(prior["Low"].min())
    price = float(last["Close"])
    if direction == "UP": broke = price > hi; dist = (hi - price) / atr if atr else 99.0
    else: broke = price < lo; dist = (price - lo) / atr if atr else 99.0
    rng = float(last["High"] - last["Low"])
    pos = (price - float(last["Low"])) / rng if rng > 0 else 0.5
    align = pos if direction == "UP" else 1 - pos
    prox = 1.0 if broke else max(0.0, 1 - max(dist, 0) / 2)
    score = 40 * min(rvol / 2, 1) + 35 * prox + 25 * align
    return {"rvol": rvol, "broke": broke, "dist": max(dist, 0), "score": int(round(score))}

def breakout_for(hist, direction):
    if direction in ("UP", "DOWN"): return breakout_metrics(hist, direction)
    a, b = breakout_metrics(hist, "UP"), breakout_metrics(hist, "DOWN")
    if a and b: return a if a["score"] >= b["score"] else b
    return a or b

def day_rvol(hD, symbol):
    f = hD.get(symbol)
    if f is None or len(f) < 6: return None
    now_et = datetime.now(US_EASTERN)
    if f.index[-1].date() != now_et.date(): return None
    avg = float(f["Volume"].iloc[-21:-1].mean())
    if avg <= 0: return None
    open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = (now_et - open_et).total_seconds() / 60
    frac = min(max(elapsed, 5) / 390, 1.0)
    return float(f["Volume"].iloc[-1]) / (avg * frac)

def daily_levels(f):
    if f is None or len(f) < 22: return None
    today = datetime.now(US_EASTERN).date()
    prior = f.iloc[:-1].tail(20) if f.index[-1].date() == today else f.tail(20)
    sma50 = float(f["Close"].rolling(50).mean().iloc[-1]) if len(f) >= 50 else None
    return {"hi20": float(prior["High"].max()), "lo20": float(prior["Low"].min()), "sma50": sma50}

def pick_direction(symbol, h5, h15, hD):
    up = total = 0
    try:
        c = h5[symbol]["Close"]; total += 1; up += 1 if c.iloc[-1] >= c.rolling(20).mean().iloc[-1] else 0
    except Exception: pass
    try:
        if len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]; total += 1; up += 1 if c.ewm(span=9).mean().iloc[-1] > c.ewm(span=20).mean().iloc[-1] else 0
    except Exception: pass
    try:
        if len(hD[symbol]) >= 50:
            c = hD[symbol]["Close"]; total += 1; up += 1 if c.iloc[-1] > c.ewm(span=50).mean().iloc[-1] else 0
    except Exception: pass
    if total == 0: return "NEUTRAL"
    if up * 2 > total: return "UP"
    if up * 2 < total: return "DOWN"
    return "NEUTRAL"

def get_trend_strength(symbol, h5, h15, hD, sig_dir):
    if sig_dir not in ("UP", "DOWN"): return 0, []
    want_up = sig_dir == "UP"
    score = 0; details = []
    def add(label, is_up):
        nonlocal score
        ok = (is_up == want_up)
        score += 1 if ok else 0
        details.append(f"{label}{'✅' if ok else '❌'}")
    try:
        c = h5[symbol]["Close"]
        add("5د", c.iloc[-1] >= c.rolling(20).mean().iloc[-1])
    except Exception: details.append("5د؟")
    try:
        if len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]
            add("15د", c.ewm(span=9).mean().iloc[-1] > c.ewm(span=20).mean().iloc[-1])
        else: details.append("15د؟")
    except Exception: details.append("15د؟")
    try:
        if len(hD[symbol]) >= 50:
            c = hD[symbol]["Close"]
            add("يومي", c.iloc[-1] > c.ewm(span=50).mean().iloc[-1])
        else: details.append("يومي؟")
    except Exception: details.append("يومي؟")
    return score, details

# ============ غاما ============
_gex_cache: dict = {}
def _norm_pdf(x): return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
def _bs_gamma(S, K, T, iv, r=RISK_FREE):
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0: return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        return _norm_pdf(d1) / (S * iv * math.sqrt(T))
    except Exception:
        return 0.0
def _list_contracts(symbol, today, max_exp, low, high, ctype=None):
    out = []; page_token = None
    for _ in range(10):
        kwargs = {"underlying_symbols": [symbol], "status": AssetStatus.ACTIVE, "expiration_date_gte": today,
                  "expiration_date_lte": max_exp, "strike_price_gte": f"{low:.2f}", "strike_price_lte": f"{high:.2f}", "limit": 500}
        if ctype is not None: kwargs["type"] = ctype
        if page_token: kwargs["page_token"] = page_token
        try: resp = trading_client.get_option_contracts(GetOptionContractsRequest(**kwargs))
        except Exception as e:
            logger.warning(f"فشل جلب العقود {symbol}: {e}"); break
        out.extend(_field(resp, "option_contracts", []) or [])
        page_token = _field(resp, "next_page_token")
        if not page_token: break
    return out
def compute_gex(symbol, spot):
    cached = _gex_cache.get(symbol)
    if cached and time.time() - cached[0] < GEX_CACHE_TTL and abs(cached[2] - spot) / spot < 0.02:
        return cached[1]
    try:
        now = datetime.now(US_EASTERN)
        today = now.date()
        max_exp = today + timedelta(days=GEX_MAX_DTE)
        low, high = spot * 0.80, spot * 1.20
        contracts = _list_contracts(symbol, today, max_exp, low, high)
        if not contracts: return None
        chain = option_client.get_option_chain(OptionChainRequest(
            underlying_symbol=symbol, strike_price_gte=low, strike_price_lte=high,
            expiration_date_gte=today, expiration_date_lte=max_exp))
        if not isinstance(chain, dict): return None
        rows = []
        for c in contracts:
            cs = _field(c, "symbol"); strike = _as_float(_field(c, "strike_price"))
            expiry = _as_date(_field(c, "expiration_date")); oi = _as_float(_field(c, "open_interest"), 0) or 0
            ctype = _field(c, "type"); ctype = str(getattr(ctype, "value", ctype)).lower()
            if not cs or not strike or not expiry or oi <= 0: continue
            snap = chain.get(str(cs))
            if snap is None: continue
            iv = _as_float(_field(snap, "implied_volatility"))
            if iv is None or not (0.05 <= iv <= 5.0): continue
            exp_dt = datetime(expiry.year, expiry.month, expiry.day, 16, 0, tzinfo=US_EASTERN)
            secs = (exp_dt - now).total_seconds()
            if secs <= 0: continue
            T = max(secs, 1800) / 31536000.0
            rows.append((ctype.startswith("c"), strike, T, iv, oi))
        if len(rows) < 20: return None
        def net_at(S):
            tot = 0.0
            for is_call, K, T, iv, oi in rows:
                g = _bs_gamma(S, K, T, iv) * oi * 100 * S * S * 0.01
                tot += g if is_call else -g
            return tot
        per_strike = {}
        for is_call, K, T, iv, oi in rows:
            g = _bs_gamma(spot, K, T, iv) * oi * 100 * spot * spot * 0.01
            d = per_strike.setdefault(K, [0.0, 0.0])
            if is_call: d[0] += g
            else: d[1] += g
        above = {k: v[0] for k, v in per_strike.items() if k >= spot}
        below = {k: v[1] for k, v in per_strike.items() if k <= spot}
        call_wall = max(above, key=above.get) if above and max(above.values()) > 0 else max(per_strike, key=lambda k: per_strike[k][0])
        put_wall = max(below, key=below.get) if below and max(below.values()) > 0 else max(per_strike, key=lambda k: per_strike[k][1])
        net_now = net_at(spot)
        grid = [spot * (0.85 + 0.005 * i) for i in range(61)]
        vals = [net_at(s) for s in grid]
        flips = []
        for i in range(len(grid) - 1):
            a, b = vals[i], vals[i + 1]
            if a == 0: flips.append(grid[i])
            elif a * b < 0: flips.append(grid[i] + (grid[i + 1] - grid[i]) * (abs(a) / (abs(a) + abs(b))))
        flip = min(flips, key=lambda x: abs(x - spot)) if flips else None
        res = {"call_wall": float(call_wall), "put_wall": float(put_wall), "flip": flip, "net_gex": net_now, "n": len(rows)}
        _gex_cache[symbol] = (time.time(), res, spot)
        return res
    except Exception as e:
        logger.warning(f"فشل حساب غاما {symbol}: {e}")
        return None
def format_gex_line(gex, price):
    if not gex: return "🧱 غاما: غير متاح"
    parts = [f"جدار كول {gex['call_wall']:.0f}$", f"جدار بوت {gex['put_wall']:.0f}$"]
    if gex.get("flip"): parts.append(f"فليب {gex['flip']:.1f}$")
    regime = "غاما موجبة ✅ (تهدئة)" if gex["net_gex"] > 0 else "غاما سالبة ⚠️ (تضخيم)"
    pos = ""
    if gex.get("flip"): pos = " | السعر فوق الفليب" if price > gex["flip"] else " | السعر تحت الفليب"
    return f"🧱 غاما: {' | '.join(parts)}\n    {regime} ({gex['net_gex'] / 1e6:+.1f}مليون){pos}"

# ==========================================================================
# ===== جديد: انفجار سعري + نقطة دافعة + فاصل يومي/أسبوعي + تمركز صانع =====
# ==========================================================================
EXPL_BODY_MULT = 1.8                 # الانفجار: الجسم > 1.8 ضعف متوسط آخر 20 جسم
EXPL_LOOKBACK = {"W": 3, "D": 8}     # كم شمعة أخيرة نبحث فيها عن الانفجار
ZONE_FRAC = 0.30                     # النقطة الدافعة = 30% من الجسم عند الافتتاح
CONF_TOL = 0.004                     # 0.4% تسامح للفواصل
MM_TOL = 0.008                       # 0.8% تسامح لجدران الغاما (السترايكات متباعدة)
SEP_MIN_SCORE = 1                    # أقل وزن فواصل (أسبوعي=2، يومي=1)
TARGET_EXT = (1.0, 1.272, 1.618)     # امتدادات الأهداف من نقطة الافتتاح


def _completed(f, kind):
    """يحذف الشمعة اليومية/الأسبوعية الجارية - المكتملة فقط"""
    if f is None or len(f) < 2:
        return f
    now = datetime.now(US_EASTERN)
    last_d = f.index[-1].tz_convert(US_EASTERN).date()
    if kind == "D":
        if last_d == now.date() and now.hour < 16:
            return f.iloc[:-1]
    else:
        monday = now.date() - timedelta(days=now.weekday())
        week_done = now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 16)
        # الشمعة الأسبوعية قد تُختم الأحد بتوقيت نيويورك، لذلك نطرح يوماً
        if last_d >= monday - timedelta(days=1) and not week_done:
            return f.iloc[:-1]
    return f


def find_explosion(f, lookback):
    """آخر شمعة انفجار + منطقتها الدافعة (عند الافتتاح)"""
    if f is None or len(f) < 25:
        return None
    body = (f["Close"] - f["Open"]).abs()
    avg = body.rolling(20).mean().shift(1)
    for ts in reversed(list(f.index[-lookback:])):
        a = avg.loc[ts]
        if pd.notna(a) and a > 0 and body.loc[ts] > EXPL_BODY_MULT * a:
            r = f.loc[ts]
            o, c = float(r["Open"]), float(r["Close"])
            b = abs(c - o)
            if c < o:
                d, lo, hi = "DOWN", o - ZONE_FRAC * b, o
            else:
                d, lo, hi = "UP", o, o + ZONE_FRAC * b
            return {"ts": ts, "dir": d, "lo": lo, "hi": hi, "open": o, "close": c, "body": b}
    return None


def _levels_before(f, ts, tag, w):
    """فواصل: فتح/إغلاق/قمة/قاع الشمعة السابقة + قمة/قاع 20 شمعة (قبل ts)"""
    lv = {}
    before = f.loc[f.index < ts]
    if len(before) < 2:
        return lv
    p = before.iloc[-1]
    for k, n in (("High", "قمة"), ("Low", "قاع"), ("Open", "فتح"), ("Close", "إغلاق")):
        lv[f"{tag} {n} سابق"] = (float(p[k]), w)
    t = before.tail(20)
    lv[f"{tag} قمة 20"] = (float(t["High"].max()), w)
    lv[f"{tag} قاع 20"] = (float(t["Low"].min()), w)
    return lv


def explosion_setup(symbol, price, hD, hW, atr_daily, gex_fn):
    """الشروط الثلاثة: انفجار + نقطة دافعة على فاصل + تمركز صانع (غاما). يرجع setup أو None"""
    fD = _completed(hD.get(symbol), "D")
    fW = _completed(hW.get(symbol), "W")
    for kind, fc in (("W", fW), ("D", fD)):
        ex = find_explosion(fc, EXPL_LOOKBACK[kind])
        if not ex:
            continue
        lo, hi, d = ex["lo"], ex["hi"], ex["dir"]

        # إلغاء المنطقة: إغلاق خارجها عكس الاتجاه بعد الانفجار
        after = fc.loc[fc.index > ex["ts"]]
        if len(after):
            if d == "DOWN" and float(after["Close"].max()) > hi * (1 + CONF_TOL):
                continue
            if d == "UP" and float(after["Close"].min()) < lo * (1 - CONF_TOL):
                continue

        # السعر الحالي يعيد اختبار المنطقة
        pad = max(price * CONF_TOL, 0.25 * (atr_daily or 0))
        if not (lo - pad <= price <= hi + pad):
            continue

        # الفواصل الكبيرة عند المنطقة
        lv = _levels_before(fc, ex["ts"], "أسبوعي" if kind == "W" else "يومي", 2 if kind == "W" else 1)
        if kind == "D" and fW is not None and len(fW) >= 3:
            lv.update(_levels_before(fW, fW.index[-1] + pd.Timedelta(days=1), "أسبوعي", 2))
        sep_hits = [(n, v, w) for n, (v, w) in lv.items()
                    if lo * (1 - CONF_TOL) <= v <= hi * (1 + CONF_TOL)]
        sep_score = sum(w for _, _, w in sep_hits)
        if sep_score < SEP_MIN_SCORE:
            continue

        # تمركز صانع السوق (يُحسب فقط بعد نجاح الشروط السابقة لتوفير الطلبات)
        gex = gex_fn(symbol, price)
        mm_hits = []
        if gex:
            for n, key in (("جدار كول", "call_wall"), ("جدار بوت", "put_wall"), ("فليب", "flip")):
                v = gex.get(key)
                if v and lo * (1 - MM_TOL) <= v <= hi * (1 + MM_TOL):
                    mm_hits.append((n, float(v)))
        # بدون غاما متاحة نطلب فواصل أقوى (وزن 3+)
        if not mm_hits and not (gex is None and sep_score >= 3):
            continue

        atr = atr_daily or 0
        b, o = ex["body"], ex["open"]
        sgn = -1 if d == "DOWN" else 1
        stop = (hi + 0.25 * atr) if d == "DOWN" else (lo - 0.25 * atr)
        targets = [round(o + sgn * b * m, 2) for m in TARGET_EXT]
        return {"kind": kind, "dir": d, "ts": ex["ts"], "lo": lo, "hi": hi,
                "sep_hits": sep_hits, "sep_score": sep_score, "mm_hits": mm_hits,
                "gex": gex, "stop": round(stop, 2), "targets": targets}
    return None


def build_explosion_msg(symbol, price, s, atr_d):
    is_up = s["dir"] == "UP"
    emoji = "🟢" if is_up else "🔴"
    tf = "أسبوعية" if s["kind"] == "W" else "يومية"
    otype = "كول" if is_up else "بوت"
    lines = [
        f"💣 {emoji} <b>{html.escape(symbol)}</b> إعادة اختبار منطقة انفجار {tf} - السعر <b>{price:.2f}$</b>",
        f"📍 النقطة الدافعة: {s['lo']:.2f} - {s['hi']:.2f}$ (شمعة {s['ts'].date()} {'صاعدة' if is_up else 'هابطة'})",
        "📏 فواصل عند المنطقة: " + " | ".join(f"{n} {v:.2f}$" for n, v, _ in s["sep_hits"]),
    ]
    if s["mm_hits"]:
        lines.append("🏦 تمركز صانع السوق عند المنطقة: " + " | ".join(f"{n} {v:.1f}$" for n, v in s["mm_hits"]))
    if s["gex"]:
        lines.append(format_gex_line(s["gex"], price))
    t1, t2, t3 = s["targets"]
    lines.append(f"\n🚀 دخول {otype}: {price:.2f}$")
    lines.append(f"🛑 وقف (خلف المنطقة): {s['stop']:.2f}$")
    lines.append(f"🎯 أهداف (امتداد الشمعة): {t1:.2f}$ / {t2:.2f}$ / {t3:.2f}$")
    lv = (s["stop"], t1, t2, t3)
    try:
        opts = get_option_recommendations(symbol, price, "CALL" if is_up else "PUT", atr_d,
                                          levels_intraday=lv, levels_swing=lv)
    except Exception:
        opts = {}
    for b, lab in (("daily", "🔥 يومي"), ("weekly", "📅 أسبوعي"), ("monthly", "🛡️ شهري")):
        o = opts.get(b)
        if o:
            lines.append(f"\n{lab} {o['exp']} سترايك {o['strike']:.1f}$ بسعر {o['mid']:.2f}$ | "
                         f"سبريد {o['spread_pct']:.0f}% | OI {o['oi']}\n<code>{html.escape(o['symbol'])}</code>")
    lines.append("\n⚠️ تعليمي فقط - مو نصيحة مالية")
    return "\n".join(lines)


def run_explosion_scan(state, hD, hW, prices):
    """يُستدعى مرة كل 5 دقايق من main_loop. تنبيه واحد لكل شركة/منطقة في اليوم"""
    today = datetime.now(US_EASTERN).date()
    for symbol in SYMBOLS:
        try:
            price = prices.get(symbol)
            if not price:
                continue
            atr_d = calc_atr(hD.get(symbol))
            s = explosion_setup(symbol, price, hD, hW, atr_d, compute_gex)
            if not s:
                continue
            key = f"expl:{symbol}:{s['kind']}:{s['ts'].date()}:{today}"
            if key in state:
                continue
            if send_telegram(build_explosion_msg(symbol, price, s, atr_d)):
                _mark(state, key)
            time.sleep(SEND_GAP_SECONDS)
        except Exception as e:
            logger.exception(f"خطأ انفجار {symbol}: {e}")
# ===================== نهاية قسم الانفجار =====================

# ============ العقود ============
def get_option_recommendations(symbol, stock_price, option_type, atr_daily, levels_intraday=None, levels_swing=None):
    """levels_* = (stop, t1, t2, t3). اليومي يستخدم مستويات 15د، الأسبوعي والشهري يستخدمون مستويات اليومي (أوسع)"""
    if stock_price <= 0: return {}
    target = ContractType.CALL if option_type == "CALL" else ContractType.PUT
    today = datetime.now(timezone.utc).date()
    max_expiry = today + timedelta(days=45)
    if option_type == "CALL": low = stock_price * 0.99; high = stock_price * 1.04
    else: low = stock_price * 0.96; high = stock_price * 1.01
    all_contracts = _list_contracts(symbol, today, max_expiry, low, high, target)
    if not all_contracts: return {}
    try: chain = option_client.get_option_chain(OptionChainRequest(underlying_symbol=symbol, type=target, strike_price_gte=low, strike_price_lte=high, expiration_date_gte=today, expiration_date_lte=max_expiry))
    except Exception: return {}
    if not isinstance(chain, dict): return {}
    buckets = {"daily": [], "weekly": [], "monthly": []}
    for contract in all_contracts:
        expiry = _as_date(_field(contract, "expiration_date")); strike = _as_float(_field(contract, "strike_price")); cs = _field(contract, "symbol")
        if not expiry or not strike or not cs: continue
        dte = (expiry - today).days
        if 0 <= dte <= 7: bucket = "daily"; max_spread = MAX_SPREAD_DAILY
        elif 8 <= dte <= 20: bucket = "weekly"; max_spread = MAX_SPREAD_WEEKLY
        elif 21 <= dte <= 45: bucket = "monthly"; max_spread = MAX_SPREAD_MONTHLY
        else: continue
        snap = chain.get(str(cs))
        if snap is None: continue
        quote = _field(snap, "latest_quote")
        bid = _as_float(_field(quote, "bid_price")) if quote else None
        ask = _as_float(_field(quote, "ask_price")) if quote else None
        if bid is None or ask is None or bid <= 0 or ask < bid: continue
        mid = (bid + ask) / 2
        if not MIN_MID <= mid <= MAX_MID: continue
        spread_pct = ((ask - bid) / mid) * 100
        if spread_pct > max_spread: continue
        oi = _as_float(_field(contract, "open_interest"), 0) or 0
        if oi < MIN_OI: continue
        daily = _field(snap, "daily_bar")
        vol = (_as_float(_field(daily, "volume"), 0) if daily else 0) or 0
        diff_pct = abs(strike - stock_price) / stock_price * 100
        if diff_pct > MAX_DIFF_PCT: continue
        if option_type == "CALL":
            be = strike + mid; be_move = max(0.0, be - stock_price)
        else:
            be = strike - mid; be_move = max(0.0, stock_price - be)
        exp_move = (atr_daily or 0) * math.sqrt(max(dte, 1))
        be_ratio = (be_move / exp_move) if exp_move > 0 else 0.0
        if exp_move > 0 and be_ratio > MAX_BE_RATIO: continue
        greeks = _field(snap, "greeks")
        delta = _as_float(_field(greeks, "delta")) if greeks else None
        gamma = _as_float(_field(greeks, "gamma")) if greeks else None
        lv = levels_intraday if bucket == "daily" else (levels_swing or levels_intraday)
        t1 = lv[1] if lv else None
        stop = lv[0] if lv else None
        def est_pct(px, _delta=delta, _gamma=gamma, _mid=mid):
            if px is None or _delta is None: return None
            mv = px - stock_price
            val = max(0.0, _mid + _delta * mv + 0.5 * (_gamma or 0.0) * mv * mv)
            return (val - _mid) / _mid * 100
        est_t1 = est_pct(t1); est_stop = est_pct(stop)
        ratio = vol / max(oi, 1)
        unusual = " 🔥 نشاط غير عادي" if ratio >= 2 and vol >= 100 else ""
        whale_tag = (" 🐋 دخول حيتان" if vol > 5000 or oi > 10000 else "") + unusual
        delta_pen = abs(abs(delta) - TARGET_DELTA) * 30 if delta is not None else 0.4 * diff_pct
        score = spread_pct + delta_pen + 10 * be_ratio - 0.5 * math.log1p(max(oi, 0)) - 2 * min(ratio, 5)
        buckets[bucket].append({"symbol": str(cs), "strike": strike, "exp": expiry.isoformat(), "dte": dte, "mid": mid, "bid": bid, "ask": ask,
                                "spread_pct": spread_pct, "oi": int(oi), "vol": int(vol), "whale": whale_tag, "diff": round(diff_pct, 1),
                                "be": be, "be_move": be_move, "be_move_pct": be_move / stock_price * 100, "exp_move": exp_move,
                                "est_t1": est_t1, "est_stop": est_stop, "score": score})
    result = {}
    for b, cands in buckets.items():
        if cands: result[b] = min(cands, key=lambda x: x["score"])
    return result

# ============ الحالة (تنحفظ في ملف عشان تتحمل إعادة التشغيل) ============
def _load_state():
    try: data = json.loads(STATE_FILE.read_text(encoding="utf-8")); return data if isinstance(data, dict) else {}
    except Exception: return {}
def _save_state(state):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e: logger.warning(f"فشل حفظ الحالة: {e}")
def _prune_state(state):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STATE_RETENTION_HOURS)
    kept = {}
    for k, v in state.items():
        try:
            stamp = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff: kept[k] = v
        except Exception: continue
    return kept
def _mark(state, key):
    state[key] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
def _last_sent(state, symbol):
    v = state.get(f"sent:{symbol}")
    if not v: return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None

# ============ سجل الإشارات والتتبع ============
_sig_lock = Lock()
def _load_signals():
    try:
        data = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception: return []
def _save_signals(sigs):
    try:
        tmp = SIGNALS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(sigs[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(SIGNALS_FILE)
    except Exception as e: logger.warning(f"فشل حفظ سجل الإشارات: {e}")
def log_signal(symbol, direction, entry, stop, targets, option):
    now = datetime.now(timezone.utc).isoformat()
    sig = {"id": f"{symbol}-{int(time.time())}", "symbol": symbol, "dir": direction, "entry": entry, "stop": stop,
           "targets": list(targets), "opt": option["symbol"] if option else None, "opt_mid": option["mid"] if option else None,
           "ts": now, "last_ts": now, "status": "open", "best": 0, "outcome": None, "exit_price": None, "pnl_pct": None}
    with _sig_lock:
        sigs = _load_signals(); sigs.append(sig); _save_signals(sigs)
def _close_signal(s, outcome, exit_price):
    s["status"] = "closed"; s["outcome"] = outcome; s["exit_price"] = round(float(exit_price), 2)
    sign = 1 if s["dir"] == "UP" else -1
    s["pnl_pct"] = round((exit_price - s["entry"]) / s["entry"] * 100 * sign, 2)
def _active_stop(s):
    # وقف متحرك: بعد هدف1 يصير على الدخول، بعد هدف2 يصير على هدف1
    b = s["best"]
    if b == 0: return s["stop"]
    if b == 1: return s["entry"]
    return s["targets"][b - 2]
def track_open_signals():
    with _sig_lock:
        open_syms = sorted({s["symbol"] for s in _load_signals() if s["status"] == "open"})
    if not open_syms: return
    try: frames = _fetch_frames(open_syms, TIMEFRAME_5M, 5)
    except Exception as e:
        logger.warning(f"فشل تتبع الإشارات: {e}"); return
    now_utc = pd.Timestamp.now(tz="UTC"); now_et = datetime.now(US_EASTERN)
    with _sig_lock:
        sigs = _load_signals(); changed = False
        for s in sigs:
            if s["status"] != "open": continue
            sig_date = datetime.fromisoformat(s["ts"]).astimezone(US_EASTERN).date()
            eod = now_et.date() > sig_date or now_et.hour >= 16
            f = frames.get(s["symbol"])
            if f is None or not len(f):
                if eod:
                    _close_signal(s, "EOD", s["entry"]); changed = True
                continue
            long = s["dir"] == "UP"
            new = f[f.index > pd.Timestamp(s["last_ts"])]
            new = new[new.index + pd.Timedelta(minutes=5) <= now_utc]
            for ts, row in new.iterrows():
                hi, lo = float(row["High"]), float(row["Low"])
                s["last_ts"] = ts.isoformat(); changed = True
                stop = _active_stop(s)
                if (lo <= stop) if long else (hi >= stop):
                    _close_signal(s, "STOP" if s["best"] == 0 else f"TRAIL{s['best']}", stop)
                    break
                for i, t in enumerate(s["targets"], 1):
                    if i > s["best"] and ((hi >= t) if long else (lo <= t)): s["best"] = i
                if s["best"] == len(s["targets"]):
                    _close_signal(s, f"T{s['best']}", s["targets"][-1]); break
            if s["status"] == "open" and eod:
                closed_f = f[f.index + pd.Timedelta(minutes=5) <= now_utc]
                last_px = float(closed_f["Close"].iloc[-1]) if len(closed_f) else s["entry"]
                _close_signal(s, "EOD", last_px); changed = True
        if changed: _save_signals(sigs)
def compute_stats():
    with _sig_lock: sigs = _load_signals()
    closed = [s for s in sigs if s["status"] == "closed"]
    counts = {}
    for s in closed: counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1
    pnl = [s["pnl_pct"] for s in closed if s.get("pnl_pct") is not None]
    wins = sum(1 for p in pnl if p > 0)
    hit_t1 = sum(1 for s in closed if s.get("best", 0) >= 1)
    return {"total": len(sigs), "open": len(sigs) - len(closed), "closed": len(closed), "outcomes": counts,
            "hit_t1_rate_pct": round(hit_t1 / len(closed) * 100, 1) if closed else None,
            "win_rate_pct": round(wins / len(pnl) * 100, 1) if pnl else None,
            "avg_stock_pnl_pct": round(sum(pnl) / len(pnl), 2) if pnl else None}

# ============ تيليجرام والتقرير ============
def _chunk_message(msg, limit=3900):
    if len(msg) <= limit: return [msg]
    chunks, cur = [], ""
    for line in msg.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur); cur = ""
        cur += line + "\n"
    if cur: chunks.append(cur)
    return chunks
def send_telegram(msg):
    ok_all = True
    for chunk in _chunk_message(msg):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                              json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=20)
            ok = r.ok and r.json().get("ok", False)
            if not ok: logger.warning(f"تيليجرام رفض الرسالة: {r.text[:200]}")
            ok_all = ok_all and ok
        except Exception as e:
            logger.warning(f"فشل إرسال تيليجرام: {e}"); ok_all = False
    return ok_all

def _strength_label(score):
    if score >= 70: return "قوي 🔥"
    if score >= 40: return "متوسط"
    return "ضعيف"

def build_report(symbol, price, hist, h15sym, hDsym, direction, sq, bo, drvol, trend_score, trend_details, gap_info, atr_daily, gex, reason=""):
    is_neutral = direction not in ("UP", "DOWN") or trend_score < 2
    if is_neutral: option_type = "محايد ⏸️"; emoji = "⚪"
    else: option_type = "كول" if direction == "UP" else "بوت"; emoji = "🟢" if option_type == "كول" else "🔴"
    rsi = calc_rsi(hist)
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "غير متوفر"
    dl = daily_levels(hDsym)
    sma_txt = f"متوسط 50 يومي: {dl['sma50']:.2f}$" if dl and dl["sma50"] else "متوسط 50 يومي: غير متوفر"
    if dl and direction == "UP":
        lv = dl["hi20"]
        lvl_txt = f"تم كسر مقاومة 20 يوم {lv:.2f}$ ✅" if price > lv else f"مقاومة 20 يوم {lv:.2f}$ (يبعد {lv - price:.2f}$)"
    elif dl and direction == "DOWN":
        lv = dl["lo20"]
        lvl_txt = f"تم كسر دعم 20 يوم {lv:.2f}$ ✅" if price < lv else f"دعم 20 يوم {lv:.2f}$ (يبعد {price - lv:.2f}$)"
    else:
        lvl_txt = "مستويات 20 يوم: غير متوفر"
    atr15 = calc_atr(h15sym) or ((calc_atr(hist) or 0) * 2) or None
    is_put = direction == "DOWN"
    stops = None if is_neutral else calc_levels(atr15, price, is_put=is_put)
    swing = None if is_neutral else calc_levels(atr_daily, price, is_put=is_put)
    lines = [f"📡 تنبيه فوري {emoji} <b>{html.escape(symbol)}</b> سعره <b>{price:.2f}$</b>"]
    if reason:
        lines.append(f"⚡ <b>سبب التنبيه:</b> {html.escape(reason)}")
    if gap_info:
        src = gap_info["source"]
        if gap_info["is_big"]:
            lines.append(f"⚠️ <b>قاب كبير جداً ({src}) {gap_info['gap_points']:+.2f}$ ({gap_info['gap_pct']:.1f}%) - لا تدخل أول 15 دقيقة</b>")
        else:
            valid_txt = "✅ صالح" if gap_info["is_valid"] else "❌ غير صالح"
            lines.append(f"🔍 <b>القاب ({src}):</b> {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} متوسط يومي) - {valid_txt}")
        if gap_info["is_valid"] and not gap_info["in_window"]:
            lines.append("⌛ انتهت نافذة قانون القاب - القاب للمعلومة فقط")
        if gap_info["active"] and not is_neutral:
            lines.append(f"⚡ <b>قانون القاب:</b> {'كول' if gap_info['is_up'] else 'بوت'} باتجاه القاب (نافذة أول {GAP_RULE_WINDOW_MIN} دقيقة)")
    else:
        lines.append("🔍 <b>القاب:</b> لا يوجد")
    if sq:
        state_txt = f"منضغط منذ {sq['bars']} شمعة" if sq["in_squeeze"] else "غير منضغط"
        lines.append(f"🗜️ الانضغاط: <b>{sq['score']}/100</b> ({_strength_label(sq['score'])}) - {state_txt} | أضيق من {sq['tight_pct']:.0f}%")
    if bo:
        status = "تم الكسر ✅ (قمة/قاع آخر 20 شمعة 5د)" if bo["broke"] else f"يبعد {bo['dist']:.1f} متوسط"
        lines.append(f"💥 الانفجار: <b>{bo['score']}/100</b> - {status}")
    vol_day = f"اليوم {drvol:.1f}×" if drvol is not None else "اليوم غير متوفر"
    vol_15 = f"آخر 15د {bo['rvol']:.1f}×" if bo else "آخر 15د غير متوفر"
    lines.append(f"📊 الفوليوم: {vol_day} | {vol_15}")
    if is_neutral:
        lines.append(f"🧭 الاتجاه: محايد ⏸️ ({' '.join(trend_details) if trend_details else 'لا إجماع'}) - لا تدخل")
    else:
        lines.append(f"🧭 الاتجاه: {option_type} | {' '.join(trend_details)} | القوة {trend_score}/3")
    lines.append(f"📊 {sma_txt} | {lvl_txt} | مؤشر القوة (5د): {rsi_txt}")
    lines.append(format_gex_line(gex, price))
    if gex and stops and not is_neutral:
        t1 = stops[1]
        if direction == "DOWN" and gex["put_wall"] < price and t1 < gex["put_wall"]:
            lines.append("⚠️ الهدف 1 تحت جدار البوت (دعم قوي قبله)")
        if direction == "UP" and gex["call_wall"] > price and t1 > gex["call_wall"]:
            lines.append("⚠️ الهدف 1 فوق جدار الكول (مقاومة قوية قبله)")
    info = {"option_type": None, "stops": stops, "option": None}
    if is_neutral:
        lines.append("\n⏸️ محايد - انتظر")
    else:
        info["option_type"] = "CALL" if direction == "UP" else "PUT"
        lines.append(f"\n🚀 دخول {option_type}: {price:.2f}$")
        if stops:
            stop, t1, t2, t3 = stops
            lines.extend([f"🛑 وقف (يومي/مضاربة): {stop:.2f}$ (1.5 متوسط على 15د)",
                          f"🎯 أهداف: {t1:.2f}$ / {t2:.2f}$ / {t3:.2f}$",
                          "🔁 بعد هدف1 حرّك الوقف للدخول"])
        if swing:
            lines.append(f"🛡️ للعقود الأسبوعي والشهري - وقف أوسع على اليومي: {swing[0]:.2f}$ | هدف1 {swing[1]:.2f}$")
        options = get_option_recommendations(symbol, price, info["option_type"], atr_daily,
                                             levels_intraday=stops, levels_swing=swing)
        labels = {"daily": "🔥 يومي", "weekly": "📅 أسبوعي", "monthly": "🛡️ شهري"}
        if options:
            for b in ("daily", "weekly", "monthly"):
                if b in options:
                    o = options[b]
                    if info["option"] is None: info["option"] = o
                    spread_d = o["ask"] - o["bid"]
                    if spread_d < 0.3: liq = f"سيولة ممتازة ✅ سبريد ${spread_d:.2f}"
                    elif spread_d < 0.6: liq = f"سيولة متوسطة ⚠️ سبريد ${spread_d:.2f}"
                    else: liq = f"سيولة ضعيفة ❌ سبريد ${spread_d:.2f}"
                    txt = (f"{labels[b]} {o['exp']} ({o['dte']} يوم) {o['whale']}\n"
                           f"💰 سترايك {o['strike']:.1f}$ - {o['mid']:.1f}$ | {liq} | فوليوم {o['vol']} مصلحة مفتوحة {o['oi']}\n"
                           f"⚖️ تعادل {o['be']:.2f}$ (يحتاج {o['be_move_pct']:.1f}%) | الحركة المتوقعة ±{o['exp_move']:.1f}$")
                    if o["est_t1"] is not None and o["est_stop"] is not None:
                        txt += f"\n📐 تقدير العقد: عند هدف1 {o['est_t1']:+.0f}% | عند الوقف {o['est_stop']:+.0f}% (بدون ثيتا)"
                    if o["dte"] <= 2: txt += "\n⏳ ينتهي قريب جداً - ثيتا عالية"
                    txt += f"\n🔹 <code>{html.escape(o['symbol'])}</code>\n"
                    lines.append(txt)
        else:
            lines.append("\n❌ ما فيه عقد يحقق شروط السيولة ونقطة التعادل")
    lines.append("\n⚠️ تعليمي فقط - مو نصيحة مالية")
    return "\n".join(lines), info

def build_weekly_report(results):
    lines = ["📊 <b>تقرير الجمعة الأسبوعي - خطة الأسبوع الجاي</b>", ""]
    if not results:
        lines.append("هذا الأسبوع ما فيه ولا شركة قوية على الأسبوعي - لا تدخل، انتظر")
        return "\n".join(lines)
    for i, r in enumerate(results[:3], 1):
        lines.append(f"<b>{i}. {html.escape(r['symbol'])} - {r['price']:.2f}$ - قوة أسبوعية {r['w_score']}/100</b>")
        lines.append(f"   {html.escape(r['w_desc'])}")
        lines.append(f"   الاتجاه: {r['direction']} | دخول {r['option_type']} | وقف {r['stop']:.2f}$ | هدف {r['t1']:.2f}$ (مستويات يومية)")
        lines.append("")
    lines.append("الباقي: ما فيها فرصة أسبوعية، تجاهلها")
    lines.append("\n⚠️ تعليمي فقط")
    return "\n".join(lines)

# ============ الحلقة الرئيسية ============
def main_loop():
    state = _prune_state(_load_state())
    last_track = 0.0
    last_pre_bucket = None
    last_scan_bucket = None
    send_telegram("✅ <b>تم تشغيل البوت - نسخة معدلة</b>\n"
                  f"مراقبة {len(SYMBOLS)} شركة\n"
                  "📡 فحص مع كل إغلاق شمعة 5 دقايق - شموع مكتملة فقط\n"
                  "💣 جديد: تنبيه انفجار + نقطة دافعة + فاصل + غاما\n"
                  "📊 تقرير الجمعة الأسبوعي بعد الإغلاق")
    while True:
        try:
            now_et = datetime.now(US_EASTERN)
            if time.time() - last_track >= 300:
                track_open_signals(); last_track = time.time()
            bucket = (now_et.date(), now_et.hour, now_et.minute // 5)

            # === تقرير الجمعة - بعد 4:15 عصراً نيويورك، مرة وحدة ===
            weekly_key = f"weekly:{now_et.date()}"
            if (now_et.weekday() == 4 and (now_et.hour, now_et.minute) >= (16, 15)
                    and weekly_key not in state and not _time_based_open()):
                try:
                    h5, h15, hD, hW, prices = fetch_market_data(SYMBOLS, force_slow=True)
                    today = now_et.date()
                    traded_today = any(f.index[-1].date() == today for f in hD.values() if len(f))
                    if traded_today:
                        results = []
                        for symbol in SYMBOLS:
                            price = prices.get(symbol)
                            if not price: continue
                            w_score, w_desc, w_dir = weekly_score(hW.get(symbol))
                            if w_score < 40: continue
                            direction = pick_direction(symbol, h5, h15, hD)
                            if direction == "NEUTRAL" or w_dir != direction: continue
                            atr_d = calc_atr(hD.get(symbol))
                            stops = calc_levels(atr_d, price, is_put=(direction == "DOWN"))
                            if not stops: continue
                            results.append({"symbol": symbol, "price": price, "w_score": w_score, "w_desc": w_desc,
                                            "direction": "صاعد" if direction == "UP" else "هابط",
                                            "option_type": "كول" if direction == "UP" else "بوت",
                                            "stop": stops[0], "t1": stops[1]})
                        results.sort(key=lambda x: x["w_score"], reverse=True)
                        if send_telegram(build_weekly_report(results)):
                            _mark(state, weekly_key)
                    else:
                        _mark(state, weekly_key)  # الجمعة إجازة
                except Exception as e:
                    logger.exception(f"خطأ تقرير الجمعة: {e}")
                    _mark(state, weekly_key)

            # === البري ماركت (4:00 - 9:30) - مرة كل 5 دقايق ===
            if ((4 <= now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30))
                    and bucket != last_pre_bucket and now_et.second >= 15 and is_premarket_trading_day()):
                last_pre_bucket = bucket
                h5, h15, hD, hW, prices = fetch_market_data(SYMBOLS)
                for symbol in SYMBOLS:
                    price = prices.get(symbol)
                    if not price: continue
                    key = f"pre:{symbol}:{now_et.date()}"
                    if key in state: continue
                    atr_d = calc_atr(hD.get(symbol))
                    gap_info = calc_gap_metrics(symbol, hD, price, atr_d)
                    if gap_info and gap_info["is_valid"]:
                        gex = compute_gex(symbol, price)
                        msg = (
                            f"⏰ <b>تنبيه قاب وغاما قبل الافتتاح {html.escape(symbol)}</b>\n"
                            f"💰 بري ماركت: {price:.2f}$ (إغلاق أمس {gap_info['prev_close']:.2f}$)\n"
                            f"🔍 القاب: {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} متوسط يومي)\n"
                            f"{format_gex_line(gex, price)}\n"
                        )
                        if gap_info["is_big"]:
                            msg += "⚠️ قاب كبير جداً - لا تدخل أول 15 دقيقة بعد الافتتاح\n"
                        msg += "⏳ جهز العقد وانتظر تأكيد أول 5 دقايق"
                        if send_telegram(msg):
                            _mark(state, key)
                        time.sleep(SEND_GAP_SECONDS)

            # === داخل السوق ===
            if not is_market_open():
                state = _prune_state(state)
                time.sleep(30)
                continue

            # نفحص مرة وحدة لكل شمعة 5د مكتملة (بعد 15 ثانية من الإغلاق)
            if bucket == last_scan_bucket or now_et.second < 15:
                time.sleep(5)
                continue
            last_scan_bucket = bucket

            h5, h15, hD, hW, prices = fetch_market_data(SYMBOLS)

            # === جديد: شرط الانفجار + النقطة الدافعة + الفاصل + غاما (مستقل عن السكويز) ===
            run_explosion_scan(state, hD, hW, prices)

            candidates = []
            for symbol in SYMBOLS:
                try:
                    hist = h5.get(symbol)
                    hist15 = h15.get(symbol)
                    price = prices.get(symbol)
                    if hist is None or hist15 is None or price is None or len(hist) < 30: continue

                    last_sent = _last_sent(state, symbol)
                    if last_sent and (datetime.now(timezone.utc) - last_sent).total_seconds() < RESEND_BLOCK_SECONDS:
                        continue

                    atr_daily = calc_atr(hD.get(symbol))
                    gap_info = calc_gap_metrics(symbol, hD, price, atr_daily)
                    direction = pick_direction(symbol, h5, h15, hD)
                    if gap_info and gap_info["active"]:
                        direction = "UP" if gap_info["is_up"] else "DOWN"
                    if direction == "NEUTRAL": continue

                    sq = squeeze_metrics(hist)
                    bo = breakout_for(hist, direction)
                    if not bo: continue

                    last15 = hist15.iloc[-1]  # آخر شمعة 15د مكتملة
                    last5 = hist.iloc[-1]     # آخر شمعة 5د مكتملة
                    mar15 = aligned_marubozu(last15, direction)
                    mar5 = aligned_marubozu(last5, direction)

                    fD = hD.get(symbol)
                    open_today = float(fD["Open"].iloc[-1]) if fD is not None and len(fD) else price
                    move_today_pct = abs(price - open_today) / open_today * 100 if open_today else 0
                    min_move = min_move_for_symbol(symbol)

                    dl = daily_levels(fD)
                    daily_broke = bool(dl and ((price > dl["hi20"]) if direction == "UP" else (price < dl["lo20"])))

                    score = (sq["score"] if sq else 0) * 0.4 + bo["score"] * 0.6
                    is_breakout = bo["broke"] and (mar15 or mar5) and bo["rvol"] >= 1.2
                    is_strong_enough = is_breakout or (move_today_pct >= min_move and bo["broke"] and bo["rvol"] >= 1.0)

                    if not is_strong_enough: continue
                    if score < MIN_SCORE: continue  # الفلتر يطبق على الكل بدون استثناء

                    parts = []
                    if is_breakout and move_today_pct < min_move:
                        parts.append(f"بداية الحركة (لسا {move_today_pct:.2f}% فقط)")
                    else:
                        parts.append(f"تحرك {move_today_pct:.2f}% اليوم (المطلوب {min_move:.0f}%)")
                    parts.append("كسر قمة/قاع آخر 20 شمعة 5د")
                    if daily_broke: parts.append("كسر مستوى 20 يوم ✅")
                    if mar15 or mar5: parts.append("شمعة ماروبوزو باتجاه الكسر")
                    parts.append(f"فوليوم {bo['rvol']:.1f}×")
                    reason = " + ".join(parts)

                    tscore, tdetails = get_trend_strength(symbol, h5, h15, hD, direction)
                    if tscore < 2: continue

                    candidates.append({
                        "symbol": symbol, "price": price, "hist": hist, "h15": hist15, "hD": fD,
                        "direction": direction, "sq": sq, "bo": bo, "gap": gap_info, "atr_daily": atr_daily,
                        "tscore": tscore, "tdetails": tdetails, "score": score, "reason": reason,
                        "move_pct": move_today_pct
                    })
                except Exception as e:
                    logger.exception(f"خطأ {symbol}: {e}")

            if candidates:
                candidates.sort(key=lambda x: x["score"], reverse=True)
                to_send = [candidates[0]]
                if len(candidates) > 1 and candidates[1]["score"] >= 80:
                    to_send.append(candidates[1])
                for c in to_send:
                    gex = compute_gex(c["symbol"], c["price"])
                    drvol = day_rvol(hD, c["symbol"])
                    msg, info = build_report(c["symbol"], c["price"], c["hist"], c["h15"], c["hD"],
                                             c["direction"], c["sq"], c["bo"], drvol,
                                             c["tscore"], c["tdetails"], c["gap"], c["atr_daily"], gex,
                                             reason=c["reason"])
                    if send_telegram(msg):
                        _mark(state, f"sent:{c['symbol']}")
                        if info["option_type"] and info["stops"]:
                            st = info["stops"]
                            log_signal(c["symbol"], c["direction"], c["price"], st[0], st[1:], info["option"])
                    time.sleep(SEND_GAP_SECONDS)
            state = _prune_state(state)
        except Exception as e:
            logger.exception(f"خطأ رئيسي: {e}")
            time.sleep(20)

@app.get("/")
def home(): return "Bot OK - كسر مبكر + انفجار + تقرير جمعة"
@app.get("/health")
def health(): return jsonify({"status": "ok", "symbols": len(SYMBOLS), "market_open": is_market_open()})
@app.get("/stats")
def stats(): return jsonify(compute_stats())

# ============ تشغيل العامل (نسخة وحدة فقط) ============
_worker_lock = Lock()
_worker_started = False
_lock_fh = None
def _acquire_single_instance() -> bool:
    """يمنع تشغيل أكثر من حلقة (مثلاً gunicorn بعدة workers)"""
    global _lock_fh
    try:
        import fcntl
        fh = open("/tmp/squeeze_bot.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh = fh
        return True
    except ImportError:
        return True
    except OSError:
        return False
def start_background_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started: return
        if not _acquire_single_instance():
            logger.warning("عامل ثاني شغال أصلاً - ما راح أشغل حلقة مكررة")
            return
        Thread(target=main_loop, name="squeeze-worker", daemon=True).start()
        _worker_started = True
start_background_worker()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True, use_reloader=False)
