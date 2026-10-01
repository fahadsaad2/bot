from flask import Flask
import threading, yfinance as yf, requests, time, os, json, math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home(): return 'Bot V4.9 14 Tickers Arabic'
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def send_tg(text):
    try:
        if not BOT_TOKEN or not CHAT_ID: return False
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        r = requests.post(url, json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=15)
        return r.ok
    except Exception as ex:
        print(f"tg error {ex}")
        return False

KSA = ZoneInfo('Asia/Riyadh')
US_EASTERN = ZoneInfo('America/New_York')
def now_ksa(): return datetime.now(KSA)
def now_us(): return datetime.now(US_EASTERN)

# رجعت لك كل شركاتك الـ 14
TICKERS = ['NVDA','TSLA','META','AMD','AMZN','MSFT','PLTR','AVGO','SNDK','LITE','MU','QCOM','APP','SPY']

MIN_VOLUME = 30
MIN_OI = 30
MIN_SCORE = 35
MAX_SPREAD_PCT = 25
MIN_OPTION_PRICE = 0.25
SCAN_INTERVAL = 600
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
            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d', interval='1d', auto_adjust=False)
            if not hist.empty:
                return stock, hist
        except Exception as ex:
            print(f"{ticker} retry {i} {ex}")
        time.sleep(8)
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
    be = best['strike'] + entry if best['cp']=='C' else best['strike'] - entry
    return (f"{icon} <b>🐋 حوت دخل - {best['ticker']} {tipo}</b> | {best['spy']}\n\n"
            f"📌 <b>{best['ticker']}</b> ${best['price']:.2f}\n"
            f"🎯 <b>{best['strike']:g}{best['cp']}</b> ينتهي {best['exp']} ({best['dte']} يوم)\n\n"
            f"💵 <b>دخول العقد:</b> ${entry:.2f} ({entry*100:.0f}$ للعقد)\n"
            f"🛑 <b>وقف خسارة:</b> ${stop_o} (-40%)\n"
            f"🎯 <b>هدف 1:</b> ${target_o} (+80%)\n"
            f"🚀 <b>هدف 2:</b> ${target2_o} (+150%)\n"
            f"💰 تعادل ${be:.2f}\n\n"
            f"📊 فوليوم {best['vol']:,} | OI {best['oi']:,}\n"
            f"⭐ قوة {best['score']}/100\n"
            f"🕐 {now_ksa().strftime('%H:%M')} KSA")

def scan_one(ticker, spy):
    try:
        stock, hist = get_price_safe(ticker)
        if hist.empty:
            print(f"{ticker}: No data skip")
            return False
        price = safe_float(hist['Close'].iloc[-1])
        if price==0: return False

        exps = stock.options[:2]
        best = None
        for exp in exps:
            dte = calculate_dte(exp)
            if dte<4 or dte>25: continue
            try:
                chain = stock.option_chain(exp)
                rows = [(r,'C') for _,r in chain.calls.iterrows()] + [(r,'P') for _,r in chain.puts.iterrows()]
                for row, cp in rows:
                    vol = safe_int(row.get('volume')); oi = safe_int(row.get('openInterest'))
                    if vol < MIN_VOLUME or oi < MIN_OI: continue
                    prem = safe_float(row.get('lastPrice'))
                    if prem < MIN_OPTION_PRICE: continue

                    # فلتر سبريد
                    try:
                        bid = safe_float(row.get('bid')); ask = safe_float(row.get('ask'))
                        if bid>0 and ask>0 and prem>0:
                            spr = (ask-bid)/prem*100
                            if spr > MAX_SPREAD_PCT: continue
                    except: pass

                    score = 35 + min(vol/5, 35) + min(oi/10, 20)
                    cid = f"{ticker}_{exp}_{row.get('strike')}_{cp}_{dte}"
                    if cid in sent_contracts: continue

                    if best is None or score > best['score']:
                        best = {'ticker':ticker,'price':price,'strike':safe_float(row.get('strike')),'cp':cp,'exp':exp,'dte':dte,'prem':prem,'vol':vol,'oi':oi,'score':int(min(score,100)),'spy':spy}
            except Exception as ex:
                print(f"{ticker} {exp} err {ex}")
                time.sleep(4)

        if best and best['score'] >= MIN_SCORE:
            if send_tg(format_signal(best)):
                sent_contracts.add(f"{best['ticker']}_{best['exp']}_{best['strike']}_{best['cp']}_{best['dte']}")
                daily_count[ticker]+=1
                print(f"OK {ticker} {best['cp']} {best['strike']} score {best['score']}")
                return True
        return False
    except Exception as ex:
        print(f"{ticker} failed {ex}")
        return False

spy_now = get_spy_trend()
send_tg(f"🚀 <b>البوت V4.9 شغال - 14 شركة</b>\n📋 {', '.join(TICKERS)}\n📈 SPY: {spy_now}\n⚙️ فوليوم {MIN_VOLUME}+ | سكور {MIN_SCORE}+\n✅ يرسل: حوت + دخول + وقف + هدفين\n🕐 {now_ksa().strftime('%H:%M')}")

no_signal = 0
while True:
    try:
        # تصفير يومي
        if now_ksa().date()!= last_date:
            daily_count.clear()
            sent_contracts.clear()
            last_date = now_ksa().date()

        if not is_us_market_open():
            time.sleep(60)
            continue

        spy = get_spy_trend()
        print(f"SCAN {spy} {now_ksa().strftime('%H:%M:%S')} tickers {len(TICKERS)}")
        found = False

        for t in TICKERS:
            if daily_count[t] >= MAX_SIGNALS_PER_TICKER:
                continue
            try:
                if scan_one(t, spy):
                    found = True
                time.sleep(12) # 12 ثانية بين كل شركة عشان لا ينحظر
            except Exception as ex:
                print(f"{t} loop err {ex}")
                time.sleep(5)

        if not found:
            no_signal+=1
            if no_signal >= 6: # كل ساعة
                send_tg(f"ℹ️ <b>فحص مستمر - 14 شركة</b> | SPY: {spy}\nلا يوجد حيتان مطابقة حاليا\nالبوت يفحص كل 10 دقايق ✅")
                no_signal=0
                if len(sent_contracts) > 150:
                    sent_contracts.clear()
        else:
            no_signal=0

        time.sleep(SCAN_INTERVAL)
    except Exception as ex:
        print(f"main loop {ex}")
        time.sleep(30)
