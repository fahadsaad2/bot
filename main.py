from flask import Flask
import os, requests, threading, time
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "HERO V4 LIVE"

TOKEN = os.getenv("BOT_TOKEN","").strip()
CHAT_ID = os.getenv("CHAT_ID","").strip()
ETH_KEY = os.getenv("ETHERSCAN_API_KEY","").strip()

TICKERS = ["SPY","QQQ","AAPL","NVDA","TSLA","META","MSTR"]
WALLETS = [
 "0x8eb8a3b98659cce290402893d0123abb75e3ab28",
 "0xf89d7b9c864f589bbf53a821051eb7710272a70e",
 "0x8315177ab297ba92a66054fe80af0024f393c04"
]
seen=set()

def send(text):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=15)
    except Exception as e: print(e)

def get_strikes():
    out=[]
    for sym in TICKERS:
        try:
            tk = yf.Ticker(sym)
            if not tk.options: continue
            exp = tk.options[0]
            calls = tk.option_chain(exp).calls
            calls = calls[(calls['openInterest']>100)&(calls['volume']>100)]
            if calls.empty: continue
            calls['prem'] = calls['openInterest']*calls['lastPrice']*100
            top = calls.sort_values('prem', ascending=False).head(1).iloc[0]
            out.append(f"🔥 {sym} {top['strike']}C {exp} Vol:{int(top['volume'])} OI:{int(top['openInterest'])}")
        except: continue
    return out

def check_whales():
    if not ETH_KEY: return []
    alerts=[]
    for w in WALLETS:
        try:
            url = f"https://api.etherscan.io/api?module=account&action=txlist&address={w}&page=1&offset=3&sort=desc&apikey={ETH_KEY}"
            r = requests.get(url, timeout=10).json()
            for tx in r.get('result',[]):
                h=tx['hash']
                if h in seen: continue
                seen.add(h)
                val=float(tx['value'])/1e18
                if val>0.1:
                    alerts.append(f"🐋 حوت حرك {val:.2f} ETH\n{w[:10]}...\nhttps://etherscan.io/tx/{h}")
            time.sleep(0.5)
        except: pass
    return alerts

def bot_loop():
    time.sleep(3)
    send("✅ البوت اشتغل! ارسل /strikes")
    offset=0
    while True:
        try:
            # استقبال رسائل
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=20", timeout=25).json()
            for u in r.get('result',[]):
                offset = u['update_id']+1
                msg = u.get('message',{}).get('text','')
                if '/strikes' in msg or '/start' in msg:
                    send("⏳ جاري فحص الضربات...")
                    s = get_strikes()
                    if s: send("🎯 <b>TOP STRIKES:</b>\n\n" + "\n".join(s))
                    else: send("السوق هادي حاليا")
            # فحص حيتان
            for a in check_whales(): send(a)
        except Exception as e:
            print("loop error", e)
            time.sleep(5)
        time.sleep(5)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv("PORT",10000)))
