from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)
@app.route("/")
def home():
    return "V29 GEX + EXIT + $10 MAX"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TICKERS = ["^GSPC","QQQ","IWM","DIA","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","NFLX","PLTR"]
NAMES = {"^GSPC":"SPX"}
INDEX_WAVE = ["SPX","QQQ","IWM","DIA"]

monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{},"EXPLOSIVE":{}}
double_sent = {}
message_queue = deque()
rsi_cache = {}
wave_cache = {}
sent_today = set()
active_trades = {} # لتتبع خروج الحيتان
last_gex_time = 0
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

def get_wave_state(sym):
    try:
        if sym in wave_cache and time.time()-wave_cache[sym]["t"] < 180:
            return wave_cache[sym]["v"]
        yf_sym = "^GSPC" if sym=="SPX" else sym
        hist=yf.Ticker(yf_sym).history(period="5d", interval="5m")
        if len(hist)<100: return {"allow":True,"wave":"مومنتوم","fib":0}
        high=hist["High"][-60:].max(); low=hist["Low"][-60:].min(); last=hist["Close"].iloc[-1]
        swing=high-low
        if swing==0: return {"allow":True,"wave":"?","fib":0}
        drop=(high-last)/swing*100
        if 23 <= drop <= 50: res={"allow":True,"wave":f"موجة 4->{drop:.0f}% جاهز","fib":drop}
        elif drop < 23: res={"allow":False,"wave":f"قمة موجة 3 ({drop:.0f}%) انتظار","fib":drop}
        else: res={"allow":True,"wave":f"تصحيح {drop:.0f}%","fib":drop}
        wave_cache[sym]={"v":res,"t":time.time()}
        return res
    except: return {"allow":True,"wave":"?","fib":0}

def get_above_ask(row):
    try:
        ask=float(row.get("ask",0)); last=float(row.get("lastPrice",0))
        return 90 if ask>0 and last>=ask else 0
    except: return 0

# === تقرير GEX الجديد ===
def get_gex_report():
    try:
        tk=yf.Ticker("^GSPC")
        if not tk.options: return None
        exp=tk.options[0]
        chain=tk.option_chain(exp)
        spot=tk.history(period="1d")["Close"].iloc[-1]
        calls=chain.calls.fillna(0)
        puts=chain.puts.fillna(0)
        # حساب مبسط GEX = OI * Gamma * 100 * Spot^2
        # yfinance ما يعطي Gamma فنقربها
        call_gex = (calls["openInterest"] * calls["lastPrice"] * 100).sum() / 1e9
        put_gex = (puts["openInterest"] * puts["lastPrice"] * 100).sum() / 1e9
        net_gex = call_gex - put_gex
        # الجدران = اعلى OI
        call_wall = calls.loc[calls["openInterest"].idxmax()]["strike"] if not calls.empty else 0
        put_wall = puts.loc[puts["openInterest"].idxmax()]["strike"] if not puts.empty else 0
        # Flip تقريبي = متوسط الجدارين
        flip = (call_wall + put_wall)/2
        now=datetime.now(KSA).strftime("%d %b %Y • %I:%M %p KSA")
        report = f"📈 <b>Gamma Exposure Report</b>\n\n🏷 Ticker : $SPX\n📅 Expiration : {exp}\n💰 Spot : ${spot:.2f}\n\nالسيولة:\n{'🟢' if net_gex>0 else '🔴'} Net GEX ${net_gex:.2f}B\n🟢 Call GEX ${call_gex:.2f}B\n🔴 Put GEX -${put_gex:.2f}B\n\nالجدران:\n🟢 Call Wall {call_wall:g}\n🔴 Put Wall {put_wall:g}\n\nالفاصل:\n⚖️ Gamma Flip {flip:.2f}\n\n🕒 {now}"
        return report
    except Exception as e:
        print(f"GEX error {e}")
        return None

def check_double_monster(ticker,typ,vol_k,strike,exp,price,opt_type,premium,row, above_ask=0):
    global last_reset_day
    if datetime.now().day!= last_reset_day:
        sent_today.clear(); double_sent.clear(); active_trades.clear()
        last_reset_day=datetime.now().day
    if ticker in INDEX_WAVE:
        wave=get_wave_state(ticker)
        if not wave["allow"]: return
        wave_text=f"\n🌊 {wave['wave']}"
    else: wave_text=""

    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row,"above_ask":above_ask}
    if above_ask<80: return

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
        # === حد السعر الجديد 10 دولار ===
        if not (0.25 <= ref["price"] <= 10.0): continue
        try:
            iv=float(ref["row"].get("impliedVolatility",0))*100
            if not (10 <= iv <= 120): continue
        except: continue
        rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
        if ref["type"]=="C" and rsi>55: continue
        if ref["type"]=="P" and rsi<65: continue
        double_sent[base_key]=time.time()
        sent_today.add(contract_key)
        # حفظ للخروج
        active_trades[contract_key]={"entry":ref["price"],"strike":ref["strike"],"type":ref["type"],"ticker":ticker,"exp":ref["exp"],"high":ref["price"]}
        entry=ref["price"]
        t1,t2,t3,stop = (entry*1.3, entry*1.6, entry*2.2, entry*0.7) if entry>3 else (entry*1.5, entry*2.0, entry*3.0, entry*0.65)
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)
        now_ksa=datetime.now(KSA)
        display_strike = ref['strike']
        if ticker=="SPX" and display_strike < 2000: display_strike = display_strike*10
        exp_occ = datetime.strptime(ref['exp'],"%Y-%m-%d").strftime("%y%m%d")
        strike_occ = int(display_strike*1000)
        occ_symbol = f"SPXW {exp_occ}{ref['type']}{strike_occ:08d}" if ticker=="SPX" else f"{ticker} {exp_occ}{ref['type']}{strike_occ:08d}"
        queue_send(f"🚨 دخول حوت مزدوج 🚨\n\n🎯 {ticker} - {desc}{wave_text}\n💥 {display_strike:g}{ref['type']} - {ref['exp']}\n📋 عقد: <code>{occ_symbol}</code>\n✅ IV {iv:.0f}% | RSI {rsi:.0f} | فوق Ask {ref['above_ask']}% ✅\n💵 دخول: ${entry:.2f}\n🎯1: ${t1:.2f} 🎯2: ${t2:.2f} 🎯3: ${t3:.2f}\n🛑 وقف: ${stop:.2f}\n💰 ${total:,.0f}k\n⏰ {now_ksa.strftime('%H:%M:%S KSA')}")

# === خروج الحيتان الجديد ===
def check_whale_exit():
    while True:
        try:
            time.sleep(60)
            if not active_trades: continue
            for key, trade in list(active_trades.items()):
                try:
                    ticker=trade["ticker"]
                    yf_sym="^GSPC" if ticker=="SPX" else ticker
                    tk=yf.Ticker(yf_sym)
                    if not trade["exp"] in tk.options: continue
                    chain=getattr(tk.option_chain(trade["exp"]), "calls" if trade["type"]=="C" else "puts")
                    row=chain[chain["strike"]==trade["strike"]]
                    if row.empty: continue
                    price=float(row.iloc[0]["lastPrice"])
                    vol=int(row.iloc[0]["volume"])
                    # تحديث الهاي
                    if price>trade["high"]: active_trades[key]["high"]=price
                    # خروج: اذا ن
