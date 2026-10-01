from flask import Flask
import threading
import yfinance as yf
import requests
import time
import os
import json
import math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from curl_cffi import requests as c_requests

app = Flask(__name__)
@app.route('/')
def home():
    return 'Bot V4.7 Fixed Render Block'

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# جلسة تفك حظر ياهوو
session = c_requests.Session(impersonate="chrome")

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except: return False

KSA = ZoneInfo('Asia/Riyadh')
US_EASTERN = ZoneInfo('America/New_York')
def now_ksa(): return datetime.now(KSA)
def now_us(): return datetime.now(US_EASTERN)

TICKERS = ['NVDA','TSLA','META','AMD','AMZN','MSFT','PLTR','AVGO','MU','QCOM'] # شلت LITE/SNDK/APP اللي عليها مشاكل مؤقتا

MAX_SIGNALS_PER_TICKER = 3
MIN_VOLUME = 80
MIN_OI = 80
MIN_DTE = 5
MAX_DTE = 21
MAX_SPREAD_PCT = 15.0
MIN_OPTION_PRICE = 0.35
MIN_STRIKE_DISTANCE = -0.08
MAX_STRIKE_DISTANCE = 0.08
MIN_SCORE = 50
SCAN_INTERVAL = 300 # 5 دقايق عشان لا ينحظر
MAX_EXPIRATIONS = 2

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
    except: pass

def save_state():
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': str(now_ksa().date()), 'daily_count': dict(daily_count), 'sent_contracts': list(sent_contracts)}, f, ensure_ascii=False, indent=2)
    except: pass

load_state()

def safe_float(v, d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except: return d
def safe_int(v, d=0):
    try:
        if pd.isna(v): return d
        return int(float(v))
    except: return d
def clamp(v, l, h): return max(l, min(h, v))
def calculate_dte(exp):
    try: return (datetime.strptime(exp, '%Y-%m-%d').date() - now_us().date()).days
    except: return 0

def is_us_market_open():
    try:
        cur = now_us()
        if cur.weekday() >= 5: return False
        s = datetime(cur.year, cur.month, cur.day, 9, 30, tzinfo=US_EASTERN).time()
        e = datetime(cur.year, cur.month, cur.day, 16, 0, tzinfo=US_EASTERN).time()
        return s <= cur.time() < e
    except: return True

def safe_history(ticker_obj, **kwargs):
    for i in range(3):
        try:
            df = ticker_obj.history(**kwargs)
            if not df.empty: return df
        except Exception as ex:
            print(f"Retry {ticker_obj.ticker} {i}: {ex}")
            time.sleep(5)
    return pd.DataFrame()

def get_spy_trend():
    try:
        t = yf.Ticker('SPY', session=session)
        df = safe_history(t, period='1d', interval='5m', auto_adjust=False)
        if df.empty or len(df) < 20: return 'NEUTRAL'
        close = df['Close']; vol = df['Volume']
        vwap = (close * vol).cumsum() / vol.cumsum()
        ema20 = close.ewm(span=20, adjust=False).mean()
        price = safe_float(close.iloc[-1]); v = safe_float(vwap.iloc[-1]); e = safe_float(ema20.iloc[-1])
        if price > v and price > e: return 'BULLISH'
        if price < v and price < e: return 'BEARISH'
        return 'NEUTRAL'
    except: return 'NEUTRAL'

def calculate_atr(hist, period=14):
    try:
        df = hist.copy()
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L','H-PC','L-PC']].max(axis=1)
        return safe_float(df['TR'].rolling(period).mean().iloc[-1], 1.0)
    except: return 1.0

def get_intraday_levels(ticker_obj):
    try:
        df = safe_history(ticker_obj, period='1d', interval='1m', auto_adjust=False)
        if df.empty or len(df) < 5: return None
        pv = df['Close'] * df['Volume']
        vwap = (pv.cumsum() / df['Volume'].cumsum()).iloc[-1]
        return {'vwap': safe_float(vwap), 'day_low': safe_float(df['Low'].min()), 'day_high': safe_float(df['High'].max()), 'current': safe_float(df['Close'].iloc[-1])}
    except: return None

def technical_analysis(hist):
    result = {'trend': 'NEUTRAL', 'momentum': 'NEUTRAL', 'rsi': 50.0, 'support': 0, 'resistance': 0, 'volume_ratio': 1.0, 'atr': 1.0}
    try:
        close = hist['Close'].dropna(); volume = hist['Volume'].fillna(0)
        if len(close) < 20: return result
        ema20 = close.ewm(span=20, adjust=False).mean(); ema50 = close.ewm(span=min(50, len(close)), adjust=False).mean()
        delta = close.diff(); gain = delta.clip(lower=0).rolling(14).mean(); loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float('nan')); rsi = 100 - (100 / (1 + rs))
        current = safe_float(close.iloc[-1]); e20 = safe_float(ema20.iloc[-1]); e50 = safe_float(ema50.iloc[-1]); crsi = safe_float(rsi.iloc[-1], 50)
        recent = close.tail(20); avg_vol = safe_float(volume.tail(20).mean()); cur_vol = safe_float(volume.iloc[-1])
        trend = 'BULLISH' if current > e20 and e20 > e50 else 'BEARISH' if current < e20 and e20 < e50 else 'NEUTRAL'
        momentum = 'BULLISH' if crsi >= 52 and current > e20 else 'BEARISH' if crsi <= 48 and current < e20 else 'NEUTRAL'
        vr = cur_vol / avg_vol if avg_vol > 0 else 1
        result = {'trend': trend, 'momentum': momentum, 'rsi': crsi, 'support': safe_float(recent.min()), 'resistance': safe_float(recent.max()), 'volume_ratio': vr, 'atr': calculate_atr(hist)}
    except: pass
    return result

def norm_cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def norm_pdf(x): return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
def calculate_greeks(S, K, iv, dte, typ):
    try:
        if S <= 0 or K <= 0 or iv <= 0 or dte <= 0: return {}
        T = dte / 365; r = 0.04; sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_t)
        return {'delta': norm_cdf(d1) if typ == 'CALL' else norm_cdf(d1)-1, 'gamma': norm_pdf(d1)/(S*iv*sqrt_t), 'vega': S*norm_pdf(d1)*sqrt_t/100}
    except: return {}
def normalize_iv(iv):
    iv = safe_float(iv)
    if iv > 5: iv = iv / 100
    return iv
def iv_score(iv):
    p = iv*100
    if 15 <= p <= 55: return 10
    if 10 <= p <= 70: return 6
    if p <= 90: return 2
    return 0

def analyze_contract(ticker, stock_price, row, expiration, option_type, technical, intraday, spy_trend):
    try:
        strike = safe_float(row.get('strike')); volume = safe_int(row.get('volume')); oi = safe_int(row.get('openInterest'))
        if strike <= 0 or volume < MIN_VOLUME or oi < MIN_OI: return None
        bid = safe_float(row.get('bid')); ask = safe_float(row.get('ask')); last = safe_float(row.get('lastPrice'))
        premium = (bid + ask)/2 if bid>0 and ask>=bid else last
        if premium < MIN_OPTION_PRICE: return None
        spread = (ask-bid)/premium*100 if bid>0 and ask>0 else 999
        if spread > MAX_SPREAD_PCT: return None
        dte = calculate_dte(expiration)
        if dte < MIN_DTE or dte > MAX_DTE: return None
        dist = (strike-stock_price)/stock_price
        if dist < MIN_STRIKE_DISTANCE or dist > MAX_STRIKE_DISTANCE: return None
        iv = normalize_iv(row.get('impliedVolatility'))
        if iv <= 0: return None
        greeks = calculate_greeks(stock_price, strike, iv, dte, option_type)
        delta = abs(safe_float(greeks.get('delta')))
        score=0; conf=0; reasons=[]
        if spy_trend!='NEUTRAL': score+=10; conf+=1; reasons.append('سوق متوافق '+spy_trend)
        if intraday and intraday['vwap']>0: score+=15; conf+=1; reasons.append('فوق VWAP' if option_type=='CALL' else 'تحت VWAP')
        if technical['trend']=='BULLISH' and option_type=='CALL': score+=15; conf+=1; reasons.append('اتجاه صاعد')
        if technical['trend']=='BEARISH' and option_type=='PUT': score+=15; conf+=1; reasons.append('اتجاه هابط')
        if technical['momentum']==('BULLISH' if option_type=='CALL' else 'BEARISH'): score+=10; conf+=1; reasons.append('زخم صاعد' if option_type=='CALL' else 'زخم هابط')
        rsi=technical['rsi']
        if 35 <= rsi <= 72: score+=8; reasons.append(f'RSI {rsi:.0f} مناسب')
        if technical['volume_ratio']>=1.5: score+=8; reasons.append('فوليوم عالي')
        if volume/max(oi,1)>=1.5: score+=10; conf+=1; reasons.append('فوليوم قوي')
        if spread<=5: score+=8; conf+=1; reasons.append('سبريد ضيق')
        if 0.30<=delta<=0.70: score+=10; conf+=1; reasons.append(f'دلتا {delta:.2f}')
        score+=iv_score(iv); score=int(clamp(score,0,100))
        if score < MIN_SCORE or conf < 2: return None
        atr=technical['atr']
        stop_stock = stock_price - atr*1.2 if option_type=='CALL' else stock_price + atr*1.2
        target_stock = stock_price + atr*1.8 if option_type=='CALL' else stock_price - atr*1.8
        cid=f'{ticker}_{expiration}_{option_type}_{strike}'
        return {'id': cid, 'ticker': ticker, 'type': option_type, 'expiration': expiration, 'strike': strike, 'stock_price': stock_price, 'premium': premium, 'spread': spread, 'volume': volume, 'oi': oi, 'iv': iv, 'dte': dte, 'score': score, 'conf': conf, 'delta': greeks.get('delta',0), 'rsi': rsi, 'vwap': intraday['vwap'] if intraday else 0, 'stop_stock': stop_stock, 'target_stock': target_stock, 'atr': atr, 'spy': spy_trend, 'reasons': reasons}
    except: return None

def scan_ticker(ticker, spy_trend):
    try:
        stock = yf.Ticker(ticker, session=session)
        hist = safe_history(stock, period='1mo', interval='1d', auto_adjust=False)
        if hist.empty:
            print(f"{ticker}: No history")
            return []
        price = safe_float(hist['Close'].dropna().iloc[-1])
        tech = technical_analysis(hist)
        intra = get_intraday_levels(stock)
        if not intra: return []
        results=[]
        exps = stock.options[:MAX_EXPIRATIONS]
        for exp in exps:
            if calculate_dte(exp) < MIN_DTE or calculate_dte(exp) > MAX_DTE: continue
            try:
                chain = stock.option_chain(exp)
                for _, row in chain.calls.iterrows():
                    r = analyze_contract(ticker, price, row, exp, 'CALL', tech, intra, spy_trend)
                    if r: results.append(r)
                for _, row in chain.puts.iterrows():
                    r = analyze_contract(ticker, price, row, exp, 'PUT', tech, intra, spy_trend)
                    if r: results.append(r)
            except Exception as ex:
                print(f"{ticker} {exp} error {ex}")
                time.sleep(2)
        return results
    except Exception as ex:
        print(f"scan {ticker} failed {ex}")
        return []

def format_signal(x):
    is_call = x['type']=='CALL'
    icon = '🟢' if is_call else '🔴'
    cp = 'C' if is_call else 'P'
    entry = x['premium']; stop_o = round(entry*0.60,2); target_o = round(entry*1.80,2); target2_o = round(entry*2.5,2)
    be = x['strike'] + entry if is_call else x['strike'] - entry
    txt = ''.join([f'• {rr}\n' for rr in x['reasons']])
    return (f"{icon} <b>🐋 حوت دخل - {x['ticker']} {x['type']}</b> | {x['spy']}\n\n"
            f"📌 <b>{x['ticker']}</b> ${x['stock_price']:.2f} | VWAP ${x['vwap']:.2f}\n"
            f"🎯 <b>{x['strike']:g}{cp}</b> ينتهي {x['expiration']} ({x['dte']} يوم)\n\n"
            f"💵 <b>دخول:</b> ${entry:.2f} ({entry*100:.0f}$)\n"
            f"🛑 <b>وقف:</b> ${stop_o} (-40%)\n"
            f"🎯 <b>هدف 1:</b> ${target_o} (+80%)\n"
            f"🚀 <b>هدف 2:</b> ${target2_o} (+150%)\n"
            f"💰 سهم هدف ${x['target_stock']:.2f} وقف ${x['stop_stock']:.2f}\n"
            f"تعادل ${be:.2f}\n\n"
            f"📊 فوليوم {x['volume']:,} | OI {x['oi']:,} | سبريد {x['spread']:.1f}%\n"
            f"⭐ {x['score']}/100 | ثقة {x['conf']}\n{txt}\n🕐 {now_ksa().strftime('%H:%M')}")

def process_ticker(ticker, spy_trend):
    try:
        if daily_count[ticker] >= MAX_SIGNALS_PER_TICKER: return False
        res = scan_ticker(ticker, spy_trend)
        if not res: return
