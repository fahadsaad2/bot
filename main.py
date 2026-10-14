"""
تنبيهات Squeeze + Breakout - النسخة ثلاثية الفريمات بدرجات
5m + 15m + Daily - بدون حذف شركات ولا تغيير رسالة

التعديلات في هذه النسخة:
1) إصلاح خطأ صيغة RSI في الرسالة (كان يمنع إرسال أي تنبيه)
2) حذف limit=1000 لأن Alpaca يحسبه على مجموع الرموز فتضيع بيانات أغلب الشركات
3) تخفيف فلاتر العقود (سبريد، OI، نطاق السعر، البعد عن السعر)
4) قراءة الفوليوم من الشمعة اليومية للعقد
5) إضافة مؤشر النشاط غير العادي (الفوليوم مقارنة بـ OI) ورفع ترتيب هذه العقود
6) إظهار الأخطاء في اللوق بدل ابتلاعها بصمت
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
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

# ===================== الإعدادات =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

# الشركات (نفس القائمة بدون أي تغيير)
TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = ["SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP", "ARM", "AVGO", "MU", "LITE", "RDDT", "SNDK"]
SYMBOLS = TIER1 + TIER2
CRAZY_SYMBOLS = {"SNDK", "MU", "LITE", "SMCI"}

BAR_MINUTES = 5
TIMEFRAME_5M = TimeFrame(5, TimeFrameUnit.Minute)
TIMEFRAME_15M = TimeFrame(15, TimeFrameUnit.Minute)
TIMEFRAME_1D = TimeFrame(1, TimeFrameUnit.Day)
POLL_SECONDS = max(15, int(os.getenv("POLL_SECONDS", "20")))
HISTORY_DAYS = 10
STATE_FILE = Path("squeeze_state.json")

# فلاتر العقود (مخففة)
MAX_SPREAD_DAILY = 60
MAX_SPREAD_WEEKLY = 60
MAX_SPREAD_MONTHLY = 60
MIN_OI = 10
MIN_MID = 0.5        # أقل سعر للعقد
MAX_MID = 15.0       # أعلى سعر للعقد
MAX_DIFF_PCT = 8     # أقصى بعد للسترايك عن سعر السهم (%)
US_EASTERN = ZoneInfo("America/New_York")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("squeeze_bot")

stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


# ===================== دوال مساعدة =====================
def _field(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _as_float(v: Any, d=None):
    try:
        r = float(v)
        return r if math.isfinite(r) else d
    except Exception:
        return d


def _as_date(v: Any):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def is_market_open() -> bool:
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=30, second=0) <= now <= now.replace(hour=16, minute=0, second=0)


def _bar_frame_multi(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.index, pd.MultiIndex):
        try:
            frame = frame.xs(symbol, level=0)
        except Exception:
            return pd.DataFrame()
    rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    frame = frame.rename(columns=rename)
    for c in ["Open", "High", "Low", "Close"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    try:
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame.index = frame.index.tz_convert("UTC")
    except Exception:
        pass
    return frame


# ===================== جلب بيانات السوق =====================
def _fetch_frames(timeframe, days):
    """يجلب الشموع لكل الرموز بدون limit حتى لا تُقطع البيانات"""
    now = datetime.now(timezone.utc)
    bar_set = stock_client.get_stock_bars(
        StockBarsRequest(symbol_or_symbols=SYMBOLS, timeframe=timeframe, start=now - timedelta(days=days))
    )
    raw = bar_set.df
    out = {}
    for s in SYMBOLS:
        f = _bar_frame_multi(raw, s)
        if not f.empty:
            out[s] = f
    return out


def fetch_market_data():
    h5 = _fetch_frames(TIMEFRAME_5M, HISTORY_DAYS)
    h15 = _fetch_frames(TIMEFRAME_15M, HISTORY_DAYS)
    hD = _fetch_frames(TIMEFRAME_1D, 60)
    prices = {}
    try:
        latest = stock_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=SYMBOLS))
        for s in SYMBOLS:
            t = latest.get(s) if isinstance(latest, dict) else None
            p = _as_float(_field(t, "price"))
            if p and p > 0:
                prices[s] = p
    except Exception as e:
        logger.warning(f"فشل جلب آخر الأسعار: {e}")
    for s, f in h5.items():
        if s not in prices and len(f):
            prices[s] = float(f["Close"].iloc[-1])
    return h5, h15, hD, prices


# ===================== المؤشرات =====================
def calc_rsi(hist, period=14):
    if len(hist) < period + 1:
        return None
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    al = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return float(100 - (100 / (1 + ag / al)))


def calc_atr(hist, period=14):
    if len(hist) < period + 1:
        return None
    pc = hist["Close"].shift(1)
    tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - pc).abs(), (hist["Low"] - pc).abs()], axis=1).max(axis=1)
    v = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else None


def calc_levels(hist, entry, is_put=False):
    atr = calc_atr(hist)
    if not atr or entry <= 0:
        return None
    if is_put:
        vals = (entry + atr, entry - 1.5 * atr, entry - 3 * atr, entry - 5 * atr)
    else:
        vals = (entry - atr, entry + 1.5 * atr, entry + 3 * atr, entry + 5 * atr)
    return tuple(round(x, 2) for x in vals)


# ===================== إشارة الانضغاط / الانفجار =====================
def check_signal(symbol, hist):
    if len(hist) < 25:
        return None
    close = hist["Close"]
    mid = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    prev_close = close.shift(1)
    tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - prev_close).abs(), (hist["Low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    upper_bb = mid + 2 * std
    lower_bb = mid - 2 * std
    upper_kc = mid + 1.5 * atr
    lower_kc = mid - 1.5 * atr
    if any(pd.isna([upper_bb.iloc[-1], lower_bb.iloc[-1], upper_kc.iloc[-1], lower_kc.iloc[-1]])):
        return None
    squeeze_now = lower_bb.iloc[-1] > lower_kc.iloc[-1] and upper_bb.iloc[-1] < upper_kc.iloc[-1]
    bb_width = float((upper_bb.iloc[-1] - lower_bb.iloc[-1]) / mid.iloc[-1] * 100)
    last = hist.iloc[-1]
    prev_20 = hist.iloc[-21:-1]
    avg_vol = hist["Volume"].tail(20).mean() if "Volume" in hist.columns else 0
    curr_vol = float(last["Volume"]) if "Volume" in hist.columns else 0
    direction = "UP" if close.iloc[-1] >= mid.iloc[-1] else "DOWN"
    is_crazy = symbol in CRAZY_SYMBOLS
    vol_multiplier = 0.8 if is_crazy else 0.9
    high_prev = float(prev_20["High"].max())
    low_prev = float(prev_20["Low"].min())
    is_breakout_up = last["Close"] > high_prev and (curr_vol > avg_vol * vol_multiplier if avg_vol > 0 else True)
    is_breakout_down = last["Close"] < low_prev and (curr_vol > avg_vol * vol_multiplier if avg_vol > 0 else True)
    if is_breakout_up:
        return {"type": "BREAKOUT", "dir": "UP", "bar_time": hist.index[-1].isoformat(), "bb_width": bb_width}
    if is_breakout_down:
        return {"type": "BREAKOUT", "dir": "DOWN", "bar_time": hist.index[-1].isoformat(), "bb_width": bb_width}
    if squeeze_now:
        return {"type": "SQUEEZE", "dir": direction, "bar_time": hist.index[-1].isoformat(), "bb_width": bb_width}
    return None


def get_trend_strength(symbol, h5, h15, hD, sig_dir):
    score = 1
    details = []
    try:
        if symbol in hD and len(hD[symbol]) >= 50:
            dc = hD[symbol]["Close"]
            ema50 = dc.ewm(span=50).mean().iloc[-1]
            if sig_dir == "UP" and dc.iloc[-1] > ema50:
                score += 1
                details.append("يومي صاعد")
            elif sig_dir == "DOWN" and dc.iloc[-1] < ema50:
                score += 1
                details.append("يومي هابط")
            else:
                details.append("يومي عكس")
    except Exception as e:
        logger.warning(f"خطأ الفريم اليومي {symbol}: {e}")
    try:
        if symbol in h15 and len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]
            e9 = c.ewm(span=9).mean().iloc[-1]
            e20 = c.ewm(span=20).mean().iloc[-1]
            if sig_dir == "UP" and e9 > e20:
                score += 1
                details.append("15m صاعد")
            elif sig_dir == "DOWN" and e9 < e20:
                score += 1
                details.append("15m هابط")
            else:
                details.append("15m عكس")
    except Exception as e:
        logger.warning(f"خطأ فريم 15 دقيقة {symbol}: {e}")
    details.insert(0, "5m انفجار")
    return score, details


# ===================== توصية العقود =====================
def get_option_recommendations(symbol, stock_price, option_type):
    if stock_price <= 0:
        return {}
    target = ContractType.CALL if option_type == "CALL" else ContractType.PUT
    today = datetime.now(timezone.utc).date()
    max_expiry = today + timedelta(days=45)
    if option_type == "CALL":
        low = stock_price * 0.99
        high = stock_price * 1.04
    else:
        low = stock_price * 0.96
        high = stock_price * 1.01
    all_contracts = []
    page_token = None
    for _ in range(6):
        kwargs = {
            "underlying_symbols": [symbol],
            "status": AssetStatus.ACTIVE,
            "expiration_date_gte": today,
            "expiration_date_lte": max_expiry,
            "type": target,
            "strike_price_gte": f"{low:.2f}",
            "strike_price_lte": f"{high:.2f}",
            "limit": 500,
        }
        if page_token:
            kwargs["page_token"] = page_token
        try:
            resp = trading_client.get_option_contracts(GetOptionContractsRequest(**kwargs))
        except Exception as e:
            logger.warning(f"فشل جلب عقود {symbol}: {e}")
            break
        contracts = _field(resp, "option_contracts", []) or []
        page_token = _field(resp, "next_page_token")
        all_contracts.extend(contracts)
        if not page_token:
            break
    if not all_contracts:
        return {}
    try:
        chain = option_client.get_option_chain(
            OptionChainRequest(
                underlying_symbol=symbol, type=target,
                strike_price_gte=low, strike_price_lte=high,
                expiration_date_gte=today, expiration_date_lte=max_expiry,
            )
        )
    except Exception as e:
        logger.warning(f"فشل جلب سلسلة الخيارات {symbol}: {e}")
        return {}
    if not isinstance(chain, dict):
        return {}
    buckets = {"daily": [], "weekly": [], "monthly": []}
    for contract in all_contracts:
        expiry = _as_date(_field(contract, "expiration_date"))
        strike = _as_float(_field(contract, "strike_price"))
        cs = _field(contract, "symbol")
        if not expiry or not strike or not cs:
            continue
        dte = (expiry - today).days
        if 0 <= dte <= 7:
            bucket = "daily"; max_spread = MAX_SPREAD_DAILY
        elif 8 <= dte <= 20:
            bucket = "weekly"; max_spread = MAX_SPREAD_WEEKLY
        elif 21 <= dte <= 45:
            bucket = "monthly"; max_spread = MAX_SPREAD_MONTHLY
        else:
            continue
        snap = chain.get(str(cs))
        if snap is None:
            continue
        quote = _field(snap, "latest_quote")
        bid = _as_float(_field(quote, "bid_price")) if quote else None
        ask = _as_float(_field(quote, "ask_price")) if quote else None
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        if not MIN_MID <= mid <= MAX_MID:
            continue
        spread_pct = ((ask - bid) / mid) * 100
        if spread_pct > max_spread:
            continue
        oi = _as_float(_field(contract, "open_interest"), 0) or 0
        if oi < MIN_OI:
            continue
        # الفوليوم من الشمعة اليومية للعقد
        daily = _field(snap, "daily_bar")
        vol = (_as_float(_field(daily, "volume"), 0) if daily else 0) or 0
        diff_pct = abs(strike - stock_price) / stock_price * 100
        if diff_pct > MAX_DIFF_PCT:
            continue
        # مؤشر النشاط غير العادي: فوليوم أعلى من OI يعني مراكز جديدة
        ratio = vol / max(oi, 1)
        unusual = " 🔥 نشاط غير عادي" if ratio >= 2 and vol >= 100 else ""
        whale_tag = (" 🐋 دخول حيتان" if vol > 5000 or oi > 10000 else "") + unusual
        score = spread_pct + 0.4 * diff_pct - 0.5 * math.log1p(max(oi, 0)) - 2 * min(ratio, 5)
        buckets[bucket].append({
            "symbol": str(cs), "strike": strike, "exp": expiry.isoformat(), "mid": mid,
            "bid": bid, "ask": ask, "spread_pct": spread_pct, "oi": int(oi), "vol": int(vol),
            "whale": whale_tag, "diff": round(diff_pct, 1), "score": score,
        })
    result = {}
    for b, cands in buckets.items():
        if cands:
            result[b] = min(cands, key=lambda x: x["score"])
    return result


# ===================== الحالة والتيليجرام =====================
def _load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e:
        logger.warning(f"فشل حفظ الحالة: {e}")


def send_telegram(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20,
        )
        ok = r.ok and r.json().get("ok", False)
        if not ok:
            logger.warning(f"تيليجرام رفض الرسالة: {r.text[:200]}")
        return ok
    except Exception as e:
        logger.warning(f"فشل إرسال تيليجرام: {e}")
        return False


def _option_line(label, opt):
    return (
        f"{label} {opt['exp']}{opt['whale']}\n"
        f"💰 سترايك {opt['strike']:.1f}$ - سعره {opt['mid']:.1f}$ | فوليوم: {opt['vol']} | OI: {opt['oi']} | فرق: {opt['diff']}%\n"
        f"🔹 العقد: <code>{opt['symbol']}</code>\n"
    )


# ===================== بناء الرسالة (نفس الشكل) =====================
def build_message(symbol, price, hist, sig, score=1, trend_details=None):
    option_type = "CALL" if sig["dir"] == "UP" else "PUT"
    rsi = calc_rsi(hist)
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"   # إصلاح خطأ الصيغة
    sma50 = float(hist["Close"].rolling(50, min_periods=50).mean().iloc[-1]) if len(hist) >= 50 else price
    prior = hist.iloc[:-1].tail(20)
    resistance = float(prior["High"].max()) if option_type == "CALL" else float(prior["Low"].min())
    stops = calc_levels(hist, price, is_put=(option_type == "PUT"))
    if score == 3:
        title = f"🚀🚀🚀 انفجار ثلاثي مؤكد {option_type} 🔥🔥🔥 [3/3]"
    elif score == 2:
        title = f"🚀🚀 انفجار جيد {option_type} ⭐⭐ [2/3]"
    else:
        base = "⚠️ انضغاط" if sig["type"] == "SQUEEZE" else "🚀 انفجار قوي"
        title = f"{base} {option_type} ⚡ [1/3]"
    emoji = "🟢" if option_type == "CALL" else "🔴"
    gamma_bar = round(price * 1.035, 2) if option_type == "CALL" else round(price * 0.965, 2)
    lines = [f"{title} {emoji} <b>{symbol}</b> سعره <b>{price:.2f}$</b>"]
    if trend_details:
        lines.append(f"📈 الفريمات: {' + '.join(trend_details)} | القوة {score}/3")
    lines.extend([
        f"📊 المتوسط 50: {sma50:.2f}$ | المقاومة: {resistance:.2f}$ | RSI: {rsi_txt}",
        f"🧱 حاجز غاما: {gamma_bar}$",
        "",
        f"🚀 دخول {option_type}: {price:.2f}$",
    ])
    if stops:
        stop, t1, t2, t3 = stops
        atr_val = round(abs(t1 - price), 2)
        lines.extend([
            f"🛑 وقف الخسارة: {stop:.2f}$ (ATR:{atr_val}$)",
            f"🎯 هدف اول: {t1:.2f}$",
            f"🎯 هدف ثاني: {t2:.2f}$",
            f"🎯 هدف ثالث: {t3:.2f}$",
            "",
        ])
    options = get_option_recommendations(symbol, price, option_type)
    labels = {
        "daily": "🔥 عقد يومي (0-7 ايام)",
        "weekly": "📅 عقد أسبوعي (8-20 يوم)",
        "monthly": "🛡️ عقد شهري (21-45 يوم)",
    }
    if options:
        for b in ("daily", "weekly", "monthly"):
            if b in options:
                lines.append(_option_line(labels[b], options[b]))
    else:
        lines.append(f"⚠️ لم يتم العثور على عقد مناسب ضمن {MIN_MID:g}$-{MAX_MID:g}$.")
    lines.append("\n⚠️ تنبيه تعليمي فقط - الخيارات عالية المخاطر.")
    return "\n".join(lines)


def _prune_state(state):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    kept = {}
    for k, v in state.items():
        try:
            stamp = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff:
                kept[k] = v
        except Exception:
            continue
    return kept


# ===================== الحلقة الرئيسية =====================
def main_loop():
    state = _prune_state(_load_state())
    last_heartbeat = datetime.now(timezone.utc)
    send_telegram(
        "✅ <b>تم تشغيل النسخة الثلاثية</b>\n"
        f"مراقبة {len(SYMBOLS)} شركة - 5m+15m+Daily\n"
        f"عقود {MIN_MID:g}$-{MAX_MID:g}$ - نظام درجات 1/3 - 3/3"
    )
    while True:
        try:
            if not is_market_open():
                time.sleep(60)
                continue
            h5, h15, hD, prices = fetch_market_data()
            state = _prune_state(state)
            for symbol in SYMBOLS:
                try:
                    hist = h5.get(symbol)
                    price = prices.get(symbol)
                    if hist is None or price is None or len(hist) < 30:
                        continue
                    sig = check_signal(symbol, hist)
                    if sig:
                        key = f"{symbol}:{sig['type']}:{sig['dir']}:{sig['bar_time']}"
                        if key not in state:
                            score, details = get_trend_strength(symbol, h5, h15, hD, sig["dir"])
                            msg = build_message(symbol, price, hist, sig, score=score, trend_details=details)
                            if send_telegram(msg):
                                state[key] = sig["bar_time"]
                                _save_state(state)
                    time.sleep(0.2)
                except Exception as e:
                    logger.exception(f"خطأ {symbol}: {e}")
            now = datetime.now(timezone.utc)
            if (now - last_heartbeat).total_seconds() > 3600:
                send_telegram("💓 البوت شغال - ثلاث فريمات")
                last_heartbeat = now
            time.sleep(POLL_SECONDS)
        except Exception as e:
            logger.exception(f"خطأ رئيسي: {e}")
            time.sleep(20)


# ===================== خادم Flask =====================
@app.get("/")
def home():
    return "Bot OK - 3 Frames Active"


@app.get("/health")
def health():
    return jsonify({"status": "ok", "symbols": len(SYMBOLS), "market_open": is_market_open()})


_worker_lock = Lock()
_worker_started = False


def start_background_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        Thread(target=main_loop, name="squeeze-worker", daemon=True).start()
        _worker_started = True


start_background_worker()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), threaded=True, use_reloader=False)
