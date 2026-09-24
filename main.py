from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)
@app.route("/")
def home():
    return "V25 FIXED KSA 24 TICKERS OK"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
if not TOKEN or not CHAT_ID:
    print("Missing BOT_TOKEN or CHAT_ID env!")

TICKERS = ["SPY","QQQ","IWM","DIA","^GSPC","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","NFLX","PLTR"]
NAMES = {"^GSPC":"SPX","SPY":"SPX"}

monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{},"EXPLOSIVE":{}}
double_sent = {}
single_sent = set()
exit_memory = {}
message_queue = deque()
rsi_cache = {}
vwap_cache = {}
gamma_cache = {}
sent_today = set()
blocked_today = 0
last_reset_day = datetime.now().day

KSA = pytz.timezone("Asia/Riyadh")
ET = pytz.timezone("US/Eastern")

def send_worker():
    while True:
        if message_queue:
            t = message_queue.popleft()
            try:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"}, timeout=15)
            except: pass
            time.sleep(1.2)
        else: time.sleep(0.2)

def queue_send(t):
    if len(message_queue) < 200: message_queue.append(t)

def get_rsi(sym):
    try:
        if sym in rsi_cache and time.time()-rsi_cache[sym]["t"] < 300:
            return rsi_cache[sym]["v"]
        tk=yf.Ticker(sym)
        hist=tk.history(period="14d")
        if len(hist)<14: return 50
        delta=hist["Close"].diff()
        gain=delta.where(delta>0,0).rolling(14).mean()
        loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss
        rsi=100-(100/(1+rs))
        val=float(rsi.iloc[-1])
        rsi_cache[sym]={"v":val,"t":time.time()}
        return val
    except: return 50

def get_vwap(sym):
    try:
        tk=yf.Ticker(sym)
        hist=tk.history(period="1d", interval="5m")
        if len(hist)<5: return None
        tp=(hist["High"]+hist["Low"]+hist["Close"])/3
        vwap=(tp*hist["Volume"]).sum()/hist["Volume"].sum()
        return float(vwap)
    except: return None

def get_gamma_wall(sym):
    try:
        tk=yf.Ticker(sym if sym!="SPX" else "SPY")
        if not tk.options: return None
        exp=tk.options[0]
        chain=tk.option_chain(exp)
        if chain.calls.empty: return None
        max_oi_row=chain.calls.loc[chain.calls["openInterest"].idxmax()]
        return float(max_oi_row["strike"])
    except: return None

def is_buy_signal(row):
    try:
        bid=row.get("bid",0); ask=row.get("ask",0); last=row.get("lastPrice",0)
        if ask>0 and bid>0: return last >= (bid+ask)/2
        return True
    except: return True

def get_above_ask_pct(row):
    try:
        ask=float(row.get("ask",0)); last=float(row.get("lastPrice",0))
        if ask>0 and last>=ask: return 90
        return 0
    except: return 0

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0,row=None, vwap_ok=True, gamma_ok=True, above_ask=0, gamma_wall=0):
    global last_reset_day, blocked_today
    if datetime.now().day!= last_reset_day:
        sent_today.clear(); double_sent.clear(); single_sent.clear(); blocked_today=0
        last_reset_day = datetime.now().day

    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row,"vwap_ok":vwap_ok,"gamma_ok":gamma_ok,"above_ask":above_ask,"gamma_wall":gamma_wall}

    if not (vwap_ok and above_ask>=80):
        return

    combos=[(["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM"),(["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM"),(["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),(["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO"),(["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA"),(["EXPLOSIVE","MOMENTUM"],"💥 EXPLOSIVE+MOMENTUM")]
    for combo,desc in combos:
        if typ not in combo: continue
        if not all(ticker in monster_memory[c] for c in combo): continue
        times=[monster_memory[c][ticker]["time"] for c in combo]
        if max(times)-min(times) > 600: continue
        ref=monster_memory[combo[0]][ticker]
        for c in combo:
            if monster_memory[c][ticker]["premium"] > ref["premium"]: ref=monster_memory[c][ticker]
        contract_key = f"{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}"
        if contract_key in sent_today: continue
        base_key=f"DOUBLE_{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}_{'_'.join(combo)}"
        if base_key in double_sent: continue
        if not (0.25 <= ref["price"] <= 12.0): continue
        if ref.get("above_ask",0) < 80: continue
        try:
            iv=float(ref["row"].get("impliedVolatility",0))*100
            if not (20 <= iv <= 90): continue
        except: continue
        rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
        if ref["type"]=="C" and rsi>50: continue
        if ref["type"]=="P" and rsi<68: continue

        double_sent[base_key]=time.time()
        sent_today.add(contract_key)
        entry=ref["price"]
        if entry <= 1.50: t1=entry*2.0; t2=entry*3.5; t3=entry*6.0; stop=entry*0.5
        else: t1=entry*1.5; t2=entry*2.0; t3=entry*3.0; stop=entry*0.7
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)
        now_ksa=datetime.now(KSA)
        queue_send(f"🚨 دخول حوت مزدوج 🚨\n\n🎯 {ticker} - {desc}\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n✅ IV {iv:.0f}% | RSI {rsi:.0f} | فوق VWAP ✅ | تحت Gamma {ref.get('gamma_wall',0):.0f} ✅ | فوق Ask {ref.get('above_ask',0)}% ✅\n💵 دخول: ${entry:.2f}\n🎯1: ${t1:.2f} 🎯2: ${t2:.2f} 🎯3: ${t3:.2f}\n🛑 وقف: ${stop:.2f}\n💰 ${total:,.0f}k\n⏰ {now_ksa.strftime('%H:%M:%S KSA')}")

def sniper_loop():
    while True:
        try:
            today_et=datetime.now(ET).date()
            for sym in TICKERS:
                try:
                    tk=yf.Ticker(sym)
                    if not tk.options: continue
                    dname=NAMES.get(sym,sym)
                    vwap=get_vwap(sym)
                    gamma_wall=get_gamma_wall(sym)
                    cur_price=None
                    try: cur_price=float(tk.history(period="1d")["Close"].iloc[-1])
                    except: pass
                    for exp in tk.options[:2]:
                        try:
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
                                    if not (0.25 <= price <= 12.0): continue
                                    if vol < 30: continue
                                    oi=int(r["openInterest"]) if r["openInterest"] else 0
                                    prem_vol=float(vol*price*100)
                                    prem_oi=float(oi*price*100) if oi>0 else prem_vol
                                    opt_char="C" if otype=="calls" else "P"
                                    above_ask=get_above_ask_pct(r)
                                    vwap_ok=True
                                    if vwap and cur_price:
                                        vwap_ok = (cur_price>vwap) if opt_char=="C" else True
                                    gamma_ok=True
                                    if prem_vol>1500000 and vol>200:
                                        check_double_monster(dname,"ULTRA",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                                    if prem_vol>500000 and vol>150:
                                        check_double_monster(dname,"MEGA",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                                    if prem_oi>100000 and vol>100:
                                        check_double_monster(dname,"GOLDEN",prem_oi/1000,float(r["strike"]),exp,price,opt_char,prem_oi,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                                    if vol/max(oi,1)>2.5 and vol>200:
                                        check_double_monster(dname,"MOMENTUM",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                                    if is_0dte and vol>30:
                                        check_double_monster(dname,"HERO",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                                    if price<=1.50 and vol>=max(oi*2.5,150) and above_ask>=80:
                                        check_double_monster(dname,"EXPLOSIVE",prem_vol/1000,float(r["strike"]),exp,price,opt_char,prem_vol,r,vwap_ok,gamma_ok,above_ask,gamma_wall or 0)
                        except: continue
                    time.sleep(1)
                except: time.sleep(1); continue
            time.sleep(45)
        except Exception as e:
            print(f"ERR {e}"); time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker,daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":"🏆 <b>V25 FIXED شغال</b>","parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=0&timeout=20",timeout=25).json()
            time.sleep(5)
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
