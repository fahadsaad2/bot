# -*- coding: utf-8 -*-
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
    return 'Bot OK - V5.7 Arabic Live'

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
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
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
    except:
        return 50.0

def calc_sma(hist, period=50):
    try:
        return float(hist['Close'].rolling(period).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1])

def calc_atr(hist, period=14):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

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
    print("Updating...", flush=True)
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if hist.empty:
                continue
            if len(hist) < 20:
                continue
            RES_CACHE[sym] = max(hist['High'].tail(5))
            HIST_CACHE[sym] = hist
            time.sleep(0.8)
        except Exception as e:
            print(f'RES ERR {sym}: {e}', flush=True)

def check_squeeze(hist):
    try:
        if len(hist) < 20:
            return None
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
    except:
        return None

def get_opt(sym, price, opt_type='CALL'):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps:
            return None
        target_exps = exps[:3]
        best = None
        best_score = -1
        max_price = 150.0 if sym in HIGH_PRICE else 30.0
        for exp in target_exps:
            try:
                chain = t.option_chain(exp)
                if opt_type == 'CALL':
                    chain_data = chain.calls
                else:
                    chain_data = chain.puts
                filt = chain_data[(chain_data['lastPrice'] <= max_price) & (chain_data['lastPrice'] >= 0.10)]
                if opt_type == 'CALL':
                    filt = filt[(filt['strike'] >= price * 0.95) & (filt['strike'] <= price * 1.10)]
                else:
                    filt = filt[(filt['strike'] <= price * 1.05) & (filt['strike'] >= price * 0.90)]
                if filt.empty:
                    continue
                for _, row in filt.iterrows():
                    vol = int(row['volume'] or 0)
                    oi = int(row['openInterest'] or 0)
                    bid = float(row.get('bid', 0) or 0)
                    ask = float(row.get('ask', 0) or 0)
                    last = float(row['lastPrice'] or 0)
                    if last == 0:
                        continue
                    spread = (ask - bid) / ask if ask > 0 else 1.0
                    if spread > 0.35:
                        continue
                    score = vol * 0.6 + oi * 0.4 - spread * 100
                    if score > best_score:
                        best_score = score
                        if oi > 0:
                            whale = vol > 300 and (vol / oi > 1.0)
                        else:
                            whale = False
                        best = {'strike': row['strike'], 'last': last, 'vol': vol, 'oi': oi, 'exp': exp, 'whale': whale, 'spread': round(spread*100,1)}
            except:
                continue
        return best
    except:
        return None

def loop():
    update_resistances()
    send('V5.7 اشتغل - فحص حي كل ساعة + SNDK/MU دخول حتى بدون انضغاط')
    last_res_update = time.time()
    last_heartbeat = time.time()
    while True:
        if time.time() - last_res_update > 28800:
            update_resistances()
            last_res_update = time.time()
            sent_squeeze.clear()

        if time.time() - last_heartbeat > 3600:
            try:
                hb_msg = "فحص حي:\n"
                for sym in ['SNDK', 'MU', 'AMD']:
                    hist = HIST_CACHE.get(sym)
                    if hist is None:
                        continue
                    p = float(hist['Close'].iloc[-1])
                    sma50 = calc_sma(hist, 50)
                    sq = check_squeeze(hist)
                    rsi = calc_rsi(hist)
                    if sq:
                        if sq['firing']:
                            status = "انفجار"
                        elif sq['squeeze']:
                            status = "انضغاط"
                        else:
                            status = "عادي"
                    else:
                        status = "لا بيانات"
                    line = f"{sym}: {p:.2f}$ | {status} | RSI:{rsi:.0f} | SMA50:{sma50:.2f}$\n"
                    hb_msg += line
                send(hb_msg)
                last_heartbeat = time.time()
            except Exception as e:
                print(f"HB ERR {e}", flush=True)

        for s in SYMBOLS:
            try:
                hist = HIST_CACHE.get(s)
                res = RES_CACHE.get(s)
                if hist is None:
                    continue
                q = get_finnhub_quote_safe(s)
                if q:
                    p = float(q.get('c', 0))
                else:
                    p = float(hist['Close'].iloc[-1])
                if p == 0:
                    continue
                rsi = calc_rsi(hist)
                sma50 = calc_sma(hist, 50)
                sq = check_squeeze(hist)
                if s in sent_squeeze:
                    if (time.time() - sent_squeeze[s] < 7200):
                        continue

                is_early_entry = False
                if s in ['SNDK', 'MU']:
                    near_sma = abs(p - sma50) / sma50 * 100 < 4.0
                    if near_sma and 35 < rsi < 70:
                        is_early_entry = True

                if not is_early_entry:
                    if not sq:
                        continue
                    if not (sq['squeeze'] or sq['firing']):
                        continue

                is_put = False
                if sq:
                    is_put = sq['dir'] == 'DOWN'
                if res:
                    drop_from_res = (res - p) / res * 100
                else:
                    drop_from_res = 0

                if s == 'AMD':
                    if not is_put and rsi < 45:
                        continue
                elif s in TIER_CRAZY:
                    if not is_put and p < sma50:
                        continue

                if is_put:
                    opt_type = 'PUT'
                else:
                    opt_type = 'CALL'
                stop, t1, t2, t3, atr = calc_levels(hist, p, is_put)
                sent_squeeze[s] = time.time()
                opt = get_opt(s, p, opt_type)
                if not opt:
                    continue

                if sq and sq['firing']:
                    firing_txt = 'انفجار'
                elif sq and sq['squeeze']:
                    firing_txt = 'انضغاط'
                else:
                    firing_txt = 'دخول مبكر'

                if s in ['SNDK', 'MU']:
                    label = 'SNDK/MU مبكر'
                elif s == 'AMD':
                    label = 'AMD'
                elif s in TIER_CRAZY:
                    label = 'مجنون'
                else:
                    label = 'مستقر'

                if opt['whale']:
                    whale_txt = 'حيتان'
                else:
                    whale_txt = ''

                msg1 = f"{firing_txt} {opt_type} {label} {s} | {p:.2f}$\n"
                msg2 = f"SMA50: {sma50:.2f}$ | مقاومة: {res:.2f}$ | RSI: {rsi:.0f}\n"
                msg3 = f"دخول: {p:.2f}$\n"
                msg4 = f"وقف: {stop}$ (ATR:{atr}$)\n"
                msg5 = f"T1:{t1}$ T2:{t2}$ T3:{t3}$\n"
                msg6 = f"{opt['exp']} {whale_txt}\n"
                msg7 = f"سترايك {opt['strike']}$ - {opt['last']}$ Vol:{opt['vol']} OI:{opt['oi']} فرق:{opt['spread']}%"
                full_msg = msg1 + msg2 + "\n" + msg3 + msg4 + msg5 + "\n" + msg6 + msg7
