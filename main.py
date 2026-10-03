from flask import Flask
from threading import Thread
import os, time, requests, finnhub, yfinance as yf
from datetime import datetime
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home(): return "البوت شغال"
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
        now = int(time.time())
        frm = now - 60*60*24*30
        c = finnhub_client.stock_candles(sym, 'D', frm, now)
        if c['s']!='ok': return None,None,None
        return c['c'][-1], max(c['h'][-20:]), min(c['l'][-20:])
    except: return None,None,None

def get_opt(sym, price, mode):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        exp = exps[0] if mode=="daily" else exps[min(2, len(exps)-1)]
        chain = t.option_chain(exp).calls
        filt = chain[(chain['strike']>=price*0.98) & (chain['strike']<=price*1.08)]
        if filt.empty: filt = chain
        best = filt.sort_values('volume', ascending=False).iloc[0]
        return {"strike":best['strike'],"last":best['lastPrice'],"vol":int(best['volume'] or 0),"oi":int(best['openInterest'] or 0),"exp":exp}
    except: return None

def loop():
    while True:
        for s in SYMBOLS:
            p,r,su = get_levels(s)
            if not p: continue
            key = f"{s}_{datetime.now().date()}"
            if p >= r*0.995 and key not in sent:
                d = get_opt(s,p,"daily")
                w = get_opt(s,p,"weekly")

                msg = f"🟢 *اختراق مقاومة*\n"
                msg += f"🏢 الشركة: {s}\n"
                msg += f"💰 السعر الحالي: {p:.2f}$\n"
                msg += f"📊 المقاومة: {r:.2f}$\n"
                msg += f"⏰ الوقت: {datetime.now().strftime('%H:%M')}\n"

                if d:
                    msg += f"\n━━━━━━━━━━━━━━\n"
                    msg += f"🔥 *عقد يومي 0DTE*\n"
                    msg += f"🎯 سترايك: {d['strike']}$\n"
                    msg += f"💵 سعر العقد: {d['last']}$\n"
                    msg += f"📈 الفوليوم: {d['vol']:,}\n"
                    msg += f"📊 الـ OI: {d['oi']:,}\n"
                    msg += f"📅 الانتهاء: {d['exp']}\n"

                if w:
                    msg += f"\n━━━━━━━━━━━━━━\n"
                    msg += f"🛡️ *عقد اسبوعي*\n"
                    msg += f"🎯 سترايك: {w['strike']}$\n"
                    msg += f"💵 سعر العقد: {w['last']}$\n"
                    msg += f"📈 الفوليوم: {w['vol']:,}\n"
                    msg += f"📊 الـ OI: {w['oi']:,}\n"
                    msg += f"📅 الانتهاء: {w['exp']}\n"

                send(msg)
                sent.add(key)
            time.sleep(4)
        time.sleep(60)

send("✅ *البوت اشتغل*\nعربي - يومي واسبوعي - فوليوم و OI\nالشركات: NVDA TSLA SMCI MSTR COIN AAPL GOOGL META AMD AMZN MSFT PLTR APP ARM AVGO MU LITE SNDK RDDT")
Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
