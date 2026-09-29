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
    return "Bot V4.5 350 Fixed Running"

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
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except Exception as e:
        print(e)
        return False

KSA = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

def now_ksa():
    return datetime.now(KSA)

def now_us():
    return datetime.now(US_EASTERN)

TICKERS = ["NVDA", "TSLA", "META", "AMD", "AMZN", "MSFT", "PLTR", "AVGO", "SNDK", "LITE", "MU", "QCOM", "APP"]
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
EARNINGS_BUFFER_DAYS = 3

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
    except:
        pass

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "date": str(now_ksa().date()),
                    "daily_count": dict(daily_count),
                    "sent_contracts": list(sent_contracts)
                },
                f,
                ensure_ascii=False,
                indent=2
            )
    except:
        pass

load_state()

def safe_float(v, d=0.0):
    try:
        if pd.isna(v):
            return d
        return float(v)
    except:
        return d

def safe_int(v, d=0):
    try:
        if pd.isna(v):
            return d
        return int(float(v))
    except:
        return d

def clamp(v, l, h):
    return max(l, min(h, v))

def nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    offset = (wd - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)

def last_weekday(y, m, wd):
    if m == 12:
        d = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(y, m + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - wd) % 7
    return d - timedelta(days=offset)

def easter_sunday(y):
    a = y % 19
    b = y // 100
    c = y % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)

def observed_date(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def us_market_holidays(y):
    holidays = set()
    holidays.add(observed_date(date(y, 1, 1)))
    holidays.add(nth_weekday(y, 1, 0, 3))
    holidays.add(nth_weekday(y, 2, 0, 3))
    easter = easter_sunday(y)
    holidays.add(easter - timedelta(days=2))
    holidays.add(last_weekday(y, 5, 0))
    holidays.add(observed_date(date(y, 6, 19)))
    holidays.add(observed_date(date(y, 7, 4)))
    holidays.add(nth_weekday(y, 9, 0, 1))
    holidays.add(nth_weekday(y, 11, 3, 4))
    holidays.add(observed_date(date(y, 12, 25)))
    return holidays

def is_us_market_open():
    cur = now_us()
    if cur.weekday() >= 5:
        return False
    if cur.date() in us_market_holidays(cur.year):
        return False
    start = datetime(cur.year, cur.month, cur.day, 9, 30, tzinfo=US_EASTERN).time()
    end = datetime(cur.year, cur.month, cur.day, 16, 0, tzinfo=US_EASTERN).time()
    return start <= cur.time() < end

def market_time_message():
    us = now_us()
    ksa = now_ksa()
    return f"توقيت امريكا: {us.strftime('%H:%M')} | السعودية: {ksa.strftime('%H:%M')}"

def calculate_dte(exp):
    try:
        return (datetime.strptime(exp, "%Y-%m-%d").date() - now_us().date()).days
    except:
        return 0

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
    except
