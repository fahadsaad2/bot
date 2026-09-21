from flask import Flask
import os, requests, threading, time
from datetime import datetime, timedelta
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)
@app.route('/')
def home(): return "V15 FIXED"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

TICKERS = ["SPX","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX", "SPX":"SPX"}
sent = {}
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}, "WHALE": {}}
double_sent = {}
exit_sent = {}
oi_memory = {}
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"

message_queue = deque()
MAX_PER_RUN = 25
DELAY = 1.2

def send_worker():
    while True:
        if message_queue:
            t = message_queue.popleft()
            try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
            except: pass
            time.sleep(DELAY)
        else: time.sleep(0.2)

def queue_send(t):
    if len(message_queue) < 100: message_queue.append(t)

def is_new(key):
    if key not in sent or time.time() - sent[key] > 3600: # غيرتها من 6 ساعات لساعة وحدة عشان يرسل اكثر
        sent[key] = time.time()
        return True
    return False

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    # نفس دالتك القديمة بدون تغيير
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]
    combos = [
        (["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER تدبيلة"),
        (["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA انفجار"),
        (["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS"),
        (["HERO","GAMMA"], "💣💥 HERO+GAMMA"),
        (["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO"),
        (["WHALE","GOLDEN"], "🐋👑 حوت SPX + GOLDEN SPY/QQQ"),
        (["WHALE","POWER"], "🐋⚡ حوت SPX + POWER"),
        (["WHALE","SWEEPS"], "🐋🌊 حوت SPX + SWEEPS")
    ]
    for combo, desc in combos:
        if typ not in combo: continue
        if "WHALE" in combo:
            if ticker not in ["SPY","QQQ","SPX","^GSPC"] and typ!= "WHALE": continue
            if typ == "WHALE":
                for market in ["SPY","QQQ","SPX"]:
                    if market in monster_memory[combo[1]]:
                        times = [monster_memory["WHALE"][ticker]["time"], monster_memory[combo[1]][market]["time"]]
                        if max(times)-min(times) < 3600:
                            ref = monster_memory[combo[1]][market]
                            base_key = f"DOUBLE_WHALE_{market}_{combo[1]}_{int(time.time()/3600)}"
                            if base_key in double_sent: continue
                            double_sent[base_key] = time.time()
                            queue_send(f"🚨🚨🚨 <b>وحش الحيتان V15</b> 🚨🚨🚨\n\n🎯 <b>{market} - {desc}</b>\n💰 حوت SPX اشترى قبل دقايق\n💥 سترايك: {ref['strike']:.0f}{ref['type']} | {ref['exp']}\n🔥 سيولة الحيتان داخلة - ادخل NOW")
            else:
                if "WHALE" in monster_memory and monster_memory["WHALE"]:
                    for w_ticker in list(monster_memory["WHALE"].keys()):
                        times = [monster_memory["WHALE"][w_ticker]["time"], monster_memory[typ][ticker]["time"]]
                        if max(times)-min(times) < 3600:
                            base_key = f"DOUBLE_WHALE_{ticker}_{typ}_{int(time.time()/3600)}"
                            if base_key in double_sent: continue
                            double_sent[base_key] = time.time()
                            queue_send(f"🚨🚨🚨 <b>وحش الحيتان V15</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💰 حوت SPX اشترى قبل دقايق\n💥 سترايك: {strike:.0f}{opt_type} | {exp}\n💵 ${price:.2f}\n🔥 ادخل NOW")
                            return
        if all(ticker in monster_memory[c] for c in combo if c!= "WHALE"):
            if "WHALE" in combo: continue
            times = [monster_memory[c][ticker]["time"] for c in combo]
            if max(times)-min(times) < 3600:
                ref = monster_memory["GOLDEN"][ticker] if "GOLDEN" in combo else monster_memory[combo[0]][ticker]
                prem_bucket = int(ref['premium'] / 50000) * 50000
                base_key = f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(ref['strike'])}{ref['type']}_{ref['exp']}_{prem_bucket}"
                if base_key in double_sent: continue
                total = sum(monster_memory[c][ticker]["vol"] for c in combo if c in monster_memory and ticker in monster_memory[c])
                double_sent[base_key] = time.time()
                queue_send(f"🚨🚨🚨 <b>الوحش المزدوج V15</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 سترايك: {ref['strike']:.0f}{ref['type']}\n📅 {ref['exp']}\n💵 ${ref['price']:.2f}\n💰 ${ref['premium']:,.0f}\n💰 مجمع ${total:,.0f}k\n🔥 ادخل NOW")

def check_exit_early(dname, strike, exp, otype_s, curr_price, vol, oi):
    key = f"{dname}_{int(strike)}{otype_s}_{exp}"
    prev_oi = oi_memory.get(key)
    oi_memory[key] = oi
    if prev_oi and prev_oi > 100:
        oi_drop = (prev_oi - oi) / prev_oi
        if oi_drop > 0.15 and vol > 500:
            if key in exit_sent and time.time() - exit_sent[key] < 10800: return
            exit_sent[key] = time.time()
            txt = f"🚨🚨 <b>خروج مبكر - حوت قفل عقده</b> 🚨🚨\n\n🎯 <b>{dname} {strike:.0f}{otype_s}</b>\n📅 {exp}\n📉 OI {prev_oi:,} -> {oi:,} نقص {oi_drop*100:.0f}%\n📦 فوليوم اغلاق {vol:,}\n💵 ${curr_price:.2f}\n⚠️ الحوت طلع قبل نزول السعر"
            queue_send(txt)

def scan_option(tk_symbol, exp, opt_type="calls"):
    try:
        # FIX: SPX ticker الحقيقي هو SPX
        y_sym = "SPX" if tk_symbol in ["^GSPC","SPX"] else tk_symbol
        tk = yf.Ticker(y_sym)
        return getattr(tk.option_chain(exp), opt_type)
    except: return None

def sniper_loop():
    et_tz = pytz.timezone('US/Eastern')
    while True:
        try:
            today_et = datetime.now(et_tz).date() # FIX تاريخ امريكا
            all_found = []
            for sym in TICKERS:
                try:
                    ysym = "SPX" if sym in ["^GSPC","SPX"] else sym
                    tk = yf.Ticker(ysym)
                    if not tk.options: continue
                    dname = NAMES.get(sym,sym)
                    for exp in tk.options[:3]:
                        try:
                            ed = datetime.strptime(exp,"%Y-%m-%d").date()
                            for otype in ["calls","puts"]:
                                chain = scan_option(sym, exp, otype)
                                if chain is None or chain.empty: continue
                                # FIX: عشان yfinance يرجع NaN
                                chain = chain.fillna(0)
                                chain = chain[(chain['openInterest']>50) & (chain['lastPrice']>=0.20)]
                                if chain.empty: continue
                                otype_s = "C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol = int(r['volume']) if r['volume'] else 0
                                    oi = int(r['openInterest'])
                                    price = float(r['lastPrice'])
                                    if vol < 300: continue # FIX نزلتها من 800 ل 300
                                    prem = float(oi*price*100) if oi>0 else float(vol*price*100)
                                    prem_vol = float(vol*price*100)
                                    check_exit_early(dname, float(r['strike']), exp, otype_s, price, vol, oi)
                                    if prem>500000 and vol>1000: # نزلتها من 1M
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"👑 <b>GOLDEN</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem:,.0f}", dname, "GOLDEN", prem/1000, float(r['strike']), exp, price, otype_s, prem))
                                    if vol/ max(oi,1) >1.2 and vol>500:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"🐋 <b>SWEEPS</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f}", dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    # FIX HERO ZERO
                                    if ed==today_et and 0.50 <= price <= 3.5 and vol>300:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"🚀 <b>HERO ZERO</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp} 0DTE\n💵 ${price:.2f}", dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    if (ed-today_et).days<=4 and 0.3 <= price <= 2.5 and vol>500:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"⏰ <b>POWER</b>\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}", dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                        except: continue
                except: continue
            all_found = sorted(all_found, key=lambda x: x[0], reverse=True)[:MAX_PER_RUN]
            for item in all_found:
                vol, msg, dname, typ, vk, strike, exp, price, otype_s, prem = item
                queue_send(msg)
                check_double_monster(dname, typ, vk, strike, exp, price, otype_s, prem)
            time.sleep(45)
        except: time.sleep(15)

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
