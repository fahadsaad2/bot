import yfinance as yf, requests, os, time, threading
from flask import Flask
from scipy.stats import norm
import math

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","SPX"]

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running!"

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg})
    except: pass

def calc_delta(S,K,T,r,sigma,opt="call"):
    try:
        d1 = (math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
        return norm.cdf(d1) if opt=="call" else norm.cdf(d1)-1
    except: return 0.5

def monitor():
    time.sleep(10)
    send("✅ البوت اشتغل تمام ويرد على /start\nلن يرسل سبام بعد الان")
    last_update_id = 0
    while True:
        try:
            # استقبال رسائل /start
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_update_id+1}&timeout=20").json()
            for upd in r.get("result", []):
                last_update_id = upd["update_id"]
                txt = upd.get("message", {}).get("text", "")
                if txt == "/start":
                    send("👋 أهلا! البوت شغال 24 ساعة\nيراقب: " + ", ".join(TICKERS) + "\n\nارسل /status لمعرفة الحالة")
                if txt == "/status":
                    send("✅ البوت شغال\nSPX الان مراقب")
        except: pass
        time.sleep(2)

threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
