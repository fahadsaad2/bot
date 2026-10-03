import yfinance as yf
import requests
import time
from datetime import datetime, timedelta

TELEGRAM_BOT_TOKEN = "حط توكنك هنا"
TELEGRAM_CHAT_ID = "حط ايديك هنا"
FINNHUB_API_KEY = "db070i9r01qn6m7vpb90db070i9r01qn6m7vpb9g"

# قائمتك الثابتة النهائية - ما تتغير
شركاتي = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]

المرسل = set()

def سعر_لحظي(رمز):
    try:
        r = requests.get(f"https://finnhub.io/api/v1/quote?symbol={رمز}&token={FINNHUB_API_KEY}", timeout=5).json()
        return r['c']
    except:
        return None

def فحص(رمز):
    try:
        حالي = سعر_لحظي(رمز)
        if not حالي: return
        سهم = yf.Ticker(رمز)
        شمعات = سهم.history(period="1mo")
        if len(شمعات) < 20: return
        مقاومة = شمعات['High'].rolling(20).max().iloc[-1]
        دعم = شمعات['Low'].rolling(20).min().iloc[-1]

        نوع = None; دخول = 0
        if abs(حالي - مقاومة)/مقاومة < 0.015:
            نوع = "CALL"; دخول = مقاومة; ايموجي = "🟢 اختراق مقاومة"
        elif abs(حالي - دعم)/دعم < 0.015:
            نوع = "PUT"; دخول = دعم; ايموجي = "🔴 كسر دعم"
        else: return

        تواريخ = سهم.options
        if not تواريخ: return
        اليوم = datetime.now().date()
        يومي = None; اسبوعي = None
        for d in تواريخ:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            if not يومي and dt >= اليوم: يومي = d
            if dt >= اليوم + timedelta(days=5): اسبوعي = d; break
        if not يومي: يومي = تواريخ[0]
        if not اسبوعي: اسبوعي = تواريخ[1] if len(تواريخ)>1 else تواريخ[0]

        for انتهاء, تاغ, ح1, ح2 in [(يومي, "يومي 0DTE 🔥", 0.3, 4), (اسبوعي, "اسبوعي 🛡️", 1, 10)]:
            try:
                سلسلة = سهم.option_chain(انتهاء)
                عقود = سلسلة.calls if نوع == "CALL" else سلسلة.puts
                عقود = عقود[(عقود['lastPrice'] >= ح1) & (عقود['lastPrice'] <= ح2)]
                عقود = عقود.sort_values(['volume','openInterest'], ascending=False)
                if عقود.empty: continue
                عقد = عقود.iloc[0]
                مفتاح = f"{رمز}-{انتهاء}-{عقد['strike']}-{نوع}"
                if مفتاح in المرسل: continue
                المرسل.add(مفتاح)
                if نوع == "CALL":
                    ه1=دخول*1.015; ه2=دخول*1.03; ه3=دخول*1.05; وقف=دخول*0.97
                    دخول_نص=f"فوق {دخول:.2f}$"
                else:
                    ه1=دخول*0.985; ه2=دخول*0.97; ه3=دخول*0.95; وقف=دخول*1.03
                    دخول_نص=f"تحت {دخول:.2f}$"

                رسالة = f"""{ايموجي}
📈 السهم: {رمز}
📊 النوع: {نوع}
💵 لحظي: {حالي:.2f}$
📍 الدخول: {دخول_نص}

💰 العقد ({تاغ}):
- استرايك: {عقد['strike']}$
- ينتهي: {انتهاء}
- سعر العقد: {عقد['lastPrice']:.2f}$
- سيولة: {int(عقد['volume'])}

🎯 اهداف: {ه1:.2f} / {ه2:.2f} / {ه3:.2f}
🛑 وقف: {وقف:.2f}
⚡ لحظي
"""
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT_ID, "text": رسالة})
                print(f"ارسل {رمز} {تاغ}")
            except: continue
    except Exception as e:
        print(f"خطأ {رمز}: {e}")

print("🚀 V9 الثابت شغال - 19 شركة")
while True:
    for ر in شركاتي:
        فحص(ر)
        time.sleep(2)
    print(f"خلص فحص - {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(20)
