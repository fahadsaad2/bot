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
  return 'Bot OK - CALL up PUT real down'

def run_web():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER_STABLE = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT', 'AAPL', 'AMD', 'AMZN', 'ARM', 'AVGO', 'RDDT']
TIER_CRAZY = ['SNDK', 'MU', 'MSTR', 'COIN', 'SMCI', 'APP', 'PLTR', 'LITE']
TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI','MSTR','COIN','AAPL','AMD','AMZN','PLTR','APP','ARM','AVGO','MU','LITE','SNDK','RDDT']
SYMBOLS = TIER1 + TIER2

MIN_VOL_TIER1 = 50
MIN_OI_TIER1 = 200
MIN_VOL_TIER2 = 10
MIN_OI_TIER2 = 50
MAX_SPREAD_PCT = 0.35

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = {}

def send(msg):
  try:
    requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=20)
  except Exception as e:
    print(f'SEND ERR {e}', flush=True)

def get_finnhub_quote_safe(sym):
  for _ in range(3):
    try:
      return finnhub_client.quote(sym)
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

def calc_levels(hist, entry_price, gamma_res=None, is_put=False):
  atr = calc_atr(hist, 14)
  if is_put:
    stop = entry_price + (atr * 1.0)
    t1 = entry_price - (atr * 1.5)
    t2 = entry_price - (atr * 3.0)
    t3 = entry_price - (atr * 5.0)
  else:
    t1 = gamma_res if (gamma_res and gamma_res > entry_price and gamma_res < entry_price + atr*2) else entry_price + (atr * 1.5)
    t2 = entry_price + (atr * 3.0)
    t3 = entry_price + (atr * 5.0)
    stop = entry_price - (atr * 1.0)
  return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2), round(atr, 2)

def update_resistances():
  for idx, sym in enumerate(SYMBOLS):
    try:
      print(f"تحميل {sym} {idx+1}/{len(SYMBOLS)}", flush=True)
      hist = yf.Ticker(sym).history(period='3mo', auto_adjust=True)
      if not hist.empty and len(hist) >= 60:
        RES_CACHE[sym] = max(hist['High'].tail(5))
        HIST_CACHE[sym] = hist
      time.sleep(3.0)
    except Exception as e:
      print(f'RES ERR {sym}: {e}', flush=True)
      time.sleep(15)

def get_gamma_walls(sym, price):
  try:
    t = yf.Ticker(sym)
    calls = pd.concat([t.option_chain(exp).calls for exp in t.options[:2]])
    above = calls[calls['strike'] >= price]
    if above.empty: return None
    return {'res_strike': float(above.sort_values('openInterest', ascending=False).iloc[0]['strike'])}
  except: return None

def check_squeeze(hist):
  try:
    if len(hist) < 20: return None
    close = hist['Close']
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper_bb = ma20 + (1.5 * std20)
    lower_bb = ma20 - (1.5 * std20)
    tr = pd.concat([hist['High']-hist['Low'], (hist['High']-close.shift()).abs(), (hist['Low']-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    upper_kc = ma20 + (1.2 * atr)
    lower_kc = ma20 - (1.2 * atr)
    is_squeeze = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
    prev = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
    return {'squeeze': is_squeeze, 'firing': prev and not is_squeeze, 'dir': 'UP' if close.iloc[-1] > ma20.iloc[-1] else 'DOWN'}
  except: return None

def get_opt(sym, price, mode, opt_type='CALL'):
  try:
    t = yf.Ticker(sym)
    exps = t.options
    if not exps: return None
    target_exps = exps[0:2] if mode == 'daily' else exps[2:6]
    best = None
    best_score = -1
    for exp in target_exps:
      try:
        chain = t.option_chain(exp)
        chain = chain.calls if opt_type == 'CALL' else chain.puts
        chain = chain[(chain['lastPrice'] <= 15.0) & (chain['lastPrice'] >= 0.5)]
        filt = chain[(chain['strike'] >= price * 0.95) & (chain['strike'] <= price * 1.10)] if opt_type == 'CALL' else chain[(chain['strike'] <= price * 1.05) & (chain['strike'] >= price * 0.90)]
        if filt.empty: continue
        for _, row in filt.iterrows():
          vol = int(row['volume'] or 0)
          oi = int(row['openInterest'] or 0)
          bid = float(row.get('bid', 0) or 0)
          ask = float(row.get('ask', 0) or 0)
          last = float(row['lastPrice'] or 0)
          if last == 0: continue
          spread = (ask - bid) / last if bid > 0 and ask > 0 else 0.2
          min_vol = MIN_VOL_TIER1 if sym in TIER1 else MIN_VOL_TIER2
          min_oi = MIN_OI_TIER1 if sym in TIER1 else MIN_OI_TIER2
          if vol < min_vol or oi < min_oi or spread > MAX_SPREAD_PCT: continue
          score = vol*0.5 + oi*0.2 - spread*100
          if score > best_score:
            best_score = score
            whale = vol > 500 and (vol / oi * 100 if oi > 0 else 0) > 120
            best = {'strike': row['strike'], 'last': last, 'vol': vol, 'oi': oi, 'exp': exp, 'whale': whale, 'spread': spread}
      except: continue
    return best
  except: return None

def loop():
  update_resistances()
  send('✅ البوت اشتغل - CALL اذا طالع | PUT اذا نزول حقيقي مو تصحيح')
  last_res_update = time.time()
  while True:
    if time.time() - last_res_update > 43200:
      update_resistances()
      last_res_update = time.time()
      sent_squeeze.clear()
    for s in SYMBOLS:
      try:
        res = RES_CACHE.get(s)
        hist = HIST_CACHE.get(s)
        if not res or hist is None:
          time.sleep(1)
          continue
        q = get_finnhub_quote_safe(s)
        if not q:
          time.sleep(1)
          continue
        p = float(q.get('c', 0))
        if p == 0:
          time.sleep(1)
          continue
        rsi = calc_rsi(hist)
        sma50 = calc_sma(hist, 50)
        sq = check_squeeze(hist)
        if sq is None or not (sq['squeeze'] or sq['firing']):
          time.sleep(1)
          continue
        if s in sent_squeeze and (time.time() - sent_squeeze[s] < 10800):
          time.sleep(1)
          continue

        is_put = sq['dir'] == 'DOWN'
        drop_from_res = (res - p) / res * 100 if res else 0

        # ===== منطق CALL و PUT الجديد =====
        if s in TIER_CRAZY:
          if not is_put: # CALL للمجنون
            if p < sma50 or rsi < 45:
              print(f"{s} CALL ملغي مجنون تحت SMA", flush=True)
              time.sleep(1)
              continue
          else: # PUT للمجنون - لازم نزول حقيقي
            is_real_down = (p < sma50) or (drop_from_res > 4.0 and rsi < 50)
            if not is_real_down:
              print(f"{s} PUT ملغي مجنون تصحيح {drop_from_res:.1f}%", flush=True)
              time.sleep(1)
              continue
        else: # العاقل
          if not is_put: # CALL
            if p < sma50 or rsi < 50:
              continue
          else: # PUT
            is_real_down = (p < sma50 and rsi < 45 and drop_from_res > 3.0)
            if not is_real_down:
              continue
        # ===== نهاية المنطق =====

        opt_type = 'PUT' if is_put else 'CALL'
        gamma = get_gamma_walls(s, p)
        stop, t1, t2, t3, atr = calc_levels(hist, p, gamma['res_strike'] if gamma else None, is_put)
        d = get_opt(s, p, 'daily', opt_type)
        w = get_opt(s, p, 'weekly', opt_type)
        if d is None and w is None:
          continue

        crazy_label = '🤪' if s in TIER_CRAZY else '🧠'
        firing_txt = f'🔥 انطلاق {opt_type}' if sq['firing'] else f'⚠️ انضغاط {opt_type}'
        msg = f'{firing_txt} {crazy_label} <b>{s}</b> ${p:.2f} نزول من القمة {drop_from_res:.1f}%\n'
        msg += f'📊 SMA50: {sma50:.2f} | RSI: {rsi:.0f} | مقاومة: {res:.2f}\n'
        if gamma: msg += f"🧱 غاما: {gamma['res_strike']:.0f}$\n"
        msg += f"\n{'🔻 نزول حقيقي' if is_put else '🚀 صعود'} {opt_type}: {p:.2f}$\n🛑 {stop}$ | 🎯 {t1}$ / {t2}$ / {t3}$ ATR {atr}$\n"
        if d: msg += f"\n🔥 يومي {d['exp']} {'🐋' if d['whale'] else ''} {d['strike']}$ @ {d['last']}$ V:{d['vol']}\n"
        if w: msg += f"🛡️ شهري {w['exp']} {w['strike']}$ @ {w['last']}$\n"
        send(msg)
        sent_squeeze[s] = time.time()
        time.sleep(2.5)
      except Exception as e:
        print(f'LOOP ERR {s}: {e}', flush=True)
        time.sleep(2)
    time.sleep(25)

Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
