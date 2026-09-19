import os, requests, time, threading
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
print(f"TOKEN={bool(BOT_TOKEN)} CHAT={CHAT_ID}")

app = Flask(__name__)
@app.route('/')
def home(): return "OK"

def send(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print(f"SEND {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"SEND ERR {e}")

def loop():
    offset = 0
    time.sleep(3)
    send("🚀 البوت اشتغل! ارسل /status")
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=20", timeout=25).json()
            for u in r.get("result", []):
                offset = u["update_id"]+1
                txt = u.get("message",{}).get("text","")
                print(f"RECV {txt}")
                if "/status" in txt or "/start" in txt:
                    send(f"✅ شغال! CHAT_ID={CHAT_ID}")
        except Exception as e:
            print(f"LOOP ERR {e}")
            time.sleep(5)

threading.Thread(target=loop, daemon=True).start()
app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
