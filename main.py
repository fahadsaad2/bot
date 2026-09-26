import yfinance as yf
import requests
import time
import pandas as pd
import matplotlib.pyplot as plt
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ============ الإعدادات ============
BOT_TOKEN = "حط توكنك"
CHAT_ID = "حط ايديك"
KSA = timezone(timedelta(hours=3))

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

daily_top = defaultdict(list)
daily_count = defaultdict(int)
last_reset_day = datetime.now(KSA).day

def now_ksa():
    return datetime.now(KSA)

def send_tg(msg, chart_path=None):
    if chart_path:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": msg, "parse_mode": "Markdown"},
            files={"photo": open(chart_path, 'rb')})
    else:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def is_market_open_ksa():
    now = now_ksa()
    # 4:30 عصر - 11 مساء الرياض
    return 16 <= now.hour <= 23 and now.weekday() < 5

def get_gamma_wall(ticker):
    stock = yf.Ticker(ticker)
    price = stock.history(period="1d")['Close'].iloc[-1]
    try:
        walls = []
        for exp in stock.options[:2]:
            chain = stock.option_chain(exp)
            biggest = chain.calls.loc[chain.calls['openInterest'].idxmax()]
            walls.append(biggest)
        wall = sorted(walls, key=lambda x: x['openInterest'], reverse=True)[0]
        return {'strike': wall['strike'], 'oi': wall['openInterest'], 'stock_price': price}
    except:
        return {'strike': price, 'oi': 0, 'stock_price': price}

def plot_gamma_chart(ticker, wall):
    stock = yf.Ticker(ticker)
    price = wall['stock_price']
    plt.figure(figsize=(10,5))
    plt.bar([wall['strike']], [wall['oi']], color='red', alpha=0.7, width=1.5, label=f"حائط {wall['strike']} - {wall['oi']:,} عقد")
    plt.axvline(price, color='green', linewidth=3, label=f'السعر {price:.2f}')
    plt.axvspan(price, wall['strike'], color='gold', alpha=0.3, label='منطقة انفجار 💎')
    plt.title(f"{ticker} - Level 4 Gamma Trap - {now_ksa().strftime('%I:%M %p')} KSA")
    plt.legend()
    plt.grid(alpha=0.3)
    path = f"/tmp/{ticker}_gamma.png"
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    return path

def get_flow_mock(ticker):
    # هنا تحط كود الفلو الحقيقي حقك (Benzinga / UnusualWhales)
    # هذا مثال
    import random
    return {
        'premium': random.randint(20000, 150000),
        'ask_pct': random.randint(60, 98),
        'vol': random.randint(1000, 5000),
        'oi': random.randint(500, 2000),
        'strike': 180,
        'option_price': 0.25,
        'stock_price': get_gamma_wall(ticker)['stock_price']
    }

# ============ اللوب الرئيسي ============
send_tg(f"🚀 بوت V35 ليفل 4 اشتغل\n📊 13 شركة × 5 عقود = 65 توب يوميا\n⏰ {now_ksa().strftime('%d/%m %I:%M %p')} الرياض")

while True:
    now = now_ksa()
    if now.day!= last_reset_day and now.hour == 0:
        daily_top.clear(); daily_count.clear()
        last_reset_day = now.day
        send_tg(f"🔄 تصفير يومي - جاهز لـ 65 عقد توب")

    if not is_market_open_ksa():
        time.sleep(60); continue

    for ticker in TICKERS:
        if daily_count[ticker] >= 5: continue

        flow = get_flow_mock(ticker)
        wall = get_gamma_wall(ticker)

        dist = ((wall['strike'] - wall['stock_price'])/wall['stock_price'])*100 if wall['stock_price'] else 10
        premium, ask_pct, vol, oi = flow['premium'], flow['ask_pct'], flow['vol'], flow['oi']

        score = 0
        reasons = []
        if premium >= 100000: score+=3; reasons.append(f"💰 ${premium/1000:.0f}K")
        elif premium >= 50000: score+=2; reasons.append(f"💰 ${premium/1000:.0f}K")
        if ask_pct >= 90: score+=3; reasons.append(f"🔥 {ask_pct}% Ask")
        elif ask_pct >= 75: score+=2; reasons.append(f"🔥 {ask_pct}% Ask")
        if vol/oi >= 3: score+=2; reasons.append(f"⚡ {vol/oi:.1f}x Vol/OI")
        if 0.3 < dist < 1.8 and wall['oi'] > 15000:
            score+=2; reasons.append(f"💎 حائط {wall['strike']} باقي {dist:.1f}% - تورط MM")

        if score < 7: continue

        data = {'score': score, 'premium': premium, 'wall': wall, 'dist': dist, 'reasons': reasons, **flow, 'sent': False}

        # هل دخل توب 5؟
        daily_top[ticker].append(data)
        daily_top[ticker] = sorted(daily_top[ticker], key=lambda x: (x['score'], x['premium']), reverse=True)[:5]

        if data in daily_top[ticker] and not data['sent'] and score >= 8:
            level = "💎💎💎 ليفل 4 TOP 85%" if score>=9 and dist<1.5 else f"🔥 ليفل {score}/10"

            forced = wall['oi']*100*0.5
            chart = plot_gamma_chart(ticker, wall)

            msg = f"""{level}
{'█'*9} {score}/10

*{ticker} - {flow['strike']}C @ ${flow['option_price']:.2f}*

{' | '.join(reasons)}

*💥 تورط صانع السوق:*
حائط {wall['strike']} فيه {wall['oi']:,} عقد
باقي {dist:.2f}% بس
اذا كسر = مجبور يشتري {forced:,.0f} سهم

*📊 الفلو:*
${premium:,.0f} - {ask_pct}% Ask - Vol/OI {vol/oi:.1f}x

*🎯 الخطة:*
دخول ${flow['option_price']:.2f} قبله
هدف +150% لما يكسر
وقف -30%

*⏰ {now.strftime('%I:%M %p')} الرياض - {daily_count[ticker]+1}/5 اليوم*
*🏢 {ticker} - 13 شركة*"""

            send_tg(msg, chart)
            data['sent'] = True
            daily_count[ticker] += 1

    time.sleep(30)
