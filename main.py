from flask import Flask
import threading, yfinance as yf, requests, time, os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from curl_cffi import requests as crequests

app = Flask(__name__)
@app.route('/')
def home(): return 'Bot V5.2 Arabic Fixed'
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def send_tg(text):
    try:
        if not BOT_TOKEN or not CHAT_ID: return False
        url = 'https://api.telegram.org/bot' + BOT_TOKEN + '/sendMessage'
        r = requests.post(url, json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=15)
        return r.ok
    except: return False

KSA = ZoneInfo('Asia/Riyadh')
US_EASTERN = ZoneInfo('America/New_York')
def now_ksa(): return datetime.now(KSA)
def now_us(): return datetime.now(US_EASTERN)

TICKERS = ['NVDA','TSLA','META','AMD','AMZN','MSFT','PLTR','AVGO','SNDK','LITE','MU','QCOM','APP','SPY']

MIN_VOLUME = 10
MIN_OI = 10
MIN_SCORE = 20
MIN_OPTION_PRICE = 1.0
MAX_OPTION_PRICE = 10.0
MIN_DTE = 7
MAX_DTE = 30
SCAN_INTERVAL = 180
MAX_SIGNALS_PER_TICKER = 3

daily_count = defaultdict(int)
sent_contracts = set()
last_date = now_ksa().date()

def safe_float(v,d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except: return d
def safe_int(v,d=0):
    try:
        if pd.isna(v): return d
        return int(float(v))
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
            sess = crequests.Session(impersonate="chrome")
            stock = yf.Ticker(ticker, session=sess)
            hist = stock.history(period='5d', interval='1d', auto_adjust=False)
            if not hist.empty: return stock, hist
        except: pass
        time.sleep(5)
    return None, pd.DataFrame()

def get_spy_trend():
    try:
        _, df = get_price_safe('SPY')
        if df.empty: return 'NEUTRAL'
        close = df['Close']
        return 'BULLISH' if close.iloc[-1] > close.ewm(20).mean().iloc[-1] else 'BEARISH'
    except: return 'NEUTRAL'

def format_signal(best):
    entry = best['prem']
    stop_o = round(entry*0.60,2)
    target_o = round(entry*1.80,2)
    target2_o = round(entry*2.5,2)
    icon = '🟢' if best['cp']=='C' else '🔴'
    tipo = 'شراء' if best['cp']=='C' else 'بيع'
    # هنا رجعت العربي المفصل
    msg = f"{icon} <b>حوت دخل - {best['ticker']} {tipo} | {best['spy']}</b>\n\n"
    msg += f"📌 <b>{best['ticker']}</b> ${round(best['price'],2)}\n"
    msg += f"🎯 <b>{best['strike']}{best['cp']}</b> ينتهي {best['exp']} ({best['dte']} يوم)\n"
    msg += f"💵 دخول: ${entry} | وقف: ${stop_o} (-40%)\n"
    msg += f"🎯 هدف1: ${target_o} (+80%)\n"
    msg += f"🚀 هدف2: ${target2_o} (+150%)\n"
    msg += f"📊 فوليوم {best['vol']} | OI {best['oi']}\n"
    msg += f"⭐ قوة {best['score']}/100\n"
    msg += f"🕐 {now_ksa().strftime('%H:%M')} KSA"
    return msg

def scan_one(ticker, spy):
    try:
        stock, hist = get_price_safe(ticker)
        if hist.empty or stock is None: return False
        price = safe_float(hist['Close'].iloc[-1])
        if price==0: return False
        try: exps = stock.options
        except: return False
        best = None
        for exp in exps:
            dte = calculate_dte(exp)
            if dte < MIN_DTE or dte > MAX_DTE: continue
            try:
                chain = stock.option_chain(exp)
                all_rows = [(r,'C') for _,r in chain.calls.iterrows()] + [(r,'P') for _,r in chain.puts.iterrows()]
                for row, cp in all_rows:
                    vol = safe_int(row.get('volume')); oi = safe_int(row.get('openInterest'))
                    if vol < MIN_VOLUME or oi < MIN_OI: continue
                    prem = safe_float(row.get('lastPrice'))
                    if prem < MIN_OPTION_PRICE or prem > MAX_OPTION_PRICE: continue
                    score = 35 + min(vol/5, 35) + min(oi/10, 20)
                    cid = ticker + "_" + exp + "_" + str(row.get('strike')) + "_" + cp
                    if cid in sent_contracts: continue
                    if best is None or score > best['score']:
                        best = {'ticker':ticker,'price':price,'strike':safe_float(row.get('strike')),'cp':cp,'exp':exp,'dte':dte,'prem':prem,'vol':vol,'oi':oi,'score':int(min(score,100)),'spy':spy}
            except: time.sleep(2)
        if best and best['score'] >= MIN_SCORE:
            if send_tg(format_signal(best)):
                sent_contracts.add(best['ticker'] + "_" + best['exp'] + "_" + str(best['strike']) + "_" + best['cp'])
                daily_count[ticker]+=1
                return True
        return False
    except Exception as ex:
        print(ticker + " failed " + str(ex))
        return False

# رسالة البداية عربي مفصل - مصلحة بدون رموز تخرب
spy_now = get_spy_trend()
tickers_text = ", ".join(TICKERS)
start_msg = f"🚀 <b>البوت V5.2 شغال</b>\n💵 عقود ${MIN_OPTION_PRICE}-${MAX_OPTION_PRICE}\n📅 مدة {MIN_DTE}-{MAX_DTE} يوم\n📋 {tickers_text}\n📈 SPY: {spy_now}\n🕐 {now_ksa().strftime('%H:%M')}"
send_tg(start_msg)

no_signal = 0
while True:
    try:
        if now_ksa().date()!= last_date:
            daily_count.clear(); sent_contracts.clear(); last_date = now_ksa().date()
        if not is_us_market_open():
            time.sleep(60); continue
        spy = get_spy_trend()
        found = False
        for t in TICKERS:
            if daily_count[t] >= MAX_SIGNALS_PER_TICKER: continue
            if scan_one(t, spy): found = True
            time.sleep(8)
        if not found:
            no_signal+=1
            if no_signal >= 10:
                # مصلح بدون $
                msg2 = f"فحص مستمر - 14 شركة | SPY: {spy} - لا يوجد حيتان حاليا - السوق هادي"
                send_tg(msg2)
                no_signal=0
        else: no_signal=0
        time.sleep(SCAN_INTERVAL)
    except Exception as ex:
        print("main loop " + str(ex))
        time.sleep(30)
