import os
import time
import requests
import yfinance as yf

# يقرأ التوكن من الصورة اللي انت حطيتها في Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# قائمة شركاتك الـ 19
الشركات = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]

# عشان ما يكرر نفس التوصية
تم_الارسال = {}

def ارسل_تيليجرام(نص):
    try:
        رابط = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(رابط, data={"chat_id": TELEGRAM_CHAT_ID, "text": نص})
    except:
        pass

def افحص_الشركة(الشركة):
    try:
        السهم = yf.Ticker(الشركة)
        البيانات = السهم.history(period="1mo")
        if len(البيانات) < 20:
            return

        السعر_الحالي = البيانات['Close'].iloc[-1]
        المقاومة = البيانات['High'].iloc[:-1].rolling(20).max().iloc[-1]
        الدعم = البيانات['Low'].iloc[:-1].rolling(20).min().iloc[-1]

        النوع = None
        سعر_الدخول = 0

        # اذا قرب من المقاومة = CALL
        if abs(السعر_الحالي - المقاومة) / المقاومة < 0.02:
            النوع = "CALL"
            سعر_الدخول = المقاومة
        # اذا قرب من الدعم = PUT
        elif abs(السعر_الحالي - الدعم) / الدعم < 0.02:
            النوع = "PUT"
            سعر_الدخول = الدعم
        else:
            return

        # يفحص 3 اسابيع قدام (من اسبوع الى شهر)
        for تاريخ_الانتهاء in السهم.options[1:4]:
            try:
                if النوع == "CALL":
                    العقود = السهم.option_chain(تاريخ_الانتهاء).calls
                else:
                    العقود = السهم.option_chain(تاريخ_الانتهاء).puts

                # من 1 دولار الى 10 دولار فقط
                العقود = العقود[(العقود["lastPrice"] >= 1) & (العقود["lastPrice"] <= 10)]
                # قريب من سعر السهم
                العقود = العقود[(العقود["strike"] >= السعر_الحالي*0.92) & (العقود["strike"] <= السعر_الحالي*1.08)]

                if العقود.empty:
                    continue

                # يختار اكثر عقد عليه تداول
                افضل_عقد = العقود.sort_values("volume", ascending=False).iloc[0]

                المفتاح = f"{الشركة}_{النوع}_{افضل_عقد['strike']}_{تاريخ_الانتهاء}"
                if تم_الارسال.get(المفتاح):
                    continue

                if النوع == "CALL":
                    الرسالة = f"""🎯 {الشركة} - CALL 🟢

💰 الاسترايك: {افضل_عقد['strike']}$
📈 الدخول: فوق {سعر_الدخول:.2f}$
🎯 الاهداف: {سعر_الدخول*1.03:.2f} / {سعر_الدخول*1.06:.2f} / {سعر_الدخول*1.10:.2f}$
🛑 وقف الخسارة: {سعر_الدخول*0.95:.2f}$
📅 تاريخ الانتهاء: {تاريخ_الانتهاء}
💵 سعر العقد: {افضل_عقد['lastPrice']}$"""
                else:
                    الرسالة = f"""🎯 {الشركة} - PUT 🔴

💰 الاسترايك: {افضل_عقد['strike']}$
📉 الدخول: تحت {سعر_الدخول:.2f}$
🎯 الاهداف: {سعر_الدخول*0.97:.2f} / {سعر_الدخول*0.94:.2f} / {سعر_الدخول*0.90:.2f}$
🛑 وقف الخسارة: {سعر_الدخول*1.05:.2f}$
📅 تاريخ الانتهاء: {تاريخ_الانتهاء}
💵 سعر العقد: {افضل_عقد['lastPrice']}$"""

                ارسل_تيليجرام(الرسالة)
                print(الرسالة)
                تم_الارسال[المفتاح] = True
                break

            except:
                continue
    except:
        pass

# يشتغل 24 ساعة
print("✅ البوت اشتغل - يفحص 19 شركة كل دقيقة")
ارسل_تيليجرام("✅ البوت اشتغل - يفحص 19 شركة حقتك")

while True:
    for شركة in الشركات:
        افحص_الشركة(شركة)
        time.sleep(2)
    time.sleep(60)
