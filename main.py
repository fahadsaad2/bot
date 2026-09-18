import yfinance as yf, requests, os, time, threading
from flask import Flask

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","SNDK","SMCI","AVGO","PLTR","SPX"]

app = Flask(__name__)
@app.route('/')
def home(): return "Bot 16 stocks running!"

def send(msg):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
    except: pass

last_prices = {}
def check_market():
    while True:
        try:
            for t in TICKERS:
                price = yf.Ticker(t).history(period="1d")["Close"].iloc[-1]
                prev = last_prices.get(t)
                if prev and abs((price-prev)/prev*100) >= 1.0:
                    emoji = "🚀" if price>prev else "🔻"
                    send(f"{emoji} <b>{t}</b> {price:.2f} ({(price-prev)/prev*100:+.2f}%)")
                last_prices[t]=price
            time.sleep(300)
        except: time.sleep(60)

def bot_loop():
    time.sleep(3)
    try:
        r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1").json()
        if r.get("result"):
            requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={r['result'][-1]['update_id']+1}")
    except: pass
    send(f"✅ بوت 16 سهم اشتغل\n{', '.join(TICKERS)}\nارسل /start")
    offset=0
    while True:
        try:
            res=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset+1}&timeout=25").json()
            for upd in res.get("result",[]):
                offset=upd["update_id"]
                msg=upd.get("message",{}); text=msg.get("text",""); chat=str(msg.get("chat",{}).get("id",""))
                if chat!=str(CHAT_ID): continue
                if text=="/start": send(f"👋 16 سهم شغال\n{', '.join(TICKERS)}\n/status - الحالة")
                elif text=="/status": send(f"✅ شغال - {len(last_prices)}/16\n" + "\n".join([f"{k}: {v:.2f}" for k,v in list(last_prices.items())[:6]]))
                elif text.startswith("/price"):
                    try:
                        t=text.split()[1].upper(); p=yf.Ticker(t).history(period="1d")["Close"].iloc[-1]
                        send(f"{t}: {p:.2f}")
                    except: send("مثال: /price SNDK")
        except: time.sleep(2)
        time.sleep(1)

threading.Thread(target=bot_loop, daemon=True).start()
threading.Thread(target=check_market, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
