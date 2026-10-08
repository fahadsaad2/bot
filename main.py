# -*- coding: utf-8 -*-
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
    return 'Bot OK V58'

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
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    except:
        pass

def get_quote(sym):
    try:
        q = finnhub_client.quote(sym)
        if q and q.get('c',0) > 0:
            return float(q['c'])
    except:
        pass
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
            if hist.empty:
                continue
            if len(hist) < 20:
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

def get_opt(sym, price, opt_type):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps:
            return None
        best = None
        best_score = -999
        max_price = 150 if sym in HIGH_PRICE else 30
        for exp in exps[:3]:
            try:
                ch = t.option_chain(exp)
                if opt_type == 'CALL':
                    data = ch.calls
                else:
                    data = ch.puts
                filt = data[(data['lastPrice'] <= max_price) & (data['lastPrice'] >= 0.10)]
                if opt_type == 'CALL':
                    filt = filt[(filt['strike'] >= price*0.95) & (filt['strike'] <= price*1.10)]
                else:
                    filt = filt[(filt['strike'] <= price*1.05) & (filt['strike'] >= price*0.90)]
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
                    if ask > 0:
                        spread = (ask-bid)/ask
                    else:
                        spread = 1
                    if spread > 0.35:
                        continue
                    score = vol*0.6 + oi*0.4 - spread*100
                    if score > best_score:
                        best_score = score
                        whale = False
                        if oi > 0:
                            if vol > 300 and vol/oi > 1.0:
                                whale = True
                        best = {'strike':row['strike'],'last':last,'vol':vol,'oi':oi,'exp':exp,'whale':whale,'spread':round(spread*100,1)}
            except:
                continue
        return best
    except:
        return None

def loop():
    update_res()
    send('V58 اشتغل - عربي + فحص حي + SNDK MU مبكر')
    last_res = time.time()
    last_hb = time.time()
    while True:
        try:
            if time.time() - last_res > 28800:
                update_res()
                last_res = time.time()
                sent_squeeze.clear()

            if time.time() - last_hb > 3600:
                txt = "فحص حي:\n"
                for sym in ['SNDK','MU','AMD']:
                    hist = HIST_CACHE.get(sym)
                    if hist is None:
                        continue
                    p = float(hist['Close'].iloc[-1])
                    sma = calc_sma(hist)
                    rsi = calc_rsi(hist)
                    sq = check_squeeze(hist)
                    if sq is None:
                        st = "لا بيانات"
                    else:
                        if sq['firing']:
                            st = "انفجار"
                        elif sq['squeeze']:
                            st = "انضغاط"
                        else:
                            st = "عادي"
                    txt = txt + f"{sym}: {p:.2f}$ | {st} | RSI:{rsi:.0f} SMA:{sma:.2f}$\n"
                send(txt)
                last_hb = time.time()

            for s in SYMBOLS:
                try:
                    hist = HIST_CACHE.get(s)
                    res = RES_CACHE.get(s)
                    if hist is None:
                        continue
                    p = get_quote(s)
                    if p == 0:
                        p = float(hist['Close'].iloc[-1])
                    if p == 0:
                        continue
                    if s in sent_squeeze:
                        if time.time() - sent_squeeze[s] < 7200:
                            continue

                    rsi = calc_rsi(hist)
                    sma = calc_sma(hist)
                    sq = check_squeeze(hist)

                    early = False
                    if s in ['SNDK','MU']:
                        if abs(p-sma)/sma*100 < 4.0:
                            if rsi > 35 and rsi < 70:
                                early = True

                    if not early:
                        if sq is None:
                            continue
                        if not sq['squeeze'] and not sq['firing']:
                            continue

                    is_put = False
                    if sq is not None:
                        if sq['dir'] == 'DOWN':
                            is_put = True

                    if is_put:
                        otype = 'PUT'
                    else:
                        otype = 'CALL'

                    atr = calc_atr(hist)
                    stop, t1, t2, t3, atr2 = calc_levels(p, atr, is_put)
                    sent_squeeze[s] = time.time()

                    opt = get_opt(s, p, otype)
                    if opt is None:
                        continue

                    if sq is not None:
                        if sq['firing']:
                            ftxt = 'انفجار'
                        elif sq['squeeze']:
                            ftxt = 'انضغاط'
                        else:
                            ftxt = 'دخول مبكر'
                    else:
                        ftxt = 'دخول مبكر'

                    if s in ['SNDK','MU']:
                        lbl = 'SNDK/MU مبكر'
                    elif s == 'AMD':
                        lbl = 'AMD'
                    elif s in TIER_CRAZY:
                        lbl = 'مجنون'
                    else:
                        lbl = 'مستقر'

                    if opt['whale']:
                        wtxt = 'حيتان'
                    else:
                        wtxt = ''

                    m1 = f"{ftxt} {otype} {lbl} {s} | {p:.2f}$\n"
                    m2 = f"SMA50: {sma:.2f}$ | مقاومة: {res:.2f}$ | RSI: {rsi:.0f}\n"
                    m3 = f"دخول: {p:.2f}$\n"
                    m4 = f"وقف: {stop}$ ATR:{atr2}$\n"
                    m5 = f"T1:{t1}$ T2:{t2}$ T3:{t3}$\n"
                    m6 = f"{opt['exp']} {wtxt}\n"
                    m7 = f"سترايك {opt['strike']}$ - {opt['last']}$ Vol:{opt['vol']} OI:{opt['oi']} فرق:{opt['spread']}%"

                    full = m1 + m2 + "\n" + m3 + m4 + m5 + "\n" + m6 + m7
                    send(full)
                    time.sleep(1.5)
                except Exception as e:
                    print(f"ERR {s} {e}")
                    time.sleep(0.5)
                    continue

        except Exception as e:
            print(f"LOOP MAIN ERR {e}")
            time.sleep(5)
            continue

        time.sleep(15)

Thread(target=loop, daemon=True).start()
while True:
    time.sleep(3600)
