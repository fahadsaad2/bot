from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V16 MEGA MOMENTUM"
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","SPX"]
NAMES = {"^GSPC":"SPX", "SPX":"SPX"}
sent = {}
seen_tx = set()
monster_memory = {"GOLDEN": {}, "MEGA": {}, "ULTRA": {}, "MOMENTUM": {}, "MONEY": {}, "HERO": {}, "POWER": {}, "SWEEPS": {}, "WHALE": {}}
double_sent = {}
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"
message_queue = deque()

def send_worker():
    while True:
        if message_queue:
            t = message_queue.popleft()
            try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
            except: pass
            time.sleep(1.2)
        else: time.sleep(0.2)

def queue_send(t):
    if len(message_queue) < 200: message_queue.append(t)

def is_new(key, h=1):
    if key not in sent or time.time() - sent[key] > h*3600:
        sent[key] = time.time()
        return True
    return False

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]
    combos = [
        (["MEGA","MOMENTUM"], "🐋🔥 حوت + زخم انفجاري"),
        (["MEGA","MONEY"], "🐋💸 حوت + سيولة ضخمة"),
        (["ULTRA","MOMENTUM"], "🐳🚀 خارق + زخم"),
        (["GOLDEN","MOMENTUM"], "👑🔥 قولدن + زخم"),
        (["GOLDEN","POWER"], "GOLDEN+POWER تدبيلة"),
        (["GOLDEN","HERO"], "GOLDEN+HERO"),
        (["GOLDEN","SWEEPS"], "GOLDEN+SWEEPS"),
        (["WHALE","GOLDEN"], "حوت SPX + GOLDEN"),
        (["WHALE","MEGA"], "حوت SPX + MEGA WHALE"),
    ]
    for combo, desc in combos:
        if typ not in combo: continue
        if "WHALE" in combo:
            other = combo[1] if combo[0]=="WHALE" else combo[0]
            if typ == "WHALE":
                for market in ["SPY","QQQ","SPX","AAPL","NVDA","TSLA"]:
                    if market in monster_memory.get(other, {}):
                        times = [monster_memory["WHALE"][ticker]["time"], monster_memory[other][market]["time"]]
                        if max(times)-min(times) < 3600:
                            base_key = f"DOUBLE_WHALE_{market}_{other}_{int(time.time()/1800)}"
                            if base_key in double_sent: continue
                            double_sent[base_key] = time.time()
                            ref = monster_memory[other][market]
                            queue_send(f"🚨🚨🚨 <b>وحش الحيتان V16</b> 🚨🚨🚨\n\n🎯 <b>{market} - {desc}</b>\n💥 سترايك: {ref['strike']:.0f}{ref['type']} | {ref['exp']}\n💵 ${ref['price']:.2f}\n🔥 ادخل NOW")
            else:
                if monster_memory["WHALE"]:
                    for w_ticker in list(monster_memory["WHALE"].keys()):
                        times = [monster_memory["WHALE"][w_ticker]["time"], monster_memory[typ][ticker]["time"]]
                        if max(times)-min(times) < 3600:
                            base_key = f"DOUBLE_WHALE_{ticker}_{typ}_{int(time.time()/1800)}"
                            if base_key in double_sent: continue
                            double_sent[base_key] = time.time()
                            queue_send(f"🚨🚨🚨 <b>وحش الحيتان V16</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💰 حوت SPX قبل دقايق\n💥 سترايك: {strike:.0f}{opt_type} | {exp}\n💵 ${price:.2f}\n🔥 ادخل NOW")
                            return
        else:
            if all(ticker in monster_memory[c] for c in combo):
                times = [monster_memory[c][ticker]["time"] for c in combo]
                if max(times)-min(times) < 3600:
                    ref = monster_memory[combo[0]][ticker]
                    if "MEGA" in monster_memory and ticker in monster_memory["MEGA"]:
                        ref = monster_memory["MEGA"][ticker]
                    elif "ULTRA" in monster_memory and ticker in monster_memory["ULTRA"]:
                        ref = monster_memory["ULTRA"][ticker]
                    base_key = f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(ref['strike'])}_{ref['exp']}"
                    if base_key in double_sent: continue
                    total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                    double_sent[base_key] = time.time()
                    queue_send(f"🚨🚨🚨 <b>الوحش المزدوج V16 - زخم + سيولة</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 سترايك: {ref['strike']:.0f}{ref['type']}\n📅 {ref['exp']}\n💵 ${ref['price']:.2f}\n💰 مجمع ${total:,.0f}k\n🔥 ادخل NOW - انفجار")

def check_wallets():
    alerts=[]
    if not WALLETS or not ETHERSCAN_API: return alerts
    for w in WALLETS[:8]:
        try:
            url=f"https://api.etherscan.io/api?module=account&action=tokentx&contractaddress={SPX_CONTRACT}&address={w}&sort=desc&apikey={ETHERSCAN_API}"
            data=requests.get(url,timeout=15).json()
            if data.get("status")!="1" or not data["result"]:
                time.sleep(0.3)
                continue
            tx=data["result"][0]
            if tx["hash"] in seen_tx:
                time.sleep(0.3)
                continue
            seen_tx.add(tx["hash"])
            val=float(tx.get("value",0))/10**18
            is_buy = tx['to'].lower()==w.lower()
            tx_time = datetime.fromtimestamp(int(tx['timeStamp'])).strftime('%m/%d %I:%M%p')
            if is_buy:
                msg = f"💰 <b>حوت {w[:6]}...{w[-4:]}</b>\n🟢 شراء {val:,.0f} SPX\n📅 {tx_time}"
                check_double_monster(f"{w[:6]}", "WHALE", val/1000, 0, tx_time, 0, "C", val)
            else:
                msg = f"🚨 <b>خروج حوت</b>\n🔴 بيع {val:,.0f} SPX\n📅 {tx_time}"
            alerts.append(msg)
        except: pass
        time.sleep(0.3)
    return alerts

def sniper_loop():
    et_tz = pytz.timezone('US/Eastern')
    while True:
        try:
            today_et = datetime.now(et_tz).date()
            all_found = []
            scanned = 0
            for sym in TICKERS:
                try:
                    ysym = "^GSPC" if sym == "SPX" else sym
                    tk = yf.Ticker(ysym)
                    if not tk.options: continue
                    dname = NAMES.get(sym,sym)
                    for exp in tk.options[:2]:
                        try:
                            ed = datetime.strptime(exp,"%Y-%m-%d").date()
                            for otype in ["calls","puts"]:
                                try: chain = getattr(tk.option_chain(exp), otype)
                                except: continue
                                if chain is None or chain.empty: continue
                                chain = chain.fillna(0)
                                chain = chain[(chain['openInterest']>=5) & (chain['lastPrice']>=0.05)]
                                if chain.empty: continue
                                otype_s = "C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol = int(r['volume']) if r['volume'] else 0
                                    oi = int(r['openInterest']) if r['openInterest'] else 0
                                    price = float(r['lastPrice']) if r['lastPrice'] else 0
                                    scanned+=1
                                    if vol < 50: continue
                                    prem_vol = float(vol*price*100)
                                    prem_oi = float(oi*price*100) if oi>0 else prem_vol

                                    if prem_vol > 2000000 and vol>300:
                                        key = f"ULTRA{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"🐳🐳🐳 <b>ULTRA WHALE {dname}</b>\n<b>{r['strike']:.0f}{otype_s} {exp}</b>\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f} Vol{vol} OI{oi}\n🔥 حوت خارق"
                                            all_found.append((prem_vol, msg, dname, "ULTRA", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    elif prem_vol > 800000 and vol>300:
                                        key = f"MEGA{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"🐋🐋 <b>MEGA WHALE {dname}</b>\n<b>{r['strike']:.0f}{otype_s} {exp}</b>\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f} Vol{vol}\n💸 سيولة ضخمة"
                                            all_found.append((prem_vol, msg, dname, "MEGA", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    elif prem_oi>150000 and vol>200:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            msg = f"👑 <b>GOLDEN {dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem_oi:,.0f} Vol{vol}"
                                            all_found.append((vol, msg, dname, "GOLDEN", prem_oi/1000, float(r['strike']), exp, price, otype_s, prem_oi))

                                    if vol / max(oi,1) > 3.0 and vol>500 and price>=0.5:
                                        key = f"MOMENTUM{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"🔥🔥 <b>MOMENTUM {dname}</b>\n<b>{r['strike']:.0f}{otype_s} {exp}</b>\n💵 ${price:.2f}\n📈 Vol {vol} vs OI {oi} = {vol/max(oi,1):.1f}x\n💰 ${prem_vol:,.0f}\n🚀 زخم انفجاري"
                                            all_found.append((vol*2, msg, dname, "MOMENTUM", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))

                                    if prem_vol > 500000 and vol>400 and vol / max(oi,1) > 1.5:
                                        key = f"MONEY{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"💸💸 <b>MONEY FLOW {dname}</b>\n<b>{r['strike']:.0f}{otype_s} {exp}</b>\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f} داخلة الان\n📊 Vol{vol} OI{oi}"
                                            all_found.append((prem_vol*1.2, msg, dname, "MONEY", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))

                                    if vol / max(oi,1) > 1.2 and vol>100:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            msg = f"🌊 <b>SWEEPS {dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f}"
                                            all_found.append((vol, msg, dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    if ed==today_et and 0.10 <= price <= 6.0 and vol>50:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            msg = f"🚀 <b>HERO 0DTE {dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}"
                                            all_found.append((vol, msg, dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    if (ed-today_et).days<=4 and 0.20 <= price <= 4.0 and vol>100:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            msg = f"⚡ <b>POWER {dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}"
                                            all_found.append((vol, msg, dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                        except: continue
                except: continue
            if not all_found:
                if is_new("HEARTBEAT", h=0.25):
                    queue_send(f"💓 البوت شغال - فحص {scanned} عقد - السوق هادي")
            all_found = sorted(all_found, key=lambda x: x[0], reverse=True)[:30]
            for item in all_found:
                vol, msg, dname, typ, vk, strike, exp, price, otype_s, prem = item
                queue_send(msg)
                check_double_monster(dname, typ, vk, strike, exp, price, otype_s, prem)
            time.sleep(30)
        except Exception as e:
            print(f"SNIPER ERR {e}")
            time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker, daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":"✅ <b>V16 MEGA MOMENTUM شغال</b>\n🐋 MEGA 800k+\n🐳 ULTRA 2M+\n🔥 MOMENTUM\n💸 MONEY FLOW\n/test", "parse_mode":"HTML"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0
    last_wallet=0
    while True:
        try:
            if time.time()-last_wallet>30:
                for a in check_wallets(): queue_send(a)
                last_wallet=time.time()
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/test" in txt: queue_send("🧪 V16 شغال - MEGA+MOMENTUM+MONEY ✅")
                if "/status" in txt: queue_send(f"✅ LIVE طابور {len(message_queue)} ذاكرة {sum(len(v) for v in monster_memory.values())}")
                if "/clear" in txt: sent.clear(); double_sent.clear(); message_queue.clear(); queue_send("✅ تم المسح")
                if "/memory" in txt:
                    mem_txt = "\n".join([f"{k}: {len(v)}" for k,v in monster_memory.items() if len(v)>0])
                    queue_send(f"🧠 الذاكرة:\n{mem_txt if mem_txt else 'فاضية'}")
        except: time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
