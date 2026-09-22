from flask import Flask
import os
import requests
import threading
import time
from datetime import datetime
import yfinance as yf
from collections import deque
import pytz

app = Flask(__name__)

@app.route("/")
def home():
    return "V21 RSI 40/70 FIXED"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TICKERS = ["SPY","QQQ","IWM","DIA","^GSPC","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK","NFLX","PLTR"]
NAMES = {"^GSPC": "SPX", "SPY": "SPX"}

monster_memory = {"GOLDEN": {}, "MEGA": {}, "ULTRA": {}, "MOMENTUM": {}, "HERO": {}}
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
            try:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": t, "parse_mode": "HTML"}, timeout=15)
            except:
                pass
            time.sleep(1.2)
        else:
            time.sleep(0.2)

def queue_send(t):
    if len(message_queue) < 200:
        message_queue.append(t)

def get_rsi(sym):
    try:
        if sym in rsi_cache and time.time() - rsi_cache[sym]["t"] < 300:
            return rsi_cache[sym]["v"]
        tk = yf.Ticker(sym)
        hist = tk.history(period="14d")
        if len(hist) < 14:
            return 50
        delta = hist["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        val = float(rsi.iloc[-1])
        rsi_cache[sym] = {"v": val, "t": time.time()}
        return val
    except:
        return 50

def is_buy_signal(row):
    try:
        bid = row.get("bid", 0)
        ask = row.get("ask", 0)
        last = row.get("lastPrice", 0)
        if ask > 0 and bid > 0:
            return last >= (bid + ask) / 2
        return True
    except:
        return True

def is_sell_signal(row):
    try:
        bid = row.get("bid", 0)
        ask = row.get("ask", 0)
        last = row.get("lastPrice", 0)
        if ask > 0 and bid > 0:
            return last <= (bid + ask) / 2 + 0.02
        return False
    except:
        return False

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0, row=None):
    global last_reset_day
    if datetime.now().day!= last_reset_day:
        sent_today.clear()
        double_sent.clear()
        last_reset_day = datetime.now().day

    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium, "row": row}

    combos = [(["MEGA", "MOMENTUM"], "MEGA+MOMENTUM"), (["ULTRA", "MOMENTUM"], "ULTRA+MOMENTUM"), (["GOLDEN", "MOMENTUM"], "GOLDEN+MOMENTUM"), (["GOLDEN", "HERO"], "GOLDEN+HERO"), (["GOLDEN", "ULTRA"], "GOLDEN+ULTRA")]

    for combo, desc in combos:
        if typ not in combo:
            continue
        if not all(ticker in monster_memory[c] for c in combo):
            continue

        times = [monster_memory[c][ticker]["time"] for c in combo]
        if max(times) - min(times) > 600:
            continue

        ref = monster_memory[combo[0]][ticker]
        for c in combo:
            if monster_memory[c][ticker]["premium"] > ref["premium"]:
                ref = monster_memory[c][ticker]

        contract_key = f"{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}"
        if contract_key in sent_today:
            continue

        base_key = f"DOUBLE_{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}_{'_'.join(combo)}"
        if base_key in double_sent:
            continue

        if not (1.0 <= ref["price"] <= 10.0):
            continue

        if not is_buy_signal(ref.get("row", {})):
            continue

        try:
            iv = float(ref["row"].get("impliedVolatility", 0)) * 100
            if not (35 <= iv <= 80):
                continue
        except:
            continue

        rsi = get_rsi("^GSPC" if "SPX" in ticker else ticker)
        opt_t = ref["type"]

        if opt_t == "C" and rsi > 40:
            continue
        if opt_t == "P" and rsi < 70:
            continue

        double_sent[base_key] = time.time()
        sent_today.add(contract_key)

        exit_key = f"{ticker}_{ref['strike']}_{ref['exp']}_{ref['type']}"
        exit_memory[exit_key] = {"entry": ref["price"], "high": ref["price"], "time": time.time(), "ticker": ticker, "strike": ref["strike"], "exp": ref["exp"], "type": ref["type"]}

        entry = ref["price"]
        t1 = entry * 1.5
        t2 = entry * 2.0
        t3 = entry * 3.0
        stop = entry * 0.7
        total = sum(monster_memory[c][ticker]["vol"] for c in combo)

        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)

        queue_send(f"🚨 دخول حوت 🚨\n\n🎯 {ticker} - {desc}\n💥 {ref['strike']:g}{ref['type']} - {ref['exp']}\n✅ IV {iv:.0f}%\n✅ RSI {rsi:.0f}\n✅ BUY\n\n💵 دخول: ${entry:.2f}\n🎯1: ${t1:.2f}\n🎯2: ${t2:.2f}\n🎯3: ${t3:.2f}\n🛑 وقف: ${stop:.2f}\n💰 ${total:,.0f}k\n⏰ {now_et.strftime('%H:%M:%S ET')}")

def check_exits():
    while True:
        try:
            time.sleep(20)
            if not exit_memory:
                continue
            for key, mem in list(exit_memory.items()):
                if time.time() - mem["time"] > 14400:
                    del exit_memory[key]
                    continue
                try:
                    sym = "SPY" if "SPX" in mem["ticker"] else mem["ticker"].split()[0]
                    tk = yf.Ticker("^GSPC" if sym == "SPX" else sym)
                    try:
                        chain = tk.option_chain(mem["exp"])
                    except:
                        continue
                    df = chain.calls if mem["type"] == "C" else chain.puts
                    row = df[df["strike"] == mem["strike"]]
                    if row.empty:
                        continue
                    r = row.iloc[0]
                    cur = float(r["lastPrice"])
                    vol = int(r["volume"])
                    if cur > mem["high"]:
                        exit_memory[key]["high"] = cur
                    drop_from_high = (mem["high"] - cur) / mem["high"] * 100 if mem["high"] > 0 else 0
                    is_heavy_sell = is_sell_signal(r) and vol > 300
                    if drop_from_high >= 20 and is_heavy_sell:
                        et_tz = pytz.timezone("US/Eastern")
                        now_et = datetime.now(et_tz)
                        profit = (cur - mem["entry"]) / mem["entry"] * 100
                        queue_send(f"🚨 خروج حوت\n🎯 {mem['ticker']} {mem['strike']:g}{mem['type']} {mem['exp']}\n📉 نزل {drop_from_high:.0f}%\n💵 {profit:+.0f}%\n⏰ {now_et.strftime('%H:%M:%S ET')}")
                        del exit_memory[key]
                    elif cur <= mem["entry"] * 0.7:
                        queue_send(f"🛑 وقف\n🎯 {mem['ticker']} {mem['strike']:g}{mem['type']} {mem['exp']}\n💵 ${mem['entry']:.2f} -> ${cur:.2f}")
                        del exit_memory[key]
                except:
                    continue
        except:
            time.sleep(5)

def sniper_loop():
    et_tz = pytz.timezone("US/Eastern")
    while True:
        try:
            today_et = datetime.now(et_tz).date()
            for sym in TICKERS:
                try:
                    tk = yf.Ticker(sym)
                    if not tk.options:
                        continue
                    dname = NAMES.get(sym, sym.replace("^GSPC", "SPX"))
                    for exp in tk.options[:3]:
                        try:
                            ed = datetime.strptime(exp, "%Y-%m-%d").date()
                            if not (0 <= (ed - today_et).days <= 4):
                                continue
                            is_0dte = (ed == today_et)
                            for otype in ["calls", "puts"]:
                                try:
                                    chain = getattr(tk.option_chain(exp), otype)
                                except:
                                    continue
                                if chain is None or chain.empty:
                                    continue
                                chain = chain.fillna(0)
                                for _, r in chain.iterrows():
                                    vol = int(r["volume"]) if r["volume"] else 0
                                    price = float(r["lastPrice"]) if r["lastPrice"] else 0
                                    if not (1.0 <= price <= 10.0):
                                        continue
                                    if vol < 50:
                                        continue
                                    oi = int(r["openInterest"]) if r["openInterest"] else 0
                                    prem_vol = float(vol * price * 100)
                                    prem_oi = float(oi * price * 100) if oi > 0 else prem_vol
                                    opt_char = "C" if otype == "calls" else "P"

                                    if prem_vol > 2000000 and vol > 300:
                                        check_double_monster(dname, "ULTRA", prem_vol/1000, float(r["strike"]), exp, price, opt_char, prem_vol, r)
                                    elif prem_vol > 800000 and vol > 300:
                                        check_double_monster(dname, "MEGA", prem_vol/1000, float(r["strike"]), exp, price, opt_char, prem_vol, r)
                                    elif prem_oi > 150000 and vol > 200:
                                        check_double_monster(dname, "GOLDEN", prem_oi/1000, float(r["strike"]), exp, price, opt_char, prem_oi, r)

                                    if vol / max(oi, 1) > 3.0 and vol > 500:
                                        check_double_monster(dname, "MOMENTUM", prem_vol/1000, float(r["strike"]), exp, price, opt_char, prem_vol, r)

                                    if is_0dte and vol > 50:
                                        check_double_monster(dname, "HERO", prem_vol/1000, float(r["strike"]), exp, price, opt_char, prem_vol, r)

                        except:
                            continue
                except:
                    continue
            time.sleep(30)
        except Exception as e:
            print(f"ERR {e}")
            time.sleep(10)

def main_loop():
    time.sleep(2)
    threading.Thread(target=send_worker, daemon=True).start()
    threading.Thread(target=check_exits, daemon=True).start()
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": "🏆 <b>V21 RSI 40/70 FIXED شغال</b>", "parse_mode": "HTML"}, timeout=15)
    except:
        pass
    threading.Thread(target=sniper_loop, daemon=True).start()
    off = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20", timeout=25).json()
            for u in r.get("result", []):
                off = u["update_id"]
                txt = u.get("message", {}).get("text", "").lower()
                if "/test" in txt:
                    queue_send("🏆 V21 ✅")
                if "/status" in txt:
                    queue_send(f"✅ طابور {len(message_queue)} | خروج {len(exit_memory)} | اليوم {len(sent_today)}")
                if "/clear" in txt:
                    double_sent.clear()
                    exit_memory.clear()
                    message_queue.clear()
                    sent_today.clear()
                    queue_send("✅ تم المسح")
        except:
            time.sleep(3)

threading.Thread(target=main_loop, daemon=True).start()
app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
