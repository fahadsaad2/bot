from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "V10 ALL - 20 HALAL + 8 WHALES + DOUBLE"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX"}
sent = {} # غيرناه لديكشنري عشان نمنع التكرار 6 ساعات
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}}
double_sent = {}
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"

def send(t):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
    except: pass

def is_new(key):
    if key not in sent or time.time() - sent[key] > 21600: # 6 ساعات ما يكرر
        sent[key] = time.time()
        return True
    return False

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]
    combos = [(["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER تدبيلة"),(["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA انفجار"),(["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS"),(["HERO","GAMMA"], "💣💥 HERO+GAMMA"),(["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO")]
    for combo, desc in combos:
        if typ not in combo: continue
        if all(ticker in monster_memory[c] for c in combo):
            times = [monster_memory[c][ticker]["time"] for c in combo]
            if max(times)-min(times) < 3600:
                ref = monster_memory["GOLDEN"][ticker] if "GOLDEN" in combo else monster_memory[combo[0]][ticker]
                prem_bucket = int(ref['premium'] / 50000) * 50000
                base_key = f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(ref['strike'])}{ref['type']}_{ref['exp']}_{prem_bucket}"
                if base_key in double_sent: continue
                total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                double_sent[base_key] = time.time()
                send(f"🚨🚨🚨 <b>الوحش المزدوج V10</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 سترايك: {ref['strike']:.0f}{ref['type']}\n📅 {ref['exp']}\n💵 ${ref['price']:.2f}\n💰 ${ref['premium']:,.0f}\n💰 مجمع ${total:,.0f}k\n🔥 ادخل NOW")

def scan_option(tk_symbol, exp, opt_type="calls"):
    try:
        tk = yf.Ticker("^SPX" if tk_symbol=="^GSPC" else tk_symbol)
        return getattr(tk.option_chain(exp), opt_type)
    except: return None

def sniper_loop():
    while True:
        try:
            today = datetime.now().date()
            for sym in TICKERS:
                try:
                    ysym = "^SPX" if sym=="^GSPC" else sym
                    tk = yf.Ticker(ysym)
                    if not tk.options: continue
                    dname = NAMES.get(sym,sym)
                    for exp in tk.options[:3]:
                        try:
                            ed = datetime.strptime(exp,"%Y-%m-%d").date()
                            for otype in ["calls","puts"]:
                                chain = scan_option(sym, exp, otype)
                                if chain is None or chain.empty: continue
                                chain = chain[(chain['openInterest']>100) & (chain['lastPrice']>=0.30)]
                                if chain.empty: continue
                                otype_s = "C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol = int(r['volume']); oi = int(r['openInterest']); price = float(r['lastPrice'])
                                    if vol < 800: continue
                                    prem = float(r['openInterest']*price*100) if oi>0 else float(vol*price*100)
                                    prem_vol = float(vol*price*100)
                                    if prem>1000000 and vol>3000:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            send(f"👑 <b>GOLDEN</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem:,.0f}")
                                            check_double_monster(dname, "GOLDEN", prem/1000, float(r['strike']), exp, price, otype_s, prem)
                                    if vol/ max(oi,1) >1.2 and vol>800:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            send(f"🐋 <b>SWEEPS</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f}")
                                            check_double_monster(dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if ed==today and 0.90 <= price <= 2.5 and vol>1000:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            send(f"🚀 <b>HERO ZERO</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp} 0DTE\n💵 ${price:.2f}")
                                            check_double_monster(dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if (ed-today).days<=4 and 0.3 <= price <= 2.0 and vol>1000:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            send(f"⏰ <b>POWER</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}")
                                            check_double_monster(dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                        except: continue
                except: continue
            time.sleep(60)
        except: time.sleep(20)

def check_wallets():
    alerts=[]
    if not WALLETS or not ETHERSCAN_API: return alerts
    for w in WALLETS[:8]:
        try:
            url=f"https://api.etherscan.io/api?module=account&action=tokentx&contractaddress={SPX_CONTRACT}&address={w}&sort=desc&apikey={ETHERSCAN_API}"
            r=requests.get(url,timeout=15).json()
            if r.get("status")=="1" and r["result"]:
                tx=r["result"][0]; h=tx["hash"]
                if h in seen_tx: continue
                seen_tx.add(h)
                val=float(tx.get("value",0))/10**18
                side="🟢 شراء" if tx['to'].lower()==w.lower() else "🔴 بيع"
                alerts.append(f"💰 <b>حوت {w[:6]}...{w[-4:]}</b>\n{side} {val:,.0f} SPX")
        except: pass
        time.sleep(0.3)
    return alerts

def main_loop():
    time.sleep(3)
    send("✅ <b>V10 كامل - 20 شركة + 8 حيتان + مزدوج - ما يكرر 6 ساعات</b>")
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0; last_wallet=0
    while True:
        try:
            if time.time()-last_wallet>120:
                for a in check_wallets(): send(a)
                last_wallet=time.time()
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/status" in txt: send(f"✅ V10 كامل LIVE\n👀 {len(TICKERS)} شركة حلال\n💰 {len(WALLETS)} حوت\n👹 {len(double_sent)} مزدوج\n⏰ {datetime.now().strftime('%m/%d %I:%M%p')}\n\nما يكرر نفس السترايك 6 ساعات")
                if "/clear" in txt: sent.clear(); double_sent.clear(); send("✅ تم مسح الذاكرة")
        except Exception as e: print(f"ERR {e}"); time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
