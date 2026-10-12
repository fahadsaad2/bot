from datetime import datetime, timedelta
import os
from threading import Thread
import time
from flask import Flask
import pandas as pd
import requests
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest, OptionChainRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import ContractType

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot OK - RealTime + Liquidity + OB + Squeeze + Greeks Filter'
@app.route('/health')
def health():
    return 'OK'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')

print(f"CHECK KEYS: API={bool(ALPACA_API_KEY)} SECRET={bool(ALPACA_SECRET_KEY)} TG={bool(TELEGRAM_BOT_TOKEN)} CHAT={bool(TELEGRAM_CHAT_ID)}", flush=True)

if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
else:
    stock_client = None
    option_client = None

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI', 'MSTR', 'COIN', 'AAPL', 'AMD', 'AMZN', 'PLTR', 'APP', 'ARM', 'AVGO', 'MU', 'LITE', 'RDDT']
SYMBOLS = TIER1 + TIER2

MIN_VOL_TIER1, MIN_OI_TIER1 = 50, 200
MIN_VOL_TIER2, MIN_OI_TIER2 = 10, 50
MAX_SPREAD_PCT = 0.35
RES_CACHE, HIST_CACHE = {}, {}
sent_squeeze = set()
last_cache_clear = datetime.now()

def send(msg):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=20)
        print(f"TG sent", flush=True)
    except Exception as e:
        print(f'SEND ERR {e}', flush=True)

def get_alpaca_price(sym):
    try:
        req = StockLatestTradeRequest(symbol_or_symbols=sym)
        trade = stock_client.get_stock_latest_trade(req)
        return float(trade[sym].price)
    except:
        return None

def calc_atr(hist, p=14):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return float(tr.rolling(p).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(hist, entry, is_squeeze=False, gamma_res=None, is_put=False):
    atr = calc_atr(hist, 14)
    if is_put:
        stop = entry + atr * 1.0
        t1 = entry - atr * 1.5
        t2 = entry - atr * 3.0
        t3 = entry - atr * 5.0
    else:
        if is_squeeze and gamma_res and entry < gamma_res < entry + atr * 2:
            t1 = gamma_res
        else:
            t1 = entry + atr * 1.5
        t2 = entry + atr * 3.0
        t3 = entry + atr * 5.0
        stop = entry - atr * 1.0
    return round(stop,2), round(t1,2), round(t2,2), round(t3,2), round(atr,2)

def calc_liquidity_and_ob(hist):
    try:
        low_20 = hist['Low'].tail(20).min()
        high_20 = hist['High'].tail(20).max()
        curr_low = hist['Low'].iloc[-1]
        curr_high = hist['High'].iloc[-1]
        curr_close = hist['Close'].iloc[-1]
        liq_txt = ""
        if curr_low <= low_20 and curr_close > low_20:
            liq_txt = "SSL"
        elif curr_high >= high_20 and curr_close < high_20:
            liq_txt = "BSL"
        ob_txt = f"{float(hist['Low'].tail(5).min()):.2f}$"
        return None, liq_txt, ob_txt, 0
    except:
        return None, "", "غير محدد", 0

def update_resistances():
    for idx, sym in enumerate(SYMBOLS):
        try:
            print(f"تحميل {sym} {idx+1}/{len(SYMBOLS)}", flush=True)
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day, start=datetime.now() - timedelta(days=120))
            bars = stock_client.get_stock_bars(req)
            df = bars.df
            if df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                hist = df.xs(sym, level=0)
            else:
                hist = df
            hist = hist.rename(columns={'close': 'Close', 'high': 'High', 'low': 'Low', 'open': 'Open', 'volume': 'Volume'})
            if len(hist) >= 60:
                RES_CACHE[sym] = max(hist['High'].tail(5))
                HIST_CACHE[sym] = hist
        except Exception as e:
            print(f'RES ERR {sym}: {e}', flush=True)
            time.sleep(1)

def get_gamma_walls(sym, price):
    try:
        req = OptionChainRequest(underlying_symbol=sym)
        chain = option_client.get_option_chain(req)
        data = chain.option_chain.get(sym, [])
        best = None
        max_oi = 0
        for c in data:
            if c.strike_price >= price and c.open_interest and c.open_interest > max_oi:
                max_oi = c.open_interest
                best = c
        if best:
            return {'res_strike': float(best.strike_price)}
        return None
    except:
        return None

def check_squeeze(hist):
    try:
        if len(hist) < 20:
            return None
        close = hist['Close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + 2 * std20
        lower_bb = ma20 - 2 * std20
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + 1.5 * atr
        lower_kc = ma20 - 1.5 * atr
        is_sq = lower_bb.iloc[-1] > lower_kc.iloc[-1] and upper_bb.iloc[-1] < upper_kc.iloc[-1]
        prev_sq = lower_bb.iloc[-2] > lower_kc.iloc[-2] and upper_bb.iloc[-2] < upper_kc.iloc[-2]
        direction = 'UP' if close.iloc[-1] > ma20.iloc[-1] else 'DOWN'
        return {'squeeze': is_sq, 'firing': prev_sq and not is_sq, 'dir': direction}
    except:
        return None

def get_opt(sym, price, mode, opt_type='CALL'):
    try:
        req = OptionChainRequest(underlying_symbol=sym)
        resp = option_client.get_option_chain(req)
        contracts = resp.option_chain.get(sym, [])
        if not contracts:
            return None
        today = datetime.now().date()
        best = None
        best_score = -1
        if opt_type == 'CALL':
            target = ContractType.CALL
        else:
            target = ContractType.PUT
        if sym in TIER1:
            min_vol = MIN_VOL_TIER1
            min_oi = MIN_OI_TIER1
        else:
            min_vol = MIN_VOL_TIER2
            min_oi = MIN_OI_TIER2
        for c in contracts:
            if c.type!= target:
                continue
            dte = (c.expiration_date - today).days
            if mode == 'daily' and not (0 <= dte <= 7):
                continue
            if mode == 'weekly' and not (8 <= dte <= 14):
                continue
            if mode == 'monthly' and not (15 <= dte <= 45):
                continue
            strike = float(c.strike_price)
            if opt_type == 'CALL':
                if not (price * 0.95 <= strike <= price * 1.10):
                    continue
            else:
                if not (price * 0.90 <= strike <= price * 1.05):
                    continue
            g = getattr(c, 'greeks', None)
            if g:
                d = float(g.delta) if g.delta is not None else 0.5
                th = float(g.theta) if g.theta is not None else 0.0
                if opt_type == 'CALL' and not (0.35 <= d <= 0.70):
                    continue
                if opt_type == 'PUT' and not (-0.70 <= d <= -0.35):
                    continue
                if th < -1.5:
                    continue
            last = 0
            if c.latest_trade:
                last = float(c.latest_trade.price)
            if last == 0 and c.latest_quote:
                b = float(c.latest_quote.bid_price or 0)
                a = float(c.latest_quote.ask_price or 0)
                last = (b + a) / 2
            if not (0.5 <= last <= 15.0):
                continue
            vol = int(c.day.volume) if c.day else 0
            oi = int(c.open_interest or 0)
            if vol < min_vol or oi < min_oi:
                continue
            bid = 0
            ask = 0
            if c.latest_quote:
                if c.latest_quote.bid_price:
                    bid = float(c.latest_quote.bid_price)
                if c.latest_quote.ask_price:
                    ask = float(c.latest_quote.ask_price)
            if ask > 0:
                spread = (ask - bid) / ask
                if spread > MAX_SPREAD_PCT:
                    continue
            score = vol + oi
            if score > best_score:
                best_score = score
                best = {'symbol': c.symbol, 'strike': strike, 'exp': str(c.expiration_date), 'last': last, 'vol': vol, 'oi': oi, 'bid': bid, 'ask': ask}
        return best
    except Exception as e:
        print(f"OPT ERR {sym}: {e}", flush=True)
        return None

def clear_old_squeeze_cache():
    global last_cache_clear, sent_squeeze
    if datetime.now() - last_cache_clear > timedelta(hours=12):
        sent_squeeze.clear()
        last_cache_clear = datetime.now()

def main_loop():
    time.sleep(5)
    send("✅ Bot Started - Squeeze + Gamma")
    update_resistances()
    while True:
        try:
            clear_old_squeeze_cache()
            for sym in SYMBOLS:
                price = get_alpaca_price(sym)
                if not price:
                    continue
                hist = HIST_CACHE.get(sym)
                if hist is None:
                    continue
                sq = check_squeeze(hist)
                gamma = get_gamma_walls(sym, price)
                if sq and sq.get('firing') and sym not in sent_squeeze:
                    if sq['dir'] == 'UP':
                        otype = 'CALL'
                    else:
                        otype = 'PUT'
                    opt = get_opt(sym, price, 'daily', otype)
                    if opt:
                        _, liq_txt, ob_txt, _ = calc_liquidity_and_ob(hist)
                        is_put = (sq['dir'] == 'DOWN')
                        gl = gamma['res_strike'] if gamma else None
                        stop, t1, t2, t3, atr = calc_levels(hist, price, True, gl, is_put=is_put)
                        msg = f"🔥 {sym} SQUEEZE {sq['dir']} ${price:.2f} -> {opt['strike']} {opt['exp']} SL:{stop} T1:{t1}"
                        send(msg)
                        sent_squeeze.add(sym)
                time.sleep(1)
            time.sleep(60)
        except Exception as e:
            print(f"LOOP ERR {e}", flush=True)
            time.sleep(10)

if not hasattr(app, 'started'):
    Thread(target=main_loop, daemon=True).start()
    app.started = True

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
