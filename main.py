from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
from collections import deque

app = Flask(__name__)
@app.route('/')
def home(): return "V14 FIXED"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX"}
sent = {}
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}, "WHALE": {}}
double_sent = {}
exit_sent = {}
oi_memory = {}
SPX_CONTRACT = "0xE0f63A315d53ff878dCF4d31D367a67b6479a9f4F"

message_queue = deque()
MAX_PER_RUN = 25
DELAY = 1.5

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
    if key not in sent or time.time() - sent[key] > 21600:
        sent[key] = time.time()
        return True
    return False

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600: del monster_memory[k][tk]

    combos = [
        (["GOLDEN","POWER"], "GOLDEN+POWER"),
        (["GOLDEN","GAMMA"], "GOLDEN+GAMMA"),
        (["HERO","SWEEPS"], "HERO+SWEEPS"),
        (["HERO","GAMMA"], "HERO+GAMMA"),
        (["GOLDEN","HERO"], "GOLDEN+HERO"),
        (["WHALE","GOLDEN"], "WHALE+GOLDEN"),
        (["WHALE","POWER"], "WHALE+POWER"),
        (["WHALE","SWEEPS"], "WHALE+SWEEPS")
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
                            queue_send(f"WHALE MONSTER {market} - {desc} Strike {ref['strike']:.0f}{ref['type']} {ref['exp']}")
            else:
                if "WHALE" in monster_memory and monster_memory["WHALE"]:
                    for w_ticker in list(monster_memory["WHALE"].keys()):
                        times = [monster_memory["WHALE"][w_ticker]["time"], monster_memory[typ][ticker]["time"]]
                        if max(times)-min(times) < 3600:
                            base_key = f"DOUBLE_WHALE_{ticker}_{typ}_{int(time.time()/3600)}"
                            if base_key in double_sent: continue
                            double_sent[base_key] = time.time()
                            queue_send(f"WHALE MONSTER {ticker} - {desc} Strike {strike:.0f}{opt_type} {exp} ${price:.2f}")
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
                queue_send(f"DOUBLE MONSTER {ticker} - {desc} Strike {ref['strike']:.0f}{ref['type']} {ref['exp']} ${ref['price']:.2f}")

def check_exit_early(dname, strike, exp, otype_s, curr_price, vol, oi):
    key = f"{dname}_{int(strike)}{otype_s}_{exp}"
    prev_oi = oi_memory.get(key)
    oi_memory[key] = oi
    if prev_oi and prev_oi > 100:
        oi_drop = (prev_oi - oi) / prev_oi
        if oi_drop > 0.15 and vol > 800:
            if key in exit_sent and time.time() - exit_sent[key] < 10800: return
            exit_sent[key] = time.time()
            msg = f"EXIT EARLY {dname} {strike:.0f}{otype_s} {exp} OI {prev_oi} -> {oi} drop {oi_drop*100:.0f}% Vol {vol} Price ${curr_price:.2f}"
            queue_send(msg)

def scan_option(tk_symbol, exp, opt_type="calls"):
    try:
        tk = yf.Ticker("^SPX" if tk_symbol=="^GSPC" else tk_symbol)
        return getattr(tk.option_chain(exp), opt_type)
    except: return None

def sniper_loop():
    while True:
        try:
            today = datetime.now().date()
            all_found = []
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
                                    check_exit_early(dname, float(r['strike']), exp, otype_s, price, vol, oi)
                                    if prem>1000000 and vol>3000:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"GOLDEN {dname} {r['strike']:.0f}{otype_s} {exp} ${price:.2f} ${prem:,.0f}", dname, "GOLDEN", prem/1000, float(r['strike']), exp, price, otype_s, prem))
                                    if vol/ max(oi,1) >1.2 and vol>800:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"SWEEPS {dname} {r['strike']:.0f}{otype_s} {exp} ${price:.2f}", dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    if ed==today and 0.90 <= price <= 2.5 and vol>1000:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"HERO ZERO {dname} {r['strike']:.0f}{otype_s} {exp} 0DTE ${price:.2f}", dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                                    if (ed-today).days<=4 and 0.3 <= price <= 2.0 and vol>1000:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}"
                                        if is_new(key):
                                            all_found.append((vol, f"POWER {dname} {r['strike']:.0f}{otype_s} {exp} ${price:.2f}", dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol))
                        except: continue
                except: continue
            all_found = sorted(all_found, key=lambda x: x[0], reverse=True)[:MAX_PER_RUN]
            for item in all_found:
                vol, msg, dname, typ, vk, strike, exp, price, otype_s, prem = item
                queue_send(msg)
                check_double_monster(dname, typ, vk, strike, exp, price, otype_s, prem)
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
                is_buy = tx['to'].lower()==w.lower()
                tx_time = datetime.fromtimestamp(int(tx['timeStamp'])).strftime('%m/%d %I:%M%p')
                if is_buy:
                    msg = f"WHALE IN {w[:6]}...{w[-4:]} BUY {val:,.0f} SPX {tx_time}"
                    check_double_monster(f"WHALE_{w[:6]}", "WHALE", val, 0, "", 0, "C", val*1000)
                else:
                    msg = f"WHALE EXIT {w[:6]}...{w[-4:]} SELL {val:,.0f} SPX Contract {SPX_CONTRACT[:6]} Date {tx_time} Tx {h[:10]}"
                alerts.append(msg)
        except: pass
        time.sleep(0.3)
    return alerts

def main_loop():
    time.sleep(3)
    threading.Thread(target=send_worker, daemon=True).start()
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":"V14 LIVE FIXED"}, timeout=15)
    except: pass
    threading.Thread(target=sniper_loop,daemon=True).start()
    off=0; last_wallet=0
    while True:
        try:
            if time.time()-last_wallet>30:
                for a in check_wallets(): queue_send(a)
                last_wallet=time.time()
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]
                txt=u.get("message",{}).get("text","").lower()
                if "/start" in txt:
                    t_count = len(TICKERS)
                    w_count = len(WALLETS)
                    q_count = len(message_queue)
                    now = datetime.now().strftime('%m/%d %I:%M%p')
                    queue_send(f"V14 LIVE Bot Working {t_count} tickers {w_count} whales queue {q_count} time {now} /status /whales /top /clear")
                if "/status" in txt:
                    t_count = len(TICKERS)
                    w_count = len(WALLETS)
                    q_count = len(message_queue)
                    now = datetime.now().strftime('%m/%d %I:%M%p')
                    queue_send(f"V14 LIVE Status {t_count} companies {w_count} whales queue {q_count} {now}")
                if "/clear" in txt:
                    sent.clear(); double_sent.clear(); exit_sent.clear(); message_queue.clear()
                    queue_send("Memory cleared")
                if "/whales" in txt:
                    if WALLETS:
                        w_list = "\n".join([f"{w[:6]}...{w[-4:]}" for w in WALLETS])
                        queue_send(f"Whales {len(WALLETS)} \n{w_list}")
                    else:
                        queue_send("No wallets")
                if "/top" in txt:
                    queue_send(f"Queue {len(message_queue)} Double {len(double_sent)} Exit {len(exit_sent)}")
        except Exception as e:
            print(f"ERR {e}")
            time.sleep(3)

threading.Thread(target=main_loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
