from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "SPX FINAL - ALL IN ONE V10 LIVE - 19 HALAL"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
MORALIS = os.getenv("MORALIS_API","").strip()
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

# 19 شركة حلال - شلنا NFLX وضفنا MU SMCI ARM QCOM
TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","PLTR","MSTR","COIN","MU","SMCI","ARM","QCOM"]
NAMES = {"^GSPC":"SPX"}
sent = set()
seen_tx = set()

monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}}

def send(t):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
    except: pass

def check_double_monster(ticker, typ, vol_k):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k}
    for k in monster_memory:
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600:
                del monster_memory[k][tk]
    combos = [
        (["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA انفجار جاما"),
        (["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER تدبيلة بور هور"),
        (["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS صندوق يخفي دخول"),
        (["HERO","GAMMA"], "💣💥 HERO+GAMMA انفجار لحظي"),
        (["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO أقوى دخول"),
    ]
    for combo, desc in combos:
        if all(ticker in monster_memory[c] for c in combo):
            times = [monster_memory[c][ticker]["time"] for c in combo]
            if max(times)-min(times) < 3600:
                total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                send(f"🚨🚨🚨 <b>الوحش المزدوج V10</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {' + '.join(combo)}</b>\n📝 {desc}\n💰 سيولة: ${total:,.0f}k\n⏰ خلال ساعة\n🔥 ادخل NOW")

def score(vol, oi, price, prem):
    s=0; r=vol/max(oi,1)
    if r>3: s+=40
    elif r>1.5: s+=30
    if prem>1500000: s+=30
    elif prem>500000: s+=20
    if 0.90 <= price <= 2.5: s+=20
    if vol>5000: s+=10
    return min(s,99)

def get_all():
    hero=[]; sweeps=[]; golden=[]; gamma=[]; power=[]
    today=datetime.now().date()
    now_str=datetime.now().strftime("%m/%d %I:%M%p")
    for sym in TICKERS:
        try:
            ysym="^SPX" if sym=="^GSPC" else sym
            tk=yf.Ticker(ysym)
            if not tk.options: continue
            try:
                ch0=tk.option_chain(tk.options[0]).calls
                if not ch0.empty:
                    g=ch0.sort_values(by='openInterest',ascending=False).iloc[0]
                    prem=g['openInterest']*g['lastPrice']*100
                    gamma.append((g['openInterest'],f"💥 <b>{NAMES.get(sym,sym)} {g['strike']:.0f}C ${g['lastPrice']:.2f}</b> OI:{int(g['openInterest']):,} ${prem:,.0f} {tk.options[0]} | {now_str}\n🎯 ${g['lastPrice']*1.3:.2f} / ${g['lastPrice']*1.6:.2f}\n"))
                    check_double_monster(NAMES.get(sym,sym), "GAMMA", prem/1000)
            except: pass
            for exp in tk.options[:4]:
                try:
                    ed=datetime.strptime(exp,"%Y-%m-%d").date()
                    chain=tk.option_chain(exp).calls
                    chain=chain[(chain['openInterest']>150) & (chain['lastPrice']>=0.30)]
                    if chain.empty: continue
                    chain=chain.copy()
                    chain['premium']=chain['openInterest']*chain['lastPrice']*100
                    if ed==today:
                        h=chain[(chain['lastPrice']>=0.90)&(chain['lastPrice']<=2.5)].sort_values(by='volume',ascending=False).head(1)
                        for _,r in h.iterrows():
                            hero.append((r['volume'],f"🚀 <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> 0DTE Vol:{int(r['volume']):,} {exp} {now_str}\n🎯 +35% ${r['lastPrice']*1.35:.2f} +80% ${r['lastPrice']*1.8:.2f}\n"))
                            check_double_monster(NAMES.get(sym,sym), "HERO", float(r['premium'])/1000)
                    sw=chain[(chain['volume']>800)&(chain['volume']/chain['openInterest'].replace(0,1)>1.2)].sort_values(by='volume',ascending=False).head(1)
                    for _,r in sw.iterrows():
                        prem=float(r['premium']); sc=score(int(r['volume']),int(r['openInterest']),float(r['lastPrice']),prem)
                        msg=f"<b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} x{float(r['volume']/max(r['openInterest'],1)):.1f} Vol:{int(r['volume']):,} ${prem:,.0f} Score:{sc} {now_str}\n🎯 ${r['lastPrice']*1.3:.2f} / ${r['lastPrice']*1.6:.2f}\n"
                        if prem>1000000 and r['volume']>3000:
                            golden.append((prem,f"👑 {msg}"))
                            check_double_monster(NAMES.get(sym,sym), "GOLDEN", prem/1000)
                        else:
                            sweeps.append((r['volume'],f"🐋 {msg}"))
                            check_double_monster(NAMES.get(sym,sym), "SWEEPS", prem/1000)
                    if (ed-today).days<=4:
                        ph=chain[(chain['lastPrice']>=0.3)&(chain['lastPrice']<=2.0)].sort_values(by='volume',ascending=False).head(1)
                        for _,r in ph.iterrows():
                            power.append((r['volume'],f"⏰ <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} Vol:{int(r['volume']):,} {now_str}\n"))
                            check_double_monster(NAMES.get(sym,sym), "POWER", float(r['premium'])/1000)
                except: continue
        except: continue
    return [sorted(x,key=lambda y:y[0],reverse=True)[:6] for x in [hero,golden,sweeps,gamma,power]]

def check_wallets():
    if not MORALIS and not ETHERSCAN_API: return []
    if not WALLETS: return []
    alerts=[]
    if ETHERSCAN_API:
        SPX = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"
        for w in WALLETS:
            try:
                url=f"https://api.etherscan.io/api?module=account&action=tokentx&contractaddress={SPX}&address={w}&sort=desc&apikey={ETHERSCAN_API}"
                r=requests.get(url,timeout=15).json()
                if r.get("status")=="1" and r["result"]:
                    tx=r["result"][0]
                    h=tx["hash"]
                    if h in seen_tx: continue
                    seen_tx.add(h)
                    val=float(tx.get("value",0))/10**18
                    alerts.append(f"💰 <b>محفظة Murad SPX</b> {w[:6]}...{w[-4:]}\n{'🟢 شراء' if tx['to'].lower()==w.lower() else '🔴 بيع'} {val:,.0f} SPX\n🔗 {h[:10]}...\n")
            except: pass
            time.sleep(0.3)
        if alerts: return alerts
    for w in WALLETS:
        try:
            url=f"https://deep-index.moralis.io/api/v2.2/{w}/history?chain=bsc&order=DESC&limit=5"
            r=requests.get(url,headers={"X-API-Key":MORALIS},timeout=15).json()
            for tx in r.get("result",[]):
                h=tx.get("hash")
                if h in seen_tx: continue
                seen_tx.add(h)
                val=int(tx.get("value","0"))/1e18
                if val<0.05: continue
                to_addr=tx.get("to_address","")[:10]
                alerts.append(f"💰 <b>محفظة حوت</b> {w[:6]}...{w[-4:]}\n🏢 دخلت: {to_addr}... (BSC)\n💵 قيمة الدخول: {val:.3f} BNB (${val*600:.0f})\n📅 التاريخ: {datetime.now().strftime('%m/%d %I:%M%p')}\n🔗 Strike/Hash: {h[:12]}...\n")
        except: pass
        time.sleep(1)
    return alerts

def sniper_loop():
    while True:
        try:
            for sym in TICKERS:
                try:
                    ysym="^SPX" if sym=="^GSPC" else sym
                    tk=yf.Ticker(ysym)
                    if not tk.options: continue
                    for exp in tk.options[:2]:
                        try:
                            chain=tk.option_chain(exp).calls
                            chain=chain[(chain['volume']>1500)&(chain['lastPrice']>=0.90)]
                            for _,r in chain.iterrows():
                                key=f"{sym}{r['strike']}{exp}{int(r['volume']/100)*100}"
                                if key in sent: continue
                                prem=float(r['openInterest']*r['lastPrice']*100)
                                sc=score(int(r['volume']),int(r['openInterest']),float(r['lastPrice']),prem)
                                if sc>=90 and prem>=800000:
                                    sent.add(key); d=NAMES.get(sym,sym)
                                    send(f"🚨 <b>حوت لحظي SCORE {sc}/100</b>\n\n👑 <b>{d} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b>\n📅 {exp}\n💰 ${prem:,.0f} Vol:{int(r['volume']):,}\n\n🎯 دخول ${r['lastPrice']:.2f} هدف ${r['lastPrice']*1.8:.2f}")
                                    if prem>1000000: check_double_monster(d, "GOLDEN", prem/1000)
                        except: continue
                except: continue
            time.sleep(60)
        except: time.sleep(30)

def main_loop():
    time.sleep(3)
    send("✅ <b>البوت الوحش النهائي شغال V10 - 19 شركة HALAL</b>\n\n🚀 HERO\n👑 GOLDEN\n🐋 SWEEPS\n💥 GAMMA\n⏰ POWER\n💰 محافظ\n🚨 لحظي\n🚨🚨 مزدوج\n\n/strikes - كل الطلبات\n/wallets - المحافظ\n/status - الحالة")
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
                if "/strikes" in txt or "/start" in txt:
                    send(f"⏳ اجيب لك كل الطلبات... {datetime.now().strftime('%H:%M')}")
                    h,g,s,ga,p=get_all()
                    if h: send("🚀 <b>HERO ZERO:</b>\n\n"+"".join([x[1] for x in h]))
                    if g: send("👑 <b>GOLDEN:</b>\n\n"+"".join([x[1] for x in g]))
                    if s: send("🐋 <b>SWEEPS:</b>\n\n"+"".join([x[1] for x in s]))
                    if ga: send("💥 <b>GAMMA WALL:</b>\n\n"+"".join([x[1] for x in ga[:5]]))
                    if p: send("⏰ <b>POWER HOUR:</b>\n\n"+"".join([x[1] for x in p[:5]]))
                    wa=check_wallets()
                    if wa: send("💰 <b>محافظ الحيتان (اخر دخول):</b>\n\n"+"\n".join(wa[:5]))
                    else: send(f"💰 <b>المحافظ:</b> يراقب {len(WALLETS)} محافظ - لا يوجد دخول جديد")
                elif "/wallets" in txt:
                    wa=check_wallets()
                    send("💰 <b>تقرير المحافظ:</b>\n\n"+"\n".join(wa) if wa else f"💰 يراقب {len(WALLETS)} محافظ")
                elif "/status" in txt:
                    send(f"✅ شغال V10\n👀 يراقب {len(TICKERS)} شركة\n💰 يراقب {len(WALLETS)} محفظة\n⏰ {datetime.now().strftime('%m/%d %I:%M%p')}")
        except Exception as e:
            print(f"LOOP ERR {e}"); time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
