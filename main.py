"""
تنبيهات Squeeze + Breakout للأسهم الأمريكية عبر Alpaca وTelegram.
محدث خصيصاً للنسخة القديمة الممتازة مع تخصيص الأسهم المجنونة (SNDK, MU, LITE, SMCI).
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
from zoneinfo import ZoneInfo

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

# ====================== الإعدادات ======================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = ["SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP", "ARM", "AVGO", "MU", "LITE", "RDDT", "SNDK"]
SYMBOLS = TIER1 + TIER2

# الأسهم المجنونة التي تتطلب حساسية عالية جداً وقنص فوري للاستيمال
CRAZY_SYMBOLS = {"SNDK", "MU", "LITE", "SMCI"}

BAR_MINUTES = 5
TIMEFRAME = TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute)
POLL_SECONDS = max(15, int(os.getenv("POLL_SECONDS", "20")))
HISTORY_DAYS = max(8, int(os.getenv("HISTORY_DAYS", "10")))
STATE_FILE = Path(os.getenv("STATE_FILE", "squeeze_state.json"))

MAX_SPREAD_DAILY = float(os.getenv("MAX_SPREAD_DAILY", "40"))
MAX_SPREAD_WEEKLY = float(os.getenv("MAX_SPREAD_WEEKLY", "45"))
MAX_SPREAD_MONTHLY = float(os.getenv("MAX_SPREAD_MONTHLY", "50"))
MIN_OI = int(os.getenv("MIN_OI", "50"))

US_EASTERN = ZoneInfo("America/New_York")

app = Flask(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("squeeze_bot")

_required = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    "ALPACA_API_KEY": ALPACA_API_KEY,
    "ALPACA_SECRET_KEY": ALPACA_SECRET_KEY,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise RuntimeError("متغيرات البيئة المطلوبة غير موجودة: " + ", ".join(_missing))

stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

# ====================== أدوات مساعدة ======================
def _field(obj: Any, name: str, default: Any = None) -> Any:
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

def is_market_open() -> bool:
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def _bar_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.index, pd.MultiIndex):
        try:
            frame = frame.xs(symbol, level=0)
        except KeyError:
            return pd.DataFrame()
    rename = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
        "trade_count": "TradeCount", "vwap": "VWAP"
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
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=SYMBOLS,
        timeframe=TIMEFRAME,
        start=now - timedelta(days=HISTORY_DAYS),
        limit=1000
    )
    bar_set = stock_client.get_stock_bars(request)
    raw = bar_set.df
    histories: dict[str, pd.DataFrame] = {}

    for symbol in SYMBOLS:
        frame = _bar_frame(raw, symbol)
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

    for symbol, frame in histories.items():
        if symbol not in prices and len(frame):
            fallback = _as_float(frame["Close"].iloc[-1])
            if fallback and fallback > 0:
                prices[symbol] = fallback
    return histories, prices

def calc_rsi(hist: pd.DataFrame, period: int = 14) -> float | None:
    if len(hist) < period + 1:
        return None
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    if not math.isfinite(float(avg_gain)) or not math.isfinite(float(avg_loss)):
        return None
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return float(100 - (100 / (1 + (avg_gain / avg_loss))))

def calc_atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    if len(hist) < period + 1:
        return None
    previous_close = hist["Close"].shift(1)
    tr = pd.concat([
        hist["High"] - hist["Low"],
        (hist["High"] - previous_close).abs(),
        (hist["Low"] - previous_close).abs()
    ], axis=1).max(axis=1)
    value = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(value) if pd.notna(value) and math.isfinite(float(value)) and value > 0 else None

def calc_levels(hist: pd.DataFrame, entry: float, is_put: bool = False) -> tuple[float, float, float, float] | None:
    atr = calc_atr(hist)
    if atr is None or entry <= 0:
        return None
    if is_put:
        values = (entry + atr, entry - 1.5*atr, entry - 3*atr, entry - 5*atr)
    else:
        values = (entry - atr, entry + 1.5*atr, entry + 3*atr, entry + 5*atr)
    return tuple(round(x, 2) for x in values)

# ====================== منطق كشف الانضغاط والانفجار المطور للأسهم المجنونة ======================
def check_signal(symbol: str, hist: pd.DataFrame) -> dict[str, Any] | None:
    if len(hist) < 25:
        return None
    close = hist["Close"]
    mid = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    prev_close = close.shift(1)
    tr = pd.concat([
        hist["High"] - hist["Low"],
        (hist["High"] - prev_close).abs(),
        (hist["Low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(20, min_periods=20).mean()

    upper_bb = mid + 2 * std
    lower_bb = mid - 2 * std
    upper_kc = mid + 1.5 * atr
    lower_kc = mid - 1.5 * atr

    if any(pd.isna([upper_bb.iloc[-1], lower_bb.iloc[-1], upper_kc.iloc[-1], lower_kc.iloc[-1], mid.iloc[-1]])):
        return None

    squeeze_now = lower_bb.iloc[-1] > lower_kc.iloc[-1] and upper_bb.iloc[-1] < upper_kc.iloc[-1]
    bb_width = float((upper_bb.iloc[-1] - lower_bb.iloc[-1]) / mid.iloc[-1] * 100)

    last = hist.iloc[-1]
    prev_20 = hist.iloc[-21:-1]
    avg_vol = hist["Volume"].tail(20).mean() if "Volume" in hist.columns else 0
    curr_vol = float(last["Volume"]) if "Volume" in hist.columns else 0

    direction = "UP" if close.iloc[-1] >= mid.iloc[-1] else "DOWN"

    # حساسية فائقة للأسهم المجنونة (SNDK, MU, LITE, SMCI) لالتقاط أي انفجار أو نزول مجنون فوراً
    is_crazy = symbol in CRAZY_SYMBOLS
    vol_multiplier = 1.05 if is_crazy else 1.25

    high_prev = float(prev_20["High"].max())
    low_prev = float(prev_20["Low"].min())

    is_breakout_up = last["Close"] > high_prev and (curr_vol > avg_vol * vol_multiplier if avg_vol > 0 else True)
    is_breakout_down = last["Close"] < low_prev and (curr_vol > avg_vol * vol_multiplier if avg_vol > 0 else True)

    if squeeze_now:
        return {
            "type": "SQUEEZE",
            "dir": direction,
            "bar_time": hist.index[-1].isoformat(),
            "bb_width": bb_width
        }
    elif is_breakout_up:
        return {
            "type": "BREAKOUT",
            "dir": "UP",
            "bar_time": hist.index[-1].isoformat(),
            "bb_width": bb_width
        }
    elif is_breakout_down:
        return {
            "type": "BREAKOUT",
            "dir": "DOWN",
            "bar_time": hist.index[-1].isoformat(),
            "bb_width": bb_width
        }

    return None

# ====================== جلب عقود الأوبشن الثلاثة (يومي، أسبوعي، شهري) ======================
def get_option_recommendations(symbol: str, stock_price: float, option_type: str) -> dict[str, dict[str, Any]]:
    if stock_price <= 0:
        return {}
    target = ContractType.CALL if option_type == "CALL" else ContractType.PUT
    today = datetime.now(timezone.utc).date()
    max_expiry = today + timedelta(days=45)
    low = stock_price * (0.80 if option_type == "CALL" else 0.70)
    high = stock_price * (1.25 if option_type == "CALL" else 1.20)

    all_contracts: list[Any] = []
    page_token = None
    for _ in range(6):
        request_kwargs = {
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
            request_kwargs["page_token"] = page_token
        try:
            response = trading_client.get_option_contracts(GetOptionContractsRequest(**request_kwargs))
        except Exception:
            break
        contracts = _field(response, "option_contracts", []) or []
        page_token = _field(response, "next_page_token")
        all_contracts.extend(contracts)
        if not page_token:
            break

    if not all_contracts:
        return {}

    try:
        chain = option_client.get_option_chain(
            OptionChainRequest(
                underlying_symbol=symbol,
                type=target,
                strike_price_gte=low,
                strike_price_lte=high,
                expiration_date_gte=today,
                expiration_date_lte=max_expiry
            )
        )
    except Exception:
        return {}

    if not isinstance(chain, dict):
        return {}

    buckets: dict[str, list[dict[str, Any]]] = {"daily": [], "weekly": [], "monthly": []}
    for contract in all_contracts:
        expiry = _as_date(_field(contract, "expiration_date"))
        strike = _as_float(_field(contract, "strike_price"))
        contract_symbol = _field(contract, "symbol")
        if not expiry or not strike or not contract_symbol:
            continue
        days_to_expiry = (expiry - today).days

        if 0 <= days_to_expiry <= 7:
            bucket = "daily"
            max_spread = MAX_SPREAD_DAILY
            max_diff = 12
        elif 8 <= days_to_expiry <= 20:
            bucket = "weekly"
            max_spread = MAX_SPREAD_WEEKLY
            max_diff = 16
        elif 21 <= days_to_expiry <= 45:
            bucket = "monthly"
            max_spread = MAX_SPREAD_MONTHLY
            max_diff = 22
        else:
            continue

        snapshot = chain.get(str(contract_symbol))
        if snapshot is None:
            continue
        quote = _field(snapshot, "latest_quote")
        bid = _as_float(_field(quote, "bid_price")) if quote else None
        ask = _as_float(_field(quote, "ask_price")) if quote else None
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        if not 0.10 <= mid <= 50:
            continue
        spread_pct = ((ask - bid) / mid) * 100
        if spread_pct > max_spread:
            continue
        
        greeks = _field(snapshot, "greeks")
        vol = _as_float(_field(snapshot, "volume"), 0.0) or 0.0
        oi = _as_float(_field(contract, "open_interest"), 0.0) or 0.0
        if oi < MIN_OI:
            continue
        diff_pct = abs(strike - stock_price) / stock_price * 100
        if diff_pct > max_diff:
            continue

        whale_tag = " 🐋 دخول حيتان" if vol > 5000 or oi > 10000 else ""
        score = spread_pct + 0.3 * diff_pct - 0.5 * min(math.log1p(max(oi, 0)), 12)
        
        buckets[bucket].append({
            "symbol": str(contract_symbol),
            "strike": strike,
            "exp": expiry.isoformat(),
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "oi": int(oi),
            "vol": int(vol),
            "whale": whale_tag,
            "diff": round(diff_pct, 1),
            "score": score
        })

    result = {}
    for bucket, candidates in buckets.items():
        if candidates:
            result[bucket] = min(candidates, key=lambda x: x["score"])
    return result

# ====================== بناء الرسائل والحالة ======================
def _load_state() -> dict[str, str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STATE_FILE)
    except Exception:
        pass

def send_telegram(message: str) -> bool:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=20
        )
        return response.ok and response.json().get("ok", False)
    except Exception:
        return False

def _option_line(label: str, opt: dict[str, Any]) -> str:
    return (
        f"{label} {opt['exp']}{opt['whale']}\n"
        f"💰 سترايك {opt['strike']:.1f}$ - سعره {opt['mid']:.1f}$ | فوليوم: {opt['vol']} | OI: {opt['oi']} | فرق: {opt['diff']}%\n"
        f"🔹 العقد: <code>{opt['symbol']}</code>\n"
    )

def build_message(symbol: str, price: float, hist: pd.DataFrame, sig: dict[str, Any]) -> str:
    option_type = "CALL" if sig["dir"] == "UP" else "PUT"
    rsi = calc_rsi(hist)
    sma50 = float(hist["Close"].rolling(50, min_periods=50).mean().iloc[-1]) if len(hist) >= 50 else price
    prior = hist.iloc[:-1].tail(20)
    resistance = float(prior["High"].max()) if option_type == "CALL" else float(prior["Low"].min())
    stops = calc_levels(hist, price, is_put=(option_type == "PUT"))

    title = "⚠️ انضغاط" if sig["type"] == "SQUEEZE" else "🚀 انفجار قوي"
    emoji = "🟢" if option_type == "CALL" else "🔴"

    lines = [
        f"{title} {option_type} {emoji} <b>{symbol}</b> سعره <b>{price:.2f}$</b>",
        f"📊 المتوسط 50: {sma50:.2f}$ | المستوى المهم: {resistance:.2f}$ | RSI: {rsi:.1f if rsi else 0}",
        f"🧱 عرض BB: {sig['bb_width']:.2f}%",
        ""
    ]

    if stops:
        stop, t1, t2, t3 = stops
        atr_val = round(abs(t1 - price) if option_type == "CALL" else abs(price - t1), 2)
        lines.extend([
            f"🛑 وقف الخسارة: {stop:.2f}$ (ATR:{atr_val}$)",
            f"🎯 هدف اول: {t1:.2f}$",
            f"🎯 هدف ثاني: {t2:.2f}$",
            f"🎯 هدف ثالث: {t3:.2f}$",
            ""
        ])

    options = get_option_recommendations(symbol, price, option_type)
    labels = {
        "daily": "🔥 عقد يومي (0-7 ايام)",
        "weekly": "📅 عقد أسبوعي (8-20 يوم)",
        "monthly": "🛡️ عقد شهري (21-45 يوم)"
    }

    if options:
        for b in ("daily", "weekly", "monthly"):
            if b in options:
                lines.append(_option_line(labels[b], options[b]))
    else:
        lines.append("⚠️ لم يتم العثور على عقد مناسب ضمن الشروط الحالية.")

    lines.append("\n⚠️ تنبيه تعليمي فقط - الخيارات عالية المخاطر.")
    return "\n".join(lines)

# ====================== الحلقة الرئيسية ======================
def _prune_state(state: dict[str, str]) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    kept = {}
    for key, value in state.items():
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff:
                kept[key] = value
        except Exception:
            continue
    return kept

def main_loop() -> None:
    state = _prune_state(_load_state())
    last_heartbeat = datetime.now(timezone.utc)

    send_telegram(
        "✅ <b>تم تشغيل النسخة الذهبية للبوت بنجاح</b>\n"
        f"مراقبة {len(SYMBOLS)} شركة (مع تخصيص الأسهم المجنونة SNDK, MU, LITE, SMCI).\n"
        "إرسال العقود الثلاثة (يومي، أسبوعي، شهري) مع كشف الحيتان والفوليوم."
    )

    while True:
        try:
            if not is_market_open():
                logger.info("السوق مغلق - انتظار...")
                time.sleep(60)
                continue

            histories, prices = fetch_market_data()
            state = _prune_state(state)
            signals_today = 0

            for symbol in SYMBOLS:
                try:
                    hist = histories.get(symbol)
                    price = prices.get(symbol)
                    if hist is None or price is None or len(hist) < 30:
                        continue

                    sig = check_signal(symbol, hist)
                    if sig:
                        signal_key = f"{symbol}:{sig['type']}:{sig['dir']}:{sig['bar_time']}"
                        if signal_key not in state:
                            msg = build_message(symbol, price, hist, sig)
                            if send_telegram(msg):
                                state[signal_key] = sig["bar_time"]
                                _save_state(state)
                                signals_today += 1
                                logger.info("Signal sent for %s: %s", symbol, sig['type'])

                    time.sleep(0.2)

                except Exception as exc:
                    logger.exception("خطأ في معالجة %s: %s", symbol, exc)

            now = datetime.now(timezone.utc)
            if (now - last_heartbeat).total_seconds() > 3600:
                send_telegram(f"💓 البوت شغال | إشارات اليوم: {signals_today}")
                last_heartbeat = now

            time.sleep(POLL_SECONDS)

        except Exception as exc:
            logger.exception("خطأ في الحلقة الرئيسية: %s", exc)
            time.sleep(20)

@app.get("/")
def home():
    return "Bot OK - Golden Version Active"

@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "symbols": len(SYMBOLS),
        "market_open": is_market_open()
    })

_worker_lock = Lock()
_worker_started = False

def start_background_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        Thread(target=main_loop, name="squeeze-worker", daemon=True).start()
        _worker_started = True

start_background_worker()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
