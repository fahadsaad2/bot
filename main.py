from flask import Flask
import threading
import yfinance as yf
import requests
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home():
    return f"V35 FREE LVL4 - {datetime.now(timezone(timedelta(hours=3))).strftime('%I:%M %p')} KSA - 13 tickers"
def run_flask():
    app.run(host='0.0.0.0', port=10000)
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = "توكنك"
CHAT_ID = "ايدك"
KSA = timezone(timedelta(hours=3))
TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

daily_count = defaultdict(int)
last_reset = datetime.now(KSA).day
sent_contracts = set()

def now_ksa(): return datetime.now(KSA)
def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def scan_ticker(tk):
    try:
        stock = yf.Ticker(tk)
        hist = stock.history(period="1d")
        if hist.empty: return []
        price = float(hist['Close'].iloc[-1])

        # 1. جيب حائط Gamma
        max_oi = 0
        wall_strike = 0
        for exp in stock.options[:2]:
            chain = stock.option_chain(exp)
            if chain.calls.empty: continue
            best = chain.calls.loc[chain.calls['openInterest'].idxmax()]
            if best['openInterest'] > max_oi:
                max_oi = int(best['openInterest'])
                wall_strike = float(best['strike'])

        if not max_oi: return []
        dist_to_wall = ((wall_strike - price) / price * 100)

        opportunities = []
        # 2. افحص عقود الاسبوع هذا
        for exp in stock.options[:2]:
            chain = stock.option_chain(exp)
            calls = chain.calls
            if calls.empty: continue

            # فلتر العقود الرخيصة والقريبة
            calls = calls[(calls['strike'] >= price*0.95) & (calls['strike'] <= price*1.08)]

            for _, c in calls.iterrows():
                vol = int(c['volume']) if pd.notna(c['volume']) else 0
                oi = int(c['openInterest']) if pd.notna(c['openInterest']) else 1
                if oi == 0: oi = 1
                if vol < 800: continue # حجم ضعيف تجاهله

                vol_oi = vol / oi
                contract_id = f"{tk}_{exp}_{c['strike']}"
                if contract_id in sent_contracts: continue

                score = 0
                reasons = []

                # Vol/OI انفجار = Sweep مجاني
                if vol_oi >= 5:
                    score += 4
                    reasons.append(f"🧹 انفجار {vol_oi:.1f}x Vol/OI")
                elif vol_oi >= 3:
                    score += 3
                    reasons.append(f"⚡ {vol_oi:.1f}x")
                elif vol_oi >= 1.5:
                    score += 1

                # قرب من الحائط
                dist_c = ((c['strike'] - price)/price*100)
                if 0.1 < dist_c < 2.5 and max_oi > 15000:
                    score += 4
                    reasons.append(f"💎 حائط {wall_strike:.0f} باقي {dist_c:.1f}%")

                # MU و SNDK نعطيها بوست لانها تتحرك بقوة
                if tk in ["MU","SNDK","LITE"] and vol > 2000:
                    score += 1
                    reasons.append(f"🔥 {tk} مومنتوم")

                if score >= 6: # 6 وفوق نرسل
                    opportunities.append({
                        "tk": tk,
                        "strike": float(c['strike']),
                        "price": float(c['lastPrice']) if pd.notna(c['lastPrice']) else 0,
                        "vol": vol,
                        "oi": oi,
                        "vol_oi": vol_oi,
                        "wall": wall_strike,
                        "wall_oi": max_oi,
                        "dist": dist_c,
                        "score": score,
                        "reasons": reasons,
                        "id": contract_id,
                        "stock_price": price
                    })
        return opportunities
    except Exception as e:
        print(f"{tk} err {e}")
        return []

send_tg(f"🚀 *V35 المجاني اشتغل*\n📊 13 شركة MU موجودة\n🧠 سكانر Vol/OI + Gamma\n⏰ {now_ksa().strftime('%I:%M %p')}")

while True:
    try:
        n = now_ksa()
        if n.day!= last_reset and n.hour==0:
            daily_count.clear()
            sent_contracts.clear()
            last_reset = n.day

        if not (n.weekday()<5 and 16 <= n.hour <= 23):
            time.sleep(60)
            continue

        for tk in TICKERS:
            if daily_count[tk] >= 5: continue
            ops = scan_ticker(tk)
            if not ops: continue

            # خذ اقوى عقد فقط
            best = sorted(ops, key=lambda x: x['score'], reverse=True)[0]

            if best['score'] >= 7:
                forced = best['wall_oi']*50
                msg = f"""💎 *LVL4 {best['score']}/10 - {best['tk']} مجاني* 💎
{'█'*10}

*📊 سكانر ذكي:*
{' | '.join(best['reasons'])}
Vol {best['vol']:,} | OI {best['oi']:,}
عقد {best['strike']:.0f}C @ ${best['price']:.2f}

*💥 تورط MM:*
حائط {best['wall']:.0f} فيه {best['wall_oi']:,} عقد
سهم {best['tk']} سعره {best['stock_price']:.2f} باقي {best['dist']:.2f}%
كسر = شراء {forced:,} سهم جبرا

*⏰ {n.strftime('%I:%M %p')} - {daily_count[tk]+1}/5*"""
                send_tg(msg)
                daily_count[tk] += 1
                sent_contracts.add(best['id'])
                time.sleep(3)

        time.sleep(30)
    except Exception as e:
        print(e)
        time.sleep(10)
