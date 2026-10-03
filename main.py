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

print(f"ENV OK BOT={bool(TELEGRAM_BOT_TOKEN)} CHAT={bool(TELEGRAM_CHAT_ID)} FINN={bool(FINNHUB_API_KEY)}", flush=True)

SYMBOLS = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]
RES_CACHE = {}
sent = set()

def send(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=15)
        print(f"SEND {r.status_code} {r.text[:100]}", flush=True)
    except Exception as e:
        print(f"SEND ERR {e}", flush=True)

def update_resistances():
    print("تحديث المقاومات...", flush=True)
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period="1mo")
            if not hist.empty and len(hist) >= 20:
                RES_CACHE[sym] = float(max(hist['High'].tail(20)))
                print(f"{sym} -> {RES_CACHE[sym]}", flush=True)
            time.sleep(0.3)
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
    except Exception as e:
        print(f"OPT ERR {sym} {e}", flush=True)
        return None

def loop():
    update_resistances()
    send("✅ البوت اشتغل - يفحص 19 شركة حقتك")
    print("LOOP STARTED", flush=True)
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
                    entry, stop = res, round(res*0.985,2)
                    t1,t2,t3 = round(entry*1.02,2), round(entry*1.04,2), round(entry*1.06,2)
                    msg = f"🟢 <b>اختراق لحظي - {s}</b>\n💰 {p:.2f}$ | مقاومة {res:.2f}$\n\n🎯 {entry:.2f}$ 🛑 {stop:.2f}$ ✅ {t1}$ | {t2}$ | {t3}$\n"
                    if d: msg += f"\n🔥 يومي {d['exp']} سترايك {d['strike']}$ سعر {d['last']}$ فوليوم {d['vol']:,} OI {d['oi']:,}"
                    if w: msg += f"\n🛡️ اسبوعي {w['exp']} سترايك {w['strike']}$ سعر {w['last']}$ فوليوم {w['vol']:,} OI {w['oi']:,}"
                    send(msg)
                    sent.add(key)
                time.sleep(1)
            except Exception as e:
                print(f"LOOP ERR {s}: {e}", flush=True)
        time.sleep(15)

Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
