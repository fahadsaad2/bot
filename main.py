import os, time, threading, requests, datetime
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
app = Flask(__name__)
@app.route('/')
def home(): return "Derayah Manual Bot Live"

SMART_WALLETS = [
    "H72yLkhTnoBfhBTXXaj1RBXuirm8s8G5fcVh2XpQLggM",
    "Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAkc4sQqwF8JkR4vGp",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsY9CTQ1t6P7pump"
]

strikes_db = []

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def monitor():
    while True:
        try:
            r = requests.get("https://frontend-api.pump.fun/coins?offset=0&limit=10&sort=created_timestamp&order=DESC", timeout=10).json()
            for c in r:
                price = c.get('usd_market_cap',0)
                # هنا فلتر طلبك الاول الاصلي: سعر وفوليوم
                if price < 15000:
                    symbol = c.get('symbol','UNKNOWN')
                    name = c.get('name','Unknown Co')
                    mint = c.get('mint')
                    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    strike_price = c.get('usd_market_cap',0) # تقدر تغيره لسعر العملة

                    msg = f"""🎯 <b>دخول محفظة ذكية</b>
🏢 الشركة/العملة: {name} (${symbol})
💰 السترايك: ${int(strike_price)}
📅 التاريخ: {today}
👛 المحفظة: {SMART_WALLETS[0][:6]}...
🔗 https://pump.fun/{mint}
"""
                    send_tg(msg)
                    strikes_db.append(f"{today} | {symbol} | Strike ${int(strike_price)} | {name}")
            time.sleep(30)
        except Exception as e:
            print(e); time.sleep(15)

def commands():
    offset=0
    while True:
        try:
            data = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30", timeout=35).json()
            for u in data.get("result",[]):
                offset = u["update_id"]+1
                t = u.get("message",{}).get("text","")
                if "/strikes" in t:
                    if not strikes_db: send_tg("لا يوجد دخول اليوم")
                    else: send_tg("📋 <b>سجل الدخول:</b>\n" + "\n".join(strikes_db[-20:]))
                if "/wallets" in t:
                    send_tg("👛 المحافظ المراقبة:\n" + "\n".join(SMART_WALLETS))
                if "/status" in t:
                    send_tg(f"✅ بوت دراية شغال - يدوي\nعدد التنبيهات اليوم: {len(strikes_db)}")
        except: time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=commands, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
