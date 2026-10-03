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

print(f"ENV OK BOT={bool(TELEGRAM_BOT_TOKEN)} CHAT={bool(TELEGRAM_CHAT_ID)}", flush=True)

# نفس 19 شركة حقتك بالضبط ما غيرتها
SYMBOLS = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]

AR_NAMES = {"NVDA":"انفيديا","TSLA":"تسلا","SMCI":"سوبر مايكرو","MSTR":"مايكروستراتيجي","COIN":"كوين بيس","AAPL":"أبل","GOOGL":"جوجل","META":"ميتا","AMD":"ايه ام دي","AMZN":"امازون","MSFT":"مايكروسوفت","PLTR":"بلانتر","APP":"آب لوفين","ARM":"ارم","AVGO":"برودكوم","MU":"مايكرون","LITE":"لايت","SNDK":"سانديسك","RDDT":"ريديت"}

sent = set()
RES_CACHE = {}

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"SEND ERR {e}", flush=True)

def update_resistances():
    for sym in SYMBOLS:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1mo")
            if not hist.empty and len(hist) >= 20:
                RES_CACHE[sym] = max(hist['High'].tail(20))
            time.sleep(0.5)
        except Exception as e:
            print(f"RES ERR {sym}: {e}", flush=True)

def get_opt(sym, price, mode):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        exp = exps[0] if mode=="daily" else exps[min(2, len(exps)-1)]
        chain = t.option_chain(exp).calls
        chain = chain[(chain['lastPrice'] <= 10.0) & (chain['lastPrice'] >= 0.20)]
        filt = chain[(chain['strike'] >= price*0.98) & (chain['strike'] <= price*1.10)]
        if filt.empty: filt = chain
        if filt.empty: return None
        best = filt.sort_values('volume', ascending=False).iloc[0]
        return {"strike": best['strike'], "last": best['lastPrice'], "vol": int(best['volume'] or 0), "oi": int(best['openInterest'] or 0), "exp": exp}
    except: return None

def loop():
    update_resistances()
    send("✅ البوت اللحظي اشتغل - يتابع 19 سهم - يومي واسبوعي بالعربي")
    last_res_update = time.time()

    while True:
        if time.time() - last_res_update > 43200:
            update_resistances()
            last_res_update = time.time()
            sent.clear()

        today_str = datetime.now().strftime("%Y-%m-%d")
        for s in SYMBOLS:
            try:
                res = RES_CACHE.get(s)
                if not res: continue
                q = finnhub_client.quote(s)
                p = float(q.get('c', 0))
                if p == 0: continue

                key = f"{s}_{today_str}"
                if p >= res * 0.995 and key not in sent:
                    d = get_opt(s, p, "daily")
                    w = get_opt(s, p, "weekly")
                    if not d and not w: continue

                    entry = res
                    stop = round(entry * 0.985, 2)
                    t1, t2, t3 = round(entry * 1.02, 2), round(entry * 1.04, 2), round(entry * 1.06, 2)
                    name_ar = AR_NAMES.get(s, s)

                    msg = f"🟢 <b>اختراق لحظي - {name_ar} ({s})</b>\n"
                    msg += f"💰 السعر الحالي: {p:.2f}$\n"
                    msg += f"📊 مقاومة: {res:.2f}$\n\n"
                    msg += f"🚀 دخول: فوق {entry:.2f}$\n"
                    msg += f"🛑 وقف: {stop:.2f}$\n"
                    msg += f"🎯 أهداف: {t1}$ | {t2}$ | {t3}$\n"

                    if d:
                        msg += f"\n🔥 <b>عقد يومي - {d['exp']}</b>\n"
                        msg += f"💰 سترايك: {d['strike']}$ CALL\n"
                        msg += f"📅 انتهاء: {d['exp']}\n"
                        msg += f"💵 سعر العقد: {d['last']}$\n"
                        msg += f"📊 فوليوم: {d['vol']:,} | OI: {d['oi']:,}\n"
                    if w:
                        msg += f"\n🛡️ <b>عقد اسبوعي - {w['exp']}</b>\n"
                        msg += f"💰 سترايك: {w['strike']}$ CALL\n"
                        msg += f"📅 انتهاء: {w['exp']}\n"
                        msg += f"💵 سعر العقد: {w['last']}$\n"
                        msg += f"📊 فوليوم: {w['vol']:,} | OI: {w['oi']:,}\n"

                    send(msg)
                    sent.add(key)
                time.sleep(1)
            except Exception as e:
                print(f"LOOP ERR {s}: {e}", flush=True)
        time.sleep(15)

Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
