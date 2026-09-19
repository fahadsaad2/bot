from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app=Flask(__name__)
@app.route('/')
def home(): return "HERO V4 + WHALES LIVE"

TOKEN=os.getenv("BOT_TOKEN","").strip()
CHAT_ID=os.getenv("CHAT_ID","").strip()
ETH_KEY=os.getenv("ETHERSCAN_API_KEY","").strip()

TICKERS=["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","TSLA","META","AMZN","GOOGL","NFLX","AMD","SMCI","ARM","AVGO","MSTR","COIN","PLTR","GME"]
NAMES={"^GSPC":"SPX"}

# 5 محافظ حيتان
WALLETS=[
 "0x8eb8a3b98659cce290402893d0123abb75e3ab28",
 "0xf89d7b9c864f589bbf53a821051eb7710272a70e",
 "0x8315177ab297ba92a66054fe80af0024f393c04",
 "0x28c6c06298d514db089934071355e5743bf21d60",
 "0xdfd5293d8e7b0f91dfe01d7d3af3e0d44c0d4b0d"
]

seen=set()
last_tx={}

def send(t):
    if not TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"}, timeout=15)
    except: pass

def check_whales():
    if not ETH_KEY: return []
    alerts=[]
    for w in WALLETS:
        try:
            url=f"https://api.etherscan.io/api?module=account&action=txlist&address={w}&startblock=0&endblock=99999999&page=1&offset=5&sort=desc&apikey={ETH_KEY}"
            r=requests.get(url, timeout=10).json()
            if r.get("status")!="1": continue
            for tx in r.get("result",[])[:2]:
                h=tx['hash']
                if h in seen: continue
                seen.add(h)
                val=float(tx['value'])/1e18
                if val>0.5:
                    alerts.append(f"🐋 <b>Whale Move:</b> {val:.2f} ETH\nFrom: {w[:6]}...{w[-4:]}\n<a href='https://etherscan.io/tx/{h}'>View Tx</a>")
            time.sleep(0.3)
        except: continue
    return alerts

def get_strikes():
    hero=[]; golden=[]
    for sym in TICKERS:
        try:
            ysym="^SPX" if sym=="^GSPC" else sym
            tk=yf.Ticker(ysym)
            if not tk.options: continue
            for exp in tk.options[:3]:
                try:
                    chain=tk.option_chain(exp).calls
                    chain=chain[(chain['openInterest']>200)&(chain['volume']>500)]
                    if chain.empty: continue
                    chain['prem']=chain['openInterest']*chain['lastPrice']*100
                    top=chain.sort_values(by='prem',ascending=False).head(1)
                    if not top.empty:
                        r=top.iloc[0]
                        if r['prem']>500000:
                            hero.append(f"🔥 <b>{NAMES.get(sym,sym)} {r['strike']}C {exp[5:]} Prem:${int(r['prem']/1000)}k Vol:{int(r['volume'])}</b>")
                except: continue
        except: continue
    return hero[:10]

def loop():
    time.sleep(5)
    send(f"✅ <b>Hero + 5 Whales شغال!</b>\nالمفتاح: Etherscan مجاني\nارسل /strikes")
    off=0
    while True:
        try:
            # فحص تليقرام
            r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off}&timeout=15", timeout=20).json()
            for u in r.get("result",[]):
                off=u["update_id"]+1
                txt=u.get("message",{}).get("text","")
                if "/strikes" in txt or "/start" in txt:
                    send("⏳ اجيب لك الضربات...")
                    strikes=get_strikes()
                    if strikes: send("🎯 <b>TOP STRIKES:</b>\n\n"+"\n".join(strikes))
                    else: send("⏳ السوق هادئ")
            # فحص الحيتان كل 2 دقيقة
            whales=check_whales()
            for w in whales: send(w)
        except Exception as e:
            print(e); time.sleep(5)
        time.sleep(10)

threading.Thread(target=loop,daemon=True).start()
if __name__=="__main__":
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
