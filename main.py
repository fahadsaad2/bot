from flask import Flask
import threading, yfinance as yf, requests, time, os, json, math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home(): return 'Bot V4.8 Arabic Fixed'
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def send_tg(text):
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        requests.post(url, json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=15)
        return True
    except: return False

KSA = ZoneInfo('Asia/Riyadh')
US_EASTERN = ZoneInfo('America/New_York')
def now_ksa(): return datetime.now(KSA)
def now_us(): return datetime.now(US_EASTERN)

# قللت الشركات عشان ما ينحظر
TICKERS = ['NVDA','TSLA','META','AMD','MSFT','AVGO']

MIN_VOLUME, MIN_OI, MIN_SCORE = 50, 50, 45
MAX_SPREAD_PCT = 20
SCAN_INTERVAL = 400

daily_count = defaultdict(int)
sent_contracts = set()

def safe_float(v,d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except: return d
def safe_int(v,d=0):
    try: return int(float(v))
    except: return d
def calculate_dte(exp):
    try: return (datetime.strptime(exp, '%Y-%m-%d').date() - now_us().date()).days
    except: return 0
def is_us_market_open():
    try:
        cur = now_us()
        if cur.weekday()>=5: return False
        s = datetime(cur.year,cur.month,cur.day,9,30,tzinfo=US_EASTERN).time()
        e = datetime(cur.year,cur.month,cur.day,16,0,tzinfo=US_EASTERN).time()
        return s <= cur.time() < e
    except: return True

def get_price_safe(ticker):
    for i in range(4):
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d', interval='1d', auto_adjust=False)
            if not hist.empty:
                return stock, hist
        except Exception as ex:
            print(f"{ticker} retry {i} {ex}")
        time.sleep(8) # اهم شي - 8 ثواني عشان ياهو يفك الحظر
    return None, pd.DataFrame()

def get_spy_trend():
    try:
        _, df = get_price_safe('SPY')
        if df.empty: return 'NEUTRAL'
        close = df['Close']
        return 'BULLISH' if close.iloc[-1] > close.ewm(20).mean().iloc[-1] else 'BEARISH'
    except: return 'NEUTRAL'

def format_signal(ticker, stock_price, strike, cp, exp, dte, premium, vol, oi, score):
    entry = premium
    stop_o = round(entry*0.60,2)
    target_o = round(entry*1.80,2)
    target2_o = round(entry*2.5,2)
    icon = '🟢' if cp=='C' else '🔴'
    tipo = 'شراء' if cp=='C' else 'بيع'
    return (f"{icon} <b>🐋 حوت دخل - {ticker} {tipo}</b>\n\n"
            f"📌 <b>{ticker}</b> ${stock_price:.2f}\n"
            f"🎯 <b>{strike:g}{cp}</b> ينتهي {exp} ({dte} يوم)\n\n"
            f"💵 <b>دخول العقد:</b> ${entry:.2f}\n"
            f"🛑 <b>وقف:</b> ${stop_o} (-40%)\n"
            f"🎯 <b>هدف 1:</b> ${target_o} (+80%)\n"
            f"🚀 <b>هدف 2:</b> ${target2_o} (+150%)\n\n"
            f"📊 فوليوم {vol:,} | OI {oi:,}\n"
            f"⭐ قوة {score}/100\n"
            f"🕐 {now_ksa().strftime('%H:%M')}")

def scan_one(ticker, spy):
    try:
        stock, hist = get_price_safe(ticker)
        if hist.empty: 
            print(f"{ticker}: No data - skip")
            return
        price = safe_float(hist['Close'].iloc[-1])
        if price==0: return
        
        # خذ اقرب تاريخين بس عشان ما ينحظر
        exps = stock.options[:2]
        for exp in exps:
            dte = calculate_dte(exp)
            if dte<4 or dte>21: continue
            try:
                chain = stock.option_chain(exp)
                for _, row in chain.calls.iterrows():
                    vol = safe_int(row.get('volume')); oi = safe_int(row.get('openInterest'))
                    if vol < MIN_VOLUME or oi < MIN_OI: continue
                    prem = safe_float(row.get('lastPrice'))
                    if prem < 0.30: continue
                    spread = 0
                    try:
                        bid = safe_float(row.get('bid')); ask = safe_float(row.get('ask'))
                        if bid>0 and ask>0: spread = (ask-bid)/prem*100
                    except: pass
                    if spread > MAX_SPREAD_PCT: continue
                    # سكور بسيط
                    score = 50 + (vol/100)
                    cid = f"{ticker}_{exp}_{row.get('strike')}"
                    if cid in sent_contracts: continue
                    if score >= MIN_SCORE:
                        send_tg(format_signal(ticker, price, safe_float(row.get('strike')), 'C', exp, dte, prem, vol, oi, int(score)))
                        sent_contracts.add(cid)
                        print(f"OK {ticker} CALL")
                        return
            except Exception as ex:
                print(f"{ticker} chain error {ex}")
                time.sleep(5)
    except Exception as ex:
        print(f"{ticker} scan failed {ex}")

spy_now = get_spy_trend()
send_tg(f"🚀 <b>البوت V4.8 شغال</b>\n📋 {', '.join(TICKERS)}\n📈 SPY: {spy_now}\nتم تقليل الفلتر عشان يفك حظر ياهوو")

while True:
    try:
        if not is_us_market_open():
            time.sleep(60)
            continue
        spy = get_spy_trend()
        print(f"SCAN SPY {spy}")
        for t in TICKERS:
            scan_one(t, spy)
            time.sleep(10) # 10 ثواني بين كل شركة
        time.sleep(SCAN_INTERVAL)
    except Exception as ex:
        print(ex)
        time.sleep(20)
