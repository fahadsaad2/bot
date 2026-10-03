from flask import Flask
from threading import Thread
import os, time, requests, finnhub, yfinance as yf
from datetime import datetime

app = Flask(__name__)
@app.route('/')
def home(): return "البوت شغال - لحظي"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

SYMBOLS = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]
sent = set()

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def get_levels(sym):
    try:
        # سعر لحظي بالثانية من Finnhub
        q = finnhub_client.quote(sym)
        price = float(q['c'])
        if price == 0: return None,None,None,None
        
        # مقاومة من الشموع
        now = int(time.time())
        frm = now - 60*60*24*30
        c = finnhub_client.stock_candles(sym, 'D', frm, now)
        if c['s']!='ok': return None,None,None,None
        res = max(c['h'][-20:])
        
        entry = res
        stop = round(price * 0.985, 2)
        t1 = round(price * 1.02, 2)
        t2 = round(price * 1.04, 2)
        t3 = round(price * 1.06, 2)
        return price,res,None,(entry,stop,t1,t2,t3)
    except: return None,None,None,None

def get_opt(sym, price, mode):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        exp = exps[0] if mode=="daily" else exps[min(2, len(exps)-1)]
        chain = t.option_chain(exp).calls
        chain = chain[chain['lastPrice'] <= 10.0]
        chain = chain[chain['lastPrice'] >= 0.20]
        filt = chain[(chain['strike']>=price*0.98) & (chain['strike']<=price*1.10)]
        if filt.empty: filt = chain
        if filt.empty: return None
        best = filt.sort_values('volume', ascending=False).iloc[0]
        return {"strike":best['strike'],"last":best['lastPrice'],"vol":int(best['volume'] or 0),"oi":int(best['openInterest'] or 0),"exp":exp}
    except: return None

def loop():
    while True:
        for s in SYMBOLS:
            p,r,_,levels = get_levels(s)
            if not p: continue
            key = f"{s}_{datetime.now().date()}"
            if p >= r*0.995 and key not in sent:
                entry,stop,t1,t2,t3 = levels
                d = get_opt(s,p,"daily")
                w = get_opt(s,p,"weekly")
                if not d and not w: continue

                msg = f"🟢 *اختراق لحظي - {s}*\n"
                msg += f"💰 السعر اللحظي: {p:.2f}$\n"
                msg += f"📊 مقاومة: {r:.2f}$\n\n"
                msg += f"🎯 دخول: {entry:.2f}$\n"
                msg += f"🛑 وقف: {stop:.2f}$\n"
                msg += f"✅ هدف1: {t1:.2f}$ | هدف2: {t2:.2f}$ | هدف3: {t3:.2f}$\n"

                if d:
                    msg += f"\n🔥 *يومي* {d['exp']}\nسترايك {d['strike']}$ | سعر {d['last']}$\nفوليوم {d['vol']:,} | OI {d['oi']:,}\n"
                if w:
                    msg += f"\n🛡️ *اسبوعي* {w['exp']}\nسترايك {w['strike']}$ | سعر {w['last']}$\nفوليوم {w['vol']:,} | OI {w['oi']:,}\n"

                send(msg)
                sent.add(key)
            time.sleep(3)
        time.sleep(30) # يفحص كل 30 ثانية لحظي

send("✅ البوت اشتغل - لحظي بالثانية + عقد <10$")
Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
