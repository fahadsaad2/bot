from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "SPX V10 PRIVATE DOUBLE MONSTER - 20 HALAL"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
MORALIS = os.getenv("MORALIS_API","").strip()
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS","").split(",") if x.strip()]

TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX"}
sent = set()
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}}

def is_allowed(uid, cid):
    if ALLOWED_IDS: return uid in ALLOWED_IDS
    return str(cid) == str(CHAT_ID)

def send(t):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
    except: pass

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, otype="C", prem=0):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": otype, "premium": prem}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]
    combos = [
        (["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER تدبيلة بور هور"),
        (["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA انفجار جاما"),
        (["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS صندوق يخفي دخول"),
        (["HERO","GAMMA"], "💣💥 HERO+GAMMA انفجار لحظي"),
        (["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO أقوى دخول"),
    ]
    for combo, desc in combos:
        if all(ticker in monster_memory[c] for c in combo):
            if max([monster_memory[c][ticker]["time"] for c in combo]) - min([monster_memory[c][ticker]["time"] for c in combo]) < 3600:
                total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                last = monster_memory[combo[-1]][ticker]
                send(f"🚨🚨🚨 <b>الوحش المزدوج V10</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {' + '.join(combo)}</b>\n📝 {desc}\n💥 <b>سترايك: {last['strike']:.0f}{last['type']}</b>\n📅 تاريخ: {last['exp']}\n💵 عقد: ${last['price']:.2f}\n💰 دخول: ${last['premium']:,.0f}\n💰 مجمع: ${total:,.0f}k\n🔥 NOW")

def score(vol, oi, price, prem):
    s=0; r=vol/max(oi,1)
    if r>3: s+=40
    elif r>1.5: s+=30
    if prem>1500000: s+=30
    elif prem>500000: s+=20
    if 0.90 <= price <= 2.5: s+=20
    if vol>5000: s+=10
    return min(s,99)

def sniper_loop():
    while True:
        try:
            today=datetime.now().date()
            for sym in TICKERS:
                try:
                    tk=yf.Ticker("^SPX" if sym=="^GSPC" else sym)
                    if not tk.options: continue
                    d=NAMES.get(sym,sym)
                    # GAMMA
                    try:
                        exp0=tk.options[0]
                        for ot in ["calls","puts"]:
                            ch=tk.option_chain(exp0).__getattribute__(ot)
                            if ch.empty: continue
                            g=ch.sort_values(by='openInterest',ascending=False).iloc[0]
                            if int(g['openInterest'])>3000:
                                ots="C" if ot=="calls" else "P"; prem=float(g['openInterest']*g['lastPrice']*100)
                                key=f"GAMMA{sym}{g['strike']}{exp0}{ots}"
                                if key not in sent:
                                    sent.add(key)
                                    send(f"💥 <b>GAMMA لحظي</b>\n<b>{d} {g['strike']:.0f}{ots}</b>\n📅 {exp0}\n💵 ${g['lastPrice']:.2f}\n💰 دخول: ${prem:,.0f}\n📊 OI:{int(g['openInterest']):,}")
                                    check_double_monster(d, "GAMMA", prem/1000, float(g['strike']), exp0, float(g['lastPrice']), ots, prem)
                    except: pass
                    for exp in tk.options[:3]:
                        try:
                            ed=datetime.strptime(exp,"%Y-%m-%d").date()
                            for ot in ["calls","puts"]:
                                chain=tk.option_chain(exp).__getattribute__(ot)
                                chain=chain[(chain['openInterest']>100) & (chain['lastPrice']>=0.30)]
                                if chain.empty: continue
                                ots="C" if ot=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol=int(r['volume']); oi=int(r['openInterest']); price=float(r['lastPrice'])
                                    if vol<800: continue
                                    prem=float(oi*price*100); prem_vol=float(vol*price*100); sc=score(vol,oi,price,prem)
                                    if prem>1000000 and vol>3000:
                                        key=f"GOLDEN{sym}{r['strike']}{exp}{ots}{int(vol/100)*100}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"👑 <b>GOLDEN لحظي {sc}</b>\n<b>{d} {r['strike']:.0f}{ots}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem:,.0f}\nVol:{vol:,} OI:{oi:,}\n🎯 ${price*1.8:.2f}")
                                            check_double_monster(d, "GOLDEN", prem/1000, float(r['strike']), exp, price, ots, prem)
                                    elif vol/max(oi,1)>1.2 and vol>800:
                                        key=f"SWEEPS{sym}{r['strike']}{exp}{ots}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🐋 <b>SWEEPS لحظي</b>\n<b>{d} {r['strike']:.0f}{ots}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}\nVol:{vol:,} x{vol/max(oi,1):.1f}")
                                            check_double_monster(d, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, ots, prem_vol)
                                    if ed==today and 0.90<=price<=2.5 and vol>1000:
                                        key=f"HERO{sym}{r['strike']}{exp}{ots}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🚀 <b>HERO 0DTE لحظي</b>\n<b>{d} {r['strike']:.0f}{ots}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}")
                                            check_double_monster(d, "HERO", prem_vol/1000, float(r['strike']), exp, price, ots, prem_vol)
                                    if (ed-today).days<=4 and 0.3<=price<=2.0 and vol>1000:
                                        key=f"POWER{sym}{r['strike']}{exp}{ots}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"⏰ <b>POWER لحظي</b>\n<b>{d} {r['strike']:.0f}{ots}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}")
                                            check_double_monster(d, "POWER", prem_vol/1000, float(r['strike']), exp, price, ots, prem_vol)
                        except: continue
                except: continue
            time.sleep(45)
        except: time.sleep(20)

def check_wallets():
    alerts=[]
    if not WALLETS: return alerts
    if ETHERSCAN_API:
        SPX="0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"
        for w in WALLETS[:8]:
            try:
                url=f"https://api.etherscan.io/api?module=account&action=tokentx&contractaddress={SPX}&address={w}&sort=desc&apikey={ETHERSCAN_API}"
                r=requests.get(url,timeout=15).json()
                if r.get("status")=="1" and r["result"]:
                    tx=r["result"][0]; h=tx["hash"]
                    if h in
