import requests, time, threading
from flask import Flask
app = Flask(__name__)

BOT_TOKEN = "ضع التوكن هنا مؤقتا"
CHAT_ID = "ضع الايدي هنا"

# هذا بمنع التكرار - يرسل العملة مرة وحدة بس
sent_coins = set()

def check_pump():
    while True:
        try:
            url = "https://api.dexscreener.com/latest/dex/search/?q=SOL"
            # نجيب العملات الجديدة فقط
            # فلتر قوي
            # ...
            # سأكمل الكود بعد ما ترسل لي محتوى main.py القديم لاعرف الAPI اللي كنت تستخدمه
        except:
            time.sleep(5)

@app.route('/')
def home():
    return "Hero Bot Running - Filter $0.50 Volume 2000+"

threading.Thread(target=check_pump, daemon=True).start()
