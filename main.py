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
def home(): return 'Bot V72 - Clean Format + RVol + VWAP'

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI','MSTR','COIN','AAPL','AMD','AMZN','PLTR','APP','ARM','AVGO','MU','LITE','SNDK','RDDT']
SYMBOLS = TIER1 + TIER2

MIN_VOL_TIER1 = 1000; MIN_OI_TIER1 = 3000
MIN_VOL_TIER2 = 300; MIN_OI_TIER2 = 800
MAX_SPREAD_PCT = 0.08
MIN_PRICE = 0.4; MAX_PRICE = 12.0

RES_CACHE = {}; HIST_CACHE = {}; sent_squeeze = set()

def send(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                      json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=20)
    except Exception as e: print(f'SEND ERR {e}', flush=True)

def get_finnhub_quote_safe(sym):
    for _ in range(3):
        try: return finnhub_client.quote(sym)
        except Exception as e:
            if '429' in str(e): time.sleep(60)
            else: break
    return None

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff()
        gain = delta.where(delta>0,0).rolling(period).mean()
        loss = -delta.where(delta<0,0).rolling(period).mean()
        return float((100-(100/(1+gain/loss))).iloc[-1])
    except: return 50.0

def calc_sma(hist, period=50):
    try: return float(hist['Close'].rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1])

def calc_atr(hist, period=14):
    try:
        hl=hist['High']-hist['Low']
        hc=(hist['High']-hist['Close'].shift()).abs()
        lc=(hist['Low']-hist['Close'].shift()).abs()
        tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1]*0.02)

def calc_rvol(hist):
    try:
        if len(hist)<21: return 1.0
        today_vol=float(hist['Volume'].iloc[-1])
        avg20=float(hist['Volume'].tail(21).head(20).mean())
        return today_vol/avg20 if avg20!=0 else 1.0
    except: return 1.0

def calc_vwap(hist):
    try:
        typical = (hist['High']+hist['Low']+hist['Close'])/3
        last = hist.tail(78) if len(hist)>78 else hist
        return float((typical.tail(len(last)) * last['Volume']).sum() / last['Volume'].sum())
    except: return float(hist['Close'].iloc[-1])

def calc_levels(hist, entry, gamma_res=None, is_put=False):
    atr=calc_atr(hist)
    if is_put:
        stop=entry+atr*1.0; t1=entry-atr*1.5; t2=entry-atr*3.0; t3=entry-atr*5.0
    else:
        t1=gamma_res if (gamma_res and entry<gamma_res<entry+atr*2) else entry+atr*1.5
        t2=entry+atr*3.0; t3=entry+atr*5.0; stop=entry-atr*1.0
    return round(stop,2), round(t1,2), round(t2,2), round(t3,2), round(atr,2)

def update_resistances():
    for sym in SYMBOLS:
        try:
            hist=yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if not hist.empty and len(hist)>=60:
                RES_CACHE[sym]=max(hist['High'].tail(5)); HIST_CACHE[sym]=hist
            time.sleep(2.5)
        except Exception as e: print(f'RES ERR {sym}:{e}'); time.sleep(10)

def get_gamma_walls(sym, price):
    try:
        t=yf.Ticker(sym); calls=pd.concat([t.option_chain(exp).calls for exp in t.options[:2]])
        above=calls[calls['strike']>=price]
        if above.empty: return None
        wall=above.sort_values('openInterest', ascending=False).iloc[0]
        return {'res_strike':float(wall['strike'])}
    except: return None

def check_squeeze(hist):
    try:
        if len(hist)<20: return None
        close=hist['Close']; ma20=close.rolling(20).mean(); std20=close.rolling(20).std()
        upper_bb=ma20+2*std20; lower_bb=ma20-2*std20
        tr=pd.concat([hist['High']-hist['Low'], (hist['High']-hist['Close'].shift()).abs(), (hist['Low']-hist['Close'].shift()).abs()], axis=1).max(axis=1)
        atr=tr.rolling(20).mean()
        upper_kc=ma20+1.5*atr; lower_kc=ma20-1.5*atr
        is_sq=(lower_bb.iloc[-1]>lower_kc.iloc[-1]) and (upper_bb.iloc[-1]<upper_kc.iloc[-1])
        prev_sq=(lower_bb.iloc[-2]>lower_kc.iloc[-2]) and (upper_bb.iloc[-2]<upper_kc.iloc[-2])
        return {'squeeze':is_sq,'firing':prev_sq and not is_sq,'dir':'UP' if close.iloc[-1]>ma20.iloc[-1] else 'DOWN'}
    except: return None

def get_opt(sym, price, mode, opt_type='CALL'):
    try:
        t=yf.Ticker(sym); exps=t.options
        if not exps: return None
        today=datetime.now().date()
        daily=[]; weekly=[]
        for exp in exps:
            d=(datetime.strptime(exp, '%Y-%m-%d').date()-today).days
            if 0<=d<=3: daily.append(exp)
            elif 4<=d<=12: weekly.append(exp)
        target = daily if mode=='daily' else weekly
        if not target: return None
        best=None; best_score=-1
        for exp in target[:3]:
            try:
                chain=t.option_chain(exp); df=chain.calls if opt_type=='CALL' else chain.puts
                df=df[(df['lastPrice']>=MIN_PRICE)&(df['lastPrice']<=MAX_PRICE)]
                filt=df[(df['strike']>=price*0.97)&(df['strike']<=price*1.08)] if opt_type=='CALL' else df[(df['strike']<=price*1.03)&(df['strike']>=price*0.92)]
                if filt.empty: continue
                for _, row in filt.iterrows():
                    vol=int(row['volume'] or 0); oi=int(row['openInterest'] or 0)
                    bid=float(row.get('bid',0) or 0); ask=float(row.get('ask',0) or 0); last=float(row['lastPrice'] or 0)
                    if last==0 or bid==0 or ask==0: continue
                    spread=(ask-bid)/last
                    min_v=MIN_VOL_TIER1 if sym in TIER1 else MIN_VOL_TIER2
                    min_o=MIN_OI_TIER1 if sym in TIER1 else MIN_OI_TIER2
                    if vol<min_v or oi<min_o or spread>MAX_SPREAD_PCT: continue
                    score=(vol*0.7+oi*0.3)-(spread*1000)-abs(row['strike']-price)*2
                    if score>best_score:
                        best_score=score; flow=(vol/oi*100) if oi>0 else 0
                        best={'strike':row['strike'],'last':last,'vol':vol,'oi':oi,'exp':exp,'whale':vol>2000 and flow>150,'spread':spread}
            except: continue
        return best
    except: return None

def loop():
    update_resistances()
    send('✅ V72 اشتغل - تنسيق نظيف + RVol + VWAP')
    last_res=time.time()
    while True:
        if time.time()-last_res>43200:
            update_resistances(); last_res=time.time(); sent_squeeze.clear()
        today_str=datetime.now().strftime('%Y-%m-%d')
        for s in SYMBOLS:
            try:
                res=RES_CACHE.get(s); hist=HIST_CACHE.get(s)
                if not res or hist is None: continue
                q=get_finnhub_quote_safe(s)
                if not q: continue
                p=float(q.get('c',0))
                if p==0: continue
                rsi=calc_rsi(hist); sma50=calc_sma(hist); sq=check_squeeze(hist)
                if sq is None: continue
                rvol=calc_rvol(hist); vwap=calc_vwap(hist)
                if rvol < 1.2: continue
                sq_key=f'{s}_{today_str}_sq'
                if not (sq['squeeze'] or sq['firing']) or sq_key in sent_squeeze:
                    time.sleep(0.5); continue
                is_put=sq['dir']=='DOWN'
                if not is_put and p < vwap: continue
                if is_put and p > vwap: continue
                if sq['firing']:
                    if is_put and p>sma50: continue
                    if not is_put and (p<sma50 or rsi<50): continue
                else:
                    if p<sma50 and rsi<50: continue
                opt_type='PUT' if is_put else 'CALL'
                gamma=get_gamma_walls(s,p); gamma_strike=gamma['res_strike'] if gamma else None
                stop,t1,t2,t3,atr=calc_levels(hist,p,gamma_res=gamma_strike,is_put=is_put)
                d=get_opt(s,p,'daily',opt_type); w=get_opt(s,p,'weekly',opt_type)
                if d is None and w is None: continue

                # --- تنسيق الرسالة الجديد المرتب ---
                icon = "🔥" if sq['firing'] else "⏳"
                type_txt = "تنبيه انطلاق" if sq['firing'] else "تنبيه انضغاط متوقع انفجاره"
                tier_emoji = "🧠" if s in TIER1 else "💡"

                msg = f"{icon} {type_txt} ({opt_type}) {tier_emoji} {s}\n"
                msg += f"💵 نقطة الدخول اللحظية: {p:.2f}$\n"
                msg += f"📊 RSI: {rsi:.0f} | المتوسط 50: {sma50:.2f}$\n"
                msg += f"📈 RVol: {rvol:.2f}x | VWAP: {vwap:.2f}$\n"
                if gamma and gamma['res_strike']:
                    msg += f"🧱 حاجز المقاومة/غاما: {gamma['res_strike']:.2f}$\n"
                msg += f"\n🛑 وقف الخسارة: {stop}$\n"
                msg += f"🎯 الهدف الأول: {t1}$\n"
                msg += f"🎯 الهدف الثاني: {t2}$\n"
                msg += f"🎯 الهدف الثالث: {t3}$\n"

                if d:
                    whale_txt = " 🐋 دخول حيتان" if d['whale'] else ""
                    msg += f"\n📅 عقد يومي (0-3 أيام) ({d['exp']}){whale_txt}\n"
                    msg += f"🔹 سترايك: {d['strike']}$ | السعر: {d['last']}$\n"
                    msg += f"📈 فوليوم: {d['vol']} | OI: {d['oi']} | الفرق: {d['spread']:.1%}\n"

                if w:
                    msg += f"\n🛡️ عقد أسبوعي/آمن ({w['exp']})\n"
                    msg += f"🔹 سترايك: {w['strike']}$ | السعر: {w['last']}$\n"
                    msg += f"📈 فوليوم: {w['vol']} | OI: {w['oi']} | الفرق: {w['spread']:.1%}\n"

                send(msg); sent_squeeze.add(sq_key); time.sleep(2)
            except Exception as e:
                print(f'LOOP ERR {s}:{e}'); time.sleep(2)
        time.sleep(30)

Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
