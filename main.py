from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)
@app.route("/")
def home():
    return "V28 WAVE + 6 DOUBLES"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TICKERS = ["^GSPC","QQQ","IWM","DIA","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","PLTR"]
NAMES = {"^GSPC":"SPX"}
INDEX_WAVE = ["SPX","QQQ","IWM","DIA"] # الموجات بس على هذول

monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{},"EXPLOSIVE":{}}
double_sent = {}
single_sent = set()
message_queue = deque()
rsi_cache = {}
wave_cache = {}
sent_today = set()
last_reset_day = datetime.now().day

KSA = pytz.timezone("Asia/Riyadh")
ET = pytz.timezone("US/Eastern")

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
        if sym in rsi_cache and time.time()-rsi_cache[sym]["t"] < 300: return rsi_cache[sym]["v"]
        hist=yf.Ticker(sym).history(period="14d")
        if len(hist)<14: return 50
        delta=hist["Close"].diff()
        gain=delta.where(delta>0,0).rolling(14).mean()
        loss=-delta.where(delta<0,0).rolling(14).mean()
        val=float(100-(100/(1+gain/loss)).iloc[-1])
        rsi_cache[sym]={"v":val,"t":time.time()}
        return val
    except: return 50

# === فلتر الموجات الجديد ===
def get_wave_state(sym):
    try:
        cache_key = sym
        if cache_key in wave_cache and time.time()-wave_cache[cache_key]["t"] < 180:
            return wave_cache[cache_key]["v"]
        # ^GSPC للـ SPX
        yf_sym = "^GSPC" if sym=="SPX" else sym
        hist=yf.Ticker(yf_sym).history(period="5d", interval="5m")
        if len(hist)<100: return {"allow":True,"wave":"مومنتوم","fib":0}
        high=hist["High"][-60:].max()
        low=hist["Low"][-60:].min()
        last=hist["Close"].iloc[-1]
        swing=high-low
        if swing==0: return {"allow":True,"wave":"?","fib":0}
        drop_pct=(high-last)/swing*100
        # موجة 4 = تصحيح 23-50% من القمة
        if 23 <= drop_pct <= 50:
            res={"allow":True,"wave":f"موجة 4->{drop_pct:.0f}% جاهز","fib":drop_pct}
        elif drop_pct < 23:
            res={"allow":False,"wave":f"قمة موجة 3 ({drop_pct:.0f}%) انتظار","fib":drop_pct}
        else:
            res={"allow":True,"wave":f"تصحيح {drop_pct:.0f}%","fib":drop_pct}
        wave_cache[cache_key]={"v":res,"t":time.time()}
        return res
    except:
        return {"allow":True,"wave":"?","fib":0}

def get_above_ask(row):
    try:
        ask=float(row.get("ask",0)); last=float(row.get("lastPrice",0))
        return 90 if ask>0 and last>=ask else 0
    except: return 0

def check_double_monster(ticker,typ,vol_k,strike,exp,price,opt_type,premium,row, above_ask=0):
    global last_reset_day
    if datetime.now().day!= last_reset_day:
        sent_today.clear(); double_sent.clear(); single_sent.clear()
        last_reset_day=datetime.now().day

    # === فلتر الموجات - بس للمؤشرات ===
    wave_text=""
    if ticker in INDEX_WAVE:
        wave=get_wave_state(ticker)
        if not wave["allow"]:
            return # في قمة موجة 3 لا ترسل
        wave_text=f"\n🌊 {wave['wave']}"

    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row,"above_ask":above_ask}
    if above_ask<80: return

    # === الـ 6 المزدوجة كاملة بالترتيب اللي تبيه ===
    combos=[
        (["GOLDEN","ULTRA"],"🏆 GOLDEN+ULTRA"),
        (["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM"),
        (["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM"),
        (["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),
        (["EXPLOSIVE","MOMENTUM"],"💥 EXPLOSIVE+MOMENTUM"),
        (["GOLDEN","HERO"],"⚡ GOLDEN+HERO")
    ]
    for combo,desc in combos:
        if typ not in combo: continue
        if not all(ticker in monster_memory[c] for c in combo): continue
        if max([monster_memory[c][ticker]["time"] for c in combo]) - min([monster_memory[c][ticker]["time"] for c in combo]) > 600: continue
        ref=monster_memory[combo[0]][ticker]
        for c in combo:
            if monster_memory[c][ticker]["premium"]>ref["premium"]: ref=monster_memory[c][ticker]
        contract_key=f"{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}"
        if contract_key in sent_today: continue
        base_key=f"DOUBLE_{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}_{'_'.join(combo)}"
        if base_key in double_sent: continue
        if not (0.25 <= ref["price"] <= 500.0): continue
        try:
            iv=float(ref["row"].get("impliedVolatility",0))*100
            if not (10 <= iv <= 120): continue
        except: continue
        rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
        if ref["type"]=="C" and rsi>55: continue
        if ref["type"]=="P" and rsi<65: continue
        double_sent[base_key]=time.time()
        sent_today.add(contract_key)
        entry=ref["price"]
        t1,t2,t3,stop = (entry*1.3, entry*1.6, entry*2.2, entry*0.7) if entry>10 else (entry*1.5, entry*2.0, entry*3.0, entry*0.7)
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)
        now_ksa=datetime.now(KSA)
        display_strike = ref['strike']
        if ticker=="SPX" and display_strike < 2000:
            display_strike = display_strike*10
        exp_occ = datetime.strptime(ref['exp'],"%Y-%m-%d").strftime("%y%m%d")
        strike_occ = int(display_strike*1000)
        occ_symbol = f"SPXW {exp_occ}{ref['type']}{strike_occ:08d}" if ticker=="SPX" else f"{ticker} {exp_occ}{ref['type']}{strike_occ:08d}"

        queue_send(f"🚨 دخول حوت مزدوج 🚨\n\n🎯 {ticker} - {desc}{wave_text}\n💥 {display_strike:g}{ref['type']} - {ref['exp']}\n📋 عقد: <code>{occ_symbol}</code>\n✅ IV {iv:.0f}% | RSI {rsi:.0f} | فوق Ask {ref['above_ask']}% ✅\n💵 دخول: ${entry:.2f}\n🎯1: ${t1:.2f} 🎯2: ${t2:.2f} 🎯3: ${t3:.2f}\n🛑 وقف: ${stop:.2f}\n💰 ${total:,.0f}k\n⏰ {now_ksa.strftime('%H:%M:%S KSA')}")

def sniper_loop():
    while True:
        try:
            today_et=datetime.now(ET).date()
            for sym in TICKERS:
                try:
                    tk=yf.Ticker(sym)
                    if not tk.options: continue
                    dname=NAMES.get(sym,sym)
                    for exp in tk.options[:2]:
                        ed=datetime.strptime(exp,"%Y-%m-%d").date()
                        if not (0 <= (ed-today_et).days <= 5): continue
                        is_0dte=(ed==today_et)
                        for otype in ["calls","puts"]:
                            try: chain=getattr(tk.option_chain(exp),otype)
                            except: continue
                            if chain is None or chain.empty: continue
                            chain=chain.fillna(0)
                            for _,r in chain.iterrows():
                                vol=int(r["volume"]) if r["volume"] else 0
                                price=float(r["lastPrice"]) if r["lastPrice"] else 0
                                if price<=0 or vol<20: continue
                                oi=int(r["openInterest"]) if r["openInterest"] else 0
                                prem_vol=float(vol*price*100)
                                prem_oi=float(oi*price*100) if oi>0 else prem_vol
                                opt_char="C" if otype=="calls" else "P"
                                above_ask=get_above_ask(r)
                                if prem_vol>1500000 and vol>200: check_double_monster(dname,"ULTRA",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,above_ask)
                                if prem_vol>500000 and vol>150: check_double_monster(dname,"MEGA",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,above_ask)
                                if prem_oi>100000 and vol>100: check_double_monster(dname,"GOLDEN",prem_oi/1000,float(r["strike"]),exp,price,opt_char,prem_oi,r,above_ask)
                                if vol/max(oi,1)>2.5 and vol>200: check_double_monster(dname,"MOMENTUM",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,above_ask)
                                if is_0dte and vol>30: check_double_monster(dname,"HERO",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,above_ask)
                                if price<=1.50 and vol>=max(oi*2.5,150) and above_ask>=80: check_double_monster(dname,"EXPLOSIVE",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,above_ask)
                except: continue
            time.sleep(45)
        except: time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker,daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":"🏆 <b>V28 WAVE + 6 DOUBLES شغال</b>","parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    while True:
        try:
            res=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=30",timeout=35).json()
            for u in res.get("result",[]):
                off=u["update_id"]
                msg=u.get("message",{})
                if str(msg.get("chat",{}).get("id"))!=str(CHAT_ID): continue
                txt=msg.get("text","").lower()
                if "/test" in txt: queue_send("🏆 V28 WAVE ✅ شغال - 6 مزدوجة + موجات")
                if "/status" in txt:
                    w=get_wave_state("SPX")
                    queue_send(f"✅ ارسل اليوم {len(sent_today)} | RSI {get_rsi('^GSPC'):.0f} | موجة {w['wave']}")
                if "/wave" in txt:
                    w=get_wave_state("SPX")
                    queue_send(f"🌊 SPX: {w['wave']}")
                if "/clear" in txt: double_sent.clear(); sent_today.clear(); single_sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
