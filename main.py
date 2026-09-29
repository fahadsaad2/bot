from flask import Flask
import threading
import yfinance as yf
import requests
import time
import os
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pandas as pd

app = Flask(__name__)
@app.route("/")
def home():
    return "Bot V4.5 Arabic 350 Running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except Exception as ex:
        print(ex)
        return False

KSA = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

def now_ksa():
    return datetime.now(KSA)

def now_us():
    return datetime.now(US_EASTERN)

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","LITE","MU","QCOM","APP"]
MAX_SIGNALS_PER_TICKER = 3
MIN_VOLUME = 350
MIN_OI = 250
MIN_DTE = 7
MAX_DTE = 21
MAX_SPREAD_PCT = 10.0
MIN_OPTION_PRICE = 0.40
MIN_STRIKE_DISTANCE = -0.06
MAX_STRIKE_DISTANCE = 0.06
MIN_SCORE = 65
SCAN_INTERVAL = 90
MAX_EXPIRATIONS = 4

STATE_FILE = "bot_state_v4.json"
daily_count = defaultdict(int)
sent_contracts = set()
last_state_date = now_ksa().date()

def load_state():
    global last_state_date
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == str(now_ksa().date()):
                daily_count.update(data.get("daily_count", {}))
                sent_contracts.update(data.get("sent_contracts", []))
                last_state_date = now_ksa().date()
    except Exception as ex:
        print(ex)

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": str(now_ksa().date()), "daily_count": dict(daily_count), "sent_contracts": list(sent_contracts)}, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        print(ex)

load_state()

def safe_float(v, d=0.0):
    try:
        if pd.isna(v):
            return d
        return float(v)
    except Exception as ex:
        return d

def safe_int(v, d=0):
    try:
        if pd.isna(v):
            return d
        return int(float(v))
    except Exception as ex:
        return d

def clamp(v, l, h):
    return max(l, min(h, v))

def calculate_dte(exp):
    try:
        return (datetime.strptime(exp, "%Y-%m-%d").date() - now_us().date()).days
    except Exception as ex:
        return 0

def is_us_market_open():
    try:
        cur = now_us()
        if cur.weekday() >= 5:
            return False
        start = datetime(cur.year, cur.month, cur.day, 9, 30, tzinfo=US_EASTERN).time()
        end = datetime(cur.year, cur.month, cur.day, 16, 0, tzinfo=US_EASTERN).time()
        return start <= cur.time() < end
    except Exception as ex:
        return True

def get_spy_trend():
    try:
        df = yf.Ticker("SPY").history(period="1d", interval="5m", auto_adjust=False)
        if df.empty or len(df) < 20:
            return "NEUTRAL"
        close = df['Close']
        vol = df['Volume']
        vwap = (close * vol).cumsum() / vol.cumsum()
        ema20 = close.ewm(span=20, adjust=False).mean()
        price = safe_float(close.iloc[-1])
        v = safe_float(vwap.iloc[-1])
        e = safe_float(ema20.iloc[-1])
        if price > v and price > e:
            return "BULLISH"
        if price < v and price < e:
            return "BEARISH"
        return "NEUTRAL"
    except Exception as ex:
        print(ex)
        return "NEUTRAL"

def calculate_atr(hist, period=14):
    try:
        df = hist.copy()
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L','H-PC','L-PC']].max(axis=1)
        return safe_float(df['TR'].rolling(period).mean().iloc[-1], 1.0)
    except Exception as ex:
        return 1.0

def get_intraday_levels(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m", auto_adjust=False)
        if df.empty or len(df) < 5:
            return None
        pv = df['Close'] * df['Volume']
        vwap = (pv.cumsum() / df['Volume'].cumsum()).iloc[-1]
        return {"vwap": safe_float(vwap), "day_low": safe_float(df['Low'].min()), "day_high": safe_float(df['High'].max()), "current": safe_float(df['Close'].iloc[-1])}
    except Exception as ex:
        return None

def technical_analysis(hist):
    result = {"trend": "NEUTRAL", "momentum": "NEUTRAL", "rsi": 50.0, "support": 0, "resistance": 0, "volume_ratio": 1.0, "atr": 1.0}
    try:
        close = hist["Close"].dropna()
        volume = hist["Volume"].fillna(0)
        if len(close) < 20:
            return result
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=min(50, len(close)), adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        current = safe_float(close.iloc[-1])
        e20 = safe_float(ema20.iloc[-1])
        e50 = safe_float(ema50.iloc[-1])
        crsi = safe_float(rsi.iloc[-1], 50)
        recent = close.tail(20)
        avg_vol = safe_float(volume.tail(20).mean())
        cur_vol = safe_float(volume.iloc[-1])
        if current > e20 and e20 > e50:
            trend = "BULLISH"
        elif current < e20 and e20 < e50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        if crsi >= 52 and current > e20:
            momentum = "BULLISH"
        elif crsi <= 48 and current < e20:
            momentum = "BEARISH"
        else:
            momentum = "NEUTRAL"
        vr = cur_vol / avg_vol if avg_vol > 0 else 1
        result = {"trend": trend, "momentum": momentum, "rsi": crsi, "support": safe_float(recent.min()), "resistance": safe_float(recent.max()), "volume_ratio": vr, "atr": calculate_atr(hist)}
    except Exception as ex:
        print(ex)
    return result

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def calculate_greeks(S, K, iv, dte, typ):
    try:
        if S <= 0 or K <= 0 or iv <= 0 or dte <= 0:
            return {}
        T = dte / 365
        r = 0.04
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_t)
        gamma = norm_pdf(d1) / (S * iv * sqrt_t)
        vega = S * norm_pdf(d1) * sqrt_t / 100
        if typ == "CALL":
            delta = norm_cdf(d1)
        else:
            delta = norm_cdf(d1) - 1
        return {"delta": delta, "gamma": gamma, "vega": vega}
    except Exception as ex:
        return {}

def normalize_iv(iv):
    iv = safe_float(iv)
    if iv > 5:
        iv = iv / 100
    return iv

def iv_score(iv):
    p = iv * 100
    if 15 <= p <= 55:
        return 10
    if 10 <= p <= 70:
        return 6
    if p <= 90:
        return 2
    return 0

def analyze_contract(ticker, stock_price, row, expiration, option_type, technical, intraday, spy_trend):
    try:
        if spy_trend == "BEAR
