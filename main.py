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
    return 'Bot OK - Full Details Arabic Style'

@app.route('/health')
def health():
    return 'OK'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')

stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI', 'MSTR', 'COIN', 'AAPL', 'AMD', 'AMZN', 'PLTR', 'APP', 'ARM', 'AVGO', 'MU', 'LITE', 'RDDT', 'SNDK']
SYMBOLS = TIER1 + TIER2

RES_CACHE, HIST_CACHE = {}, {}
sent_signals = {}

def send(msg):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=20
        )
    except Exception as e:
        print(f'SEND ERR {e}')

def get_alpaca_price(sym):
    try:
        req = StockLatestTradeRequest(symbol_or_symbols=sym)
        trade = stock_client.get_stock_latest_trade(req)
        return float(trade[sym].price)
    except:
        return None

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        return float((100 - (100 / (1 + rs))).iloc[-1])
    except:
        return 50.0

def calc_atr(hist, p=14):
    try:
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return float(tr.rolling(p).mean().iloc[-1])
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(hist, entry, is_put=False):
    atr = calc_atr(hist, 14)
    if is_put:
        stop = entry + (atr * 1.0)
        t1 = entry - (atr * 1.5)
        t2 = entry - (atr * 3.0)
        t3 = entry - (atr * 5.0)
    else:
        stop = entry - (atr * 1.0)
        t1 = entry + (atr * 1.5)
        t2 = entry + (atr * 3.0)
        t3 = entry + (atr * 5.0)
    return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2)

def calc_liquidity_and_ob(hist):
    try:
        low_20 = hist['Low'].tail(20).min()
        high_20 = hist['High'].tail(20).max()
        curr_low = hist['Low'].iloc[-1]
        curr_high = hist['High'].iloc[-1]
        curr_close = hist['Close'].iloc[-1]

        liq_type, liq_txt = None, "بدون سحب سيولة"
        if curr_low <= low_20 and curr_close > low_20:
            liq_type, liq_txt = "SSL", "💧 سحب سيولة SSL + ارتداد"
        elif curr_high >= high_20 and curr_close < high_20:
            liq_type, liq_txt = "BSL", "💧 سحب سيولة BSL + ارتداد"

        ob_low, ob_high = 0, 0
        ob_txt = "غير محدد"
        last = hist.tail(25)

        for i in range(len(last) - 4, 5, -1):
            if last['Close'].iloc[i] < last['Open'].iloc[i]:
                if last['Close'].iloc[i + 1] > last['Open'].iloc[i + 1] and last['Close'].iloc[i + 1] > last['Close'].iloc[i]:
                    ob_low = float(last['Low'].iloc[i])
                    ob_high = float(last['High'].iloc[i])
                    ob_txt = f"{ob_low:.2f}$ - {ob_high:.2f}$"
                    break
        if ob_low == 0:
            ob_low = float(hist['Low'].tail(5).min())
            ob_high = float(curr_close)
            ob_txt = f"{ob_low:.2f}$ - {ob_high:.2f}$"

        return liq_type, liq_txt, ob_txt
    except:
        return None, "بدون سحب سيولة", "غير محدد"

def update_resistances():
    for idx, sym in enumerate(SYMBOLS):
        try:
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day, start=datetime.now() - timedelta(days=120))
            bars = stock_client.get_stock_bars(req)
            df = bars.df
            if df.empty:
                continue
            hist = df.xs(sym, level=0) if isinstance(df.index, pd.MultiIndex) else df
            hist = hist.rename(columns={'close': 'Close', 'high': 'High', 'low': 'Low', 'open': 'Open', 'volume': 'Volume'})
            if len(hist) >= 20:
                RES_CACHE[sym] = {
                    'res': max(hist['High'].tail(5)),
                    'sup': min(hist['Low'].tail(5))
                }
                HIST_CACHE[sym] = hist
            time.sleep(0.2)
        except Exception as e:
            print(f'RES ERR {sym}: {e}')

def get_specific_opt(sym, price, mode, opt_type):
    try:
        req = OptionChainRequest(underlying_symbol=sym)
        resp = option_client.get_option_chain(req)
        contracts = resp.option_chain.get(sym, [])
        if not contracts:
            return None

        today = datetime.now().date()
        best = None
        best_score = -1
        target = ContractType.CALL if opt_type == 'CALL' else ContractType.PUT

        for c in contracts:
            if c.type != target:
                continue

            dte = (c.expiration_date - today).days
            if mode == 'daily' and not (0 <= dte <= 7):
                continue
            if mode == 'weekly' and not (8 <= dte <= 14):
                continue
            if mode == 'monthly' and not (15 <= dte <= 45):
                continue

            strike = float(c.strike_price)
            if opt_type == 'CALL' and not (price * 0.90 <= strike <= price * 1.15):
                continue
            if opt_type == 'PUT' and not (price * 0.85 <= strike <= price * 1.10):
                continue

            last = float(c.latest_trade.price) if c.latest_trade else 0
            if last == 0 and c.latest_quote:
                b = float(c.latest_quote.bid_price or 0)
                a = float(c.latest_quote.ask_price or 0)
                last = (b + a) / 2

            if not (0.3 <= last <= 50.0):
                continue

            vol = int(c.day.volume) if c.day else 0
            oi = int(c.open_interest or 0)

            score = vol + oi
            if score > best_score:
                best_score = score
                
                # حساب الفرق النسبي بين Strike والسعر الحالي
                diff_pct = abs(strike - price) / price * 100
                
                best = {
                    'strike': strike,
                    'exp': c.expiration_date.strftime('%Y-%m-%d'),
                    'last': last,
                    'vol': vol,
                    'oi': oi,
                    'diff': round(diff_pct, 1)
                }
        return best
    except:
        return None

def main_loop():
    time.sleep(5)
    send("✅ تم تشغيل البوت - الشكل الاحترافي الكامل (عربي)")
    update_resistances()
    
    while True:
        try:
            for sym in SYMBOLS:
                price = get_alpaca_price(sym)
                if not price:
                    continue
                
                levels = RES_CACHE.get(sym)
                hist = HIST_CACHE.get(sym)
                if not levels or hist is None:
                    continue

                now = datetime.now()
                last_sent = sent_signals.get(sym)
                if last_sent and (now - last_sent) < timedelta(hours=2):
                    continue

                res = levels['res']
                sup = levels['sup']

                otype = None
                reason = ""
                if price >= res:
                    otype = 'CALL'
                    reason = "اختراق مقاومة يومية"
                elif price <= sup:
                    otype = 'PUT'
                    reason = "كسر دعم يومي"

                if otype:
                    rsi = calc_rsi(hist)
                    sma50 = float(hist['Close'].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else price
                    stop, t1, t2, t3 = calc_levels(hist, price, is_put=(otype == 'PUT'))
                    liq_type, liq_txt, ob_txt = calc_liquidity_and_ob(hist)

                    # جلب العقود الثلاثة
                    opt_daily = get_specific_opt(sym, price, 'daily', otype)
                    opt_weekly = get_specific_opt(sym, price, 'weekly', otype)
                    opt_monthly = get_specific_opt(sym, price, 'monthly', otype)

                    # بناء الرسالة بنفس الشكل المطلوب
                    msg = f"⏳ تنبيه {reason} ({otype}) ⚡ {sym}\n"
                    msg += f"💵 نقطة الدخول اللحظية: {price:.2f}$\n"
                    msg += f"📊 RSI: {rsi:.0f} | المتوسط 50: {sma50:.2f}$\n"
                    msg += f"🧱 حاجز المقاومة/الدعم: {res if otype=='CALL' else sup:.2f}$\n"
                    msg += f"{liq_txt} | OB: {ob_txt}\n\n"

                    msg += f"🛑 وقف الخسارة: {stop}$\n"
                    msg += f"🎯 الهدف الأول: {t1}$\n"
                    msg += f"🎯 الهدف الثاني: {t2}$\n"
                    msg += f"🎯 الهدف الثالث: {t3}$\n\n"

                    if opt_daily:
                        msg += f"⚡ عقد يومي/سريع ({opt_daily['exp']})\n"
                        msg += f"🔹 سترايك: {opt_daily['strike']}$ | السعر: {opt_daily['last']}$\n"
                        msg += f"📈 فوليوم: {opt_daily['vol']} | OI: {opt_daily['oi']} | الفرق: {opt_daily['diff']}%\n\n"

                    if opt_weekly:
                        msg += f"📅 عقد أسبوعي ({opt_weekly['exp']})\n"
                        msg += f"🔹 سترايك: {opt_weekly['strike']}$ | السعر: {opt_weekly['last']}$\n"
                        msg += f"📈 فوليوم: {opt_weekly['vol']} | OI: {opt_weekly['oi']} | الفرق: {opt_weekly['diff']}%\n\n"

                    if opt_monthly:
                        msg += f"🛡️ عقد شهري/آمن ({opt_monthly['exp']})\n"
                        msg += f"🔹 سترايك: {opt_monthly['strike']}$ | السعر: {opt_monthly['last']}$\n"
                        msg += f"📈 فوليوم: {opt_monthly['vol']} | OI: {opt_monthly['oi']} | الفرق: {opt_monthly['diff']}%\n"

                    send(msg)
                    sent_signals[sym] = now

                time.sleep(1)
            time.sleep(30)
        except Exception as e:
            print(f"LOOP ERR: {e}")
            time.sleep(10)

if not hasattr(app, 'started'):
    Thread(target=main_loop, daemon=True).start()
    app.started = True

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
