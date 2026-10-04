from flask import Flask
from threading import Thread
import os, time, requests, finnhub, yfinance as yf
from datetime import datetime
import pandas as pd
import numpy as np

app = Flask(__name__)
@app.route('/')
def home(): return "البوت شغال - نهائي ATR + غاما + حماية 0DTE"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

SYMBOLS = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]
AR_NAMES = {"NVDA":"انفيديا","TSLA":"تسلا","SMCI":"سوبر مايكرو","MSTR":"مايكروستراتيجي","COIN":"كوين بيس","AAPL":"أبل","GOOGL":"جوجل","META":"ميتا","AMD":"ايه ام دي","AMZN":"امازون","MSFT":"مايكروسوفت","PLTR":"بلانتر","APP":"آب لوفين","ARM":"ارم","AVGO":"برودكوم","MU":"مايكرون","LITE":"لايت","SNDK":"سانديسك","RDDT":"ريديت"}

sent = set()
sent_squeeze = set()
RES_CACHE = {}
HIST_CACHE = {}

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        print(f"SEND ERR {e}", flush=True)

def calc_atr(hist, period=14):
    try:
        high_low = hist['High'] - hist['Low']
        high_close = (hist['High'] - hist['Close'].shift()).abs()
        low_close = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else float(hist['Close'].iloc[-1] * 0.02)
    except:
        return float(hist['Close'].iloc[-1] * 0.02)

# فكرتك الذكية: الهدف الاول هو جدار الغاما
def calc_levels(hist, entry_price, is_squeeze=False, gamma_res=None):
    atr = calc_atr(hist, 14)
    if is_squeeze:
        stop = entry_price - (atr * 1.0)
        if gamma_res and gamma_res > entry_price and gamma_res < (entry_price + atr * 2.0):
            t1 = gamma_res
        else:
            t1 = entry_price + (atr * 1.5)
        t2 = entry_price + (atr * 3.0)
        t3 = entry_price + (atr * 5.0)
    else:
        stop = entry_price - (atr * 1.2)
        t1 = entry_price + (atr * 1.2)
        t2 = entry_price + (atr * 2.5)
        t3 = entry_price + (atr * 4.0)
    return round(stop,2), round(t1,2), round(t2,2), round(t3,2), round(atr,2)

def update_resistances():
    for sym in SYMBOLS:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1mo")
            if not hist.empty and len(hist) >= 10:
                RES_CACHE[sym] = max(hist['High'].tail(5))
                HIST_CACHE[sym] = hist
            time.sleep(0.6)
        except Exception as e:
            print(f"RES ERR {sym}: {e}", flush=True)

def get_gamma_walls(sym, price):
    try:
        t = yf.Ticker(sym)
        exps = t.options[:2]
        all_calls, all_puts = [], []
        for exp in exps:
            chain = t.option_chain(exp)
            all_calls.append(chain.calls)
            all_puts.append(chain.puts)
        if not all_calls: return None
        calls = pd.concat(all_calls)
        puts = pd.concat(all_puts)
        calls_above = calls[calls['strike'] >= price]
        puts_below = puts[puts['strike'] <= price]
        wall_res = calls_above.sort_values('openInterest', ascending=False).iloc[0] if not calls_above.empty else None
        wall_sup = puts_below.sort_values('openInterest', ascending=False).iloc[0] if not puts_below.empty else None
        return {
            "res_strike": float(wall_res['strike']) if wall_res is not None else None,
            "res_oi": int(wall_res['openInterest']) if wall_res is not None else 0,
            "sup_strike": float(wall_sup['strike']) if wall_sup is not None else None,
            "sup_oi": int(wall_sup['openInterest']) if wall_sup is not None else 0,
        }
    except: return None

def check_squeeze(hist):
    try:
        if len(hist) < 20: return None
        close = hist['Close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + (2 * std20)
        lower_bb = ma20 - (2 * std20)
        tr = pd.concat([hist['High']-hist['Low'], (hist['High']-hist['Close'].shift()).abs(), (hist['Low']-hist['Close'].shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + (1.5 * atr)
        lower_kc = ma20 - (1.5 * atr)
        is_squeeze = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and (upper_bb.iloc[-1] < upper_kc.iloc[-1])
        is_firing = (lower_bb.iloc[-2] > lower_kc.iloc[-2]) and (upper_bb.iloc[-2] < upper_kc.iloc[-2]) and not is_squeeze
        return {"squeeze": is_squeeze, "firing": is_firing}
    except: return None

# فكرتك + حماية 0DTE
def get_opt(sym, price, mode):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        if mode == "daily":
            exp = exps[1] if len(exps) > 1 else exps[0]
        else:
            exp = exps[min(2, len(exps)-1)]
        chain = t.option_chain(exp).calls
        chain = chain[(chain['lastPrice'] <= 10.0) & (chain['lastPrice'] >= 0.30)]
        filt = chain[(chain['strike'] >= price * 0.999) & (chain['strike'] <= price * 1.05)]
        if filt.empty: filt = chain
        if filt.empty: return None
        best = filt.sort_values('volume', ascending=False).iloc[0]
        vol = int(best['volume'] or 0)
        oi = int(best['openInterest'] or 0)
        flow_ratio = (vol / oi * 100) if oi > 0 else 0
        is_whale = vol > 1000 and flow_ratio > 150
        return {"strike": best['strike'], "last": best['lastPrice'], "vol": vol, "oi": oi, "exp": exp, "flow": flow_ratio, "whale": is_whale}
    except: return None

def loop():
    update_resistances()
    send("✅ البوت النهائي اشتغل\n🔥 انضغاط + ATR + غاما هدف + ATM")
    last_res_update = time.time()
    while True:
        if time.time() - last_res_update > 43200:
            update_resistances()
            last_res_update = time.time()
            sent.clear()
            sent_squeeze.clear()
        today_str = datetime.now().strftime("%Y-%m-%d")
        for s in SYMBOLS:
            try:
                res = RES_CACHE.get(s)
                hist = HIST_CACHE.get(s)
                if not res or hist is None: continue
                q = finnhub_client.quote(s)
                p = float(q.get('c', 0))
                if p == 0: continue

                sq = check_squeeze(hist)
                sq_key = f"{s}_{today_str}_sq"
                if sq and (sq['squeeze'] or sq['firing']) and sq_key not in sent_squeeze:
                    gamma = get_gamma_walls(s, p)
                    gamma_strike = gamma['res_strike'] if gamma else None
                    stop, t1, t2, t3, atr = calc_levels(hist, p, is_squeeze=True, gamma_res=gamma_strike)
                    d = get_opt(s, p, "daily")
                    w = get_opt(s, p, "weekly")
                    if not d and not w: continue
                    name_ar = AR_NAMES.get(s, s)
                    firing_txt = "🔥 فك ضغط - انفجار!" if sq['firing'] else "⚠️ انضغاط"
                    msg = f"{firing_txt} - <b>{name_ar} ({s})</b> {p:.2f}$\n"
                    msg += f"📊 مقاومة 5ايام: {res:.2f}$\n"
                    if gamma and gamma['res_strike']:
                        msg += f"🧱 جدار غاما: {gamma['res_strike']:.0f}$ (OI:{gamma['res_oi']:,})\n"
                    msg += f"\n🚀 دخول: {p:.2f}$ الحين\n🛑 وقف: {stop}$ (ATR:{atr}$)\n"
                    msg += f"🎯 هدف1 (غاما): {t1}$\n🎯 هدف2: {t2}$\n🎯 هدف3: {t3}$\n"
                    msg += f"📍 خروج نهائي كسر {stop}$\n"
                    if d:
                        msg += f"\n🔥 يومي - {d['exp']} {'🐋' if d['whale'] else ''}\n💰 {d['strike']}$ CALL - {d['last']}$\n📊 V:{d['vol']:,} OI:{d['oi']:,}\n"
                    if w:
                        msg += f"\n🛡️ اسبوعي - {w['exp']}\n💰 {w['strike']}$ CALL - {w['last']}$\n📊 V:{w['vol']:,} OI:{w['oi']:,}\n"
                    send(msg)
                    sent_squeeze.add(sq_key)

                key = f"{s}_{today_str}"
                if p >= res * 0.995 and key not in sent:
                    gamma = get_gamma_walls(s, p)
                    gamma_strike = gamma['res_strike'] if gamma else None
                    stop, t1, t2, t3, atr = calc_levels(hist, res, is_squeeze=False, gamma_res=gamma_strike)
                    d = get_opt(s, p, "daily")
                    w = get_opt(s, p, "weekly")
                    if not d and not
