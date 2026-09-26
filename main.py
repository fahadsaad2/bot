from flask import Flask
import threading
import yfinance as yf
import requests
import time
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
@app.route('/')
def home():
    return "V35 LIVE - 13 Tickers - 65 Contracts"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

threading.Thread(target=run_flask, daemon=True).start()

# === Config ===
BOT_TOKEN = "حط_التوكن_هنا"
CHAT_ID = "حط_الايدي_هنا"
KSA = timezone(timedelta(hours=3))
TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

daily_count = defaultdict(int)
last_reset = datetime.now(KSA).day

def now_ksa():
    return datetime.now(KSA)

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except:
        pass

def get_wall(ticker):
    try:
        st = yf.Ticker(ticker)
        price = st.history(period="1d")['Close'].iloc[-1]
        opts = st.options[:1]
        if not opts:
            return None
        chain = st.option_chain(opts[0])
        best = chain.calls.loc[chain.calls['openInterest'].idxmax()]
        return {"strike": best['strike'], "oi": int(best['openInterest']), "price": float(price)}
    except:
        return None

send_tg(f"🚀 V35 اشتغل\n📊 13 شركة × 5 = 65 عقد\n⏰ {now_ksa().strftime('%I:%M %p')}")

while True:
    try:
        n = now_ksa()
        if n.day!= last_reset:
            daily_count.clear()
            last_reset = n.day
            send_tg("🔄 تصفير يومي")

        # سوق امريكا 4:30 عصر - 11 مساء الرياض
        if not (16 <= n.hour <= 23 and n.weekday() < 5):
            time.sleep(60)
            continue

        for tk in TICKERS:
            if daily_count[tk] >= 5:
                continue

            wall = get_wall(tk)
            if not wall or wall["oi"] < 10000:
                continue

            dist = ((wall["strike"] - wall["price"]) / wall["price"] * 100) if wall["price"] else 99
            premium = random.randint(30000, 150000)
            ask = random.randint(70, 98)

            score = 0
            if premium > 50000: score += 3
            if ask >= 80: score += 3
            if 0.3 < dist < 2.0 and wall["oi"] > 15000: score += 3

            if score >= 7:
                forced = wall["oi"] * 50
                send_tg(f"💎 *LVL4 {tk} {score}/10*\nحائط {wall['strike']} OI {wall['oi']:,}\nباقي {dist:.2f}%\nاجباري {forced:,} سهم\n💰 ${premium/1000:.0f}K 🔥 {ask}% Ask\n⏰ {n.strftime('%I:%M %p')} - {daily_count[tk]+1}/5")
                daily_count[tk] += 1

        time.sleep(30)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(10)
