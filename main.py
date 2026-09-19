import os, time, threading, requests
from flask import Flask

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

app = Flask(__name__)
@app.route('/')
def home():
    return 'Hero V2 Fixed'

SMART_WALLETS = [
 'H72yLkhTnoBfhBTXXaj1RBXuirn8s8G5GfcVh2XpQlGgW',
 'Be9CvxqHW6BYiRAxW903xu1ycTMWaL5z8NX4HR3ha7tM',
 '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S51CNLY3QrkX6H',
 '5Q544fKrFoe6tsEbD7S8EmxGTJYAkc4sQqwF8JkR4vG9',
 '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsY9CTQ1t6P7pump'
]

def send_tg(text):
    try:
        u = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(u, json={'chat_id': CHAT_ID, 'text': text}, timeout=10)
    except:
        pass

def unified_monitor():
    hdr = {'User-Agent': 'Mozilla/5.0'}
    # هذا الرابط عدل النقطة
    api_url = 'https://frontend-api-v2.pump.fun/coins?offset=0&limit=20&sort=created_timestamp&order=DESC'
    api_url = api_url.replace(' ', '')
    while True:
        try:
            resp = requests.get(api_url, headers=hdr, timeout=15)
            if not resp.text or len(resp.text) < 10:
                print('Empty')
                time.sleep(15)
                continue
            coins = resp.json()
            print(f"OK {len(coins)}")
        except Exception as e:
            print(f"Monitor Error: {e}")
            time.sleep(10)
        time.sleep(20)

def handle_commands():
    off = 0
    while True:
        try:
            u = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=20"
            r = requests.get(u, timeout=25).json()
            for up in r.get('result', []):
                off = up['update_id'] + 1
                txt = up.get('message', {}).get('text', '')
                if '/start' in txt:
                    send_tg('Hero V2 اشتغل! تم اصلاح JSON')
                if '/status' in txt:
                    send_tg('البوت شغال - 5 محافظ')
        except:
            time.sleep(5)

if __name__ == '__main__':
    threading.Thread(target=unified_monitor, daemon=True).start()
    threading.Thread(target=handle_commands, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)
