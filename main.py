from datetime import datetime
import os
from threading import Thread
import time
import finnhub
from flask import Flask
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot OK - V5.3 SNDK MU Early'

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI', 'MSTR', 'COIN', 'AAPL', 'AMD', 'AMZN', 'PLTR', 'APP', 'ARM', 'AVGO', 'MU', 'LITE', 'SNDK', 'RDDT']
SYMBOLS = TIER1 + TIER2
TIER_CRAZY = ['SNDK', 'MU', 'MSTR', 'COIN', 'SMCI', 'APP', 'PLTR', 'LITE']
HIGH_PRICE = ['SNDK', 'META', 'AVGO', 'MSTR', 'APP', 'GOOGL', 'MSFT', 'NVDA', 'SMCI', 'COIN']

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = {}

def send(msg):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        print(f'SEND ERR {e}', flush=True)

def get_finnhub_quote_safe(sym):
    for _ in range(3):
        try:
            q = finnhub_client.quote(sym)
            if q and q.get('c', 0) > 0:
                return q
        except Exception as e:
            if '429' in str(e):
                time.sleep(60)
            else:
                break
    return None

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except: return 50.0

def calc_sma(hist, period=50):
    try: return float(hist['Close'].rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1])

def calc_atr(hist, period=14):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(hist, entry_price, is_put=False):
    atr = calc_atr(hist, 14)
    if is_put:
        stop = entry_price + (atr * 1.2)
        t1 = entry_price - (atr * 1.5)
        t2 = entry_price - (atr * 3.0)
        t3 = entry_price - (atr * 4.5)
    else:
        stop = entry_price - (atr * 1.2)
        t1 = entry_price + (atr * 1.5)
        t2 = entry_price + (atr * 3.0)
        t3 = entry_price + (atr * 4.5)
    return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2), round(atr, 2)

def update_resistances():
    print("بدء تحديث البيانات...", flush=True)
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if not hist.empty and len(hist) >= 20:
                RES_CACHE[sym] = max(hist['High'].tail(5))
                HIST_CACHE[sym] = hist
            time.sleep(0.8)
        except Exception as e:
            print(f'RES ERR {sym}: {e}', flush=True)

def check_squeeze(hist):
    try:
        if len(hist) < 20: return None
        close = hist['Close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + (2.0 * std20)
        lower_bb = ma20 - (2.0 * std20)
        tr = pd.concat([hist['High']-hist['Low'], (hist['High']-close.shift()).abs(), (hist['Low']-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + (1.5 * atr)
        lower_kc = ma20 - (1.5 * atr)
        is_squeeze = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        prev_squeeze = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
        firing = prev_squeeze and not is_squeeze
        direction = 'UP' if close.iloc[-1] >= ma20.iloc[-1] else 'DOWN'
        return {'squeeze': is_squeeze, 'firing': firing, 'dir': direction}
    except: return None

def get_opt(sym, price, opt_type='CALL'):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        target_exps = exps[:3]
        best = None
        best_score = -1
        max_price = 150.0 if sym in HIGH_PRICE else 30.0
        for exp in target_exps:
            try:
                chain = t.option_chain(exp)
                chain = chain.calls if opt_type == 'CALL' else chain.puts
                filt = chain[(chain['lastPrice'] <= max_price) & (chain['lastPrice'] >= 0.10)]
                if opt_type == 'CALL':
                    filt = filt[(filt['strike'] >= price * 0.95) & (filt['strike'] <= price * 1.10)]
                else:
                    filt = filt[(filt['strike'] <= price * 1.05) & (filt['strike'] >= price * 0.90)]
                if filt.empty: continue
                for _, row in filt.iterrows():
                    vol = int(row['volume'] or 0)
                    oi = int(row['openInterest'] or 0)
                    bid = float(row.get('bid', 0) or 0)
                    ask = float(row.get('ask', 0) or 0)
                    last = float(row['lastPrice'] or 0)
                    if last == 0: continue
                    spread = (ask - bid) / ask if ask > 0 else 1.0
                    if spread > 0.35: continue
                    score = vol * 0.6 + oi * 0.4 - spread*100
                    if score > best_score:
                        best_score = score
                        whale = vol > 300 and (vol / oi > 1.0 if oi > 0 else False)
                        best = {'strike': row['strike'], 'last': last, 'vol': vol, 'oi': oi, 'exp': exp, 'whale': whale, 'spread': round(spread*100,1)}
            except: continue
        return best
    except: return None

def loop():
    update_resistances()
    send('🚀 V5.3 اشتغل - SNDK و MU مبكر قبل الحركة')
    last_res_update = time.time()
    while True:
        if time.time() - last_res_update > 28800:
            update_resistances()
            last_res_update = time.time()
            sent_squeeze.clear()
        for s in SYMBOLS:
            try:
                hist = HIST_CACHE.get(s)
                res = RES_CACHE.get(s)
                if hist is None: continue
                q = get_finnhub_quote_safe(s)
                p = float(q.get('c', 0)) if q else float(hist['Close'].iloc[-1])
                if p == 0: continue
                rsi = calc_rsi(hist)
                sma50 = calc_sma(hist, 50)
                sq = check_squeeze(hist)
                if not sq or not (sq['squeeze'] or sq['firing']): continue
                if s in sent_squeeze and (time.time() - sent_squeeze[s] < 7200): continue

                is_put = sq['dir'] == 'DOWN'
                drop_from_res = (res - p) / res * 100 if res else 0

                # فلتر AMD
                if s == 'AMD':
                    if not sq['firing']: continue
                    if not is_put:
                        if p < sma50 * 0.99 or rsi < 52 or rsi > 68: continue
                    else:
                        if p > sma50 * 1.01 or rsi > 48 or drop_from_res < 2.0: continue

                # فلتر SNDK و MU المبكر
                elif s in ['SNDK', 'MU']:
                    near_sma = abs(p - sma50) / sma50 * 100 < 3.0
                    if not is_put:
                        if not (near_sma and 42 < rsi < 65 and sq['firing']):
                            continue
                    else:
                        if not (near_sma and 35 < rsi < 60 and sq['firing']):
                            continue

                # باقي المجانين
                elif s in TIER_CRAZY:
                    if not is_put:
                        if p < sma50 or rsi < 45: continue
                    else:
                        if not (p < sma50 or (drop_from_res > 4.0 and rsi < 50)): continue

                # المستقرة
                else:
                    if not is_put:
                        if p < sma50 or rsi < 50: continue
                    else:
                        if not (p < sma50 and rsi < 45 and drop_from_res > 3.0): continue

                opt_type = 'PUT' if is_put else 'CALL'
                stop, t1, t2, t3, atr = calc_levels(hist, p, is_put)
                opt = get_opt(s, p, opt_type)
                if not opt: continue

                firing_txt = f'🔥 انطلاق {opt_type}' if sq['firing'] else f'⚠️ انضغاط {opt_type}'
                label = '⚡ SNDK/MU مبكر' if s in ['SNDK','MU'] else ('🔥 AMD' if s=='AMD' else '🤪' if s in TIER_CRAZY else '🧠')

                msg = f"{firing_txt} {label} <b>{s}</b> | {p:.2f}$\n"
                msg += f"📊 SMA50: {sma50:.2f}$ | مقاومة: {res:.2f}$ | RSI: {rsi:.0f}\n\n"
                msg += f"🚀 دخول: {p:.2f}$\n🛑 وقف: {stop}$ (ATR:{atr}$)\n🎯 T1:{t1}$ T2:{t2}$ T
