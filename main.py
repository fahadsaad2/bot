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

WHALE_WALLETS = {
    "Binance_Whale": "0x28C6c06298d514Db089934071355E5743bf21d60",
    "Bitfinex_Whale": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",
    "Wintermute_Whale": "0x4f3a120E72C76c22e438802Bd36C9AcC4E6464e79",
    "Robinhood_Whale": "0x5AB7124eC4a16a43439893D5F2794a6406a942c6",
    "PEPE_Whale": "0x8315177aB297bA92A02aE5a4d12c2E551aB8e15c7"
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
                            if value_eth >= 1:
                                is_out = tx.get("from","").lower() == wallet.lower()
                                action = "باع" if is_out else "اشترى"
                                msg = f"{name} {action} {value_eth:.2f} ETH\nhttps://etherscan.io/tx/{tx_hash}\n{datetime.now().strftime('%H:%M:%S')}"
                                send_tg(msg)
                                strikes.append(f"{name} {action} {value_eth:.1f}")
                    time.sleep(4)
                except Exception as e:
                    print(f"{name} ERR {e}")
                    time.sleep(2)
            time.sleep(20)
        except Exception as e:
            print(f"LOOP ERR {e}")
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
                if not text:
                    continue
                low = text.lower()
                if "/start" in low:
                    send_tg("✅ الوحش V10 - 5 حيتان شغال!\n/strikes - الحركات\n/whales - عناوين الحيتان\n/status - الحالة")
                elif "/strikes" in low:
                    msg = "\n".join(strikes[-20:]) if strikes else "لا يوجد حركة الان"
                    send_tg(f"حركات الحيتان:\n{msg}")
                elif "/whales" in low:
                    txt = "5 حيتان:\n\n"
                    for k,v in WHALE_WALLETS.items():
                        txt += f"{k}\n{v}\n\n"
                    send_tg(txt)
                elif "/status" in low:
                    send_tg(f"V10 Live\nحركات: {len(strikes)}\n{datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"CMD ERR {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=check_5_whales, daemon=True).start()
    threading.Thread(target=handle_commands, daemon=True).start()
    time.sleep(2)
    send_tg("🚀 الوحش V10 اشتغل! 5 حيتان كبار ✅")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
