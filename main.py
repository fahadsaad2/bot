import os, requests, time
TOKEN = os.environ.get("BOT_TOKEN","").strip()
CHAT = os.environ.get("CHAT_ID","").strip()
print(f"TOKEN len={len(TOKEN)} CHAT={CHAT}", flush=True)

if not TOKEN:
    print("BOT_TOKEN فاضي!", flush=True)
    while True: time.sleep(60)

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
try:
    r = requests.post(url, json={"chat_id":CHAT,"text":"✅ تم التصليح! البوت شغال V5"}, timeout=15)
    print(f"Telegram reply: {r.text}", flush=True)
except Exception as e:
    print(f"Send error: {e}", flush=True)

# loop عشان Render ما يطفيه
while True: time.sleep(60)
