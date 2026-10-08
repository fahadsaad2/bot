from datetime import datetime
import os
from threading import Thread
import time
import finnhub
from flask import Flask
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot OK - V5.5 Arabic Final'

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI', 'MSTR', 'COIN', 'AAPL', 'AMD', 'AMZN', 'PLTR', 'APP', 'ARM', 'AVGO', 'MU', 'LITE', 'SNDK', 'RDDT']
SYMBOLS = TIER1 + TIER2
TIER_CRAZY = ['SNDK', 'MU', 'MSTR', 'COIN', 'SMCI', 'APP', 'PLTR', 'LITE']
HIGH_PRICE = ['SNDK', 'META', 'AVGO', 'MSTR', 'APP', 'GOOGL', 'MSFT', 'NVDA', 'SMCI', 'COIN']

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = {}

def send(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    except Exception as e:
        print(f'SEND ERR {e}', flush=True)

def get_finnhub_quote_safe(sym):
    for _ in range(3):
        try:
            q = finnhub_client.quote(sym)
            if q and q.get('c', 0) > 0:
                return q
        except Exception as e:
            if '429' in str(e): time.sleep(60)
            else: break
    return None

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except: return 50.0

def calc_sma(hist, period=50):
    try: return float(hist['Close'].rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1])

def calc_atr(hist, period=14):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(hist, entry_price, is_put=False):
    atr = calc_atr(hist, 14)
    if is_put:
        stop = entry_price + (atr * 1.2)
        t1 = entry_price - (atr * 1.5)
        t2 = entry_price - (atr * 3.0)
        t3 = entry_price - (atr * 4.5)
    else:
        stop = entry_price - (atr * 1.2)
        t1 = entry_price + (atr * 1.5)
        t2 = entry_price + (atr * 3.0)
        t3 = entry_price + (atr * 4.5)
    return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2), round(atr, 2)

def update_resistances():
    print("Updating...", flush=True)
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if not hist.empty and len(hist) >= 
