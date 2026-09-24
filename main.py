from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)
@app.route("/")
def home():
    return "V25 EXPLOSIVE KSA 24 TICKERS"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

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
        if sym in vwap_cache and time.time()-vwap_cache[sym]["t"] < 120:
            return vwap_cache[sym]["v"]
        tk=yf.Ticker(sym)
        hist=tk.history(period="1d", interval="5m")
        if len(hist)<5: return None
        # VWAP = sum(price*vol)/sum(vol)
        hist["tp"] = (hist["High"]+hist["Low"]+hist["Close"])/3
        vwap = (hist["tp"]*hist["Volume"]).sum() / hist["Volume"].sum()
        vwap_cache[sym]={"v":float(vwap),"t":time.time()}
        return float(vwap)
    except: return None

def get_gamma_wall(sym):
    try:
        if sym in gamma_cache and time.time()-gamma_cache[sym]["t"] < 300:
            return gamma_cache[sym]["v"]
        tk=yf.Ticker(sym if sym!="SPX" else "SPY")
        if not tk.options: return None
        exp = tk.options[0]
        chain = tk.option_chain(exp)
        # اكبر OI في الكول = جدار جاما
        if chain.calls.empty: return None
        max_oi_row = chain.calls.loc[chain.calls["openInterest"].idxmax()]
        wall = float(max_oi_row["strike"])
        gamma_cache[sym]={"v":wall,"t":time.time()}
        return wall
    except: return None

def is_buy_signal(row):
    try:
        bid=row.get("bid",0); ask=row.get("ask",0); last=row.get("lastPrice",0)
        if ask>0 and bid>0: return last >= (bid+ask)/2
        return True
    except: return True

def is_sell_signal(row):
    try:
        bid=row.get("bid",0); ask=row.get("ask",0); last=row.get("lastPrice",0)
        if ask>0 and bid>0: return last <= (bid+ask)/2 + 0.02
        return False
    except: return False

def get_above_ask_pct(row):
    try:
        ask=row.get("ask",0); last=row.get("lastPrice",0)
        if ask>0 and last>=ask:
            return int((last-ask)/ask*100) if ask>0.2 else 100
        return 0 if last<ask else 50
    except: return 0

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0,row=None, vwap_ok=True, gamma_ok=True, above_ask=0):
    global last_reset_day, blocked_today
    if datetime.now().day!= last_reset_day:
        sent_today.clear(); double_sent.clear(); single_sent.clear(); blocked_today=0
        last_reset_day = datetime.now().day
    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row,"vwap_ok":vwap_ok,"gamma_ok":gamma_ok,"above_ask":above_ask}

    # فلتر اساسي للكل
    if not (vwap_ok and gamma_ok and above_ask>=80):
        return

    if typ in ["ULTRA","MEGA","EXPLOSIVE"]:
        # حدود ذكية
        if price <= 1.50:
            need = 70000 if typ=="EXPLOSIVE" else 150000
        else:
            need = 500000
        if premium >= need:
            single_key = f"SINGLE_{ticker}_{strike}_{exp}_{opt_type}"
            if single_key not in single_sent and single_key not in sent_today:
                rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
                if not ((opt_type=="C" and rsi>50) or (opt_type=="P" and rsi<68)):
                    if 0.25 <= price <= 12.0 and is_buy_signal(row or {}):
                        try:
                            iv=float((row or {}).get("impliedVolatility",0))*100
                            if 20 <= iv <= 90:
                                single_sent.add(single_key)
                                sent_today.add(f"{ticker}_{strike}_{exp}_{opt_type}")
                                exit_key=f"{ticker}_{strike}_{exp}_{opt_type}"
                                exit_memory[exit_key]={"entry":price,"high":price,"time":time.time(),"ticker":ticker,"strike":strike,"exp":exp,"type":opt_type}
                                now_ksa=datetime.now(KSA)
                                icon = "💥 EXPLOSIVE+🐳🚀 ULTRA" if price<=1.5 else "🐳🚀 ULTRA" if typ=="ULTRA" else "💥 EXPLOSIVE" if typ=="EXPLOSIVE" else "🐋 MEGA"
                                vwap_txt = "فوق VWAP ✅" if vwap_ok else ""
                                queue_send(f"{icon} حوت مفرد\n\n🎯 {ticker}\n💥 {strike:g}{opt_type} - {exp}\n✅ IV {iv:.0f}% | RSI {rsi:.0f} | {vwap_txt} | Ask {above_ask}% ✅\n💵 دخول: ${price:.2f}\n💰 ${premium/1000:,.0f}k\n⏰ {now_ksa.strftime('%H:%M:%S KSA')}")
                        except: pass
                else:
                    blocked_today+=1

    combos=[(["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM"),(["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM"),(["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),(["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO"),(["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA"),(["EXPLOSIVE","MOMENTUM"],"💥 EXPLOSIVE+MOMENTUM"),(["EXPLOSIVE","ULTRA"],"💥🚀 EXPLOSIVE+ULTRA")]
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
        if not is_buy_signal(ref.get("row",{})): continue
        if ref.get("above_ask",0) < 80: continue
        if not ref.get("vwap_ok",False): continue
        try:
            iv=float(ref["row"].get("impliedVolatility",0))*100
            if not (20 <= iv <= 90): continue
        except: continue
        rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
