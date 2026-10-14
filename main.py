"""
ماسح الانضغاط والانفجار - تقرير كل 60 دقيقة لكل سهم
5m + 15m + Daily - نفس الشركات

كل ساعة، لكل سهم يرسل:
- قوة الانضغاط (0-100)
- قوة الانفجار (0-100)
- الفوليوم النسبي الحقيقي (اليوم + آخر 15 دقيقة)
- الاتجاه المرجّح من الفريمات الثلاثة
- أفضل عقد يومي وأسبوعي وشهري مع فوليوم العقد و OI

ملاحظة عن البيانات: الخطة المجانية في Alpaca تعطي فوليوم IEX فقط (جزء صغير من السوق).
لذلك الأرقام النسبية (× المعتاد) تبقى مفيدة، لكن الأرقام المطلقة أقل من الواقع.
لاستخدام بيانات SIP الكاملة (تحتاج اشتراك) ضع STOCK_FEED=sip في المتغيرات.
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

# ===================== الإعدادات =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

# الشركات (نفس القائمة بدون تغيير)
TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = ["SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP", "ARM", "AVGO", "MU", "LITE", "RDDT", "SNDK"]
SYMBOLS = TIER1 + TIER2

TIMEFRAME_5M = TimeFrame(5, TimeFrameUnit.Minute)
TIMEFRAME_15M = TimeFrame(15, TimeFrameUnit.Minute)
TIMEFRAME_1D = TimeFrame(1, TimeFrameUnit.Day)
HISTORY_DAYS = 10
STATE_FILE = Path("squeeze_state.json")

REPORT_EVERY_MINUTES = int(os.getenv("REPORT_EVERY_MINUTES", "60"))   # الفاصل بين تقارير كل سهم
MIN_REPORT_SCORE = int(os.getenv("MIN_REPORT_SCORE", "0"))            # 0 = أرسل كل الأسهم، ارفعه لتصفية الضعيف
SEND_GAP_SECONDS = 3.5                                                # فاصل بين الرسائل (حد تيليجرام)

_feed_env = os.getenv("STOCK_FEED", "").strip().lower()
STOCK_FEED = DataFeed.SIP if _feed_env == "sip" else DataFeed.IEX if _feed_env == "iex" else None

# فلاتر العقود
MAX_SPREAD_DAILY = 60
MAX_SPREAD_WEEKLY = 60
MAX_SPREAD_MONTHLY = 60
MIN_OI = 10
MIN_MID = 0.5
MAX_MID = 15.0
MAX_DIFF_PCT = 8
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
def _fetch_frames(symbols, timeframe, days):
    now = datetime.now(timezone.utc)
    kwargs = {"symbol_or_symbols": symbols, "timeframe": timeframe, "start": now - timedelta(days=days)}
    if STOCK_FEED is not None:
        kwargs["feed"] = STOCK_FEED
    raw = stock_client.get_stock_bars(StockBarsRequest(**kwargs)).df
    out = {}
    for s in symbols:
        f = _bar_frame_multi(raw, s)
        if not f.empty:
            out[s] = f
    return out


def fetch_market_data(symbols):
    h5 = _fetch_frames(symbols, TIMEFRAME_5M, HISTORY_DAYS)
    h15 = _fetch_frames(symbols, TIMEFRAME_15M, HISTORY_DAYS)
    hD = _fetch_frames(symbols, TIMEFRAME_1D, 60)
    prices = {}
    try:
        kwargs = {"symbol_or_symbols": symbols}
        if STOCK_FEED is not None:
            kwargs["feed"] = STOCK_FEED
        latest = stock_client.get_stock_latest_trade(StockLatestTradeRequest(**kwargs))
        for s in symbols:
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


# ===================== قوة الانضغاط =====================
def squeeze_metrics(hist, n=20):
    """
    قوة الانضغاط من 0 إلى 100 تعتمد على:
    - 40%: ضيق عرض البولنجر مقارنة بآخر 10 أيام
    - 40%: عدد الشموع المتتالية داخل الانضغاط (بولنجر داخل كيلتنر)
    - 20%: نسبة عرض البولنجر إلى كيلتنر (كلما قلت زاد الانضغاط)
    """
    if hist is None or len(hist) < n + 30:
        return None
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
    if valid.empty or pd.isna(kcw.iloc[-1]) or kcw.iloc[-1] <= 0:
        return None
    cur = float(valid.iloc[-1])
    pct = float((valid <= cur).mean())          # قريب من 0 = ضيق جداً
    bars = 0
    for v in reversed(in_sq.values):
        if v:
            bars += 1
        else:
            break
    ratio = float(width.iloc[-1] / kcw.iloc[-1])
    score = (
        0.4 * (1 - pct) * 100
        + 0.4 * min(bars / 15, 1) * 100
        + 0.2 * max(0.0, min(1.0, (1 - ratio) / 0.3)) * 100
    )
    return {"in_squeeze": bool(in_sq.iloc[-1]), "bars": bars, "tight_pct": (1 - pct) * 100, "score": int(round(score))}


# ===================== قوة الانفجار =====================
def breakout_metrics(hist, direction):
    """
    قوة الانفجار من 0 إلى 100 تعتمد على:
    - 40%: الفوليوم النسبي (آخر 3 شموع مقابل متوسط 20 شمعة)، الحد الأعلى عند ضعفين
    - 35%: القرب من كسر أعلى/أسفل آخر 20 شمعة (أو الكسر الفعلي)
    - 25%: إغلاق الشمعة الأخيرة قرب قمتها (صعود) أو قاعها (هبوط)
    """
    if hist is None or len(hist) < 25 or "Volume" not in hist.columns:
        return None
    last = hist.iloc[-1]
    prior = hist.iloc[-21:-1]
    atr = calc_atr(hist)
    avg_vol = float(prior["Volume"].mean())
    rec_vol = float(hist["Volume"].tail(3).mean())
    rvol = rec_vol / avg_vol if avg_vol > 0 else 0.0
    hi, lo = float(prior["High"].max()), float(prior["Low"].min())
    price = float(last["Close"])
    if direction == "UP":
        broke = price > hi
        dist = (hi - price) / atr if atr else 99.0
    else:
        broke = price < lo
        dist = (price - lo) / atr if atr else 99.0
    rng = float(last["High"] - last["Low"])
    pos = (price - float(last["Low"])) / rng if rng > 0 else 0.5
    align = pos if direction == "UP" else 1 - pos
    prox = 1.0 if broke else max(0.0, 1 - max(dist, 0) / 2)
    score = 40 * min(rvol / 2, 1) + 35 * prox + 25 * align
    return {"rvol": rvol, "broke": broke, "dist": max(dist, 0), "score": int(round(score))}


def day_rvol(hD, symbol):
    """فوليوم اليوم حتى الآن مقابل المتوسط اليومي (20 يوم) بعد تعديله بنسبة الوقت المنقضي من الجلسة"""
    f = hD.get(symbol)
    if f is None or len(f) < 6:
        return None
    now_et = datetime.now(US_EASTERN)
    if f.index[-1].date() != now_et.date():
        return None
    avg = float(f["Volume"].iloc[-21:-1].mean())
    if avg <= 0:
        return None
    open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = (now_et - open_et).total_seconds() / 60
    frac = min(max(elapsed, 5) / 390, 1.0)
    return float(f["Volume"].iloc[-1]) / (avg * frac)


# ===================== الاتجاه المرجّح من الفريمات =====================
def pick_direction(symbol, h5, h15, hD):
    up = total = 0
    try:
        c = h5[symbol]["Close"]
        total += 1
        up += 1 if c.iloc[-1] >= c.rolling(20).mean().iloc[-1] else 0
    except Exception:
        pass
    try:
        if len(h15[symbol]) >= 30:
            c = h15[symbol]["Close"]
            total += 1
            up += 1 if c.ewm(span=9).mean().iloc[-1] > c.ewm(span=20).mean().iloc[-1] else 0
    except Exception:
        pass
    try:
        if len(hD[symbol]) >= 50:
            c = hD[symbol]["Close"]
            total += 1
            up += 1 if c.iloc[-1] > c.ewm(span=50).mean().iloc[-1] else 0
    except Exception:
        pass
    return "UP" if up * 2 >= total else "DOWN"


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
    details.insert(0, "5m")
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
        daily = _field(snap, "daily_bar")
        vol = (_as_float(_field(daily, "volume"), 0) if daily else 0) or 0
        diff_pct = abs(strike - stock_price) / stock_price * 100
        if diff_pct > MAX_DIFF_PCT:
            continue
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


def _last_sent(state, symbol):
    v = state.get(f"sent:{symbol}")
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


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


def _strength_label(score):
    if score >= 70:
        return "قوي 🔥"
    if score >= 40:
        return "متوسط"
    return "ضعيف"


# ===================== بناء التقرير =====================
def build_report(symbol, price, hist, direction, sq, bo, drvol, trend_score, trend_details):
    option_type = "CALL" if direction == "UP" else "PUT"
    emoji = "🟢" if option_type == "CALL" else "🔴"
    rsi = calc_rsi(hist)
    rsi_txt = f"{rsi:.1f}" if rsi is not None else "N/A"
    sma50 = float(hist["Close"].rolling(50, min_periods=50).mean().iloc[-1]) if len(hist) >= 50 else price
    prior = hist.iloc[:-1].tail(20)
    resistance = float(prior["High"].max()) if option_type == "CALL" else float(prior["Low"].min())
    stops = calc_levels(hist, price, is_put=(option_type == "PUT"))
    gamma_bar = round(price * 1.035, 2) if option_type == "CALL" else round(price * 0.965, 2)

    lines = [f"📡 تقرير الساعة {emoji} <b>{symbol}</b> سعره <b>{price:.2f}$</b>"]

    if sq:
        state_txt = f"منضغط منذ {sq['bars']} شمعة" if sq["in_squeeze"] else "غير منضغط حالياً"
        lines.append(
            f"🗜️ قوة الانضغاط: <b>{sq['score']}/100</b> ({_strength_label(sq['score'])}) - {state_txt} | "
            f"أضيق من {sq['tight_pct']:.0f}% من آخر 10 أيام"
        )
    else:
        lines.append("🗜️ قوة الانضغاط: بيانات غير كافية")

    if bo:
        status = "تم الكسر ✅" if bo["broke"] else f"يبعد {bo['dist']:.1f} ATR عن الكسر"
        lines.append(f"💥 قوة الانفجار: <b>{bo['score']}/100</b> ({_strength_label(bo['score'])}) - {status}")
    else:
        lines.append("💥 قوة الانفجار: بيانات غير كافية")

    vol_parts = []
    if drvol is not None:
        vol_parts.append(f"اليوم {drvol:.1f}× المعتاد")
    if bo:
        vol_parts.append(f"آخر 15 دقيقة {bo['rvol']:.1f}× المعتاد")
    if vol_parts:
        lines.append(f"📊 الفوليوم: {' | '.join(vol_parts)}")

    lines.append(f"🧭 الاتجاه المرجّح: {option_type} | الفريمات: {' + '.join(trend_details)} | القوة {trend_score}/3")
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


# ===================== الحلقة الرئيسية =====================
def main_loop():
    state = _prune_state(_load_state())
    last_heartbeat = datetime.now(timezone.utc)
    send_telegram(
        "✅ <b>تم تشغيل الماسح الساعي</b>\n"
        f"مراقبة {len(SYMBOLS)} شركة - 5m+15m+Daily\n"
        f"تقرير كل {REPORT_EVERY_MINUTES} دقيقة لكل سهم: قوة الانضغاط + قوة الانفجار + الفوليوم + أفضل العقود"
    )
    while True:
        try:
            if not is_market_open():
                time.sleep(60)
                continue
            now = datetime.now(timezone.utc)
            state = _prune_state(state)
            due = []
            for s in SYMBOLS:
                ls = _last_sent(state, s)
                if ls is None or (now - ls).total_seconds() >= REPORT_EVERY_MINUTES * 60:
                    due.append(s)

            if due:
                h5, h15, hD, prices = fetch_market_data(due)
                for symbol in due:
                    try:
                        hist = h5.get(symbol)
                        price = prices.get(symbol)
                        if hist is None or price is None or len(hist) < 30:
                            continue
                        direction = pick_direction(symbol, h5, h15, hD)
                        sq = squeeze_metrics(hist)
                        bo = breakout_metrics(hist, direction)
                        drvol = day_rvol(hD, symbol)
                        best = max(sq["score"] if sq else 0, bo["score"] if bo else 0)
                        if best >= MIN_REPORT_SCORE:
                            tscore, tdetails = get_trend_strength(symbol, h5, h15, hD, direction)
                            msg = build_report(symbol, price, hist, direction, sq, bo, drvol, tscore, tdetails)
                            if not send_telegram(msg):
                                continue
                            time.sleep(SEND_GAP_SECONDS)
                        state[f"sent:{symbol}"] = datetime.now(timezone.utc).isoformat()
                        _save_state(state)
                    except Exception as e:
                        logger.exception(f"خطأ {symbol}: {e}")

            now = datetime.now(timezone.utc)
            if (now - last_heartbeat).total_seconds() > 3600:
                send_telegram("💓 البوت شغال - تقارير ساعية")
                last_heartbeat = now
            time.sleep(30)
        except Exception as e:
            logger.exception(f"خطأ رئيسي: {e}")
            time.sleep(20)


# ===================== خادم Flask =====================
@app.get("/")
def home():
    return "Bot OK - Hourly Scanner Active"


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
