import os, time, yfinance as yf
from flask import Flask
from threading import Thread

app = Flask(__name__)

def get_sndk():
    try:
        return round(yf.Ticker("SNDK").fast_info['last_price'], 2)
    except:
        return 0

def bot_loop():
    while True:
        print(f"SNDK: {get_sndk()}")
        time.sleep(60)

@app.route('/')
def home():
    return f"SNDK Bot is Running - Price: {get_sndk()}"

Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
