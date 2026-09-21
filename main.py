from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz
app = Flask(__name__)
@app.route('/')
def home(): return "V16.4 FINAL - FIXED STRIKE"
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
        (["GOLDEN","HERO"], "GOLDEN+HERO تدبيلة 0DTE"),
        (["GOLDEN","POWER"], "GOLDEN+POWER تدبيلة"),
        (["GOLDEN","SWEEPS"], "GOLDEN+SWEEPS وحش"),
        (["HERO","SWEEPS"], "HERO+SWEEPS"),
        (["GOLDEN","MOMENTUM"], "GOLDEN+MOMENTUM زخم"),
        (["MEGA","MOMENTUM"], "🐋🔥 حوت + زخم انفجاري"),
        (["ULTRA","MOMENTUM"], "🐳🚀 خارق + زخم"),
        (["MEGA","MONEY"], "🐋💸 حوت + سيولة ضخمة"),
        (["WHALE","GOLDEN"], "حوت SPX + GOLDEN"),
        (["WHALE","MEGA"], "حوت SPX + MEGA WHALE"),
    ]
    for combo, desc in combos:
        if typ not in combo: continue
        if all(ticker in monster_memory[c] for c in combo):
            times = [monster_memory[c][ticker]["time"] for c in combo]
            if max(times)-min(times) < 3600:
                ref = monster_memory[combo[0]][ticker]
                for c in combo:
                    if monster_memory[c][ticker]["premium"] > ref["premium"]: ref = monster_memory[c][ticker]
                base_key = f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(time.time()/600)}"
                if base_key in double_sent: continue
                double_sent[base_key] = time.time()
                total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                queue_send(f"🚨🚨🚨 <b>الوحش المزدوج V16.4</b> 🚨🚨🚨\n\n🎯 <b>{ticker} - {desc}</b>\n💥 سترايك: {ref['strike']:g}{ref['type']}\n📅 {ref['exp']}\n💵 ${ref['price']:.2f}\n💰 مجمع ${total:,.0f}k\n🔥 ادخل NOW")

def check_wallets():
    alerts=[]
    if not WALLETS or not ETHERSCAN_API: return alerts
    for w in WALLETS[:8]:
        try:
            url=f"https://api.etherscan.io/api?module=account&action=tokentx&contractaddress={SPX_CONTRACT}&address={w}&sort=desc&apikey={ETHERSCAN_API}"
            data=requests.get(url,timeout=15).json()
            if data.get("status")!="1" or not data["result"]: time.sleep(0.3); continue
            tx=data["result"][0]
            if tx["hash"] in seen_tx: time.sleep(0.3); continue
            seen_tx.add(tx["hash"])
            val=float(tx.get("value",0))/10**18
            is_buy = tx['to'].lower()==w.lower()
            tx_time = datetime.fromtimestamp(int(tx['timeStamp'])).strftime('%m/%d %I:%M%p')
            if is_buy:
                msg = f"💰 <b>حوت {w[:6]}...{w[-4:]}</b>\n🟢 شراء {val:,.0f} SPX\n📅 {tx_time}"
                check_double_monster(f"{w[:6]}", "WHALE", val/1000, 0, tx_time, 0, "C", val)
            else: msg = f"🚨 <b>خروج حوت</b>\n🔴 بيع {val:,.0f} SPX\n📅 {tx_time}"
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
                                            msg = f"🐳🐳🐳 <b>ULTRA WHALE {dname}</b>\n<b>{r['strike']:g}{otype_s} {exp}</b>\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f} Vol{vol} OI{oi}\n🔥 حوت خارق"
                                            all_found.append((prem_vol, msg, dname, "ULTRA", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    elif prem_vol > 800000 and vol>300:
                                        key = f"MEGA{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"🐋🐋 <b>MEGA WHALE {dname}</b>\n<b>{r['strike']:g}{otype_s} {exp}</b>\n💵 ${price:.2f}\n💰 ${prem_vol:,.0f} Vol{vol}\n💸 سيولة ضخمة"
                                            all_found.append((prem_vol, msg, dname, "MEGA", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    elif prem_oi>150000 and vol>200:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            msg = f"👑 <b>GOLDEN {dname} {r['strike']:g}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 ${prem_oi:,.0f} Vol{vol}"
                                            all_found.append((vol, msg, dname, "GOLDEN", prem_oi/1000, float(r['strike']), exp, price, otype_s, prem_oi))
                                    if vol / max(oi,1) > 3.0 and vol>500 and price>=0.5:
                                        key = f"MOMENTUM{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key, h=0.5):
                                            msg = f"🔥🔥 <b>MOMENTUM {dname}</b>\n<b>{r['strike']:g}{otype_s} {exp}</b>\n💵 ${price:.2f}\n📈 Vol {vol} vs OI {oi} = {vol/max(oi,1):.1f}x\n💰 ${prem_vol:,.0f}\n🚀 زخم انفجاري"
                                            all
