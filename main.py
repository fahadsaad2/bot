import os, time, threading, requests, datetime
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

print(f"BOT_TOKEN set: {bool(BOT_TOKEN)}")
print(f"CHAT_ID set: {bool(CHAT_ID)} - value: {CHAT_ID}")

app = Flask(__name__)
@app.route('/')
def home(): return "Bot Fixed Live ✅"

SMART_WALLETS = [
    "H72yLkhTnoBfhBTXXaj1RBXuirm8s8G5fcVh2XpQLggM",
    "Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
]

strikes_db = []

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        print(f"TG sent: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"TG Error: {e}")

def monitor():
    time.sleep(5)
    send_tg("🚀 <b>البوت اشتغل!</b>\nجرب /status")
    while True:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get("https://frontend-api-v3.pump.fun/coins?offset=0&limit=5&sort=created_timestamp&order=DESC", headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"Pump API blocked: {r.status_code}")
                time.sleep(60)
                continue
            data = r.json()
            for c in data:
                symbol = c.get('symbol','?')
                name = c.get('name','?')
                mint = c.get('mint')
                today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                msg = f"🎯 <b>دخول جديد</b>\n🏢 الشركة: {name} (${symbol})\n💰 سترايك: ${int(c.get('usd_market_cap',0))}\n📅 التاريخ: {today}\n🔗 https://pump.fun/{mint}"
                # send_tg(msg) # فعله بعد ما يشتغل /status
                strikes_db.append(f"{today} | {symbol} | ${int(c.get('usd_market_cap',0))}")
            time.sleep(45)
        except Exception as e:
            print(f"Monitor loop error: {e}")
            time.sleep(30)

def commands():
    offset = 0
    print("Commands thread started")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=25"
            resp = requests.get(url, timeout=30).json()
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                txt = u.get("message", {}).get("text", "")
                print(f"Got command: {txt}")
                if "/start" in txt or "/status" in txt:
                    send_tg(f"✅ <b>البوت شغال 100%!</b>\nCHAT_ID: {CHAT_ID}\nالضربات: {len(strikes_db)}\nالمحافظ: {len(SMART_WALLETS)}\n\n/strikes - السجل\n/wallets - المحافظ")
                elif "/strikes" in txt:
                    send_tg("\n".join(strikes_db[-15:]) if strikes_db else "لا يوجد سجل بعد")
                elif "/wallets" in txt:
                    send_tg("\n".join(SMART_WALLETS))
        except Exception as e:
            print(f"CMD Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=commands, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
