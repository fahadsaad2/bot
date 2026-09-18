import yfinance as yf, requests, os, time, threading, math
from flask import Flask
from scipy.stats import norm

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","^GSPC"]

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running!"

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
    except: pass

last_prices = {}

def check_market():
    while True:
        try:
            for ticker in TICKERS:
                stock = yf.Ticker(ticker)
                price = stock.history(period="1d")["Close"].iloc[-1]
                prev = last_prices.get(ticker)

                if prev:
                    change = ((price - prev) / prev) * 100
                    # تنبيه اذا تحرك اكثر من 1%
                    if abs(change) >= 1.0:
                        emoji = "🚀" if change > 0 else "🔻"
                        send(f"{emoji} <b>{ticker}</b>\nالسعر: {price:.2f}\nالتغير: {change:+.2f}%\nمن {prev:.2f}")

                last_prices[ticker] = price
            time.sleep(300) # كل 5 دقايق
        except Exception as e:
            print(e)
            time.sleep(60)

def bot_loop():
    time.sleep(3)
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1").json()
        if r.get("result"):
            last_id = r["result"][-1]["update_id"]
            requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}")
    except: pass

    send("✅ البوت النهائي اشتغل\nيراقب SPX و 11 سهم كل 5 دقايق\nارسل /start")

    offset = 0
    while True:
        try:
            res = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset+1}&timeout=25").json()
            for upd in res.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat = str(msg.get("chat", {}).get("id", ""))
                if chat!= str(CHAT_ID): continue

                if text == "/start":
                    send(f"👋 أهلا! البوت شغال 24 ساعة\n\nيراقب:\n{', '.join(TICKERS)}\n\n/status - الحالة\n/price SPY - سعر سهم")
                elif text == "/status":
                    prices_text = "\n".join([f"{k}: {v:.2f}" for k,v in last_prices.items()][:5])
                    send(f"✅ شغال\nاخر اسعار:\n{prices_text if prices_text else 'جاري التحميل...'}")
                elif text.startswith("/price"):
                    try:
                        t = text.split()[1].upper()
                        p = yf.Ticker(t).history(period="1d")["Close"].iloc[-1]
                        send(f"{t}: {p:.2f}")
                    except:
                        send("اكتب مثلا: /price AAPL")
        except:
            time.sleep(2)
        time.sleep(1)

threading.Thread(target=bot_loop, daemon=True).start()
threading.Thread(target=check_market, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
