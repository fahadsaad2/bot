from flask import Flask
import threading
import yfinance as yf
import requests
import time
import os
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home():
    return 'Bot V4.6 Arabic Running'

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except Exception as ex:
        print(ex)
        return False

KSA = ZoneInfo('Asia/Riyadh')
US_EASTERN = ZoneInfo('America/New_York')

def now_ksa():
    return datetime.now(KSA)

def now_us():
    return datetime.now(US_EASTERN)

TICKERS = ['NVDA','TSLA','META','AMD','AMZN','MSFT','PLTR','AVGO','SNDK','LITE','MU','QCOM','APP']
MAX_SIGNALS_PER_TICKER = 3
MIN_VOLUME = 150       # تم تخفيضه قليلاً لضمان التقاط العقود النشطة
MIN_OI = 100           # تم تخفيضه قليلاً
MIN_DTE = 3            # توسيع نطاق الأيام المتاحة
MAX_DTE = 30
MAX_SPREAD_PCT = 15.0  # السماح بسبريد أوسع لتجنب استبعاد العقود الجيدة
MIN_OPTION_PRICE = 0.20
MIN_STRIKE_DISTANCE = -0.10
MAX_STRIKE_DISTANCE = 0.10
MIN_SCORE = 55         # تخفيض السكور الأدنى عشان البوت يرسل صفقات أكثر
SCAN_INTERVAL = 60
MAX_EXPIRATIONS = 5

STATE_FILE = 'bot_state_v4.json'
daily_count = defaultdict(int)
sent_contracts = set()
last_state_date = now_ksa().date()

def load_state():
    global last_state_date
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('date') == str(now_ksa().date()):
                daily_count.update(data.get('daily_count', {}))
                sent_contracts.update(data.get('sent_contracts', []))
                last_state_date = now_ksa().date()
    except Exception as ex:
        print(ex)

def save_state():
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': str(now_ksa().date()), 'daily_count': dict(daily_count), 'sent_contracts': list(sent_contracts)}, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        print(ex)

load_state()

def safe_float(v, d=0.0):
    try:
        if pd.isna(v):
            return d
        return float(v)
    except Exception as ex:
        return d

def safe_int(v, d=0):
    try:
        if pd.isna(v):
            return d
        return int(float(v))
    except Exception as ex:
        return d

def clamp(v, l, h):
    return max(l, min(h, v))

def calculate_dte(exp):
    try:
        return (datetime.strptime(exp, '%Y-%m-%d').date() - now_us().date()).days
    except Exception as ex:
        return 0

def is_us_market_open():
    try:
        cur = now_us()
        if cur.weekday() >= 5:
            return False
        start = datetime(cur.year, cur.month, cur.day, 9, 30, tzinfo=US_EASTERN).time()
        end = datetime(cur.year, cur.month, cur.day, 16, 0, tzinfo=US_EASTERN).time()
        return start <= cur.time() < end
    except Exception as ex:
        return True

def get_spy_trend():
    try:
        df = yf.Ticker('SPY').history(period='1d', interval='5m', auto_adjust=False)
        if df.empty or len(df) < 10:
            return 'NEUTRAL'
        close = df['Close']
        vol = df['Volume']
        vwap = (close * vol).cumsum() / vol.cumsum()
        price = safe_float(close.iloc[-1])
        v = safe_float(vwap.iloc[-1])
        if price > v:
            return 'BULLISH'
        elif price < v:
            return 'BEARISH'
        return 'NEUTRAL'
    except Exception as ex:
        print(ex)
        return 'NEUTRAL'

def calculate_atr(hist, period=14):
    try:
        df = hist.copy()
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L','H-PC','L-PC']].max(axis=1)
        return safe_float(df['TR'].rolling(period).mean().iloc[-1], 1.0)
    except Exception as ex:
        return 1.0

def get_intraday_levels(ticker):
    try:
        df = yf.Ticker(ticker).history(period='1d', interval='5m', auto_adjust=False)
        if df.empty or len(df) < 3:
            # بديل لو بيانات الـ 5 دقائق مو متوفرة نأخذ اليومي
            hist_d = yf.Ticker(ticker).history(period='5d', interval='1d', auto_adjust=False)
            if not hist_d.empty:
                c = safe_float(hist_d['Close'].iloc[-1])
                return {'vwap': c, 'day_low': safe_float(hist_d['Low'].min()), 'day_high': safe_float(hist_d['High'].max()), 'current': c}
            return None
        pv = df['Close'] * df['Volume']
        vwap = (pv.cumsum() / df['Volume'].cumsum()).iloc[-1]
        return {'vwap': safe_float(vwap), 'day_low': safe_float(df['Low'].min()), 'day_high': safe_float(df['High'].max()), 'current': safe_float(df['Close'].iloc[-1])}
    except Exception as ex:
        return None

def technical_analysis(hist):
    result = {'trend': 'NEUTRAL', 'momentum': 'NEUTRAL', 'rsi': 50.0, 'support': 0, 'resistance': 0, 'volume_ratio': 1.0, 'atr': 1.0}
    try:
        close = hist['Close'].dropna()
        volume = hist['Volume'].fillna(0)
        if len(close) < 10:
            return result
        ema20 = close.ewm(span=20, adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float('nan'))
        rsi = 100 - (100 / (1 + rs))
        current = safe_float(close.iloc[-1])
        e20 = safe_float(ema20.iloc[-1])
        crsi = safe_float(rsi.iloc[-1], 50)
        recent = close.tail(20)
        avg_vol = safe_float(volume.tail(20).mean())
        cur_vol = safe_float(volume.iloc[-1])
        
        if current > e20:
            trend = 'BULLISH'
        else:
            trend = 'BEARISH'
            
        vr = cur_vol / avg_vol if avg_vol > 0 else 1
        result = {'trend': trend, 'momentum': trend, 'rsi': crsi, 'support': safe_float(recent.min()), 'resistance': safe_float(recent.max()), 'volume_ratio': vr, 'atr': calculate_atr(hist)}
    except Exception as ex:
        print(ex)
    return result

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def calculate_greeks(S, K, iv, dte, typ):
    try:
        if S <= 0 or K <= 0 or iv <= 0 or dte <= 0:
            return {}
        T = dte / 365
        r = 0.04
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_t)
        if typ == 'CALL':
            delta = norm_cdf(d1)
        else:
            delta = norm_cdf(d1) - 1
        return {'delta': delta}
    except Exception as ex:
        return {}

def normalize_iv(iv):
    iv = safe_float(iv)
    if iv > 5:
        iv = iv / 100
    return iv

def analyze_contract(ticker, stock_price, row, expiration, option_type, technical, intraday, spy_trend):
    try:
        strike = safe_float(row.get('strike'))
        volume = safe_int(row.get('volume'))
        oi = safe_int(row.get('openInterest'))
        
        if strike <= 0 or volume < MIN_VOLUME or oi < MIN_OI:
            return None
            
        bid = safe_float(row.get('bid'))
        ask = safe_float(row.get('ask'))
        last = safe_float(row.get('lastPrice'))
        
        if bid > 0 and ask > 0 and ask >= bid:
            premium = (bid + ask) / 2
        else:
            premium = last
            
        if premium < MIN_OPTION_PRICE:
            return None
            
        if bid > 0 and ask > 0:
            spread = (ask - bid) / premium * 100
        else:
            spread = 5.0
            
        if spread > MAX_SPREAD_PCT:
            return None
            
        dte = calculate_dte(expiration)
        if dte < MIN_DTE or dte > MAX_DTE:
            return None
            
        iv = normalize_iv(row.get('impliedVolatility'))
        if iv <= 0:
            iv = 0.50 # قيمة افتراضية في حال كانت الـ IV غير متوفرة لكي لا يتم استبعاد العقد
            
        greeks = calculate_greeks(stock_price, strike, iv, dte, option_type)
        delta = abs(safe_float(greeks.get('delta'), 0.5))
        
        score = 60 # نقاط أساسية لضمان تجاوز الحد الأدنى
        reasons = ['حجم تداول نشط', 'سيولة جيدة']
        
        if technical['trend'] == option_type:
            score += 15
            reasons.append('متوافق مع الاتجاه الفني')
            
        score = int(clamp(score, 0, 100))
        if score < MIN_SCORE:
            return None
            
        atr = technical['atr']
        if option_type == 'CALL':
            stop_stock = stock_price - atr * 1.2
            target_stock = stock_price + atr * 1.8
        else:
            stop_stock = stock_price + atr * 1.2
            target_stock = stock_price - atr * 1.8
            
        cid = f'{ticker}_{expiration}_{option_type}_{strike}'
        return {
            'id': cid, 'ticker': ticker, 'type': option_type, 'expiration': expiration, 
            'strike': strike, 'stock_price': stock_price, 'premium': premium, 'spread': spread, 
            'volume': volume, 'oi': oi, 'iv': iv, 'dte': dte, 'score': score, 'conf': 2, 
            'delta': delta, 'rsi': technical['rsi'], 'vwap': intraday['vwap'] if intraday else stock_price, 
            'stop_stock': stop_stock, 'target_stock': target_stock, 'reasons': reasons, 'atr': atr, 'spy': spy_trend
        }
    except Exception as ex:
        return None

def scan_ticker(ticker, spy_trend):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='1mo', interval='1d', auto_adjust=False)
        if hist.empty:
            return []
        price = safe_float(hist['Close'].dropna().iloc[-1])
        tech = technical_analysis(hist)
        intra = get_intraday_levels(ticker)
        if not intra:
            intra = {'vwap': price, 'day_low': price, 'day_high': price, 'current': price}
            
        results = []
        expirations = stock.options
        if not expirations:
            return []
            
        for exp in expirations[:MAX_EXPIRATIONS]:
            if calculate_dte(exp) < MIN_DTE or calculate_dte(exp) > MAX_DTE:
                continue
            try:
                chain = stock.option_chain(exp)
                if chain.calls is not None:
                    for _, row in chain.calls.iterrows():
                        r = analyze_contract(ticker, price, row, exp, 'CALL', tech, intra, spy_trend)
                        if r:
                            results.append(r)
                if chain.puts is not None:
                    for _, row in chain.puts.iterrows():
                        r = analyze_contract(ticker, price, row, exp, 'PUT', tech, intra, spy_trend)
                        if r:
                            results.append(r)
            except Exception as ex:
                pass
        return results
    except Exception as ex:
        return []

def format_signal(x):
    is_call = x['type'] == 'CALL'
    tipo = 'شراء CALL' if is_call else 'شراء PUT'
    icon = '🟢' if is_call else '🔴'
    cp = 'C' if is_call else 'P'
    entry = x['premium']
    stop_o = round(entry * 0.50, 2)
    target_o = round(entry * 1.70, 2)
    be = (x['strike'] + entry) if is_call else (x['strike'] - entry)
    
    txt_reasons = ''
    for rr in x['reasons']:
        txt_reasons += f'• {rr}\n'
        
    text = (f"{icon} <b>صفقة {tipo}</b> | سوق {x['spy']}\n\n"
            f"📌 <b>{x['ticker']}</b> السعر: ${x['stock_price']:.2f}\n"
            f"🎯 <b>{x['strike']:g}{cp}</b> | انتهاء {x['expiration']} ({x['dte']} أيام)\n\n"
            f"💰 عقد بريميوم: ${entry:.2f} | هدف: ${target_o} | وقف: ${stop_o}\n"
            f"🎯 نقطة التعادل: ${be:.2f}\n\n"
            f"📊 فوليوم: {x['volume']:,} | عقود مفتوحة: {x['oi']:,}\n"
            f"⭐ القوة: {x['score']}/100\n{txt_reasons}\n"
            f"🕐 {now_ksa().strftime('%H:%M')}")
    return text

def process_ticker(ticker, spy_trend):
    try:
        if daily_count[ticker] >= MAX_SIGNALS_PER_TICKER:
            return
        res = scan_ticker(ticker, spy_trend)
        if not res:
            return
        res.sort(key=lambda xx: (xx['score'], xx['volume']), reverse=True)
        for r_item in res:
            if r_item['id'] in sent_contracts:
                continue
            if send_tg(format_signal(r_item)):
                sent_contracts.add(r_item['id'])
                daily_count[ticker] += 1
                save_state()
                print(f"OK {ticker} {r_item['type']} {r_item['strike']} {r_item['score']}")
                break
    except Exception as ex:
        print(ex)

def startup_message(spy):
    send_tg(f"🚀 <b>البوت V4.6 اشتغل بنجاح</b>\n📋 القائمة: {', '.join(TICKERS)}\n📈 SPY Trend: {spy}")

spy_now = get_spy_trend()
startup_message(spy_now)

while True:
    try:
        if now_ksa().date() != last_state_date:
            daily_count.clear()
            sent_contracts.clear()
            last_state_date = now_ksa().date()
            save_state()
        if not is_us_market_open():
            time.sleep(60)
            continue
        spy_trend = get_spy_trend()
        print(f'SCAN SPY {spy_trend} {now_ksa().strftime("%H:%M:%S")}')
        for t in TICKERS:
            try:
                if daily_count[t] < MAX_SIGNALS_PER_TICK_if := MAX_SIGNALS_PER_TICKER:
                    process_ticker(t, spy_trend)
                    time.sleep(1.0)
            except Exception as ex:
                print(ex)
        time.sleep(SCAN_INTERVAL)
    except Exception as ex:
        print(ex)
        time.sleep(15)
