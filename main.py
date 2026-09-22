from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V21 RSI 30/70 NO REPEAT"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TICKERS = ["SPY","QQQ","IWM","DIA","^GSPC","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","NFLX","PLTR"]
NAMES = {"^GSPC":"SPX","SPY":"SPX 🔥"}

monster_memory = {"GOLDEN":{},"MEGA":{},"ULTRA":{},"MOMENTUM":{},"HERO":{}}
double_sent = {}
exit_memory = {}
message_queue = deque()
rsi_cache = {}
sent_today = set()
last_reset_day = datetime.now().day

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
        tk=yf.Ticker(sym); hist=tk.history(period="14d")
        if len(hist)<14: return 50
        delta=hist['Close'].diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss; rsi=100-(100/(1+rs)); val=float(rsi.iloc[-1]); rsi_cache[sym]={'v':val,'t':time.time()}; return val
    except: return 50

def is_buy_signal(row):
    try:
        bid=row.get('bid',0); ask=row.get('ask',0); last=row.get('lastPrice',0)
        if ask>0 and bid>0: return last >= (bid+ask)/2
        return True
    except: return True

def is_sell_signal(row):
    try:
        bid=row.get('bid',0); ask=row.get('ask',0); last=row.get('lastPrice',0)
        if ask>0 and bid>0: return last <= (bid+ask)/2 + 0.02
        return False
    except: return False

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0,row=None):
    global last_reset_day
    if datetime.now().day!= last_reset_day:
        sent_today.clear()
        double_sent.clear()
        last_reset_day = datetime.now().day

    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row}
    combos=[(["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM"),(["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM"),(["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),(["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO"),(["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA")]
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

        if not (1.0 <= ref['price'] <= 10.0): continue
        if not is_buy_signal(ref.get('row',{})): continue
        try:
            iv=float(ref['row'].get('impliedVolatility',0))*100
            if not (35 <= iv <= 80): continue
        except: continue

        rsi=get_rsi("^GSPC" if "SPX" in ticker else ticker)
        opt_t=ref['type']
        if opt_t=="C" and rsi>30: continue
        if opt_t=="P" and rsi<70: continue

        double_sent[base_key]=time.time()
        sent_today.add(contract_key)

        exit_key=f"{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}"
        exit_memory[exit_key]={"entry":ref['price'],"high":ref['price'],"time":time.time(),"ticker":ticker,"strike":ref['strike'],"exp":ref['exp'],"type":ref['type']}

        entry=ref['price']; t1=entry*1.5; t2=entry*2.0; t3=entry*3.0; stop=entry*0.7
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)
        et_tz=pytz.timezone('US/Eastern'); now_et=datetime.now(et_tz)
        queue_send(f"🚨 دخول حوت 🚨\n\n🎯 {ticker} - {desc}\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n✅ IV {iv:.0f}% رخيص\n✅ RSI {rsi:.0f} {'CALL قاع' if opt_t=='C' else 'PUT قمة'}\n✅ 🟢 BUY\n\n💵 دخول: ${entry:.2f}\n🎯 هدف1: ${t1:.2f} (+50%)\n🎯 هدف2: ${t2:.2f} (+100%)\n🎯 هدف3: ${t3:.2f} (+200%)\n🛑 وقف: ${stop:.2f}\n💰 مجمع ${total:,.0f}k\n⏰ {now_et.strftime('%H:%M:%S ET')}")

def check_exits():
    while True:
        try:
            time.sleep(20)
            if not exit_memory: continue
            for key, mem in list(exit_memory.items()):
                if time.time()-mem['time'] > 14400: del exit_memory[key]; continue
                try:
                    sym = "SPY" if "SPX" in mem['ticker'] else mem['ticker'].split()[0]
                    tk=yf.Ticker("^GSPC" if sym=="SPX" else sym)
                    try: chain=tk.option_chain(mem['exp'])
                    except: continue
                    df = chain.calls if mem['type']=="C" else chain.puts
                    row = df[df['strike']==mem['strike']]
                    if row.empty: continue
                    r=row.iloc[0]
                    cur=float(r['lastPrice']); vol=int(r['volume'])
