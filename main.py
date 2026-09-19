import requests, time, threading, os
from flask import Flask
app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع التوكن هنا")
CHAT_ID = os.environ.get("CHAT_ID", "ضع الايدي هنا")
sent_coins = set()

def send_tg(msg):
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={msg}&parse_mode=Markdown&disable_web_page_preview=True", timeout=10)
    except: pass

def check_pump():
    send_tg("✅ *Hero Bot اشتغل*\nفلتر: سعر 0.50$+ | حجم 2000$+ | بدون تكرار")
    while True:
        try:
            # نجيب العملات الجديدة من DexScreener
            r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15).json()
            for token in r[:20]:
                addr = token.get('tokenAddress','')
                if addr in sent_coins: continue

                # تفاصيل العملة
                info = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=15).json()
                pairs = info.get('pairs')
                if not pairs: continue
                p = pairs[0]

                price = float(p.get('priceUsd', 0) or 0)
                vol = float(p.get('volume', {}).get('h24', 0) or 0)
                chain = p.get('chainId','')

                # === الفلتر حقك ===
                if price >= 0.50 and vol >= 2000:
                    if chain.lower()!= 'solana': continue
                    sent_coins.add(addr)
                    name = p.get('baseToken',{}).get('name','')
                    symbol = p.get('baseToken',{}).get('symbol','')
                    url = p.get('url','')
                    msg = f"🚀 *عملة قوية جديدة*\n\n💰 {name} (${symbol})\n💵 السعر: ${price:.4f}\n📊 الحجم: ${vol:.0f}\n🔗 {url}\n\n`{addr}`"
                    send_tg(msg)
                    time.sleep(2)
            time.sleep(30)
        except Exception as e:
            print(e)
            time.sleep(15)

@app.route('/')
def home():
    return "Hero Bot Live - $0.50+ | 2000+ Vol | No Spam"

threading.Thread(target=check_pump, daemon=True).start()
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
