# -*- coding: utf-8 -*-
import os
from threading import Thread
import time
import finnhub
from flask import Flask
import pandas as pd
import requests
import yfinance as yf
from datetime import datetime, timedelta

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot OK V59 EMOJI'

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

SYMBOLS = ['NVDA','TSLA','GOOGL','META','MSFT','SMCI','MSTR','COIN','AAPL','AMD','AMZN','PLTR','APP','ARM','AVGO','MU','LITE','SNDK','RDDT']
TIER_CRAZY = ['SNDK','MU','MSTR','COIN','SMCI','APP','PLTR','LITE']
HIGH_PRICE = ['SNDK','META','AVGO','MSTR','APP','GOOGL','MSFT','NVDA','SMCI','COIN']

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = {}

def send(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=15)
    except Exception as e:
        print(f"SEND ERR {e}")

def get_quote(sym):
    try:
        q = finnhub_client.quote(sym)
        if q and q.get('c',0) > 0:
            fh = float(q['c'])
            hist = HIST_CACHE.get(sym)
            if hist is not None:
                yf_p = float(hist['Close'].iloc[-1])
                if yf_p > 0 and abs(fh - yf_p)/yf_p*100 > 15:
                    return yf_p
            return fh
    except:
        pass
    hist = HIST_CACHE.get(sym)
    if hist is not None:
        return float(hist['Close'].iloc[-1])
    return 0

def calc_rsi(hist):
    try:
        delta = hist['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except:
        return 50

def calc_sma(hist):
    try:
        return float(hist['Close'].rolling(50).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1])

def calc_atr(hist):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl,hc,lc], axis=1).max(axis=1)
        return float(tr.rolling(14).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(entry, atr, is_put):
    if is_put:
        stop = entry + atr*1.2
        t1 = entry - atr*1.5
        t2 = entry - atr*3.0
        t3 = entry - atr*4.5
    else:
        stop = entry - atr*1.2
        t1 = entry + atr*1.5
        t2 = entry + atr*3.0
        t3 = entry + atr*4.5
    return round(stop,2), round(t1,2), round(t2,2), round(t3,2), round(atr,2)

def update_res():
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if hist.empty or len(hist) < 20:
                continue
            RES_CACHE[sym] = float(max(hist['High'].tail(5)))
            HIST_CACHE[sym] = hist
            time.sleep(0.8)
        except:
            continue

def check_squeeze(hist):
    try:
        if len(hist) < 20:
            return None
        close = hist['Close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + 2*std20
        lower_bb = ma20 - 2*std20
        tr = pd.concat([hist['High']-hist['Low'], (hist['High']-close.shift()).abs(), (hist['Low']-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + 1.5*atr
        lower_kc = ma20 - 1.5*atr
        is_sq = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        prev_sq = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
        firing = prev_sq and not is_sq
        direction = 'UP' if close.iloc[-1] >= ma20.iloc[-1] else 'DOWN'
        return {'squeeze': is_sq, 'firing': firing, 'dir': direction}
    except:
        return None

def get_two_opts(sym, price, opt_type):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps:
            return None, None
        today = datetime.now().date()
        daily_candidates = []
        monthly_candidates = []
        for exp_str in exps:
            try:
                exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                days = (exp_date - today).days
                if 0 <= days <= 7:
                    daily_candidates.append(exp_str)
                elif 8 <= days <= 30:
                    monthly_candidates.append(exp_str)
            except:
                continue
        if not daily_candidates:
            daily_candidates = exps[:1]
        if not monthly_candidates:
            monthly_candidates = exps[1:3] if len(exps) > 1 else exps[:1]

        max_price = 150 if sym in HIGH_PRICE else 30

        def best_for_exp(exp_list):
            best = None
            best_score = -9999
            for exp in exp_list[:3]:
                try:
                    ch = t.option_chain(exp)
                    data = ch.calls if opt_type == 'CALL' else ch.puts
                    filt = data[(data['lastPrice'] <= max_price) & (data['lastPrice'] >= 0.10)]
                    if opt_type == 'CALL':
                        filt = filt[(filt['strike'] >= price*0.95) & (filt['strike'] <= price*1.12)]
                    else:
                        filt = filt[(filt['strike'] <= price*1.05) & (filt['strike'] >= price*0.88)]
                    if filt.empty:
                        continue
                    for _, row in filt.iterrows():
                        vol = int(row['volume'] or 0)
                        oi = int(row['openInterest'] or 0)
                        last = float(row['lastPrice'] or 0)
                        bid = float(row.get('bid',0) or 0)
                        ask = float(row.get('ask',0) or 0)
                        if last == 0:
                            continue
                        spread = (ask-bid)/ask if ask > 0 else 1
                        if spread > 0.4:
                            continue
                        score = vol*0.7 + oi*0.3 - spread*50
                        if score > best_score:
                            best_score = score
                            best = {'strike':row['strike'],'last':last,'vol':vol,'oi':oi,'exp':exp,'spread':round(spread*100,1)}
                except:
                    continue
            return best

        daily = best_for_exp(daily_candidates)
        monthly = best_for_exp(monthly_candidates)
        return daily, monthly
    except:
        return None, None

def loop():
    update_res()
    send('🔥 V59 شغال - تنسيق ايموجي + عقدين يومي وشهري')
    last_res = time.time()
    while True:
        try:
            if time.time() - last_res > 28800:
                update_res()
                last_res = time.time()
                sent_squeeze.clear()

            for s in SYMBOLS:
                try:
                    hist = HIST_CACHE.get(s)
                    res = RES_CACHE.get(s)
                    if hist is None:
                        continue
                    p = get_quote(s)
                    if p == 0:
                        continue
                    if p > 500 and s in ['SNDK','MU','AMD','RDDT','LITE']:
                        continue
                    if s in sent_squeeze and time.time() - sent_squeeze[s] < 7200:
                        continue

                    rsi = calc_rsi(hist)
                    sma = calc_sma(hist)
                    sq = check_squeeze(hist)

                    early = False
                    if s in ['SNDK','MU','RDDT','AMD']:
                        if abs(p-sma)/sma*100 < 4.0 and 40 < rsi < 68:
                            early = True

                    if not early:
                        if sq is None or (not sq['squeeze'] and not sq['firing']):
                            continue

                    is_put = sq['dir'] == 'DOWN' if sq else False
                    if early:
                        is_put = False

                    if is_put and rsi < 35:
                        continue

                    otype = 'PUT' if is_put else 'CALL'
                    atr = calc_atr(hist)
                    stop, t1, t2, t3, atr2 = calc_levels(p, atr, is_put)
                    sent_squeeze[s] = time.time()

                    daily_opt, monthly_opt = get_two_opts(s, p, otype)
                    if not daily_opt and not monthly_opt:
                        continue

                    # حاجز غاما = اعلى مقاومة او اقرب رقم صحيح فوق السعر ب 8%
                    gamma_barrier = res if res else round(p * 1.08, 2)

                    icon = '🧠' if s in TIER_CRAZY else '💎' if s in ['NVDA','META'] else '📈'

                    msg = f"🔥 انطلاق {otype} {icon} {s} سعره {p:.2f}$\n"
                    msg += f"📊 المتوسط 50: {sma:.2f}$ | المقاومة: {res:.2f}$ | RSI: {rsi:.0f}\n"
                    msg += f"🧱 حاجز غاما: {gamma_barrier}$\n"
                    msg += f"\n🚀 دخول {otype}: {p:.2f}$\n"
                    msg += f"🛑 وقف الخسارة: {stop}$ (ATR:{atr2}$)\n"
                    msg += f"🎯 هدف اول: {t1}$\n"
                    msg += f"🎯 هدف ثاني: {t2}$\n"
                    msg += f"🎯 هدف ثالث: {t3}$\n"

                    if daily_opt:
                        msg += f"\n🔥 عقد يومي (0-7 ايام) {daily_opt['exp']} \n"
                        msg += f"💰 سترايك {daily_opt['strike']}$ - سعره {daily_opt['last']}$ | فوليوم: {daily_opt['vol']} | OI: {daily_opt['oi']} | فرق: {daily_opt['spread']}%\n"

                    if monthly_opt:
                        msg += f"\n🛡️ عقد شهري (8-30 يوم) {monthly_opt['exp']}\n"
                        msg += f"💰 سترايك {monthly_opt['strike']}$ - سعره {monthly_opt['last']}$ | فوليوم: {monthly_opt['vol']} | OI: {monthly_opt['oi']} | فرق: {monthly_opt['spread']}%"

                    send(msg)
                    time.sleep(1.5)
                except Exception as e:
                    print(f"ERR {s} {e}")
                    continue
        except Exception as e:
            print(f"MAIN ERR {e}")
            time.sleep(5)
        time.sleep(15)

Thread(target=loop, daemon=True).start()
while True:
    time.sleep(3600)
