from flask import Flask
from threading import Thread
import os, time, requests, finnhub, yfinance as yf
from datetime import datetime
import pandas as pd
import numpy as np

app = Flask(__name__)
@app.route('/')
def home(): return "البوت شغال - غاما + فلو + انضغاط"
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

def update_resistances():
    for sym in SYMBOLS:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1mo")
            if not hist.empty and len(hist) >= 20:
                RES_CACHE[sym] = max(hist['High'].tail(20))
                HIST_CACHE[sym] = hist
            time.sleep(0.6)
        except Exception as e:
            print(f"RES ERR {sym}: {e}", flush=True)

# --- المرحلة 1: محطة الغاما ---
def get_gamma_walls(sym, price):
    try:
        t = yf.Ticker(sym)
        exps = t.options[:2] # اقرب تاريخين فقط عشان ما يثقل
        all_calls = []
        all_puts = []
        for exp in exps:
            chain = t.option_chain(exp)
            all_calls.append(chain.calls)
            all_puts.append(chain.puts)
        if not all_calls: return None
        calls = pd.concat(all_calls)
        puts = pd.concat(all_puts)
        # جدار المقاومة = اعلى OI في الكول فوق السعر
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
    except Exception as e:
        print(f"GAMMA ERR {sym}: {e}")
        return None

# --- المرحلة 2: ماسح الانضغاط ---
def check_squeeze(hist):
    try:
        if len(hist) < 20: return None
        close = hist['Close']
        # بولينجر
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_bb = ma20 + (2 * std20)
        lower_bb = ma20 - (2 * std20)
        # كيلتنر
        tr = pd.concat([hist['High']-hist['Low'], (hist['High']-hist['Close'].shift()).abs(), (hist['Low']-hist['Close'].shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        upper_kc = ma20 + (1.5 * atr)
        lower_kc = ma20 - (1.5 * atr)
        # الانضغاط اذا البولينجر داخل الكيلتنر
        last = -1
        is_squeeze = (lower_bb.iloc[last] > lower_kc.iloc[last]) and (upper_bb.iloc[last] < upper_kc.iloc[last])
        is_firing = not is_squeeze and (lower_bb.iloc[-2] > lower_kc.iloc[-2]) # كان مضغوط وفك
        return {"squeeze": is_squeeze, "firing": is_firing}
    except:
        return None

def get_opt(sym, price, mode):
    try:
        t = yf.Ticker(sym)
        exps = t.options
        if not exps: return None
        exp = exps[0] if mode=="daily" else exps[min(2, len(exps)-1)]
        chain = t.option_chain(exp).calls
        chain = chain[(chain['lastPrice'] <= 10.0) & (chain['lastPrice'] >= 0.20)]
        filt = chain[(chain['strike'] >= price*0.98) & (chain['strike'] <= price*1.10)]
        if filt.empty: filt = chain
        if filt.empty: return None
        best = filt.sort_values('volume', ascending=False).iloc[0]
        vol = int(best['volume'] or 0)
        oi = int(best['openInterest'] or 0)
        # --- المرحلة 1: سجل التدفق (حيتان) ---
        flow_ratio = (vol / oi * 100) if oi > 0 else 0
        is_whale = vol > 1000 and flow_ratio > 150 # فوليوم عالي اكبر من OI
        return {"strike": best['strike'], "last": best['lastPrice'], "vol": vol, "oi": oi, "exp": exp, "flow": flow_ratio, "whale": is_whale}
    except: return None

def loop():
    update_resistances()
    send("✅ البوت المطور اشتغل\n📦 غاما + فلو + انضغاط\nيتابع 19 سهم")
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

                # --- فحص الانضغاط قبل الاختراق ---
                sq = check_squeeze(hist)
                sq_key = f"{s}_{today_str}_sq"
                if sq and sq['squeeze'] and sq_key not in sent_squeeze:
                    name_ar = AR_NAMES.get(s, s)
                    send(f"⚠️ <b>انضغاط - {name_ar} ({s})</b>\n💰 السعر: {p:.2f}$\n📦 السهم مضغوط مثل الزنبرك، قرب ينفجر\n👀 راقب اختراق {res:.2f}$")
                    sent_squeeze.add(sq_key)

                # --- فحص الاختراق ---
                key = f"{s}_{today_str}"
                if p >= res * 0.995 and key not in sent:
                    d = get_opt(s, p, "daily")
                    w = get_opt(s, p, "weekly")
                    if not d and not w: continue

                    # جيب الغاما
                    gamma = get_gamma_walls(s, p)

                    entry = res
                    stop = round(entry * 0.985, 2)
                    t1, t2, t3 = round(entry * 1.02, 2), round(entry * 1.04, 2), round(entry * 1.06, 2)
                    name_ar = AR_NAMES.get(s, s)

                    msg = f"🟢 <b>اختراق لحظي - {name_ar} ({s})</b>\n"
                    msg += f"💰 السعر الحالي: {p:.2f}$\n"
                    msg += f"📊 مقاومة: {res:.2f}$\n"

                    # --- اضافة الغاما في الرسالة ---
                    if gamma:
                        if gamma['res_strike']:
                            msg += f"🧱 جدار غاما مقاومة: {gamma['res_strike']:.0f}$ (OI: {gamma['res_oi']:,})\n"
                        if gamma['sup_strike']:
                            msg += f"🛡️ جدار غاما دعم: {gamma['sup_strike']:.0f}$ (OI: {gamma['sup_oi']:,})\n"

                    msg += f"\n🚀 دخول: فوق {entry:.2f}$\n🛑 وقف: {stop:.2f}$\n🎯 أهداف: {t1}$ | {t2}$ | {t3}$\n"

                    if d:
                        whale_txt = "🐋 حيتان!" if d['whale'] else ""
                        msg += f"\n🔥 <b>يومي - {d['exp']}</b> {whale_txt}\n"
                        msg += f"💰 سترايك: {d['strike']}$ CALL\n💵 سعر: {d['last']}$\n"
                        msg += f"📊 فوليوم: {d['vol']:,} | OI: {d['oi']:,}\n"
                        msg += f"🌊 فلو: {d['flow']:.0f}% من OI\n"
                    if w:
                        whale_txt = "🐋 حيتان!" if w['whale'] else ""
                        msg += f"\n🛡️ <b>اسبوعي - {w['exp']}</b> {whale_txt}\n"
                        msg += f"💰 سترايك: {w['strike']}$ CALL\n💵 سعر: {w['last']}$\n"
                        msg += f"📊 فوليوم: {w['vol']:,} | OI: {w['oi']:,}\n"
                        msg += f"🌊 فلو: {w['flow']:.0f}% من OI\n"

                    send(msg)
                    sent.add(key)
                time.sleep(1.2)
            except Exception as e:
                print(f"LOOP ERR {s}: {e}", flush=True)
        time.sleep(15)

Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
