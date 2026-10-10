# -*- coding: utf-8 -*-
import os
from threading import Thread
import time
from datetime import datetime
import finnhub
from flask import Flask
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home():
    return 'Bot V69 Squeeze & Real Breakout Alerts'

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
HIGH_PRICE = ['META','AVGO','MSTR','APP','GOOGL','MSFT','NVDA','SMCI','COIN','SNDK']

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = {}
sent_firing = {}

def send(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=15)
    except Exception as e:
        print(f"SEND ERR {e}")

def get_quote_safe(sym, hist):
    try:
        q = finnhub_client.quote(sym)
        if q and q.get('c',0) > 0:
            fh = float(q['c'])
            if hist is not None and not hist.empty:
                yf_p = float(hist['Close'].iloc[-1])
                if yf_p > 0 and abs(fh - yf_p)/yf_p*100 > 15:
                    return yf_p
            return fh
    except:
        pass
    return float(hist['Close'].iloc[-1]) if hist is not None and not hist.empty else 0

def fetch_fresh_history(sym):
    try:
        df = yf.Ticker(sym).history(period='5d', interval='5m', auto_adjust=True)
        if not df.empty and len(df) >= 20:
            HIST_CACHE[sym] = df
            return df
    except: pass
    return HIST_CACHE.get(sym)

def calc_rsi(hist):
    try:
        d = hist['Close'].diff()
        g = d.where(d > 0, 0).rolling(14).mean()
        l = -d.where(d < 0, 0).rolling(14).mean()
        rs = g / l
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except: return 50

def calc_sma(hist):
    try: return float(hist['Close'].rolling(50).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1])

def calc_atr(hist):
    try:
        hl = hist['High']-hist['Low']
        hc = (hist['High']-hist['Close'].shift()).abs()
        lc = (hist['Low']-hist['Close'].shift()).abs()
        tr = pd.concat([hl,hc,lc], axis=1).max(axis=1)
        return float(tr.rolling(14).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1]*0.02)

def calc_levels(entry, atr, is_put):
    if is_put:
        return round(entry+atr*1.2,2), round(entry-atr*1.5,2), round(entry-atr*3.0,2), round(entry-atr*4.5,2)
    else:
        return round(entry-atr*1.2,2), round(entry+atr*1.5,2), round(entry+atr*3.0,2), round(entry+atr*4.5,2)

def update_res():
    for sym in SYMBOLS:
        try:
            hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if hist.empty: continue
            RES_CACHE[sym] = float(max(hist['High'].tail(20)))
            time.sleep(0.3)
        except: continue

def check_squeeze(hist, current_p):
    try:
        if len(hist) < 20: return None
        close = hist['Close'].copy()
        if current_p > 0:
            close.iloc[-1] = current_p
            
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + 2*std20
        lower_bb = ma20 - 2*std20
        
        tr = pd.concat([hist['High']-hist['Low'], (hist['High']-close.shift()).abs(), (hist['Low']-close.shift()).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        upper_kc = ma20 + 1.5*atr20
        lower_kc = ma20 - 1.5*atr20
        
        # 1. حالة الانضغاط حالياً
        is_sq = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        
        # 2. كشف الانفجار (توسع البولنجر وخروج السعر فوق/تحت القناة)
        prev_sq = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
        is_breakout = (current_p > upper_bb.iloc[-1]) or (current_p < lower_bb.iloc[-1])
        firing = (prev_sq or not is_sq) and is_breakout
        
        curr_close = close.iloc[-1]
        ma_val = ma20.iloc[-1]
        direction = 'UP' if curr_close >= ma_val else 'DOWN'
        
        return {'squeeze': is_sq, 'firing': firing, 'dir': direction}
    except: return None

def get_three_opts(sym, price, opt_type):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None,None,None
        today = datetime.now().date()
        daily_exp, weekly_exp, monthly_exp = None,None,None
        for exp_str in exps:
            try:
                d = datetime.strptime(exp_str, '%Y-%m-%d').date()
                days = (d - today).days
                if 0 <= days <= 3 and not daily_exp: daily_exp = exp_str
                elif 4 <= days <= 10 and not weekly_exp: weekly_exp = exp_str
                elif 11 <= days <= 30 and not monthly_exp: monthly_exp = exp_str
            except: continue

        max_price = 150 if sym in HIGH_PRICE else 30

        def parse_chain(exp):
            if not exp: return None
            try:
                ch = t.option_chain(exp)
                data = ch.calls if opt_type == 'CALL' else ch.puts
                if data.empty: return None
                filt = data[(data['lastPrice'] <= max_price) & (data['lastPrice'] >= 0.05)]
                if opt_type == 'CALL':
                    filt = filt[(filt['strike'] >= price*0.95) & (filt['strike'] <= price*1.12)]
                else:
                    filt = filt[(filt['strike'] <= price*1.05) & (filt['strike'] >= price*0.88)]
                if filt.empty: return None
                filt = filt.copy()
                filt['score'] = filt['volume'].fillna(0)*0.7 + filt['openInterest'].fillna(0)*0.3
                best = filt.sort_values(by='score', ascending=False).iloc[0]
                bid = float(best.get('bid',0) or 0)
                ask = float(best.get('ask',0) or 0)
                spread = round(((ask-bid)/ask)*100,1) if ask>0 else 0
                return {'strike':best['strike'],'last':float(best['lastPrice'] or 0),'vol':int(best['volume'] or 0),'oi':int(best['openInterest'] or 0),'exp':exp,'spread':spread}
            except: return None

        return parse_chain(daily_exp), parse_chain(weekly_exp), parse_chain(monthly_exp)
    except: return None,None,None

def loop():
    update_res()
    send('⚡ V69 شغال - تم تفكيك الانضغاط عن الانفجار بدقة')
    last_res = time.time()

    while True:
        try:
            if time.time() - last_res > 28800:
                update_res()
                last_res = time.time()

            for s in SYMBOLS:
                try:
                    hist = fetch_fresh_history(s)
                    if hist is None or hist.empty: continue
                    
                    p = get_quote_safe(s, hist)
                    if p == 0: continue

                    rsi = calc_rsi(hist)
                    sma = calc_sma(hist)
                    atr = calc_atr(hist)
                    sq = check_squeeze(hist, p)

                    if not sq: continue

                    now = time.time()
                    
                    # 💥 أولوية الانفجار أولاً
                    if sq['firing']:
                        if s in sent_firing and now - sent_firing[s] < 1800:
                            continue
                        event_type = "FIRING"
                    elif sq['squeeze']:
                        if s in sent_squeeze and now - sent_squeeze[s] < 2700:
                            continue
                        event_type = "SQUEEZE"
                    else:
                        continue

                    is_put = (sq['dir'] == 'DOWN')
                    if is_put and rsi < 35: continue
                    if not is_put and rsi > 65: continue

                    otype = 'PUT' if is_put else 'CALL'
                    stop, t1, t2, t3 = calc_levels(p, atr, is_put)
                    daily_opt, weekly_opt, monthly_opt = get_three_opts(s, p, otype)
                    if not daily_opt and not weekly_opt and not monthly_opt:
                        continue

                    # تسجيل التنبيه المرسل
                    if event_type == "FIRING":
                        sent_firing[s] = now
                    else:
                        sent_squeeze[s] = now

                    icon = '🧠' if s in TIER_CRAZY else '⚡'
                    res = RES_CACHE.get(s, 0)
                    gamma_barrier = res if res else round(p*1.05,2)

                    if event_type == "FIRING":
                        msg = f"🔥 انطلاق انفجار السعر الآن {otype} {icon} {s}\n"
                    else:
                        msg = f"⏳ تنبيه انضغاط متوقع انفجاره ({otype}) {icon} {s}\n"

                    msg += f"💵 نقطة الدخول اللحظية: {p:.2f}$\n"
                    msg += f"📊 RSI: {rsi:.0f} | المتوسط 50: {sma:.2f}$\n"
                    msg += f"🧱 حاجز المقاومة/غاما: {gamma_barrier:.2f}$\n\n"
                    msg += f"🛑 وقف الخسارة: {stop}$\n"
                    msg += f"🎯 الهدف الأول: {t1}$\n"
                    msg += f"🎯 الهدف الثاني: {t2}$\n"
                    msg += f"🎯 الهدف الثالث: {t3}$\n"

                    if daily_opt:
                        msg += f"\n⚡ عقد يومي/سريع ({daily_opt['exp']})\n"
                        msg += f"🔹 سترايك: {daily_opt['strike']}$ | السعر: {daily_opt['last']}$\n"
                        msg += f"📈 فوليوم: {daily_opt['vol']} | OI: {daily_opt['oi']} | الفرق: {daily_opt['spread']}%\n"
                    if weekly_opt:
                        msg += f"\n📅 عقد أسبوعي ({weekly_opt['exp']})\n"
                        msg += f"🔹 سترايك: {weekly_opt['strike']}$ | السعر: {weekly_opt['last']}$\n"
                        msg += f"📈 فوليوم: {weekly_opt['vol']} | OI: {weekly_opt['oi']} | الفرق: {weekly_opt['spread']}%\n"
                    if monthly_opt:
                        msg += f"\n🛡️ عقد شهري/آمن ({monthly_opt['exp']})\n"
                        msg += f"🔹 سترايك: {monthly_opt['strike']}$ | السعر: {monthly_opt['last']}$\n"
                        msg += f"📈 فوليوم: {monthly_opt['vol']} | OI: {monthly_opt['oi']} | الفرق: {monthly_opt['spread']}%\n"

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
