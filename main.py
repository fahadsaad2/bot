from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "SPX V10 FIXED SPX OK"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX"}
sent = set()
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}}
double_sent = {}
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"

def send(t):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
    except: pass

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]
    for k in list(double_sent.keys()):
        if time.time() - double_sent[k] > 86400: del double_sent[k]
    combos = [(["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER"),(["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA"),(["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS"),(["HERO","GAMMA"], "💣💥 HERO+GAMMA"),(["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO")]
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
                send(f"🚨🚨🚨 <b>الوحش المزدوج V10</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 <b>سترايك: {ref['strike']:.0f}{ref['type']}</b>\n📅 {ref['exp']}\n💵 ${ref['price']:.2f}\n💰 ${ref['premium']:,.0f}\n💰 مجمع ${total:,.0f}k\n🔥 NOW")
                if combo[0]!= typ and ticker in monster_memory[combo[0]]: del monster_memory[combo[0]][ticker]

def score(vol, oi, price, prem):
    s=0; r=vol/max(oi,1)
    if r>3: s+=40
    elif r>1.5: s+=30
    if prem>1500000: s+=30
    elif prem>500000: s+=20
    if 0.90 <= price <= 2.5: s+=20
    if vol>5000: s+=10
    return min(s,99)

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
                    try:
                        exp0 = tk.options[0]
                        for otype in ["calls","puts"]:
                            ch = scan_option(sym, exp0, otype)
                            if ch is None or ch.empty: continue
                            g = ch.sort_values(by='openInterest',ascending=False).iloc[0]
                            if int(g['openInterest'])>3000 and float(g['lastPrice'])>=0.3:
                                otype_s = "C" if otype=="calls" else "P"
                                prem = float(g['openInterest']*g['lastPrice']*100)
                                key = f"GAMMA{sym}{g['strike']}{exp0}{otype_s}"
                                if key not in sent:
                                    sent.add(key)
                                    send(f"💥 <b>GAMMA WALL</b>\n<b>{dname} {g['strike']:.0f}{otype_s}</b>\n📅 {exp0}\n💵 ${g['lastPrice']:.2f}")
                                    check_double_monster(dname, "GAMMA", prem/1000, float(g['strike']), exp0, float(g['lastPrice']), otype_s, prem)
                    except: pass
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
                                    sc = score(vol, oi, price, prem)
                                    if prem>1000000 and vol>3000:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}{int(vol/100)*100}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"👑 <b>GOLDEN {sc}</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem:,.0f}")
                                            check_double_monster(dname, "GOLDEN", prem/1000, float(r['strike']), exp, price, otype_s, prem)
                                    elif vol/ max(oi,1) >1.2 and vol>800:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🐋 <b>SWEEPS</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}")
                                            check_double_monster(dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if ed==today and 0.90 <= price <= 2.5 and vol>1000:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🚀 <b>HERO ZERO</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}")
                                            check_double_monster(dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if (ed-today).days<=4 and 0.3 <= price <= 2.0 and vol>1000:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"⏰ <b>POWER</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}")
                                            check_double_monster(dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                        except: continue
                except: continue
            time.sleep(45)
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
                alerts.append(f"💰 <b>حوت {w[:6]}...</b>\n{side} {val:,.0f} SPX")
        except: pass
        time.sleep(0.3)
    return alerts

def main_loop():
    time.sleep(3)
    send("✅ <b>V10 FIXED SPX OK - لا يكرر نفس الدخول</b>")
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0; last_wallet=0
    while True:
        try:
            if time.time()-last_wallet>120:
                wa=check_wallets()
                for a in wa: send(a)
                last_wallet=time.time()
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]; txt=u.get("message",{}).get("text","").lower()
                if "/status" in txt: send(f"✅ V10 LIVE\n👀 {len(TICKERS)} شركة\n💰 {len(WALLETS)} حوت")
        except Exception as e:
            print(f"LOOP ERR {e}"); time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
