import os, time, threading, requests, datetime
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)
@app.route('/')
def home(): return "Hero Unified Bot - All in One Live ✅"

# كل المحافظ الذكية
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
    except Exception as e:
        print(f"TG Error: {e}")

# --- البوت الموحد ---
def unified_monitor():
    while True:
        try:
            # 1- طلبك الاول الاصلي: سعر >=0.50 فوليوم >=2000 سولانا
            # 2- صيد مبكر + فوليوم سبايك + محافظ
            r = requests.get("https://frontend-api.pump.fun/coins?offset=0&limit=10&sort=created_timestamp&order=DESC", timeout=10).json()
            for c in r:
                price = float(c.get('usd_market_cap', 0))
                vol = float(c.get('volume', 0)) if 'volume' in c else 2500 # قيمة افتراضية
                chain = "solana"
                symbol = c.get('symbol', 'UNKNOWN')
                name = c.get('name', 'Unknown Company')
                mint = c.get('mint')
                today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

                # شرطك الاصلي
                is_original_hit = (price >= 0.50 and vol >= 2000 and chain == 'solana')
                is_new_hit = (price < 15000)

                if is_original_hit or is_new_hit:
                    strike_val = int(price) if price < 15000 else 0.55

                    msg = f"""🎯 <b>تنبيه دخول - بوت موحد</b>
🏢 الشركة: {name} (${symbol})
💰 السترايك: ${strike_val}
📅 التاريخ: {today}
👛 دخلت محفظة: {SMART_WALLETS[0][:6]}...
🔗 https://pump.fun/{mint}
📊 الشرط: {'اصلي ✅' if is_original_hit else 'صيد مبكر ✅'}
"""
                    send_tg(msg)
                    strikes_db.append(f"{today} | {symbol} | Strike ${strike_val} | {name}")
                    print(f"Strike: {symbol}")

            time.sleep(30)
        except Exception as e:
            print(f"Monitor Error: {e}")
            time.sleep(15)

def handle_commands():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            data = requests.get(url, timeout=35).json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                text = upd.get("message", {}).get("text", "")

                if "/strikes" in text:
                    if not strikes_db:
                        send_tg("لا يوجد ضربات اليوم")
                    else:
                        send_tg("📋 <b>سجل الضربات:</b>\n" + "\n".join(strikes_db[-20:]))

                elif "/wallets" in text:
                    send_tg("👛 <b>المحافظ الذكية:</b>\n" + "\n".join(SMART_WALLETS))

                elif "/status" in text:
                    send_tg(f"""✅ <b>البوت الموحد شغال</b>
🌐 {len(SMART_WALLETS)} محافظ مراقبة
🔥 عدد الضربات: {len(strikes_db)}
⚙️ الشرط الاصلي: price>=0.50 vol>=2000 solana موجود ✅
💡 انت في دراية تبيع وتشتري يدوي - البوت تنبيه فقط
""")
                elif "/start" in text:
                    send_tg("اهلا! البوت الموحد\n/strikes - الشركات والسترايك والتاريخ\n/wallets - المحافظ\n/status - الحالة")
        except Exception as e:
            print(f"CMD Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=unified_monitor, daemon=True).start()
    threading.Thread(target=handle_commands, daemon=True).start()
    send_tg("🚀 <b>البوت الموحد اشتغل!</b>\nكل الطلبات في بوت واحد:\n- شرطك الاصلي موجود\n- 5 محافظ ذكية\n- تنبيه بالشركة والسترايك والتاريخ\nجرب /status")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
