import os, yfinance as yf
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=BOT_TOKEN)

COMPANIES = ["AAPL","MSFT","NVDA","TSLA","AMD","META","GOOGL","AMZN","SPY","QQQ","NFLX","AVGO","PLTR","SMCI","MSTR","COIN","TSM","SPX","AAPL"]
AR_NAMES = {"AAPL":"أبل","MSFT":"مايكروسوفت","NVDA":"انفيديا","TSLA":"تسلا","AMD":"ايه ام دي","META":"ميتا","GOOGL":"جوجل","AMZN":"امازون","SPY":"اس اند بي 500","QQQ":"ناسداك","NFLX":"نتفلكس","AVGO":"برودكوم","PLTR":"بلانتر","SMCI":"سوبر مايكرو","MSTR":"مايكروستراتيجي","COIN":"كوين بيس","TSM":"تي اس ام سي","SPX":"اس بي اكس"}

def check_ticker(ticker):
    try:
        stock = yf.Ticker(ticker)
        price = stock.history(period="1d")['Close'].iloc[-1]
        expiries = stock.options
        if not expiries: return

        targets_expiry = [expiries[0], expiries[2] if len(expiries)>2 else expiries[-1]] # يومي + اسبوعي

        for expiry in targets_expiry:
            is_daily = "يومي" if expiry == expiries[0] else "اسبوعي"
            chain = stock.option_chain(expiry)

            for _, row in chain.calls.head(3).iterrows(): # يفحص اقرب 3 عقود
                strike = row['strike']
                vol = int(row['volume'] or 0)
                oi = int(row['openInterest'] or 0)
                contract_price = row['lastPrice']

                if vol < 300: continue # فلتر سيولة

                entry = round(price + 1, 2)
                t1 = round(strike * 1.03, 2)
                t2 = round(strike * 1.06, 2)
                t3 = round(strike * 1.10, 2)
                stop = round(price * 0.95, 2)

                msg = f"""📈 السهم: {AR_NAMES.get(ticker,ticker)} ({ticker}) - {is_daily}

💰 رقم الاسترايك: {strike}$
📄 نوع العقد: CALL

📅 تاريخ الانتهاء: {expiry}
🚀 سعر الدخول: فوق ${entry}

🎯 الأهداف:
الأول: ${t1}
الثاني: ${t2}
الثالث: ${t3}

🛑 وقف الخسارة: ${stop}

📊 الفوليوم: {vol:,}
📈 OI: {oi:,}
💵 سعر العقد: ${contract_price}

#️⃣ {is_daily}
"""
                bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"Error {ticker}: {e}")

# تشغيل على 19 شركة
for comp in COMPANIES:
    check_ticker(comp)

bot.send_message(chat_id=CHAT_ID, text="✅ البوت اشتغل - يفحص 19 شركة (يومي + اسبوعي) بالعربي")
