# -*- coding: utf-8 -*-
import os
import time
from threading import Thread
from datetime import datetime
import pytz
import finnhub
from flask import Flask
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot OK V61 Multi-Option PRO'

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

sent_signals = {}

def is_market_open():
    """تحديد ساعات العمل الرسمية لسوق الأسهم الأمريكي"""
    tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def send(msg):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
            timeout=15
        )
    except Exception as e:
        print(f"SEND ERR {e}")

def fetch_intraday_data(sym):
    try:
        df = yf.Ticker(sym).history(period='5d', interval='5m', auto_adjust=True)
        if df.empty or len(df) < 30:
            return None
        return df
    except:
        return None

def calc_indicators(df):
    try:
        close = df['Close']
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs)).iloc[-1])
        
        sma = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else float(close.iloc[-1])
        
        hl = df['High'] - df['Low']
        hc = (df['High'] - close.shift()).abs()
        lc = (df['Low'] - close.shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        
        res = float(df['High'].max())
        
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + 2 * std20
        lower_bb = ma20 - 2 * std20
        
        tr20 = tr.rolling(20).mean()
        upper_kc = ma20 + 1.5 * tr20
        lower_kc = ma20 - 1.5 * tr20
        
        is_sq = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        prev_sq = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
        firing = prev_sq and not is_sq  # خروج من الانضغاط وانطلاق السهم
        direction = 'UP' if close.iloc[-1] >= ma20.iloc[-1] else 'DOWN'
        
        return {
            'price': float(close.iloc[-1]),
            'rsi': rsi,
            'sma': sma,
            'atr': atr,
            'res': res,
            'sq_firing': firing,
            'dir': direction
        }
    except:
        return None

def calc_levels(entry, atr, is_put):
    if is_put:
        stop = entry + atr * 1.2
        t1 = entry - atr * 1.5
        t2 = entry - atr * 3.0
        t3 = entry - atr * 4.5
    else:
        stop = entry - atr * 1.2
        t1 = entry + atr * 1.5
        t2 = entry + atr * 3.0
        t3 = entry + atr * 4.5
    return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2)

def get_multi_options(sym, price, opt_type):
    """جلب العقود اليومية والأسبوعية والشهرية وفق تدرج تواريخ الانتهاء"""
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps:
            return None, None, None
            
        today = datetime.now().date()
        daily_exp, weekly_exp, monthly_exp = None, None, None
        
        for exp_str in exps:
            try:
                exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                days = (exp_date - today).days
                if 0 <= days <= 3 and not daily_exp:
                    daily_exp = exp_str
                elif 4 <= days <= 10 and not weekly_exp:
                    weekly_exp = exp_str
                elif 11 <= days <= 30 and not monthly_exp:
                    monthly_exp = exp_str
            except:
                continue
                
        def parse_chain(exp):
            if not exp:
                return None
            try:
                ch = t.option_chain(exp)
                data = ch.calls if opt_type == 'CALL' else ch.puts
                if data.empty:
                    return None
                
                if opt_type == 'CALL':
                    data = data[(data['strike'] >= price * 0.98) & (data['strike'] <= price * 1.08)]
                else:
                    data = data[(data['strike'] <= price * 1.02) & (data['strike'] >= price * 0.92)]
                
                if data.empty:
                    return None
                
                data['score'] = data['volume'].fillna(0) * 0.7 + data['openInterest'].fillna(0) * 0.3
                best_row = data.sort_values(by='score', ascending=False).iloc[0]
                
                bid = float(best_row.get('bid', 0) or 0)
                ask = float(best_row.get('ask', 0) or 0)
                spread = round(((ask - bid) / ask) * 100, 1) if ask > 0 else 0
                
                return {
                    'strike': best_row['strike'],
                    'last': float(best_row['lastPrice'] or 0),
                    'vol': int(best_row['volume'] or 0),
                    'oi': int(best_row['openInterest'] or 0),
                    'exp': exp,
                    'spread': spread
                }
            except:
                return None

        return parse_chain(daily_exp), parse_chain(weekly_exp), parse_chain(monthly_exp)
    except:
        return None, None, None

def loop():
    send("🚀 تم تشغيل البوت الاحترافي V61 - مراقبة الانفجار وتوليد عقود يومية وأسبوعية وشهرية")
    
    while True:
        try:
            if not is_market_open():
                time.sleep(300)
                continue
                
            for s in SYMBOLS:
                try:
                    if s in sent_signals and time.time() - sent_signals[s] < 7200:
                        continue

                    df = fetch_intraday_data(s)
                    if df is None:
                        continue

                    ind = calc_indicators(df)
                    if not ind:
                        continue

                    # إشارة انفجار الانضغاط
                    if not ind['sq_firing']:
                        continue

                    is_put = (ind['dir'] == 'DOWN')
                    
                    if is_put and ind['rsi'] < 32:
                        continue
                    if not is_put and ind['rsi'] > 68:
                        continue

                    otype = 'PUT' if is_put else 'CALL'
                    p = ind['price']
                    stop, t1, t2, t3 = calc_levels(p, ind['atr'], is_put)
                    
                    daily_opt, weekly_opt, monthly_opt = get_multi_options(s, p, otype)
                    
                    if not daily_opt and not weekly_opt and not monthly_opt:
                        continue

                    sent_signals[s] = time.time()

                    icon = '🧠' if s in TIER_CRAZY else '⚡'
                    gamma_barrier = ind['res'] if ind['res'] else round(p * 1.05, 2)

                    msg = f"🔥 انطلاق انفجار السعر {otype} {icon} {s}\n"
                    msg += f"💵 نقطة الدخول اللحظية: {p:.2f}$\n"
                    msg += f"📊 RSI: {ind['rsi']:.0f} | المتوسط 50: {ind['sma']:.2f}$\n"
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
                    time.sleep(2)
                except Exception as e:
                    print(f"ERR {s}: {e}")
                    continue

        except Exception as e:
            print(f"MAIN ERR: {e}")
            time.sleep(10)
            
        time.sleep(30)

Thread(target=loop, daemon=True).start()

while True:
    time.sleep(3600)
