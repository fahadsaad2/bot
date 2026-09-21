from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
import pandas as pd
app = Flask(__name__)
@app.route('/')
def home(): return "V18 IV+RSI+1-10$"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","SPX"]
NAMES = {"^GSPC":"SPX","SPX":"SPX"}
sent = {}
monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{}}
double_sent = {}
message_queue = deque()
rsi_cache = {}

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

def get_rsi(sym):
    try:
        if sym in rsi_cache and time.time()-rsi_cache[sym]['t'] < 300: return rsi_cache[sym]['v']
        tk=yf.Ticker("^GSPC" if sym=="SPX" else sym)
        hist=tk.history(period="14d")
        if len(hist)<14: return 50
        delta=hist['Close'].diff()
        gain=delta.where(delta>0,0).rolling(14).mean()
        loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss
        rsi=100-(100/(1+rs))
        val=float(rsi.iloc[-1])
        rsi_cache[sym]={'v':val,'t':time.time()}
        return val
    except: return 50

def is_buy_signal(row):
    try:
        bid=row.get('bid',0); ask=row.get('ask',0); last=row.get('lastPrice',0)
        if ask>0 and bid>0:
            mid=(bid+ask)/2
            if last >= mid: return True
            else: return False
        return True
    except: return True

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0,row=None):
    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time()-monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]

    combos=[
        (["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM 0DTE"),
        (["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM 500%"),
        (["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),
        (["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO 1000%"),
        (["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA 300%"),
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

        # === الفلاتر الجديدة ===
        # 1- فلتر السعر 1 الى 10$
        if not (1.0 <= ref['price'] <= 10.0): continue

        # 2- فلتر BUY فقط
        if not is_buy_signal(ref.get('row',{})): continue

        # 3- فلتر IV رخيص
        try:
            iv=float(ref['row'].get('impliedVolatility',0))*100
            if iv>0 and iv>130: continue # IV غالي لا تدخل
            iv_txt=f"IV {iv:.0f}% رخيص" if iv>0 else "IV -"
        except: iv=0; iv_txt="IV -"

        # 4- فلتر RSI ذهبي
        rsi=get_rsi(ticker)
        opt_t=ref['type']
        if opt_t=="C" and rsi>65: continue # CALL والسهم متشبع فوق لا تدخل
        if opt_t=="P" and rsi<35: continue # PUT والسهم بالقاع لا تدخل
        rsi_txt=f"RSI {rsi:.0f} دخول ذهبي"

        double_sent[base_key]=time.time()
        entry=ref['price']
        t1=entry*1.5; t2=entry*2.0; t3=entry*3.0; stop=entry*0.7
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)

        queue_send(f"🚨 <b>TOP5 مضمون 1-10$ + IV + RSI</b> 🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n\n✅ {iv_txt}\n✅ {rsi_txt}\n✅ 🟢 BUY TO OPEN\n✅ سعر ${entry:.2f} بين 1-10$\n\n💵 دخول: ${entry:.2f}\n🎯 هدف1: ${t1:.2f} (+50%)\n🎯 هدف2: ${t2:.2f} (+100%)\n🎯 هدف3: ${t3:.2f} (+200%)\n🛑 وقف: ${stop:.2f}\n💰 مجمع ${total:,.0f}k")

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
                                for _,r in chain.iterrows():
                                    vol=int(r['volume']) if r['volume'] else 0
                                    oi=int(r['openInterest']) if r['openInterest'] else 0
                                    price=float(r['lastPrice']) if r['lastPrice'] else 0
                                    # فلتر السعر الأساسي 1-10$
                                    if not (1.0 <= price <= 10.0): continue
                                    if vol < 50: continue
                                    prem_vol=float(vol*price*100); prem_oi=float(oi*price*100) if oi>0 else prem_vol
                                    if prem_vol > 2000000 and vol>300:
                                        check_double_monster(dname,"ULTRA",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    elif prem_vol > 800000 and vol>300:
                                        check_double_monster(dname,"MEGA",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    elif prem_oi>150000 and vol>200:
                                        check_double_monster(dname,"GOLDEN",prem_oi/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_oi,r)
                                    if vol / max(oi,1) > 3.0 and vol>500 and price>=1.0:
                                        check_double_monster(dname,"MOMENTUM",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    if is_0dte and 1.0 <= price <= 10.0 and vol>50:
                                        check_double_monster(dname,"HERO",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                        except: continue
                except: continue
            time.sleep(30)
        except Exception as e:
            print(f"ERR {e}"); time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker,daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":"✅ <b>V18 شغال</b>\n💵 1$-10$ فقط\n✅ IV <130%\n✅ RSI ذهبي\n🟢 BUY فقط","parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/test" in txt: queue_send("🧪 V18 1-10$ + IV + RSI ✅")
                if "/status" in txt: queue_send(f"✅ LIVE طابور {len(message_queue)}")
                if "/clear" in txt: sent.clear(); double_sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
