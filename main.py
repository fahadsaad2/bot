from flask import Flask
import threading
import yfinance as yf
import requests
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
@app.route('/')
def home():
    return f"V35 LIVE + Benzinga - {datetime.now(timezone(timedelta(hours=3))).strftime('%I:%M %p')} KSA"
def run_flask():
    app.run(host='0.0.0.0', port=10000)
threading.Thread(target=run_flask, daemon=True).start()

# === Config ===
BOT_TOKEN = "توكنك"
CHAT_ID = "ايدك"
BENZINGA_KEY = "حط مفتاح بنزنقا هنا" # تجيبه من benzinga.com/api
KSA = timezone(timedelta(hours=3))

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

daily_count = defaultdict(int)
last_reset = datetime.now(KSA).day
seen_ids = set() # عشان ما يكرر نفس الفلو

def now_ksa(): return datetime.now(KSA)

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def get_gamma_wall(ticker):
    try:
        stock = yf.Ticker(ticker)
        price = float(stock.history(period="1d")['Close'].iloc[-1])
        wall = None
        max_oi = 0
        for exp in stock.options[:2]:
            chain = stock.option_chain(exp)
            if chain.calls.empty: continue
            best = chain.calls.loc[chain.calls['openInterest'].idxmax()]
            if best['openInterest'] > max_oi:
                max_oi = best['openInterest']
                wall = best
        if wall is None: return None
        return {"strike": float(wall['strike']), "oi": int(wall['openInterest']), "price": price}
    except:
        return None

def get_benzinga_flow():
    # Benzinga API - احدث فلو للشركات حقتك
    try:
        url = f"https://api.benzinga.com/api/v2/option_activity?token={BENZINGA_KEY}&tickers={','.join(TICKERS)}&limit=50"
        r = requests.get(url, timeout=15).json()
        flows = []
        for item in r.get('option_activity', []):
            # فلتر: بس Calls + Premium عالي
            if item['put_call']!= 'call': continue
            if item['cost_basis'] < 20000: continue
            fid = item['id']
            if fid in seen_ids: continue
            seen_ids.add(fid)
            # لو كبرت اللستة امسح القديم
            if len(seen_ids) > 5000: seen_ids.clear()
            flows.append({
                'ticker': item['ticker'],
                'strike': float(item['strike_price']),
                'premium': float(item['cost_basis']),
                'ask_pct': 95 if 'sweep' in item['description'].lower() or item['is_sweep'] else 80,
                'vol': item['volume'],
                'oi': item['open_interest'],
                'option_price': float(item['price']),
                'desc': item['description'],
                'is_sweep': item.get('is_sweep', False),
                'is_block': item.get('is_block', False)
            })
        return flows
    except Exception as e:
        print(f"Benzinga error {e}")
        return []

send_tg(f"🚀 *V35 + Benzinga اشتغل*\n📊 13 شركة - MU موجودة\n💰 فلو حقيقي\n⏰ {now_ksa().strftime('%I:%M %p')}")

while True:
    try:
        n = now_ksa()
        if n.day!= last_reset and n.hour == 0:
            daily_count.clear()
            last_reset = n.day
            seen_ids.clear()

        if not (n.weekday() < 5 and 16 <= n.hour <= 23):
            time.sleep(60)
            continue

        flows = get_benzinga_flow()
        if not flows:
            time.sleep(15)
            continue

        for flow in flows:
            tk = flow['ticker']
            if daily_count[tk] >= 5: continue

            wall = get_gamma_wall(tk)
            if not wall: continue

            dist = ((wall["strike"] - wall["price"]) / wall["price"] * 100)
            premium = flow['premium']
            ask_pct = flow['ask_pct']

            score = 0
            reasons = []

            # Benzinga scoring
            if flow['is_sweep']:
                score += 4
                reasons.append(f"🧹 SWEEP حقيقي ${premium/1000:.0f}K")
            elif flow['is_block']:
                score += 3
                reasons.append(f"🧱 BLOCK ${premium/1000:.0f}K")
            else:
                if premium >= 100000: score+=3
                reasons.append(f"💰 ${premium/1000:.0f}K")

            if ask_pct >= 90: score+=3; reasons.append(f"🔥 {ask_pct}% Ask ماركت")
            if flow['vol']/flow['oi'] >= 3 if flow['oi'] else False: score+=2

            # ليفل 4
            if 0.2 < dist < 2.0 and wall["oi"] > 15000:
                score+=3
                reasons.append(f"💎 حائط {wall['strike']:.0f} باقي {dist:.1f}%")

            if score >= 8:
                forced = wall["oi"]*50
                msg = f"""💎 *LVL4 {score}/10 - {tk} + BENZINGA* 💎
{'█'*10}

*📊 فلو حقيقي:*
{' | '.join(reasons)}
Strike {flow['strike']} @ ${flow['option_price']:.2f}
{flow['desc'][:100]}

*💥 تورط MM:*
حائط {wall['strike']:.0f} OI {wall['oi']:,}
باقي {dist:.2f}% - مجبور {forced:,} سهم

*⏰ {n.strftime('%I:%M %p')} - {daily_count[tk]+1}/5*"""
                send_tg(msg)
                daily_count[tk] += 1

        time.sleep(20)
    except Exception as e:
        print(e)
        time.sleep(10)
