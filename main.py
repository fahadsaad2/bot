from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V16.6 TOP5 + ENTRY/EXIT"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","SPX"]
NAMES = {"^GSPC":"SPX","SPX":"SPX"}
sent = {}
monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{}}
double_sent = {}
message_queue = deque()

def send_worker():
    while True:
        if message_queue:
            t = message_queue.popleft()
            try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"}, timeout=15)
            except: pass
            time.sleep(1.2)
        else: time.sleep(0.2)

def queue_send(t):
    if len(message_queue) < 200: message_queue.append(t)

def is_new(key,h=1):
    if key not in sent or time.time()-sent[key] > h*3600:
        sent[key]=time.time()
        return True
    return False

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0):
    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time()-monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]

    combos=[
        (["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM 0DTE تدبيلة فورية"),
        (["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM أسبوعي خارق 500%"),
        (["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM أسبوعي انفجار"),
        (["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO 0DTE 1000%"),
        (["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA أسبوعي 300% مضمون"),
    ]

    for combo,desc in combos:
        if typ not in combo: continue
        if not all(ticker in monster_memory[c] for c in combo): continue
        times=[monster_memory[c][ticker]["time"] for c in combo]
        if max(times)-min(times) > 3600: continue
        ref=monster_memory[combo[0]][ticker]
        for c in combo:
            if monster_memory[c][ticker]["premium"] > ref["premium"]: ref=monster_memory[c][ticker]
        base_key=f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(time.time()/600)}"
        if base_key in double_sent: continue
        double_sent[base_key]=time.time()

        # حساب الدخول والخروج
        entry=ref['price']
        t1=entry*1.5
        t2=entry*2.0
        t3=entry*3.0
        stop=entry*0.7
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)
        is_0dte = "0DTE" in desc

        queue_send(f"🚨🚨🚨 <b>الوحش المزدوج TOP5</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n\n💵 <b>دخول:</b> ${entry:.2f}\n🎯 هدف1: ${t1:.2f} (+50% بيع نص)\n🎯 هدف2: ${t2:.2f} (+100% تدبيلة)\n🎯 هدف3: ${t3:.2f} (+200%)\n🛑 وقف: ${stop:.2f} (-30%)\n\n💰 مجمع ${total:,.0f}k\n{'⚡ 0DTE سكالب سريع' if is_0dte else '📅 أسبوعي تقدر تنام فيه'}")

def sniper_loop():
    et_tz=pytz.timezone('US/Eastern')
    while True:
        try:
            today_et=datetime.now(et_tz).date()
            for sym in TICKERS:
                try:
                    ysym="^GSPC" if sym=="SPX" else sym
                    tk=yf.Ticker(ysym)
                    if not tk.options: continue
                    dname=NAMES.get(sym,sym)
                    for exp in tk.options[:3]:
                        try:
                            ed=datetime.strptime(exp,"%Y-%m-%d").date()
                            is_0dte = (ed==today_et)
                            is_weekly = 1 <= (ed-today_et).days <= 4
                            if not (is_0dte or is_weekly): continue
                            for otype in ["calls","puts"]:
                                try: chain=getattr(tk.option_chain(exp),otype)
                                except: continue
                                if chain is None or chain.empty: continue
                                chain=chain.fillna(0)
                                chain=chain[(chain['openInterest']>=5) & (chain['lastPrice']>=0.05)]
                                if chain.empty: continue
                                otype_s="C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol=int(r['volume']) if r['volume'] else 0
                                    oi=int(r['openInterest']) if r['openInterest'] else 0
                                    price=float(r['lastPrice']) if r['lastPrice'] else 0
                                    if vol < 50: continue
                                    prem_vol=float(vol*price*100)
                                    prem_oi=float(oi*price*100) if oi>0 else prem_vol
                                    if prem_vol > 2000000 and vol>300:
                                        monster_memory["ULTRA"][dname]={"time":time.time(),"vol":prem_vol/1000,"strike":float(r['strike']),"exp":exp,"price":price,"type":otype_s,"premium":prem_vol}
                                        check_double_monster(dname,"ULTRA",prem_vol/1000,float(r['strike']),exp,price,otype_s,prem_vol)
                                    elif prem_vol > 800000 and vol>300:
                                        monster_memory["MEGA"][dname]={"time":time.time(),"vol":prem_vol/1000,"strike":float(r['strike']),"exp":exp,"price":price,"type":otype_s,"premium":prem_vol}
                                        check_double_monster(dname,"MEGA",prem_vol/1000,float(r['strike']),exp,price,otype_s,prem_vol)
                                    elif prem_oi>150000 and vol>200:
                                        monster_memory["GOLDEN"][dname]={"time":time.time(),"vol":prem_oi/1000,"strike":float(r['strike']),"exp":exp,"price":price,"type":otype_s,"premium":prem_oi}
                                        check_double_monster(dname,"GOLDEN",prem_oi/1000,float(r['strike']),exp,price,otype_s,prem_oi)
                                    if vol / max(oi,1) > 3.0 and vol>500 and price>=0.5:
                                        monster_memory["MOMENTUM"][dname]={"time":time.time(),"vol":prem_vol/1000,"strike":float(r['strike']),"exp":exp,"price":price,"type":otype_s,"premium":prem_vol}
                                        check_double_monster(dname,"MOMENTUM",prem_vol/1000,float(r['strike']),exp,price,otype_s,prem_vol)
                                    if is_0dte and 0.10 <= price <= 6.0 and vol>50:
                                        monster_memory["HERO"][dname]={"time":time.time(),"vol":prem_vol/1000,"strike":float(r['strike']),"exp":exp,"price":price,"type":otype_s,"premium":prem_vol}
                                        check_double_monster(dname,"HERO",prem_vol/1000,float(r['strike']),exp,price,otype_s,prem_vol)
                        except: continue
                except: continue
            time.sleep(30)
        except Exception as e:
            print(f"ERR {e}"); time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker,daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":"✅ <b>V16.6 TOP5 + دخول/خروج شغال</b>\n💵 دخول + 3 أهداف + وقف\n📊 20 شركة - كذا وكذا 0DTE + أسبوعي","parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/test" in txt: queue_send("🧪 V16.6 شغال ✅ دخول/خروج")
                if "/status" in txt: queue_send(f"✅ LIVE طابور {len(message_queue)}")
                if "/clear" in txt: sent.clear(); double_sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
