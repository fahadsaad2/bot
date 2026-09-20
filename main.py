import os, time, threading, requests
from flask import Flask
from datetime import datetime
from collections import defaultdict

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
CHAT_ID = os.getenv("CHAT_ID","").strip()
ETHERSCAN_KEY = os.getenv("ETHERSCAN_KEY","").strip() or os.getenv("REALTIME_KEY","").strip()

print(f"TOKEN len={len(BOT_TOKEN)} ETHERSCAN len={len(ETHERSCAN_KEY)}", flush=True)

app = Flask(__name__)
@app.route('/')
def home(): return "Monster V10 - 5 Whales Live ✅"

# 5 محافظ الحيتان الكبار الحقيقية
WHALE_WALLETS = {
    "🐋 Binance_Whale": "0x28C6c06298d514Db089934071355E5743bf21d60", # اكبر محفظة بينانس 2.1M ETH
    "🐋 Bitfinex_Whale": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8", # بتفينكس 1.5M ETH
    "🐋 Wintermute_Whale": "0x4f3a120E72C76c22e438802Bd36C9AcC4E6464e79", # وينترميوت - صانع السوق
    "🐋 Robinhood_Whale": "0x5AB7124eC4a16a43439893D5F2794a6406a942c6", # روبن هود - حوت كبير
    "🐋 PEPE_Whale": "0x8315177aB297bA92A02aE5a4d12c2E551aB8e15c7" # حوت بيبي و ميم كوينز - يربح 1000x
}

strikes = []
last_tx = defaultdict(str)

def send_tg(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"TG ERR {e}", flush=True)

def check_5_whales():
    while True:
        try:
            if not ETHERSCAN_KEY:
                print("ETHERSCAN_KEY فاضي!", flush=True)
                time.sleep(60)
                continue

            for name, wallet in WHALE_WALLETS.items():
                try:
                    url = f"https://api.etherscan.io/api?module=account&action=txlist&address={wallet}&page=1&offset=3&sort=desc&apikey={ETHERSCAN_KEY}"
                    r = requests.get(url, timeout=10).json()

                    if r.get("status") == "1":
                        for tx in r.get("result", [])[:1]:
                            tx_hash = tx.get("hash")
                            if last_tx[wallet] == tx_hash:
                                continue
                            last_tx[wallet] = tx_hash

                            value_eth = int(tx.get("value","0")) / 10**18
                            # اي حركة فوق 5 ETH نعتبرها حوت
                            if value_eth >= 1:
                                is_out = tx.get("from","").lower() == wallet.lower()
                                action = "🔴 باع" if is_out else "🟢 اشترى/استقبل"
                                msg = f"{name}\n{action} <b>{value_eth:.2f} ETH</b>\n💵 حوالي ${value_eth*2500:,.0f}\n<a href='https://etherscan.io/tx/{tx_hash}'>شوف الحركة في Etherscan</a>\n{datetime.now().strftime('%H:%M:%S')}"
                                send_tg(msg)
                                strikes.append(f"{name} {action} {value_eth:.1f} ETH")
                                print(f"WHALE MOVE: {name} {value_eth}", flush=True)

                    # فحص توكنات الميم للحوت الخامس
                    if "PEPE" in name:
                        url2 = f"https://api.etherscan.io/api?module=account&action=tokentx&address={wallet}&page=1&offset=5&sort=desc&apikey={ETHERSCAN_KEY}"
                        r2 = requests.get(url2, timeout=10).json()
                        if r2.get("status") == "1":
                            for t in r2.get("result", [])[:2]:
                                if last_tx[t.get("hash")] == "": # ما نكرر
                                    token = t.get("tokenSymbol")
                                    val = int(t.get("value","0")) / (10 ** int(t.get("tokenDecimal","18")))
                                    if val > 1000000:
                                        send_tg(f"💥 <b>حوت الميم يتحرك!</b>\n{name}\n{val:,.0f} {token}\n<a href='https://etherscan.io/tx/{t.get('hash')}'>الرابط</a>")
                                        last_tx[t.get("hash")] = "done"

                    time.sleep(4) # Etherscan يسمح 5 طلبات بالثانية
                except Exception as e:
                    print(f"{name} ERR {e}")
                    time.sleep(2)

            print(f"--- دورة 5 حيتان خلصت {datetime.now().strftime('%H:%M')} ---", flush=True)
            time.sleep(20) # كل 20 ثانية يشيك لحظي
        except Exception as e:
            print(f"WHALE LOOP ERR {e}", flush=True)
            time.sleep(20)

def handle_commands():
    offset = 0
    try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+9999}", timeout=5)
    except: pass
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            res = requests.get(url, timeout=35).json()
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                text = upd.get("message",{}).get("text","")
                if not text: continue
                if "/start" in text.lower():
                    send_tg(f"✅ <b>الوحش V10 - 5 حيتان كبار شغال!</b>\n\n{chr(10).join(WHALE_WALLETS.keys())}\n\n/strikes - حركات الحيتان\n/whales - عناوين الحيتان\n/status - الحالة")
                elif "/strikes"
