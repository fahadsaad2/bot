from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home():
    return "Live 16+SPX - Ready"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","SNDK","SMCI","AVGO","PLTR","^GSPC"]
NAMES = {"^GSPC":"SPX"}

def send(text):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML"}, timeout=15)
    except Exception as e:
        print(e)

def get_hot_strikes():
    results = []
    for symbol in TICKERS:
        try:
            yf_sym = "SPY" if symbol=="^GSPC" else symbol
            if symbol=="^GSPC":
                tk = yf.Ticker("^GSPC")
                # SPX options ticker is ^SPX? yfinance uses ^GSPC but options under ^SPX sometimes fail, نستخدم SPY كبديل للسترايك
                # نحاول SPX الحقيقي
                try:
                    tk_opt = yf.Ticker("^SPX")
                    if tk_opt.options and len(tk_opt.options)>0:
                        tk = tk_opt
                except: pass
            else:
                tk = yf.Ticker(symbol)

            if not tk.options:
                continue
            # اقرب اكسبايري فيه حركة
            exps = tk.options[:3] # اول 3 اكسبايري
            best_call = None
            best_exp = ""

            for exp in exps:
                try:
                    chain = tk.option_chain(exp).calls
                    # فلتر الزخم: فوليوم عالي + اوبن انترست عالي + سعر 1-15 دولار
                    filtered = chain[(chain['volume']>800) & (chain['openInterest']>1000) & (chain['lastPrice']>=0.5) & (chain['lastPrice']<=20)]
                    if filtered.empty:
                        continue
                    # احسب قيمة الصفقة = فوليوم * سعر * 100
                    filtered = filtered.copy()
                    filtered['premium'] = filtered['volume'] * filtered['lastPrice'] * 100
                    filtered['iv_rank'] = filtered['impliedVolatility']
                    # ترتيب حسب الزخم = فوليوم * بريميوم
                    filtered = filtered.sort_values(by=['volume','premium'], ascending=False)
                    top = filtered.iloc[0]
                    if best_call is None or top['premium'] > best_call['premium']:
                        best_call = top
                        best_exp = exp
                except:
                    continue

            if best_call is not None:
                display = NAMES.get(symbol, symbol)
                strike = best_call['strike']
                last = best_call['lastPrice']
                vol = int(best_call['volume'])
                oi = int(best_call['openInterest'])
                premium = best_call['premium']
                iv = best_call['impliedVolatility']*100

                # وقت الدخول والخروج
                entry_price = f"${strike:.0f}"
                # دخول: اذا السهم فوق السترايك
                # خروج: هدف 25% و 40% + ستوب 15%
                target1 = last * 1.25
                target2 = last * 1.40
                stop = last * 0.85

                # حساب وقت
                now = datetime.now().strftime("%I:%M %p")

                msg = (
                    f"🔥 <b>{display} {strike:.0f}C</b> {best_exp}\n"
                    f"💰 سعر العقد: ${last:.2f} | IV: {iv:.0f}%\n"
                    f"📊 فوليوم: {vol:,} | OI: {oi:,} | سيولة: ${premium:,.0f}\n"
                    f"⏰ دخول: الان {now} اذا اخترق ${strike:.0f}\n"
                    f"🎯 خروج1: ${target1:.2f} (+25%) | خروج2: ${target2:.2f} (+40%)\n"
                    f"🛑 ستوب: ${stop:.2f} (-15%)\n"
                )
                results.append((premium, msg))
        except Exception as e:
            print(f"{symbol} error {e}")
            continue

    # رتب حسب اكبر سيولة (اكبر طلب)
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results]

def bot_loop():
    time.sleep(4)
    send("✅ <b>بوت 16 شركة + SPX شغال</b>\nارسل /strikes")
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset+1}&timeout=20", timeout=25).json()
            for upd in r.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text","")
                chat_id = str(msg.get("chat",{}).get("id",""))
                if chat_id!= str(CHAT_ID):
                    continue

                if "/strikes" in text.lower() or "/start" in text.lower():
                    send("⏳ اجيب السترايكات الحارة اللي عليها زخم وطلب كبير... 30 ثانية")
                    strikes = get_hot_strikes()
                    if not strikes:
                        send("⚠️ ما فيه فلو قوي الان - السوق نايم او yfinance معلق، جرب بعد 5 دقايق")
                    else:
                        # ارسل على دفعات 5 شركات كل رسالة
                        chunk = ""
                        count = 0
                        for s in strikes:
                            chunk += s + "\n"
                            count+=1
                            if count % 4 == 0:
                                send(chunk)
                                chunk=""
                                time.sleep(1)
                        if chunk:
                            send(chunk)
                        send(f"✅ خلصنا {len(strikes)} سترايك عليها زخم | <b>وقت الدخول: عند الاختراق</b> | <b>الخروج: +25% / +40%</b>")

        except Exception as e:
            print(e)
            time.sleep(3)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
