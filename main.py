from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V15 ULTRA FIXED"
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","SPX"]
NAMES = {"^GSPC":"SPX", "SPX":"SPX"}
sent = {}
seen_tx = set()
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"
message_queue = deque()
def send_worker():
    while True:
        if message_queue:
            t = message_queue.popleft()
            try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
            except: pass
            time.sleep(1.2)
        else: time.sleep(0.2)
def queue_send(t):
    if len(message_queue) < 200: message_queue.append(t)
def is_new(key, h=1):
    if key not in sent or time.time() - sent[key] > h*3600:
        sent[key] = time.time()
        return True
    return False
def sniper_loop():
    et_tz = pytz.timezone('US/Eastern')
    while True:
        try:
            today_et = datetime.now(et_tz).date()
            all_found = []
            scanned = 0
            for sym in TICKERS:
                try:
                    ysym = "^GSPC" if sym == "SPX" else sym
                    tk = yf.Ticker(ysym)
                    if not tk.options: continue
                    dname = NAMES.get(sym,sym)
                    for exp in tk.options[:2]:
                        try:
                            ed = datetime.strptime(exp,"%Y-%m-%d").date()
                            for otype in ["calls","puts"]:
                                try: chain = getattr(tk.option_chain(exp), otype)
                                except: continue
                                if chain is None or chain.empty: continue
                                chain = chain.fillna(0)
                                chain = chain[(chain['openInterest']>=5) & (chain['lastPrice']>=0.05)]
                                if chain.empty: continue
                                otype_s = "C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol = int(r['volume']) if r['volume'] else 0
                                    oi = int(r['openInterest']) if r['openInterest'] else 0
                                    price = float(r['lastPrice']) if r['lastPrice'] else 0
                                    scanned+=1
                                    if vol < 50: continue
                                    prem = float(oi*price*100) if oi>0 else float(vol*price*100)
                                    if prem>150000 and vol>200:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"👑 GOLDEN {dname} {r['strike']:.0f}{otype_s} {exp} ${price:.2f} ${prem:,.0f} Vol{vol}"))
                                    if ed==today_et and 0.10 <= price <= 6.0 and vol>50:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"🚀 HERO {dname} {r['strike']:.0f}{otype_s} {exp} 0DTE ${price:.2f}"))
                        except: continue
                except: continue
            if not all_found:
                if is_new("HEARTBEAT", h=0.25):
                    queue_send(f"💓 البوت شغال - فحص {scanned} عقد - السوق هادي")
            all_found = sorted(all_found, key=lambda x: x[0], reverse=True)[:25]
            for item in all_found:
                queue_send(item[1])
            time.sleep(30)
        except: time.sleep(10)
def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker, daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":"✅ V15 ULTRA FIXED - يرسل الان /test", "parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/test" in txt: queue_send("🧪 تيست - البوت شغال 100% ✅")
                if "/status" in txt: queue_send(f"✅ LIVE طابور {len(message_queue)}")
                if "/clear" in txt: sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
        except: time.sleep(3)
threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
