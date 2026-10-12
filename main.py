"""\
تنبيهات Squeeze للأسهم الأمريكية عبر Alpaca وTelegram.

مهم: هذا البوت يرسل تنبيهات تحليلية فقط، ولا ينفذ أوامر تداول.
الإشارات تعتمد على شموع مكتملة مدتها 5 دقائق.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import pandas as pd
import requests
from flask import Flask, jsonify
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest


# ------------------------------- الإعدادات ----------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = [
    "SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP",
    "ARM", "AVGO", "MU", "LITE", "RDDT", "SNDK",
]
SYMBOLS = TIER1 + TIER2

BAR_MINUTES = 5
TIMEFRAME = TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute)
POLL_SECONDS = max(30, int(os.getenv("POLL_SECONDS", "60")))
HISTORY_DAYS = max(10, int(os.getenv("HISTORY_DAYS", "15")))
MAX_OPTION_SPREAD_PCT = max(1.0, float(os.getenv("MAX_OPTION_SPREAD_PCT", "35")))
STATE_FILE = Path(os.getenv("STATE_FILE", "squeeze_state.json"))

app = Flask(__name__)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("squeeze_bot")


# تحقق مبكر حتى لا يبدأ البوت بحالة ناقصة.
_required_env = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    "ALPACA_API_KEY": ALPACA_API_KEY,
    "ALPACA_SECRET_KEY": ALPACA_SECRET_KEY,
}
_missing = [key for key, value in _required_env.items() if not value]
if _missing:
    raise RuntimeError("متغيرات البيئة المطلوبة غير موجودة: " + ", ".join(_missing))

stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
# يُستخدم TradingClient هنا لجلب بيانات تعريف العقود فقط؛ لا توجد أوامر تداول.
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """قراءة حقل من كائن Alpaca أو قاموس مع دعم الحالتين."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _bar_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """توحيد أسماء أعمدة Alpaca وترتيب الشموع زمنيًا."""
    frame = raw.copy()
    if isinstance(frame.index, pd.MultiIndex):
        try:
            frame = frame.xs(symbol, level=0)
        except KeyError:
            return pd.DataFrame()
    rename = {
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "volume": "Volume", "trade_count": "TradeCount", "vwap": "VWAP",
    }
    frame = frame.rename(columns=rename)
    needed = ["Open", "High", "Low", "Close"]
    if any(col not in frame.columns for col in needed):
        return pd.DataFrame()
    for col in needed + (["Volume"] if "Volume" in frame.columns else []):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=needed).sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex):
        try:
            frame.index = pd.to_datetime(frame.index, utc=True)
        except Exception:
            return pd.DataFrame()
    elif frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    return frame


def fetch_market_data() -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """جلب شموع حديثة وأسعار آخر صفقة دفعة واحدة بدل إبقاء cache قديمًا."""
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=SYMBOLS,
        timeframe=TIMEFRAME,
        start=now - timedelta(days=HISTORY_DAYS),
        limit=1000,
    )
    bar_set = stock_client.get_stock_bars(request)
    raw = bar_set.df
    histories: dict[str, pd.DataFrame] = {}
    # نستبعد الشمعة التي لم يمض على افتتاحها وقت كافٍ لتكتمل.
    closed_before = now - timedelta(minutes=BAR_MINUTES + 1)
    for symbol in SYMBOLS:
        frame = _bar_frame(raw, symbol)
        if not frame.empty:
            frame = frame.loc[frame.index <= closed_before]
            if not frame.empty:
                histories[symbol] = frame

    prices: dict[str, float] = {}
    try:
        latest = stock_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=SYMBOLS)
        )
        for symbol in SYMBOLS:
            trade = latest.get(symbol) if isinstance(latest, dict) else None
            price = _as_float(_field(trade, "price")) if trade else None
            if price is not None and price > 0:
                prices[symbol] = price
    except Exception as exc:
        logger.exception("تعذر جلب أحدث الصفقات: %s", exc)

    # احتياط عند تعذر أحدث صفقة: آخر إغلاق شمعة مكتملة، مع تسجيل ذلك.
    for symbol, frame in histories.items():
        if symbol not in prices and len(frame):
            fallback = _as_float(frame["Close"].iloc[-1])
            if fallback and fallback > 0:
                prices[symbol] = fallback
                logger.warning("استخدام إغلاق آخر شمعة بدل السعر اللحظي للسهم %s", symbol)
    return histories, prices


def calc_rsi(hist: pd.DataFrame, period: int = 14) -> float | None:
    if len(hist) < period + 1:
        return None
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    if not math.isfinite(float(avg_gain)) or not math.isfinite(float(avg_loss)):
        return None
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return float(100 - (100 / (1 + (avg_gain / avg_loss))))


def calc_atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    if len(hist) < period + 1:
        return None
    previous_close = hist["Close"].shift(1)
    tr = pd.concat(
        [
            hist["High"] - hist["Low"],
            (hist["High"] - previous_close).abs(),
            (hist["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(value) if pd.notna(value) and math.isfinite(float(value)) and value > 0 else None


def calc_levels(hist: pd.DataFrame, entry: float, is_put: bool = False) -> tuple[float, float, float, float] | None:
    atr = calc_atr(hist)
    if atr is None or entry <= 0:
        return None
    if is_put:
        values = (entry + atr, entry - 1.5 * atr, entry - 3 * atr, entry - 5 * atr)
    else:
        values = (entry - atr, entry + 1.5 * atr, entry + 3 * atr, entry + 5 * atr)
    return tuple(round(x, 2) for x in values)


def check_squeeze(hist: pd.DataFrame) -> dict[str, Any] | None:
    """TTM-style Bollinger/Keltner squeeze على شموع الخمس دقائق المكتملة."""
    if len(hist) < 25:
        return None
    close = hist["Close"]
    mid = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            hist["High"] - hist["Low"],
            (hist["High"] - prev_close).abs(),
            (hist["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(20, min_periods=20).mean()
    upper_bb, lower_bb = mid + 2 * std, mid - 2 * std
    upper_kc, lower_kc = mid + 1.5 * atr, mid - 1.5 * atr
    values = [upper_bb.iloc[-1], lower_bb.iloc[-1], upper_kc.iloc[-1], lower_kc.iloc[-1],
              upper_bb.iloc[-2], lower_bb.iloc[-2], upper_kc.iloc[-2], lower_kc.iloc[-2], mid.iloc[-1]]
    if any(pd.isna(v) for v in values):
        return None
    squeeze_now = lower_bb.iloc[-1] > lower_kc.iloc[-1] and upper_bb.iloc[-1] < upper_kc.iloc[-1]
    squeeze_prev = lower_bb.iloc[-2] > lower_kc.iloc[-2] and upper_bb.iloc[-2] < upper_kc.iloc[-2]
    return {
        "squeeze": bool(squeeze_now),
        "firing": bool(squeeze_prev and not squeeze_now),
        # تصنيف أولي للاتجاه، وليس تأكيدًا أو توصية دخول.
        "dir": "UP" if close.iloc[-1] >= mid.iloc[-1] else "DOWN",
        "bar_time": hist.index[-1].isoformat(),
    }


def calc_liquidity_and_ob(hist: pd.DataFrame, direction: str) -> tuple[str, str]:
    """كشف تقريبي لسحب السيولة وكتلة الأوامر، مع استبعاد الشمعة الحالية من المستويات."""
    if len(hist) < 22:
        return "💧 بيانات غير كافية لتقييم السيولة", "غير محدد"
    current = hist.iloc[-1]
    previous = hist.iloc[-21:-1]
    prior_low = float(previous["Low"].min())
    prior_high = float(previous["High"].max())
    liq = "بدون سحب سيولة واضح"
    if current["Low"] < prior_low and current["Close"] > prior_low:
        liq = "💧 سحب سيولة SSL محتمل + إغلاق فوق القاع السابق"
    elif current["High"] > prior_high and current["Close"] < prior_high:
        liq = "💧 سحب سيولة BSL محتمل + إغلاق تحت القمة السابقة"

    atr = calc_atr(hist)
    ob_text = "لم تُرصد منطقة واضحة (الكشف تقريبي)"
    if atr:
        # شمعة معاكسة قبل شمعة اندفاع قوية؛ تعريف إرشادي وليس كشفًا مؤسسيًا مؤكدًا.
        start = max(0, len(hist) - 26)
        for i in range(len(hist) - 2, start - 1, -1):
            base = hist.iloc[i]
            impulse = hist.iloc[i + 1]
            impulse_body = abs(float(impulse["Close"] - impulse["Open"]))
            is_bullish_pair = direction == "UP" and base["Close"] < base["Open"] and impulse["Close"] > impulse["Open"]
            is_bearish_pair = direction == "DOWN" and base["Close"] > base["Open"] and impulse["Close"] < impulse["Open"]
            if (is_bullish_pair or is_bearish_pair) and impulse_body >= 0.8 * atr:
                ob_text = f"{float(base['Low']):.2f}$ - {float(base['High']):.2f}$ (تقريبي)"
                break
    return liq, ob_text


def _contracts_from_response(response: Any) -> tuple[list[Any], str | None]:
    contracts = _field(response, "option_contracts", None)
    if contracts is None and isinstance(response, (list, tuple)):
        contracts = response
    if contracts is None and isinstance(response, dict):
        contracts = response.get("option_contracts", [])
    next_token = _field(response, "next_page_token")
    return list(contracts or []), next_token


def get_option_recommendations(symbol: str, stock_price: float, option_type: str) -> dict[str, dict[str, Any]]:
    """اختيار عقود من بيانات Alpaca الصحيحة: قائمة العقود ثم snapshots للسلسلة."""
    if stock_price <= 0:
        return {}
    target = ContractType.CALL if option_type == "CALL" else ContractType.PUT
    today = datetime.now(timezone.utc).date()
    max_expiry = today + timedelta(days=45)
    # نطاقات قريبة نسبيًا من السعر لتقليل حجم الاستجابة.
    low = stock_price * (0.90 if option_type == "CALL" else 0.85)
    high = stock_price * (1.15 if option_type == "CALL" else 1.10)

    all_contracts: list[Any] = []
    page_token = None
    for _ in range(10):
        request_kwargs: dict[str, Any] = {
            "underlying_symbols": [symbol],
            "status": AssetStatus.ACTIVE,
            "expiration_date_gte": today,
            "expiration_date_lte": max_expiry,
            "type": target,
            "strike_price_gte": f"{low:.2f}",
            "strike_price_lte": f"{high:.2f}",
            "limit": 10000,
        }
        if page_token:
            request_kwargs["page_token"] = page_token
        response = trading_client.get_option_contracts(GetOptionContractsRequest(**request_kwargs))
        contracts, page_token = _contracts_from_response(response)
        all_contracts.extend(contracts)
        if not page_token:
            break
    if not all_contracts:
        return {}

    # تأكيد المرشحين محليًا؛ يمنع الاعتماد على اختلاف تنسيق استجابة SDK.
    filtered: list[tuple[Any, date, float]] = []
    for contract in all_contracts:
        expiry = _as_date(_field(contract, "expiration_date"))
        strike = _as_float(_field(contract, "strike_price"))
        contract_symbol = _field(contract, "symbol")
        if not expiry or not strike or not contract_symbol:
            continue
        if expiry < today or expiry > max_expiry or not low <= strike <= high:
            continue
        filtered.append((contract, expiry, strike))
    if not filtered:
        return {}

    try:
        chain = option_client.get_option_chain(
            OptionChainRequest(
                underlying_symbol=symbol,
                type=target,
                strike_price_gte=low,
                strike_price_lte=high,
                expiration_date_gte=today,
                expiration_date_lte=max_expiry,
            )
        )
    except Exception as exc:
        logger.warning("تعذر جلب snapshots خيارات %s: %s", symbol, exc)
        return {}

    if not isinstance(chain, dict):
        logger.warning("صيغة سلسلة الخيارات غير متوقعة للسهم %s", symbol)
        return {}

    buckets: dict[str, list[dict[str, Any]]] = {"daily": [], "weekly": [], "monthly": []}
    for contract, expiry, strike in filtered:
        days_to_expiry = (expiry - today).days
        if 0 <= days_to_expiry <= 7:
            bucket = "daily"
        elif 8 <= days_to_expiry <= 14:
            bucket = "weekly"
        elif 15 <= days_to_expiry <= 45:
            bucket = "monthly"
        else:
            continue

        contract_symbol = str(_field(contract, "symbol"))
        snapshot = chain.get(contract_symbol)
        if snapshot is None:
            continue
        quote = _field(snapshot, "latest_quote")
        bid = _as_float(_field(quote, "bid_price")) if quote else None
        ask = _as_float(_field(quote, "ask_price")) if quote else None
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        if not 0.30 <= mid <= 50:
            continue
        spread_pct = ((ask - bid) / mid) * 100 if mid else 1000
        if spread_pct > MAX_OPTION_SPREAD_PCT:
            continue

        oi = _as_float(_field(contract, "open_interest"), 0.0) or 0.0
        oi_date = _as_date(_field(contract, "open_interest_date"))
        # لا نعرض قيمة OI وكأنها حديثة إذا كان تاريخها قديمًا بوضوح.
        oi_is_recent = oi_date is not None and (today - oi_date).days <= 7
        diff_pct = abs(strike - stock_price) / stock_price * 100
        score = spread_pct + 0.35 * diff_pct - 0.4 * min(math.log1p(max(oi, 0)), 12)
        buckets[bucket].append({
            "symbol": contract_symbol,
            "strike": strike,
            "exp": expiry.isoformat(),
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "oi": int(oi) if oi_is_recent else None,
            "diff": round(diff_pct, 1),
            "score": score,
        })

    result: dict[str, dict[str, Any]] = {}
    for bucket, candidates in buckets.items():
        if candidates:
            result[bucket] = min(candidates, key=lambda item: item["score"])
    return result


def _load_state() -> dict[str, str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("تعذر قراءة ملف حالة التنبيهات: %s", exc)
        return {}


def _save_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STATE_FILE)
    except Exception as exc:
        logger.exception("تعذر حفظ حالة التنبيهات: %s", exc)


def send_telegram(message: str) -> bool:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=20,
        )
        if not response.ok:
            logger.error("Telegram رفض الرسالة: HTTP %s - %s", response.status_code, response.text[:500])
            return False
        payload = response.json()
        if not payload.get("ok", False):
            logger.error("Telegram أبلغ عن فشل إرسال: %s", payload)
            return False
        return True
    except Exception as exc:
        logger.exception("فشل إرسال Telegram: %s", exc)
        return False


def _option_message(label: str, opt: dict[str, Any]) -> str:
    oi_text = str(opt["oi"]) if opt["oi"] is not None else "غير متاح/غير حديث"
    return (
        f"{label} ({opt['exp']})\n"
        f"🔹 العقد: {opt['symbol']} | سترايك: {opt['strike']:.2f}$\n"
        f"💵 متوسط العرض والطلب: {opt['mid']:.2f}$ "
        f"(Bid {opt['bid']:.2f} / Ask {opt['ask']:.2f})\n"
        f"📈 OI: {oi_text} | السبريد: {opt['spread_pct']:.1f}% | البعد عن السعر: {opt['diff']}%\n"
    )


def build_alert(symbol: str, price: float, hist: pd.DataFrame, levels: dict[str, float], sq: dict[str, Any]) -> str:
    option_type = "CALL" if sq["dir"] == "UP" else "PUT"
    rsi = calc_rsi(hist)
    sma50 = float(hist["Close"].rolling(50, min_periods=50).mean().iloc[-1]) if len(hist) >= 50 else None
    resistance = levels["res"] if option_type == "CALL" else levels["sup"]
    stops = calc_levels(hist, price, is_put=(option_type == "PUT"))
    liquidity_text, ob_text = calc_liquidity_and_ob(hist, sq["dir"])

    lines = [
        f"⏳ انطلاق محتمل بعد انضغاط Squeeze ({option_type}) — {symbol}",
        f"🕯 الإطار: شموع مكتملة {BAR_MINUTES} دقائق | وقت الإشارة UTC: {sq['bar_time']}",
        f"💵 سعر السهم اللحظي/آخر شمعة: {price:.2f}$",
        f"📊 RSI: {rsi:.1f}" if rsi is not None else "📊 RSI: غير متاح",
        f"📉 المتوسط 50: {sma50:.2f}$" if sma50 is not None and math.isfinite(sma50) else "📉 المتوسط 50: بيانات غير كافية",
        f"🧱 مقاومة/دعم تقريبي (20 شمعة سابقة): {resistance:.2f}$",
        f"{liquidity_text} | OB: {ob_text}",
    ]
    if stops:
        stop, target1, target2, target3 = stops
        lines.extend([
            "",
            "مستويات ATR التقديرية للسهم (وليست لسعر عقد الخيار):",
            f"🛑 وقف مرجعي: {stop:.2f}$",
            f"🎯 أهداف مرجعية: {target1:.2f}$ / {target2:.2f}$ / {target3:.2f}$",
        ])
    else:
        lines.extend(["", "مستويات ATR: بيانات غير كافية لحسابها."])

    # جلب سلسلة الخيارات قد يفشل بسبب صلاحيات/اشتراك بيانات الخيارات؛ لا يمنع تنبيه السهم.
    try:
        options = get_option_recommendations(symbol, price, option_type)
    except Exception as exc:
        logger.exception("فشل تحليل خيارات %s: %s", symbol, exc)
        options = {}
    lines.append("")
    if options:
        labels = {
            "daily": "⚡ عقد قريب (0–7 أيام)",
            "weekly": "📅 عقد أسبوعي (8–14 يومًا)",
            "monthly": "🛡️ عقد أبعد (15–45 يومًا)",
        }
        for bucket in ("daily", "weekly", "monthly"):
            if bucket in options:
                lines.append(_option_message(labels[bucket], options[bucket]))
    else:
        lines.append("عقود الخيارات: لم يُعثر على عقد يطابق فلاتر السعر والسيولة والسبريد، أو أن بيانات الخيارات غير متاحة.")

    lines.extend([
        "",
        "تنبيه تعليمي آلي فقط؛ لا يمثل توصية مالية ولا ينفذ صفقات. الخيارات عالية المخاطر وقد تفقد كامل قيمتها.",
    ])
    return "\n".join(lines)


def _prune_state(state: dict[str, str]) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    kept: dict[str, str] = {}
    for key, value in state.items():
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff:
                kept[key] = value
        except (TypeError, ValueError):
            continue
    return kept


def main_loop() -> None:
    state = _prune_state(_load_state())
    if send_telegram(
        f"✅ بدأ بوت Squeeze. الإشارات على شموع مكتملة {BAR_MINUTES} دقائق. "
        "التنبيهات تحليلية فقط ولا تنفذ صفقات."
    ):
        logger.info("تم إرسال إشعار التشغيل")

    while True:
        try:
            histories, prices = fetch_market_data()
            logger.info("تم تحديث البيانات: %s رمزًا، %s سعرًا", len(histories), len(prices))
            state = _prune_state(state)

            for symbol in SYMBOLS:
                try:
                    hist = histories.get(symbol)
                    price = prices.get(symbol)
                    if hist is None or price is None or len(hist) < 60:
                        continue
                    sq = check_squeeze(hist)
                    if not sq or not sq["firing"]:
                        continue
                    if state.get(symbol) == sq["bar_time"]:
                        continue

                    # مستويات الدعم والمقاومة من الشموع الأسبق دون شمعة الاختراق الحالية.
                    prior = hist.iloc[:-1].tail(20)
                    if len(prior) < 10:
                        continue
                    levels = {
                        "res": float(prior["High"].max()),
                        "sup": float(prior["Low"].min()),
                    }
                    message = build_alert(symbol, price, hist, levels, sq)
                    if send_telegram(message):
                        state[symbol] = sq["bar_time"]
                        _save_state(state)
                        logger.info("تم إرسال تنبيه %s عند %s", symbol, sq["bar_time"])
                    time.sleep(1.0)
                except Exception as exc:
                    logger.exception("خطأ أثناء فحص %s: %s", symbol, exc)
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            logger.exception("خطأ في دورة البوت: %s", exc)
            time.sleep(15)


@app.get("/")
def home():
    return "Bot OK - Squeeze Alerts (5-minute closed bars)"


@app.get("/health")
def health():
    return jsonify({"status": "ok", "symbols": len(SYMBOLS), "bar_minutes": BAR_MINUTES})


_worker_lock = Lock()
_worker_started = False


def start_background_worker() -> None:
    """ابدأ حلقة البوت مرة واحدة داخل عملية التطبيق، بما فيها تشغيل Gunicorn."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        Thread(target=main_loop, name="squeeze-worker", daemon=True).start()
        _worker_started = True


# Gunicorn يستورد app من هذا الملف؛ لذلك يجب تشغيل الحلقة عند الاستيراد أيضًا.
# استخدم عامل Gunicorn واحدًا لتجنب تكرار إرسال الإشارات.
start_background_worker()


if __name__ == "__main__":
    # شغّله كعملية واحدة: python bot_revised.py
    # تجنّب تشغيل عدة Workers حتى لا تتكرر التنبيهات.
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
