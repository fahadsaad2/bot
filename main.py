import os, time, threading, requests
from flask import Flask
from datetime import datetime
from collections import defaultdict

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
CHAT_ID = os.getenv("CHAT_ID","").strip()
ETHERSCAN_KEY = os.getenv("ETHERSCAN_KEY","").strip() or os.getenv("REALTIME_KEY","").strip() or os.getenv("API_KEY","").strip()

print(f"TOKEN len={len(BOT_TOKEN)} ETHERSCAN len={len(ETHERSCAN_KEY)} CHAT={CHAT_ID}", flush=True)

app = Flask(__name__)
@app.route('/')
def home(): return f"Monster V9 Etherscan Live ✅ Key:{len(ETHERSCAN_KEY)>0}"

# 19 عملة تراقب زخمها
TOKENS_19 = ["ETH","WETH","USDT","USDC","PEPE","SHIB","LINK","UNI","ARB","OP","MATIC","LDO","MKR","AAVE","ENS","BLUR","FLOKI","MOG","WOJAK"]

# 5 محافظ حيتان - غيرها لمحافظك الحقيقية
WHALE_WALLETS = {
    "Whale_1": "0x28C6c06298d514Db089934071355E5743bf21d60", # Binance Hot Wallet
    "Whale_2": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",
    "Whale_3": "0x8315177aB297bA92A02aE5a4d12c2E551aB8e15c7",
    "Whale_4": "0x56f566612dDEd7fcB8dC2a6e993a69a111FA9d9D",
    "Whale_5": "0x4d10Ae710BdBDd07f8a494d79CB34d0762336908"
}

strikes = []
last_tx = defaultdict(str)

def send_tg(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        print(f"SENT: {text[:120]}", flush=True)
    except Exception as e:
        print(f"TG ERR {e}", flush=True)

def check_whales_etherscan():
    """يتابع 5 حيتان بمفتاح Etherscan لحظي"""
    while True:
        try:
            if not ETHERSCAN_KEY:
                print("ETHERSCAN_KEY فاضي!", flush=True)
                time.sleep(60)
                continue

            for name, wallet in WHALE_WALLETS.items():
                try:
                    # احدث حركة للمحفظة
                    url = f"https://api.etherscan.io/api?module=account&action=txlist&address={wallet}&startblock=0&endblock=99999999&page=1&offset=5&sort=desc&apikey={ETHERSCAN_KEY}"
                    r = requests.get(url, timeout=10).json()

                    if r.get("status") == "1":
                        txs = r.get("result", [])
                        for tx in txs[:2]:
                            tx_hash = tx.get("hash")
                            if last_tx[wallet] == tx_hash: continue
                            last_tx[wallet] = tx_hash

                            value_eth = int(tx.get("value","0")) / 10**18
                            if value_eth > 10: # حركة فوق 10 ايثيريوم = حوت
                                to_addr = tx.get("to","")[:10]
                                msg = f"🐋 <b>{name} تحرك!</b>\n💰 {value_eth:.2f} ETH\nمن: {wallet[:8]}... الى: {to_addr}...\n<a href='https://etherscan.io/tx/{tx_hash}'>Etherscan</a>\n{datetime.now().strftime('%H:%M:%S')}"
                                send_tg(msg)
                                strikes.append(f"🐋 {name} {value_eth:.1f} ETH")

                    # تتبع توكنات ERC20 للحوت
                    url2 = f"https://api.etherscan.io/api?module=account&action=tokentx&address={wallet}&page=1&offset=5&sort=desc&apikey={ETHERSCAN_KEY}"
                    r2 = requests.get(url2, timeout=10).json()
                    if r2.get("status") == "1":
                        for t in r2.get("result", [])[:1]:
                            token = t.get("tokenSymbol")
                            if token in TOKENS_19:
                                val = int(t.get("value","0")) / (10 ** int(t.get("tokenDecimal","18")))
                                if val > 100000: # كمية كبيرة
                                    msg = f"💥 <b>حوت يشتري {token}!</b>\n{name}: {val:,.0f} {token}\n<a href='https://etherscan.io/tx/{t.get('hash')}'>رابط</a>"
                                    send_tg(msg)
                                    strikes.append(f"💥 {name} {token} {val:,.0f}")

                    time.sleep(3) # Etherscan حد 5 طلبات بالثانية
                except Exception as e:
                    print(f"{name} ERR {e}")
                    continue

            print(f"--- دورة حيتان Etherscan خلصت {datetime.now().strftime('%H:%M:%S')} ---", flush=True)
            time.sleep(25) # كل 25 ثانية يشيك لحظي بمفتاحك

        except Exception as e:
            print(f"WHALE ERR {e}", flush=True)
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
                    send_tg(f"✅ <b>الوحش V9 Etherscan شغال!</b>\nKey: {'✅' if ETHERSCAN_KEY else '❌'}\n🐋 5 حيتان لحظي\n📊 19 عملة\n/strikes - الضربات\n/whales - الحيتان\n/status")
                elif "/strikes" in text.lower():
                    send_tg("🔥 <b>حركات الحيتان:</b>\n" + ("\n".join(strikes[-25:]) if strikes else "لا يوجد حركة كبيرة الان"))
                elif "/whales" in text.lower():
                    txt = "🐋 <b>الـ 5 حيتان:</b>\n"
                    for k,v in WHALE_WALLETS.items():
                        txt += f"{k}: {v}\n"
                    send_tg(txt)
                elif "/status" in text.lower():
                    send_tg(f"✅ V9 Live\nEtherscan Key: {'✅ len='+str(len(ETHERSCAN_KEY)) if ETHERSCAN_KEY else '❌ حط ETHERSCAN_KEY'}\nالضربات: {len(strikes)}\n{datetime.now()}")
        except Exception as e:
            print(f"CMD ERR {e}"); time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=check_whales_etherscan, daemon=True).start()
    threading.Thread(target=handle_commands, daemon=True).start()
    time.sleep(3)
    send_tg(f"🚀 <b>الوحش V9 Etherscan اشتغل بمفتاحك!</b>\nKey: {len(ETHERSCAN_KEY)>0}\nيتابع 5 حيتان لحظي 🐋")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
