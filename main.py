from __future__ import annotations
import json, logging, math, os, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = ["SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP", "ARM", "AVGO", "MU", "LITE", "RDDT", "SNDK"]
SYMBOLS = TIER1 + TIER2

TIMEFRAME_5M = TimeFrame(5, TimeFrameUnit.Minute)
TIMEFRAME_15M = TimeFrame(15, TimeFrameUnit.Minute)
TIMEFRAME_1D = TimeFrame(1, TimeFrameUnit.Day)
HISTORY_DAYS = 10
HISTORY_DAYS_DAILY = 150          # كان 60 (لا يكفي لـ EMA50 اليومي)
STATE_FILE = Path("squeeze_state.json")
SIGNALS_FILE = Path("signals_log.json")
REPORT_EVERY_MINUTES = int(os.getenv("REPORT_EVERY_MINUTES", "60"))
MIN_REPORT_SCORE = int(os.getenv("MIN_REPORT_SCORE", "0"))
SEND_GAP_SECONDS = 3.5
_feed_env = os.getenv("STOCK_FEED", "").strip().lower()
STOCK_FEED = DataFeed.SIP if _feed_env == "sip" else DataFeed.IEX if _feed_env == "iex" else None

# --- فلتر العقود (أشد من قبل) ---
MAX_SPREAD_DAILY = 15
MAX_SPREAD_WEEKLY = 12
MAX_SPREAD_MONTHLY = 10
MIN_OI = 100
MIN_MID = 0.5
MAX_MID = 15.0
MAX_DIFF_PCT = 8
MAX_BE_RATIO = 1.2                # أقصى مسافة تعادل نسبةً للحركة المتوقعة حتى الانتهاء
TARGET_DELTA = 0.40

# --- القاب ---
US_EASTERN = ZoneInfo("America/New_York")
GAP_MIN_PCT = 2.0
GAP_MAX_PCT = 8.0
BIG_GAP_PCT = 5.0                 # بدل 40$ ثابتة
GAP_RULE_WINDOW_MIN = 60          # قانون القاب يعمل فقط في أول ساعة بعد الافتتاح (وفي البري ماركت)

# --- الوقف والأهداف ---
STOP_ATR_MULT = 1.5               # على ATR شمعة 15 دقيقة
R_MULTS = (1.5, 3.0, 5.0)

# --- GEX ---
GEX_MAX_DTE = 30
GEX_CACHE_TTL = 600
RISK_FREE = 0.04

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("squeeze_bot")
stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


# ====================== أدوات عامة ======================
def _field(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
def _as_float(v: Any, d=None):
    try:
        r = float(v)
        return r if math.isfinite(r) else d
    except Exception:
        return d
def _as_date(v: Any):
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    try: return date.fromisoformat(str(v)[:10])
    except Exception: return None
def is_market_open() -> bool:
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5: return False
    return now.replace(hour=9, minute=30, second=0) <= now <= now.replace(hour=16, minute=0, second=0)
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
def fetch_market_data(symbols):
    h5 = _fetch_frames(symbols, TIMEFRAME_5M, HISTORY_DAYS)
    h15 = _fetch_frames(symbols, TIMEFRAME_15M, HISTORY_DAYS)
    hD = _fetch_frames(symbols, TIMEFRAME_1D, HISTORY_DAYS_DAILY)
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
    return h5, h15, hD, prices


# ====================== المؤشرات ======================
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
    """وقف = 1.5 × ATR(15m). الأهداف = مضاعفات R من مسافة الوقف."""
    if not atr or entry <= 0: return None
    risk = STOP_ATR_MULT * atr
    if is_put:
        vals = (entry + risk, *[entry - m * risk for m in R_MULTS])
    else:
        vals = (entry - risk, *[entry + m * risk for m in R_MULTS])
    return tuple(round(x, 2) for x in vals)
def calc_gap_metrics(symbol, hD, current_price, atr_daily):
    """القاب الحقيقي = افتتاح اليوم - إغلاق أمس. قبل الافتتاح: سعر البري ماركت - إغلاق أمس."""
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
    """دعم/مقاومة آخر 20 يوم + SMA50 يومي (من الشموع اليومية، مو الـ5 دقائق)."""
    if f is None or len(f) < 22: return None
    today = datetime.now(US_EASTERN).date()
    prior = f.iloc[:-1].tail(20) if f.index[-1].date() == today else f.tail(20)
    sma50 = float(f["Close"].rolling(50).mean().iloc[-1]) if len(f) >= 50 else None
    return {"hi20": float(prior["High"].max()), "lo20": float(prior["Low"].min()), "sma50": sma50}
def pick_direction(symbol, h5, h15, hD):
    """UP / DOWN / NEUTRAL. التعادل صار محايد (كان يميل لـ UP)."""
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
    """القوة = عدد الأطر الموافقة فعلياً للاتجاه (0 إلى 3). بدون نقطة مجانية."""
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
        add("5m", c.iloc[-1] >= c.rolling(20).mean().iloc[-1])
    except Exception: details.append("5m؟")
    try:
        if len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]
            add("15m", c.ewm(span=9).mean().iloc[-1] > c.ewm(span=20).mean().iloc[-1])
        else: details.append("15m؟")
    except Exception: details.append("15m؟")
    try:
        if len(hD[symbol]) >= 50:
            c = hD[symbol]["Close"]
            add("يومي", c.iloc[-1] > c.ewm(span=50).mean().iloc[-1])
        else: details.append("يومي؟")
    except Exception: details.append("يومي؟")
    return score, details


# ====================== GEX الحقيقي ======================
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
    """GEX حقيقي من الـ OI والـ IV: Call Wall / Put Wall / Gamma Flip / صافي GEX (بالدولار لكل 1%)."""
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
        logger.warning(f"فشل حساب GEX {symbol}: {e}")
        return None
def format_gex_line(gex, price):
    if not gex: return "🧱 غاما: غير متاح (بيانات الخيارات غير كافية)"
    parts = [f"Call Wall {gex['call_wall']:.0f}$", f"Put Wall {gex['put_wall']:.0f}$"]
    if gex.get("flip"): parts.append(f"Flip {gex['flip']:.1f}$")
    regime = "جاما موجبة ✅ (تهدئة)" if gex["net_gex"] > 0 else "جاما سالبة ⚠️ (تضخيم)"
    pos = ""
    if gex.get("flip"): pos = " | السعر فوق الـ Flip" if price > gex["flip"] else " | السعر تحت الـ Flip"
    return f"🧱 غاما: {' | '.join(parts)}\n    {regime} ({gex['net_gex'] / 1e6:+.1f}M){pos}"


# ====================== العقود ======================
def get_option_recommendations(symbol, stock_price, option_type, atr_daily, t1=None, stop=None):
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

        # --- نقطة التعادل مقابل الحركة المتوقعة حتى الانتهاء ---
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

        def est_pct(px):
            if px is None or delta is None: return None
            mv = px - stock_price
            val = max(0.0, mid + delta * mv + 0.5 * (gamma or 0.0) * mv * mv)
            return (val - mid) / mid * 100
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


# ====================== الحالة وتسجيل الإشارات ======================
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
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    kept = {}
    for k, v in state.items():
        try:
            stamp = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff: kept[k] = v
        except Exception: continue
    return kept
def _last_sent(state, symbol):
    v = state.get(f"sent:{symbol}")
    if not v: return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None

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
def track_open_signals():
    """يفحص الإشارات المفتوحة على شموع 5د المكتملة: أي هدف تحقق أولاً أو الوقف. الوقف يُفحص قبل الهدف داخل نفس الشمعة (تحفظ)."""
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
            f = frames.get(s["symbol"])
            if f is None: continue
            long = s["dir"] == "UP"
            new = f[f.index > pd.Timestamp(s["last_ts"])]
            new = new[new.index + pd.Timedelta(minutes=5) <= now_utc]
            for ts, row in new.iterrows():
                hi, lo = float(row["High"]), float(row["Low"])
                s["last_ts"] = ts.isoformat(); changed = True
                if (lo <= s["stop"]) if long else (hi >= s["stop"]):
                    if s["best"] == 0: _close_signal(s, "STOP", s["stop"])
                    else: _close_signal(s, f"T{s['best']}", s["targets"][s["best"] - 1])
                    break
                for i, t in enumerate(s["targets"], 1):
                    if i > s["best"] and ((hi >= t) if long else (lo <= t)): s["best"] = i
                if s["best"] == len(s["targets"]):
                    _close_signal(s, f"T{s['best']}", s["targets"][-1]); break
            if s["status"] == "open":
                sig_date = datetime.fromisoformat(s["ts"]).astimezone(US_EASTERN).date()
                if now_et.date() > sig_date or now_et.hour >= 16:
                    last_px = float(f["Close"].iloc[-1]) if len(f) else s["entry"]
                    if s["best"] > 0: _close_signal(s, f"T{s['best']}", s["targets"][s["best"] - 1])
                    else: _close_signal(s, "EOD", last_px)
                    changed = True
        if changed: _save_signals(sigs)
def compute_stats():
    with _sig_lock: sigs = _load_signals()
    closed = [s for s in sigs if s["status"] == "closed"]
    counts = {}
    for s in closed: counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1
    wins = sum(1 for s in closed if s["outcome"] in ("T1", "T2", "T3"))
    pnl = [s["pnl_pct"] for s in closed if s.get("pnl_pct") is not None]
    return {"total": len(sigs), "open": len(sigs) - len(closed), "closed": len(closed), "outcomes": counts,
            "hit_t1_rate_pct": round(wins / len(closed) * 100, 1) if closed else None,
            "avg_stock_pnl_pct": round(sum(pnl) / len(pnl), 2) if pnl else None}


# ====================== تيليجرام والتقرير ======================
def send_telegram(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
        ok = r.ok and r.json().get("ok", False)
        if not ok: logger.warning(f"تيليجرام رفض الرسالة: {r.text[:200]}")
        return ok
    except Exception as e: logger.warning(f"فشل إرسال تيليجرام: {e}"); return False
def _strength_label(score):
    if score >= 70: return "قوي 🔥"
    if score >= 40: return "متوسط"
    return "ضعيف"
def build_report(symbol, price, hist, h15sym, hDsym, direction, sq, bo, drvol, trend_score, trend_details, gap_info, atr_daily, gex):
    is_neutral = direction not in ("UP", "DOWN") or trend_score < 2
    if is_neutral: option_type = "محايد ⏸️"; emoji = "⚪"
    else: option_type = "CALL" if direction == "UP" else "PUT"; emoji = "🟢" if option_type == "CALL" else "🔴"
    rsi = calc_rsi(hist)
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
    dl = daily_levels(hDsym)
    sma_txt = f"SMA50 يومي: {dl['sma50']:.2f}$" if dl and dl["sma50"] else "SMA50 يومي: N/A"
    if dl and direction == "UP":
        lv = dl["hi20"]
        lvl_txt = f"تم كسر مقاومة 20 يوم {lv:.2f}$ ✅" if price > lv else f"مقاومة 20 يوم {lv:.2f}$ (يبعد {lv - price:.2f}$)"
    elif dl and direction == "DOWN":
        lv = dl["lo20"]
        lvl_txt = f"تم كسر دعم 20 يوم {lv:.2f}$ ✅" if price < lv else f"دعم 20 يوم {lv:.2f}$ (يبعد {price - lv:.2f}$)"
    else:
        lvl_txt = "مستويات 20 يوم: N/A"
    atr15 = calc_atr(h15sym) or ((calc_atr(hist) or 0) * 2) or None
    stops = None if is_neutral else calc_levels(atr15, price, is_put=(direction == "DOWN"))

    lines = [f"📡 تقرير الساعة {emoji} <b>{symbol}</b> سعره <b>{price:.2f}$</b>"]
    if gap_info:
        src = gap_info["source"]
        if gap_info["is_big"]:
            lines.append(f"⚠️ <b>قاب كبير جداً ({src}) {gap_info['gap_points']:+.2f}$ ({gap_info['gap_pct']:.1f}%) - لا تدخل أول 15 دقيقة</b>")
        else:
            valid_txt = "✅ صالح" if gap_info["is_valid"] else "❌ غير صالح"
            lines.append(f"🔍 <b>القاب ({src}):</b> {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} ATR يومي) - {valid_txt}")
        if gap_info["is_valid"] and not gap_info["in_window"]:
            lines.append("⌛ انتهت نافذة قانون القاب - القاب للمعلومة فقط")
        if gap_info["active"] and not is_neutral:
            lines.append(f"⚡ <b>قانون القاب:</b> {'CALL' if gap_info['is_up'] else 'PUT'} باتجاه القاب (نافذة أول {GAP_RULE_WINDOW_MIN} دقيقة)")
    else:
        lines.append("🔍 <b>القاب:</b> لا يوجد")
    if sq:
        state_txt = f"منضغط منذ {sq['bars']} شمعة" if sq["in_squeeze"] else "غير منضغط"
        lines.append(f"🗜️ الانضغاط: <b>{sq['score']}/100</b> ({_strength_label(sq['score'])}) - {state_txt} | أضيق من {sq['tight_pct']:.0f}%")
    if bo:
        status = "تم الكسر ✅" if bo["broke"] else f"يبعد {bo['dist']:.1f} ATR"
        lines.append(f"💥 الانفجار: <b>{bo['score']}/100</b> - {status}")
    vol_day = f"اليوم {drvol:.1f}×" if drvol is not None else "اليوم N/A"
    vol_15 = f"آخر 15د {bo['rvol']:.1f}×" if bo else "آخر 15د N/A"
    lines.append(f"📊 الفوليوم: {vol_day} | {vol_15}")
    if is_neutral:
        lines.append(f"🧭 الاتجاه: محايد ⏸️ ({' '.join(trend_details) if trend_details else 'لا إجماع'}) - لا تدخل")
    else:
        lines.append(f"🧭 الاتجاه: {option_type} | {' '.join(trend_details)} | القوة {trend_score}/3")
    lines.append(f"📊 {sma_txt} | {lvl_txt} | RSI(5د): {rsi_txt}")
    lines.append(format_gex_line(gex, price))
    if gex and stops and not is_neutral:
        t1 = stops[1]
        if direction == "DOWN" and gex["put_wall"] < price and t1 < gex["put_wall"]:
            lines.append("⚠️ الهدف 1 تحت Put Wall (دعم قوي قبله)")
        if direction == "UP" and gex["call_wall"] > price and t1 > gex["call_wall"]:
            lines.append("⚠️ الهدف 1 فوق Call Wall (مقاومة قوية قبله)")

    info = {"option_type": None, "stops": stops, "option": None}
    if is_neutral:
        lines.append("\n⏸️ محايد - انتظر")
    else:
        info["option_type"] = option_type
        lines.append(f"\n🚀 دخول {option_type}: {price:.2f}$")
        if stops:
            stop, t1, t2, t3 = stops
            lines.extend([f"🛑 وقف: {stop:.2f}$ (1.5 ATR على 15د)", f"🎯 أهداف: {t1:.2f}$ / {t2:.2f}$ / {t3:.2f}$"])
        options = get_option_recommendations(symbol, price, option_type, atr_daily, t1=stops[1] if stops else None, stop=stops[0] if stops else None)
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
                           f"💰 سترايك {o['strike']:.1f}$ - {o['mid']:.1f}$ | {liq} | فوليوم {o['vol']} OI {o['oi']}\n"
                           f"⚖️ تعادل {o['be']:.2f}$ (يحتاج {o['be_move_pct']:.1f}%) | الحركة المتوقعة ±{o['exp_move']:.1f}$")
                    if o["est_t1"] is not None and o["est_stop"] is not None:
                        txt += f"\n📐 تقدير العقد: عند هدف1 {o['est_t1']:+.0f}% | عند الوقف {o['est_stop']:+.0f}% (بدون ثيتا)"
                    if o["dte"] <= 2: txt += "\n⏳ ينتهي قريب جداً - ثيتا عالية"
                    txt += f"\n🔹 <code>{o['symbol']}</code>\n"
                    lines.append(txt)
        else:
            lines.append("\n❌ ما فيه عقد يحقق شروط السيولة ونقطة التعادل")
    lines.append("\n⚠️ تعليمي فقط")
    return "\n".join(lines), info


# ====================== الحلقة الرئيسية ======================
def main_loop():
    state = _prune_state(_load_state())
    pre_sent = {}
    last_track = 0.0
    send_telegram(f"✅ <b>تم تشغيل الماسح (نسخة مصححة: GEX حقيقي + قاب فعلي + تسجيل الإشارات)</b>\nمراقبة {len(SYMBOLS)} شركة\n📡 تقرير كل {REPORT_EVERY_MINUTES} دقيقة بعد الافتتاح")
    while True:
        try:
            now_et = datetime.now(US_EASTERN)

            # === تتبع نتائج الإشارات كل 5 دقائق ===
            if time.time() - last_track >= 300:
                track_open_signals(); last_track = time.time()

            # === البري ماركت (4:00 - 9:30 ET) ===
            if now_et.weekday() < 5 and (4 <= now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30)):
                h5, h15, hD, prices = fetch_market_data(SYMBOLS)
                for symbol in SYMBOLS:
                    price = prices.get(symbol)
                    if not price: continue
                    key = f"pre:{symbol}:{now_et.date()}"
                    if key in pre_sent: continue
                    atr_d = calc_atr(hD.get(symbol))
                    gap_info = calc_gap_metrics(symbol, hD, price, atr_d)
                    if gap_info and gap_info["is_valid"]:
                        gex = compute_gex(symbol, price)
                        msg = (
                            f"⏰ <b>تنبيه قاب وغاما قبل الافتتاح {symbol}</b>\n"
                            f"💰 بري ماركت: {price:.2f}$ (إغلاق أمس {gap_info['prev_close']:.2f}$)\n"
                            f"🔍 القاب: {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} ATR يومي)\n"
                            f"{format_gex_line(gex, price)}\n"
                        )
                        if gap_info["is_big"]:
                            msg += "⚠️ قاب كبير جداً - لا تدخل أول 15 دقيقة بعد الافتتاح\n"
                        msg += "⏳ جهز العقد وانتظر تأكيد أول 5 دقايق"
                        send_telegram(msg)
                        pre_sent[key] = True
                        time.sleep(SEND_GAP_SECONDS)
                if len(pre_sent) > 100: pre_sent.clear()

            # === بعد الافتتاح ===
            if not is_market_open():
                time.sleep(60)
                continue
            state = _prune_state(state)
            now_utc = datetime.now(timezone.utc)
            due = []
            for s in SYMBOLS:
                ls = _last_sent(state, s)
                if ls is None or (now_utc - ls).total_seconds() >= REPORT_EVERY_MINUTES * 60:
                    due.append(s)
            if due:
                h5, h15, hD, prices = fetch_market_data(due)
                for symbol in due:
                    try:
                        hist = h5.get(symbol)
                        price = prices.get(symbol)
                        if hist is None or price is None or len(hist) < 30: continue
                        atr_daily = calc_atr(hD.get(symbol))
                        gap_info = calc_gap_metrics(symbol, hD, price, atr_daily)
                        direction = pick_direction(symbol, h5, h15, hD)
                        if gap_info and gap_info["active"]:
                            direction = "UP" if gap_info["is_up"] else "DOWN"
                        sq = squeeze_metrics(hist)
                        bo = breakout_for(hist, direction)
                        drvol = day_rvol(hD, symbol)
                        best = max(sq["score"] if sq else 0, bo["score"] if bo else 0)
                        force_send = bool(gap_info and gap_info["active"])
                        if best >= MIN_REPORT_SCORE or force_send:
                            tscore, tdetails = get_trend_strength(symbol, h5, h15, hD, direction)
                            gex = compute_gex(symbol, price)
                            msg, info = build_report(symbol, price, hist, h15.get(symbol), hD.get(symbol), direction, sq, bo, drvol,
                                                     tscore, tdetails, gap_info, atr_daily, gex)
                            if not send_telegram(msg): continue
                            if info["option_type"] and info["stops"]:
                                st = info["stops"]
                                log_signal(symbol, direction, price, st[0], st[1:], info["option"])
                            time.sleep(SEND_GAP_SECONDS)
                        state[f"sent:{symbol}"] = datetime.now(timezone.utc).isoformat()
                        _save_state(state)
                    except Exception as e:
                        logger.exception(f"خطأ {symbol}: {e}")
            time.sleep(30)
        except Exception as e:
            logger.exception(f"خطأ رئيسي: {e}")
            time.sleep(20)


@app.get("/")
def home(): return "Bot OK - Real GEX + Gap + Hourly + Signal Tracking"
@app.get("/health")
def health(): return jsonify({"status": "ok", "symbols": len(SYMBOLS), "market_open": is_market_open()})
@app.get("/stats")
def stats(): return jsonify(compute_stats())

_worker_lock = Lock()
_worker_started = False
def start_background_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started: return
        Thread(target=main_loop, name="squeeze-worker", daemon=True).start()
        _worker_started = True
start_background_worker()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True, use_reloader=False)
