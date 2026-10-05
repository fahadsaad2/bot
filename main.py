from flask import Flask
from threading import Thread
import os
import time
import requests
import finnhub
import yfinance as yf
from datetime import datetime
import pandas as pd

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot OK - 19 COMPANIES FINAL ARABIC"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER1 = ["NVDA", "TSLA", "GOOGL", "META", "MSFT"]
TIER2 = ["SMCI", "MSTR", "COIN", "AAPL", "AMD", "AMZN", "PLTR", "APP", "ARM", "AVGO", "MU", "LITE", "SNDK", "RDDT"]
SYMBOLS = TIER1 + TIER2

MIN_VOL_TIER1 = 100
MIN_OI_TIER1 = 500
MIN_VOL_TIER2 = 20
MIN_OI_TIER2 = 100
MAX_SPREAD_PCT = 0.15

RES_CACHE = {}
HIST_CACHE = {}
sent_squeeze = set()

def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20
        )
    except Exception as e:
        print(f"SEND ERR {e}", flush=True)

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])
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
        atr = tr.rolling(period).mean().iloc[-1]
        if pd.isna(atr):
            return float(hist['Close'].iloc[-1] * 0.02)
        return float(atr)
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

def calc_levels(hist, entry_price, is_squeeze=False, gamma_res=None, is_put=False):
    atr = calc_atr(hist, 14)
    if is_put:
        stop = entry_price + (atr * 1.0)
        if is_squeeze:
            t1 = entry_price - (atr * 1.5)
            t2 = entry_price - (atr * 3.0)
            t3 = entry_price - (atr * 5.0)
        else:
            t1 = entry_price - (atr * 1.2)
            t2 = entry_price - (atr * 2.5)
            t3 = entry_price - (atr * 4.0)
    else:
        if is_squeeze and gamma_res and gamma_res > entry_price and gamma_res < (entry_price + atr * 2.0):
            t1 = gamma_res
        else:
            if is_squeeze:
                t1 = entry_price + (atr * 1.5)
            else:
                t1 = entry_price + (atr * 1.2)
        if is_squeeze:
            t2 = entry_price + (atr * 3.0)
            t3 = entry_price + (atr * 5.0)
        else:
            t2 = entry_price + (atr * 2.5)
            t3 = entry_price + (atr * 4.0)
        stop = entry_price - (atr * 1.0) if is_squeeze else entry_price - (atr * 1.2)
    return round(stop, 2), round(t1, 2), round(t2, 2), round(t3, 2), round(atr, 2)

def update_resistances():
    for sym in SYMBOLS:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="3mo")
            if not hist.empty and len(hist) >= 60:
                RES_CACHE[sym] = max(hist['High'].tail(5))
                HIST_CACHE[sym] = hist
            time.sleep(0.6)
        except Exception as e:
            print(f"RES ERR {sym}: {e}", flush=True)

def get_gamma_walls(sym, price):
    try:
        t = yf.Ticker(sym)
        exps = t.options[:2]
        all_calls = []
        for exp in exps:
            chain = t.option_chain(exp)
            all_calls.append(chain.calls)
        if not all_calls:
            return None
        calls = pd.concat(all_calls)
        calls_above = calls[calls['strike'] >= price]
        if calls_above.empty:
            return None
        wall_res = calls_above.sort_values('openInterest', ascending=False).iloc[0]
        return {"res_strike": float(wall_res['strike'])}
    except:
        return None

def check_squeeze(hist):
    try:
        if len(hist) < 20:
            return None
        close = hist['Close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + (2 * std20)
        lower_bb = ma20 - (2 * std20)
        hl = hist['High'] - hist['Low']
        hc = (hist['High'] - hist['Close'].shift()).abs()
        lc = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + (1.5 * atr)
        lower_kc = ma20 - (1.5 * atr)
        is_squeeze = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        prev_squeeze = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2])
        is_firing = prev_squeeze and not is_squeeze
        direction = "UP" if close.iloc[-1] > ma20.iloc[-1] else "DOWN"
        return {"squeeze": is_squeeze, "firing": is_firing, "dir": direction}
    except:
        return None

def get_opt(sym, price, mode, opt_type="CALL"):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps:
            return None
        if mode == "daily":
            target_exps = exps[0:3]
        else:
            target_exps = exps[2:8]
        best_overall = None
        best_score = -1
        for exp in target_exps:
            try:
                chain_full = t.option_chain(exp)
                if opt_type == "CALL":
                    chain = chain_full.calls
                else:
                    chain = chain_full.puts
                chain = chain[(chain['lastPrice'] <= 10.0) & (chain['lastPrice'] >= 1.0)]
                if opt_type == "CALL":
                    filt = chain[(chain['strike'] >= price * 0.97) & (chain['strike'] <= price * 1.08)]
                else:
                    filt = chain[(chain['strike'] <= price * 1.03) & (chain['strike'] >= price * 0.92)]
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
                    if bid > 0 and ask > 0:
                        spread_pct = (ask - bid) / last
                    else:
                        spread_pct = 1.0
                    is_tier1 = sym in TIER1
                    if is_tier1:
                        min_vol = MIN_VOL_TIER1
                        min_oi = MIN_OI_TIER1
                    else:
                        min_vol = MIN_VOL_TIER2
                        min_oi = MIN_OI_TIER2
                    if vol < min_vol or oi < min_oi:
                        continue
                    if spread_pct > MAX_SPREAD_PCT:
                        continue
                    score = (vol * 0.5) + (oi * 0.2) - (spread_pct * 1000)
                    if score > best_score:
                        best_score = score
                        flow = (vol / oi * 100) if oi > 0 else 0
                        whale = vol > 1000 and flow > 150
                        best_overall = {
                            "strike": row['strike'],
                            "last": last,
                            "vol": vol,
                            "oi": oi,
                            "exp": exp,
                            "flow": flow,
                            "whale": whale,
                            "spread": spread_pct
                        }
            except:
                continue
        return best_overall
    except Exception as e:
        print(f"OPT ERR {sym}: {e}", flush=True)
        return None

def loop():
    update_resistances()
    send("✅ تم تشغيل البوت - 19 شركة | سعر 1$-10$ | يومي 0-7 وشهري 8-30 يوم")
    last_res_update = time.time()
    while True:
        if time.time() - last_res_update > 43200:
            update_resistances()
            last_res_update = time.time()
            sent_squeeze.clear()
        today_str = datetime.now().strftime("%Y-%m-%d")
        for s in SYMBOLS:
            try:
                res = RES_CACHE.get(s)
                hist = HIST_CACHE.get(s)
                if not res or hist is None:
                    continue
                q = finnhub_client.quote(s)
                p = float(q.get('c', 0))
                if p == 0:
                    continue
                rsi = calc_rsi(hist)
                sma50 = calc_sma(hist, 50)
                sq = check_squeeze(hist)
                sq_key = f"{s}_{today_str}_sq"
                if sq is None:
                    continue
                if not (sq['squeeze'] or sq['firing']):
                    continue
                if sq_key in sent_squeeze:
                    continue
                is_put_signal = sq['dir'] == "DOWN"
                if sq['firing']:
                    if is_put_signal:
                        if p > sma50:
                            continue
                    else:
                        if p < sma50 or rsi < 50:
                            continue
                else:
                    if p < sma50 and rsi < 50:
                        continue
                if is_put_signal:
                    opt_type = "PUT"
                else:
                    opt_type = "CALL"
                gamma = get_gamma_walls(s, p)
                if gamma:
                    gamma_strike = gamma['res_strike']
                else:
                    gamma_strike = None
                stop, t1, t2, t3, atr = calc_levels(hist, p, is_squeeze=True, gamma_res=gamma_strike, is_put=is_put_signal)
                d = get_opt(s, p, "daily", opt_type)
                w = get_opt(s, p, "weekly", opt_type)
                if d is None and w is None:
                    continue
                tier_label = "🔵" if s in TIER1 else "🟡"
                if sq['firing']:
                    firing_txt = f"🔥 انطلاق {opt_type}"
                else:
                    firing_txt = f"⚠️ انضغاط {opt_type}"
                msg = f"{firing_txt} {tier_label} <b>{s}</b> سعره {p:.2f}$\n"
                msg += f"📊 المتوسط 50: {sma50:.2f}$ | المقاومة: {res:.2f}$ | RSI: {rsi:.0f}\n"
                if gamma and gamma['res_strike']:
                    msg += f"🧱 حاجز غاما: {gamma['res_strike']:.0f}$\n"
                if is_put_signal:
                    msg += f"\n🔻 دخول {opt_type}: {p:.2f}$\n"
                    msg += f"🛑 وقف الخسارة: {stop}$ (ATR:{atr}$)\n"
                    msg += f"🎯 هدف اول: {t1}$\n🎯 هدف ثاني: {t2}$\n🎯 هدف ثالث: {t3}$\n"
                else:
                    msg += f"\n🚀 دخول {opt_type}: {p:.2f}$\n"
                    msg += f"🛑 وقف الخسارة: {stop}$ (ATR:{atr}$)\n"
                    msg += f"🎯 هدف اول: {t1}$\n🎯 هدف ثاني: {t2}$\n🎯 هدف ثالث: {t3}$\n"
                if d:
                    if d['whale']:
                        whale_txt = "🐋 دخول حيتان"
                    else:
                        whale_txt = ""
                    msg += f"\n🔥 عقد يومي (0-7 ايام) {d['exp']} {whale_txt}\n"
                    msg += f"💰 سترايك {d['strike']}$ - سعره {d['last']}$ | فوليوم: {d['vol']} | OI: {d['oi']} | فرق: {d['spread']:.0%}\n"
                if w:
                    msg += f"\n🛡️ عقد شهري (8-30 يوم) {w['exp']}\n"
                    msg += f"💰 سترايك {w['strike']}$ - سعره {w['last']}$ | فوليوم: {w['vol']} | OI: {w['oi']} | فرق: {w['spread']:.0%}\n"
                send(msg)
                sent_squeeze.add(sq_key)
                time.sleep(1.2)
            except Exception as e:
                print(f"LOOP ERR {s}: {e}", flush=True)
        time.sleep(15)

Thread(target=loop, daemon=True).start()

while True:
    time.sleep(3600)
