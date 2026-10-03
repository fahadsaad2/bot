import os, requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

print(f"TOKEN = {TOKEN}")
print(f"CHAT_ID = {CHAT_ID}")
print(f"TOKEN len = {len(TOKEN) if TOKEN else 0}")

# 1- اختبار التوكن
print("\n--- اختبار التوكن ---")
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe")
print(r.text)

# 2- اختبار الارسال
print("\n--- اختبار الارسال ---")
r2 = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={
    "chat_id": CHAT_ID,
    "text": "✅ التوكن شغال 100% -
