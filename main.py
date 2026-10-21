"""
ماسح الانضغاط والانفجار + القاب - قبل السوق وبعد السوق
5m + 15m + Daily - نفس الشركات 19
"""
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
STATE_FILE = Path("squeeze_state.json")
REPORT_EVERY_MINUTES = int(os.getenv("REPORT_EVERY_MINUTES", "60"))
MIN_REPORT_SCORE = int(os.getenv("MIN_REPORT_SCORE", "0"))
SEND_GAP_SECONDS = 3.5
_feed_env = os.getenv("STOCK_FEED", "").strip().lower()
STOCK_FEED = DataFeed.SIP if _feed_env == "sip" else DataFeed.IEX if _feed_env == "iex" else None
MAX_SPREAD_DAILY = 60
MAX_SPREAD_WEEKLY = 60
MAX_SPREAD_MONTHLY = 60
MIN_OI = 10
MIN_MID = 0.5
MAX_MID = 15.0
MAX_DIFF_PCT = 8
US_EASTERN = ZoneInfo("America/New_York")
GAP_MIN_PCT = 2.0
GAP_MAX_PCT = 8.0

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("squeeze_bot")
stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

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
    hD = _fetch_frames(symbols, TIMEFRAME_1D, 60)
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
    if len(hist) < period + 1: return None
    pc = hist["Close"].shift(1)
    tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - pc).abs(), (hist["Low"] - pc).abs()], axis=1).max(axis=1)
    v = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else None
def calc_levels(hist, entry, is_put=False):
    atr = calc_atr(hist)
    if not atr or entry <= 0: return None
    if is_put: vals = (entry + atr, entry - 1.5 * atr, entry - 3 * atr, entry - 5 * atr)
    else: vals = (entry - atr, entry + 1.5 * atr, entry + 3 * atr, entry + 5 * atr)
    return tuple(round(x, 2) for x in vals)
def calc_gap_metrics(symbol, hD, current_price, atr_val):
    try:
        f = hD.get(symbol)
        if f is None or len(f) < 2: return None
        prev_close = float(f["Close"].iloc[-2]) if f.index[-1].date() == datetime.now(US_EASTERN).date() else float(f["Close"].iloc[-1])
        if prev_close <= 0: return None
        gap_points = current_price - prev_close
        gap_pct = (gap_points / prev_close) * 100
        gap_atr = abs(gap_points) / atr_val if atr_val else 0
        is_valid = GAP_MIN_PCT <= abs(gap_pct) <= GAP_MAX_PCT
        gap_type = "أب ⬆️" if gap_points > 0 else "داون ⬇️" if gap_points < 0 else "بدون"
        return {"prev_close": prev_close, "gap_points": gap_points, "gap_pct": gap_pct, "gap_atr": gap_atr, "gap_type": gap_type, "is_valid": is_valid, "is_up": gap_points > 0}
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
    return "UP" if up * 2 >= total else "DOWN"
def get_trend_strength(symbol, h5, h15, hD, sig_dir):
    score = 1; details = []
    try:
        if symbol in hD and len(hD[symbol]) >= 50:
            dc = hD[symbol]["Close"]; ema50 = dc.ewm(span=50).mean().iloc[-1]
            if sig_dir == "UP" and dc.iloc[-1] > ema50: score += 1; details.append("يومي صاعد")
            elif sig_dir == "DOWN" and dc.iloc[-1] < ema50: score += 1; details.append("يومي هابط")
            else: details.append("يومي عكس")
    except Exception: pass
    try:
        if symbol in h15 and len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]; e9 = c.ewm(span=9).mean().iloc[-1]; e20 = c.ewm(span=20).mean().iloc[-1]
            if sig_dir == "UP" and e9 > e20: score += 1; details.append("15m صاعد")
            elif sig_dir == "DOWN" and e9 < e20: score += 1; details.append("15m هابط")
            else: details.append("15m عكس")
    except Exception: pass
    details.insert(0, "5m")
    return score, details
def get_option_recommendations(symbol, stock_price, option_type):
    if stock_price <= 0: return {}
    target = ContractType.CALL if option_type == "CALL" else ContractType.PUT
    today = datetime.now(timezone.utc).date()
    max_expiry = today + timedelta(days=45)
    if option_type == "CALL": low = stock_price * 0.99; high = stock_price * 1.04
    else: low = stock_price * 0.96; high = stock_price * 1.01
    all_contracts = []; page_token = None
    for _ in range(6):
        kwargs = {"underlying_symbols": [symbol], "status": AssetStatus.ACTIVE, "expiration_date_gte": today, "expiration_date_lte": max_expiry, "type": target, "strike_price_gte": f"{low:.2f}", "strike_price_lte": f"{high:.2f}", "limit": 500}
        if page_token: kwargs["page_token"] = page_token
        try: resp = trading_client.get_option_contracts(GetOptionContractsRequest(**kwargs))
        except Exception: break
        contracts = _field(resp, "option_contracts", []) or []
        page_token = _field(resp, "next_page_token")
        all_contracts.extend(contracts)
        if not page_token: break
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
        ratio = vol / max(oi, 1)
        unusual = " 🔥 نشاط غير عادي" if ratio >= 2 and vol >= 100 else ""
        whale_tag = (" 🐋 دخول حيتان" if vol > 5000 or oi > 10000 else "") + unusual
        score = spread_pct + 0.4 * diff_pct - 0.5 * math.log1p(max(oi, 0)) - 2 * min(ratio, 5)
        buckets[bucket].append({"symbol": str(cs), "strike": strike, "exp": expiry.isoformat(), "mid": mid, "bid": bid, "ask": ask, "spread_pct": spread_pct, "oi": int(oi), "vol": int(vol), "whale": whale_tag, "diff": round(diff_pct, 1), "score": score})
    result = {}
    for b, cands in buckets.items():
        if cands: result[b] = min(cands, key=lambda x: x["score"])
    return result
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
def build_report(symbol, price, hist, direction, sq, bo, drvol, trend_score, trend_details, gap_info, atr_val):
    is_neutral = trend_score <= 1
    if is_neutral: option_type = "محايد ⏸️"; emoji = "⚪"
    else: option_type = "CALL" if direction == "UP" else "PUT"; emoji = "🟢" if option_type == "CALL" else "🔴"
    rsi = calc_rsi(hist)
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
    sma50 = float(hist["Close"].rolling(50, min_periods=50).mean().iloc[-1]) if len(hist) >= 50 else price
    prior = hist.iloc[:-1].tail(20)
    resistance = float(prior["High"].max()) if direction == "UP" else float(prior["Low"].min())
    if direction == "UP":
        resistance_txt = f"تم كسر المقاومة {resistance:.2f}$ ✅ والسعر فوقها" if price > resistance else f"المقاومة {resistance:.2f}$ باقي {resistance-price:+.2f}$"
    else:
        resistance_txt = f"تم كسر الدعم {resistance:.2f}$ ✅" if price < resistance else f"الدعم {resistance:.2f}$"
    stops = calc_levels(hist, price, is_put=(direction == "DOWN" and not is_neutral))
    gamma_bar = round(price * 1.035, 2) if direction == "UP" else round(price * 0.965, 2)
    if price > gamma_bar and direction == "UP": gamma_txt = f"{gamma_bar}$ - اخترق غاما ✅"
    elif price < gamma_bar and direction == "UP": gamma_txt = f"{gamma_bar}$ - تحت الحاجز باقي {gamma_bar-price:.2f}$"
    else: gamma_txt = f"{gamma_bar}$"
    lines = [f"📡 تقرير الساعة {emoji} <b>{symbol}</b> سعره <b>{price:.2f}$</b>"]
    if gap_info:
        if abs(gap_info['gap_points']) >= 40:
            lines.append(f"⚠️ <b>قاب كبير جداً {gap_info['gap_points']:+.0f}$ ({gap_info['gap_pct']:.1f}%) - لا تدخل أول 15 دقيقة</b>")
        else:
            valid_txt = "✅ صالح" if gap_info["is_valid"] else "❌ غير صالح"
            lines.append(f"🔍 <b>القاب:</b> {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} ATR) - {valid_txt}")
        if gap_info["is_valid"] and not is_neutral:
            t1 = price + atr_val if gap_info["is_up"] else price - atr_val
            t2 = price + atr_val*2 if gap_info["is_up"] else price - atr_val*2
            stop_g = price - atr_val if gap_info["is_up"] else price + atr_val
            lines.append(f"⚡ <b>قانون القاب:</b> {'CALL' if gap_info['is_up'] else 'PUT'} {price:.2f}$ → هدف {t1:.2f}$ / {t2:.2f}$ وقف {stop_g:.2f}$")
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
        lines.append(f"🧭 الاتجاه: محايد ⏸️ {trend_score}/3 ({' + '.join(trend_details)}) - لا تدخل")
    else:
        lines.append(f"🧭 الاتجاه: {option_type} | {' + '.join(trend_details)} | القوة {trend_score}/3")
    lines.extend([f"📊 متوسط 50: {sma50:.2f}$ | {resistance_txt} | RSI: {rsi_txt}", f"🧱 غاما: {gamma_txt}"])
    if is_neutral:
        lines.append("\n⏸️ محايد - انتظر")
    else:
        lines.append(f"\n🚀 دخول {option_type}: {price:.2f}$")
        if stops:
            stop, t1, t2, t3 = stops
            lines.extend([f"🛑 وقف: {stop:.2f}$", f"🎯 أهداف: {t1:.2f}$ / {t2:.2f}$ / {t3:.2f}$"])
    opt_type_for_contract = "CALL" if direction == "UP" else "PUT"
    if is_neutral: opt_type_for_contract = "CALL"
    options = get_option_recommendations(symbol, price, opt_type_for_contract)
    labels = {"daily": "🔥 يومي", "weekly": "📅 أسبوعي", "monthly": "🛡️ شهري"}
    if options:
        for b in ("daily", "weekly", "monthly"):
            if b in options:
                o = options[b]
                spread_d = o['ask'] - o['bid']
                if spread_d < 0.3: liq = f"سيولة ممتازة ✅ سبريد ${spread_d:.2f}"
                elif spread_d < 0.6: liq = f"سيولة متوسطة ⚠️ سبريد ${spread_d:.2f}"
                else: liq = f"سيولة ضعيفة ❌ سبريد ${spread_d:.2f}"
                lines.append(f"{labels[b]} {o['exp']} {o['whale']}\n💰 سترايك {o['strike']:.1f}$ - {o['mid']:.1f}$ | {liq} | فوليوم {o['vol']} OI {o['oi']}\n🔹 <code>{o['symbol']}</code>\n")
    lines.append("\n⚠️ تعليمي فقط")
    return "\n".join(lines)

def main_loop():
    state = _prune_state(_load_state())
    last_heartbeat = datetime.now(timezone.utc)
    pre_sent = {}
    send_telegram(f"✅ <b>تم تشغيل الماسح + تنبيه قبل السوق</b>\nمراقبة {len(SYMBOLS)} شركة - نفس القائمة\n📡 تقرير كل {REPORT_EVERY_MINUTES} دقيقة بعد الافتتاح\n⏰ تنبيه قاب قبل الافتتاح 3:30-4:30 عصراً الرياض")
    while True:
        try:
            now_et = datetime.now(US_EASTERN)
            now_utc = datetime.now(timezone.utc)
            # === قبل السوق 8:30-9:30 نيويورك ===
            if now_et.weekday() < 5 and ((now_et.hour == 8 and now_et.minute >= 30) or (now_et.hour == 9 and now_et.minute < 30)):
                h5, h15, hD, prices = fetch_market_data(SYMBOLS)
                for symbol in SYMBOLS:
                    price = prices.get(symbol)
                    if not price: continue
                    key = f"pre:{symbol}:{now_et.date()}"
                    if key in pre_sent: continue
                    hist = h5.get(symbol)
                    atr_val = calc_atr(hist) if hist is not None else 1.0
                    if not atr_val: atr_val = 1.0
                    gap_info = calc_gap_metrics(symbol, hD, price, atr_val)
                    if gap_info and gap_info["is_valid"]:
                        msg = f"⏰ <b>تنبيه قاب قبل الافتتاح {symbol}</b>\n💰 بري ماركت: {price:.2f}$\n🔍 القاب: {gap_info['gap_type']} {gap_info['gap_pct']:.2f}% = {gap_info['gap_points']:+.2f}$ ({gap_info['gap_atr']:.1f} ATR)\n"
                        if abs(gap_info['gap_points']) >= 40:
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
                        atr_val = calc_atr(hist) or 1.0
                        gap_info = calc_gap_metrics(symbol, hD, price, atr_val)
                        direction = pick_direction(symbol, h5, h15, hD)
                        if gap_info and gap_info["is_valid"]:
                            direction = "UP" if gap_info["is_up"] else "DOWN"
                        sq = squeeze_metrics(hist)
                        bo = breakout_metrics(hist, direction)
                        drvol = day_rvol(hD, symbol)
                        best = max(sq["score"] if sq else 0, bo["score"] if bo else 0)
                        force_send = gap_info and gap_info["is_valid"]
                        if best >= MIN_REPORT_SCORE or force_send:
                            tscore, tdetails = get_trend_strength(symbol, h5, h15, hD, direction)
                            msg = build_report(symbol, price, hist, direction, sq, bo, drvol, tscore, tdetails, gap_info, atr_val)
                            if not send_telegram(msg): continue
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
def home(): return "Bot OK - Premarket + Hourly + Gap"
@app.get("/health")
def health(): return jsonify({"status": "ok", "symbols": len(SYMBOLS), "market_open": is_market_open()})
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
