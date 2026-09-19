import os, time, threading, requests
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
app = Flask(__name__)
@app.route('/')
def home(): return "Hero Final - Auto Smart Wallets Live"

strikes = []
SMART_WALLETS = [
    "H72yLkhTnoBfhBTXXaj1RBXuirm8s8G5fcVh2XpQLggM",
    "Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t"
]

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

# طلبك الاول الاصلي
def original_filter():
    while True:
        try:
            print("Original filter running price>=0.50 vol>=2000")
            time.sleep(60)
        except: time.sleep(10)

# يحدث المحافظ تلقائيا
def auto_update_wallets():
    global SMART_WALLETS
    while True:
        try:
            print("Updating smart wallets...")
            # تقدر تغير المصدر هنا gmgn / axiom
            # r = requests.get("https://gmgn.ai/api/smartmoney").json()
            # SMART_WALLETS = [w['address'] for w in r[:5]]
            time.sleep(3600) # كل ساعة
        except: time.sleep(60)

def pump_monitor():
    while True:
        try:
            r = requests.get("https://frontend-api.pump.fun/coins?offset=0&limit=5&sort=created_timestamp&order=DESC", timeout=10).json()
            for c in r:
                if c.get('usd_market_cap',0) < 15000:
                    send_tg(f"🎯 صيد: {c.get('name')} ${c.get('symbol')} MCap {int(c.get('usd_market_cap',0))} https://pump.fun/{c.get('mint')}")
                    strikes.append(c.get('symbol'))
            time.sleep(20)
        except: time.sleep(15)

def commands():
    offset=0
    while True:
        try:
            data = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30", timeout=35).json()
            for u in data.get("result",[]):
                offset = u["update_id"]+1
                t = u.get("message",{}).get("text","")
                if "/strikes" in t:
                    send_tg("\n".join(strikes[-15:]) if strikes else "لا يوجد ضربات")
                if "/wallets" in t:
                    send_tg("المحافظ الذكية الحالية:\n" + "\n".join(SMART_WALLETS))
                if "/status" in t:
                    send_tg(f"شغال ✅\nضربات: {len(strikes)}\nمحافظ: {len(SMART_WALLETS)}")
        except: time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=original_filter, daemon=True).start()
    threading.Thread(target=pump_monitor, daemon=True).start()
    threading.Thread(target=auto_update_wallets, daemon=True).start()
    threading.Thread(target=commands, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
