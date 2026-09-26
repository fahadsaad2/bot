import yfinance as yf
import requests
import time
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BOT_TOKEN = "حط توكنك"
CHAT_ID = "حط ايديك"
KSA = timezone(timedelta(hours=3))
TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

daily_top = defaultdict(list)
daily_count = defaultdict(int)
last_reset_day = datetime.now(KSA).day

def now_ksa():
    return datetime.now(KSA)

def send_tg(msg):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def is_market_open_ksa():
    now = now_ksa()
    return 16 <= now.hour <= 23 and now.weekday() < 5

def get_gamma_wall(ticker):
    try:
        stock = yf.Ticker(ticker)
        price = stock.history(period="1d")['Close'].iloc[-1]
        walls = []
        for exp in stock.options[:2]:
            chain = stock.option_chain(exp)
            biggest = chain.calls.loc[chain.calls['openInterest'].idxmax()]
            walls.append(biggest)
        wall = sorted(walls, key=lambda x: x['openInterest'], reverse=True)[0]
        return {'strike': wall['strike'], 'oi': wall['openInterest'], 'stock_price': price}
    except:
        return {'strike': 0, 'oi': 0, 'stock_price': 0}

def get_flow_mock(ticker):
    return {
        'premium': random.randint(20000, 150000),
        'ask_pct': random.randint(60, 98),
        'vol': random.randint(1000, 5000),
        'oi': random.randint(500, 2000),
        'strike': 180,
        'option_price': 0.25,
        'stock_price': 179
    }

send_tg(f"🚀 V35 Fixed اشتغل بدون شارت\n📊 13 شركة × 5 = 65 عقد\n⏰ {now_ksa().strftime('%I:%M %p')} الرياض")

while True:
    now = now_ksa()
    if now.day!= last_reset_day and now.hour == 0:
        daily_top.clear(); daily_count.clear()
        last_reset_day = now.day

    if not is_market_open_ksa():
        time.sleep(60); continue

    for ticker in TICKERS:
        if daily_count[ticker] >= 5: continue
        flow = get_flow_mock(ticker)
        wall = get_gamma_wall(ticker)
        if wall['oi'] == 0: continue

        dist = ((wall['strike'] - wall['stock_price'])/wall['stock_price']*100) if wall['stock_price'] else 10
        premium, ask_pct, vol, oi = flow['premium'], flow['ask_pct'], flow['vol'], flow['oi']
        score = 0; reasons = []
        if premium >= 50000: score+=3; reasons.append(f"💰 ${premium/1000:.0f}K")
        if ask_pct >= 75: score+=3; reasons.append(f"🔥 {ask_pct}% Ask")
        if vol/oi >= 2: score+=2
        if 0.3 < dist < 1.8 and wall['oi'] > 15000:
            score+=2; reasons.append(f"💎 حائط {wall['strike']} باقي {dist:.1f}%")

        if score < 7: continue
        data = {'score': score, 'premium': premium, 'wall': wall, 'dist': dist, 'reasons': reasons, **flow, 'sent': False}
        daily_top[ticker].append(data)
        daily_top[ticker] = sorted(daily_top[ticker], key=lambda x: (x['score'], x['premium']), reverse=True)[:5]

        if data in daily_top[ticker] and not data['sent'] and score >= 8:
            forced = wall['oi']*100*0.5
            msg = f"""💎 *ليفل 4 - {score}/10* 💎
*{ticker} - {flow['strike']}C @ ${flow['option_price']:.2f}*
{' | '.join(reasons)}
💥 تورط: حائط {wall['strike']} فيه {wall['oi']:,} عقد باقي {dist:.1f}%
اذا كسر = {forced:,.0f} سهم شراء اجباري
${premium:,.0f} - {ask_pct}% Ask
⏰ {now.strftime('%I:%M %p')} الرياض - {daily_count[ticker]+1}/5"""
            send_tg(msg)
            data['sent'] = True
            daily_count[ticker] += 1
    time.sleep(30)
