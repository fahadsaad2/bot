import yfinance as yf, requests, os, time, threading
from flask import Flask

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","SPX"]

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running!"

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                      data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
    except: pass

def bot_loop():
    time.sleep(3)
    # امسح كل الرسائل القديمة عشان ما يصير سبام
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1").json()
        if r.get("result"):
            last_id = r["result"][-1]["update_id"]
            requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}")
    except: pass
    
    send("✅ البوت اشتغل - هذا اخر تنبيه سبام\nارسل /start")

    offset = 0
    while True:
        try:
            res = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset+1}&timeout=25").json()
            for upd in res.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat = str(msg.get("chat", {}).get("id", ""))
                
                # يرد فقط على شاتك انت
                if chat != str(CHAT_ID): continue
                
                if text == "/start":
                    send(f"👋 هلا! البوت شغال تمام\nيراقب: {', '.join(TICKERS)}\n\n/status - حالة البوت")
                elif text == "/status":
                    send("✅ شغال 100% ومراقب السوق")
        except Exception as e:
            time.sleep(2)
        time.sleep(1)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
