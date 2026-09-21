from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V20 LEGENDARY 20 COMPANIES"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 20 شركة + مؤشرات
TICKERS = ["SPY","QQQ","IWM","DIA","^GSPC","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","NFLX","PLTR","NFLX"]
NAMES = {"^GSPC":"SPX"}

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
        tk=yf.Ticker(sym)
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
            return last >= mid
        return True
    except: return True

def check_double_monster(ticker,typ,vol_k,strike=0,exp="",price=0,opt_type="C",premium=0,row=None):
    monster_memory[typ][ticker]={"time":time.time(),"vol":vol_k,"strike":strike,"exp":exp,"price":price,"type":opt_type,"premium":premium,"row":row}
    combos=[
        (["MEGA","MOMENTUM"],"🐋🔥 MEGA+MOMENTUM"),
        (["ULTRA","MOMENTUM"],"🐳🚀 ULTRA+MOMENTUM 500%"),
        (["GOLDEN","MOMENTUM"],"👑🔥 GOLDEN+MOMENTUM"),
        (["GOLDEN","HERO"],"👑🚀 GOLDEN+HERO 1000%"),
        (["GOLDEN","ULTRA"],"👑🐳 GOLDEN+ULTRA"),
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

        # فلاتر أسطوري
        if not (1.0 <= ref['price'] <= 10.0): continue
        if not is_buy_signal(ref.get('row',{})): continue
        try:
            iv=float(ref['row'].get('impliedVolatility',0))*100
            if not (35 <= iv <= 80): continue
            iv_txt=f"IV {iv:.0f}% رخيص"
        except: continue

        rsi=get_rsi("^GSPC" if ticker=="SPX" else ticker)
        opt_t=ref['type']
        if opt_t=="C" and rsi>25: continue
        if opt_t=="P" and rsi<75: continue
        rsi_txt=f"RSI {rsi:.0f} {'قاع سحيق CALL' if opt_t=='C' else 'قمة سحيقة PUT'}"

        double_sent[base_key]=time.time()
        entry=ref['price']
        t1=entry*1.5; t2=entry*2.0; t3=entry*3.0; stop=entry*0.7
        total=sum(monster_memory[c][ticker]["vol"] for c in combo)

        et_tz=pytz.timezone('US/Eastern')
        now_et=datetime.now(et_tz)
        valid_str=datetime.fromtimestamp(now_et.timestamp()+180, et_tz).strftime("%H:%M:%S ET")
        now_str=now_et.strftime("%H:%M:%S ET")

        queue_send(f"🚨 TOP5 مضمون 1-10$ + IV + RSI 🚨\n\n🎯 {ticker} - {desc}\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n\n✅ {iv_txt}\n✅ {rsi_txt}\n✅ 🟢 BUY TO OPEN\n✅ سعر ${entry:.2f} بين 1-10$\n\n💵 دخول: ${entry:.2f}\n🎯 هدف1: ${t1:.2f} (+50%)\n🎯 هدف2: ${t2:.2f} (+100%)\n🎯 هدف3: ${t3:.2f} (+200%)\n🛑 وقف: ${stop:.2f}\n💰 مجمع ${total:,.0f}k\n\n⏰ اكتشاف: {now_str}\n⚡️ ادخل قبل: {valid_str} (3 دقايق)\n🔥 سيولة حية - لا تتأخر")

def sniper_loop():
    et_tz=pytz.timezone('US/Eastern')
    while True:
        try:
            today_et=datetime.now(et_tz).date()
            for sym in TICKERS:
                try:
                    tk=yf.Ticker(sym)
                    if not tk.options: continue
                    dname=NAMES.get(sym,sym.replace("^GSPC","SPX"))
                    for exp in tk.options[:3]:
                        try:
                            ed=datetime.strptime(exp,"%Y-%m-%d").date()
                            if not (0 <= (ed-today_et).days <= 4): continue
                            is_0dte = (ed==today_et)
                            for otype in ["calls","puts"]:
                                try: chain=getattr(tk.option_chain(exp),otype)
                                except: continue
                                if chain is None or chain.empty: continue
                                chain=chain.fillna(0)
                                for _,r in chain.iterrows():
                                    vol=int(r['volume']) if r['volume'] else 0
                                    price=float(r['lastPrice']) if r['lastPrice'] else 0
                                    if not (1.0 <= price <= 10.0): continue
                                    if vol < 50: continue
                                    oi=int(r['openInterest']) if r['openInterest'] else 0
                                    prem_vol=float(vol*price*100); prem_oi=float(oi*price*100) if oi>0 else prem_vol
                                    if prem_vol > 2000000 and vol>300:
                                        check_double_monster(dname,"ULTRA",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    elif prem_vol > 800000 and vol>300:
                                        check_double_monster(dname,"MEGA",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    elif prem_oi>150000 and vol>200:
                                        check_double_monster(dname,"GOLDEN",prem_oi/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_oi,r)
                                    if vol / max(oi,1) > 3.0 and vol>500:
                                        check_double_monster(dname,"MOMENTUM",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                                    if is_0dte and vol>50:
                                        check_double_monster(dname,"HERO",prem_vol/1000,float(r['strike']),exp,price,"C" if otype=="calls" else "P",prem_vol,r)
                        except: continue
                except: continue
            time.sleep(30)
        except Exception as e:
            print(f"ERR {e}"); time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker,daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":"🏆 <b>V20 أسطوري 20 شركة شغال</b>\n💵 1-10$\n✅ IV 35-80% رخيص\n📉 RSI<25 CALL قاع\n📈 RSI>75 PUT قمة\n⏰ دخول 3 دقايق","parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/test" in txt: queue_send("🏆 V20 أسطوري 20 شركة ✅")
                if "/status" in txt: queue_send(f"✅ LIVE طابور {len(message_queue)}")
                if "/clear" in txt: double_sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
