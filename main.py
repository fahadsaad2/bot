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
def home(): return 'Bot V77 - AAA Sweep + OB Bounce + Full Metrics + Monthly'
def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY') or "db070i9r01qn6m7vpb9g"
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

TIER1 = ['NVDA', 'TSLA', 'GOOGL', 'META', 'MSFT']
TIER2 = ['SMCI','MSTR','COIN','AAPL','AMD','AMZN','PLTR','APP','ARM','AVGO','MU','LITE','SNDK','RDDT']
SYMBOLS = TIER1 + TIER2
MIN_VOL_TIER1 = 1000; MIN_OI_TIER1 = 3000
MIN_VOL_TIER2 = 300; MIN_OI_TIER2 = 800
MAX_SPREAD_PCT = 0.08
MIN_PRICE = 0.4; MAX_PRICE = 12.0
RES_CACHE = {}; HIST_CACHE = {}; sent_signals = set()

def send(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=20)
    except Exception as e: print(f'SEND ERR {e}', flush=True)

def get_finnhub_quote_safe(sym):
    for _ in range(3):
        try: return finnhub_client.quote(sym)
        except Exception as e:
            if '429' in str(e): time.sleep(65)
            else: break
    return None

def calc_rsi(hist, period=14):
    try:
        delta = hist['Close'].diff(); gain = delta.where(delta>0,0).rolling(period).mean(); loss = -delta.where(delta<0,0).rolling(period).mean()
        return float((100-(100/(1+gain/loss))).iloc[-1])
    except: return 50.0
def calc_sma(hist, period=50):
    try: return float(hist['Close'].rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1])
def calc_atr(hist, period=14):
    try:
        hl=hist['High']-hist['Low']; hc=(hist['High']-hist['Close'].shift()).abs(); lc=(hist['Low']-hist['Close'].shift()).abs()
        tr=pd.concat([hl,hc,lc],axis=1).max(axis=1); return float(tr.rolling(period).mean().iloc[-1])
    except: return float(hist['Close'].iloc[-1]*0.02)
def calc_rvol(hist):
    try:
        if len(hist)<21: return 1.0
        return float(hist['Volume'].iloc[-1]) / float(hist['Volume'].tail(21).head(20).mean())
    except: return 1.0
def calc_vwap(hist):
    try:
        typical = (hist['High']+hist['Low']+hist['Close'])/3; last = hist.tail(78) if len(hist)>78 else hist
        return float((typical.tail(len(last)) * last['Volume']).sum() / last['Volume'].sum())
    except: return float(hist['Close'].iloc[-1])
def calc_order_block(hist):
    try:
        if len(hist) < 10: return None, None
        for i in range(len(hist)-2, 5, -1):
            if hist['Close'].iloc[i] < hist['Open'].iloc[i] and hist['Close'].iloc[i+1] > hist['High'].iloc[i]:
                return float(hist['Low'].iloc[i]), float(hist['High'].iloc[i])
        return float(hist['Low'].tail(10).min()), float(hist['High'].tail(10).max())
    except: return None, None
def check_liquidity_sweep(hist):
    try:
        if len(hist) < 22: return False, False
        history_20 = hist.iloc[:-1].tail(20); low_20 = float(history_20['Low'].min()); high_20 = float(history_20['High'].max())
        last_low = float(hist['Low'].iloc[-1]); last_high = float(hist['High'].iloc[-1]); last_close = float(hist['Close'].iloc[-1])
        return (last_low < low_20) and (last_close > low_20), (last_high > high_20) and (last_close < high_20)
    except: return False, False
def check_squeeze(hist):
    try:
        if len(hist)<20: return None
        close=hist['Close']; ma20=close.rolling(20).mean(); std20=close.rolling(20).std()
        upper_bb=ma20+2*std20; lower_bb=ma20-2*std20
        tr=pd.concat([hist['High']-hist['Low'], (hist['High']-hist['Close'].shift()).abs(), (hist['Low']-hist['Close'].shift()).abs()], axis=1).max(axis=1)
        atr=tr.rolling(20).mean()
        return {'squeeze':(lower_bb.iloc[-1]>ma20.iloc[-1]-1.5*atr.iloc[-1]) and (upper_bb.iloc[-1]<ma20.iloc[-1]+1.5*atr.iloc[-1]),'firing':False,'dir':'UP' if close.iloc[-1]>ma20.iloc[-1] else 'DOWN'}
    except: return None
def calc_levels(hist, entry, gamma_res=None, is_put=False):
    atr=calc_atr(hist)
    if is_put: stop=entry+atr*1.0; t1=entry-atr*1.5; t2=entry-atr*3.0; t3=entry-atr*5.0
    else: t1=gamma_res if (gamma_res and entry<gamma_res<entry+atr*2) else entry+atr*1.5; t2=entry+atr*3.0; t3=entry+atr*5.0; stop=entry-atr*1.0
    return round(stop,2), round(t1,2), round(t2,2), round(t3,2), round(atr,2)
def update_resistances():
    for sym in SYMBOLS:
        try:
            hist=yf.Ticker(sym).history(period='3mo', auto_adjust=True)
            if not hist.empty and len(hist)>=60: RES_CACHE[sym]=max(hist['High'].tail(5)); HIST_CACHE[sym]=hist
            time.sleep(2.5)
        except: time.sleep(10)
def get_gamma_walls(sym, price):
    try:
        t=yf.Ticker(sym); calls=pd.concat([t.option_chain(exp).calls for exp in t.options[:2]])
        above=calls[calls['strike']>=price]
        if above.empty: return None
        wall=above.sort_values('openInterest', ascending=False).iloc[0]
        return {'res_strike':float(wall['strike'])}
    except: return None
def get_opt(sym, price, mode, opt_type='CALL'):
    try:
        t=yf.Ticker(sym); exps=t.options
        if not exps: return None
        today=datetime.now().date(); daily=[]; weekly=[]; monthly=[]
        for exp in exps:
            d=(datetime.strptime(exp, '%Y-%m-%d').date()-today).days
            if 0<=d<=3: daily.append(exp)
            elif 4<=d<=12: weekly.append(exp)
            elif 13<=d<=45: monthly.append(exp)
        if mode=='daily': target=daily
        elif mode=='weekly': target=weekly
        else: target=monthly
        if not target: return None
        best=None; best_score=-1
        for exp in target[:3]:
            try:
                chain=t.option_chain(exp); df=chain.calls if opt_type=='CALL' else chain.puts
                df=df[(df['lastPrice']>=MIN_PRICE)&(df['lastPrice']<=MAX_PRICE)]
                filt=df[(df['strike']>=price*0.97)&(df['strike']<=price*1.08)] if opt_type=='CALL' else df[(df['strike']<=price*1.03)&(df['strike']>=price*0.92)]
                if filt.empty: continue
                for _, row in filt.iterrows():
                    vol=int(row['volume'] or 0); oi=int(row['openInterest'] or 0); bid=float(row.get('bid',0) or 0); ask=float(row.get('ask',0) or 0); last=float(row['lastPrice'] or 0)
                    if last==0 or bid==0 or ask==0: continue
                    spread=(ask-bid)/last; min_v=MIN_VOL_TIER1 if sym in TIER1 else MIN_VOL_TIER2; min_o=MIN_OI_TIER1 if sym in TIER1 else MIN_OI_TIER2
                    if vol<min_v or oi<min_o or spread>MAX_SPREAD_PCT: continue
                    score=(vol*0.7+oi*0.3)-(spread*1000)-abs(row['strike']-price)*2
                    if score>best_score: best_score=score; flow=(vol/oi*100) if oi>0 else 0; best={'strike':row['strike'],'last':last,'vol':vol,'oi':oi,'exp':exp,'whale':vol>2000 and flow>150,'spread':spread}
            except: continue
        return best
    except: return None

def loop():
    update_resistances()
    send('✅ V77 اشتغل - يومي + اسبوعي + شهري')
    last_res=time.time()
    while True:
        if time.time()-last_res>43200: update_resistances(); last_res=time.time(); sent_signals.clear()
        today_str=datetime.now().strftime('%Y-%m-%d')
        for s in SYMBOLS:
            try:
                res=RES_CACHE.get(s); hist=HIST_CACHE.get(s)
                if not res or hist is None: time.sleep(1.1); continue
                q=get_finnhub_quote_safe(s)
                if not q: time.sleep(1.1); continue
                p=float(q.get('c',0))
                if p==0: time.sleep(1.1); continue
                rsi=calc_rsi(hist); sma50=calc_sma(hist); sq=check_squeeze(hist)
                if sq is None: time.sleep(1.1); continue
                rvol=calc_rvol(hist); vwap=calc_vwap(hist)
                bull_sweep, bear_sweep = check_liquidity_sweep(hist); ob_low, ob_high = calc_order_block(hist)
                if bull_sweep: is_put=False
                elif bear_sweep: is_put=True
                else: time.sleep(1.1); continue
                in_ob = True if ob_low is None else (p >= ob_low * 0.97)
                if not ((sq['squeeze'] or sq['firing']) and (rvol >= 1.2) and in_ob): time.sleep(1.1); continue
                sig_key = f'{s}_{today_str}_sig'
                if sig_key in sent_signals: time.sleep(1.1); continue
                opt_type = 'PUT' if is_put else 'CALL'
                gamma = get_gamma_walls(s, p); gamma_strike = gamma['res_strike'] if gamma else None
                stop, t1, t2, t3, atr = calc_levels(hist, p, gamma_res=gamma_strike, is_put=is_put)
                d = get_opt(s, p, 'daily', opt_type); w = get_opt(s, p, 'weekly', opt_type); m = get_opt(s, p, 'monthly', opt_type)
                if d is None and w is None and m is None: time.sleep(1.1); continue
                icon = "🔥" if sq['firing'] else "🎯"; type_txt = "تنبيه انطلاق" if sq['firing'] else "تنبيه انضغاط متوقع انفجاره"
                tier_emoji = "🧠" if s in TIER1 else "💡"; sweep_type = "SSL" if bull_sweep else "BSL"
                msg = f"{icon} {type_txt} ({opt_type}) {tier_emoji} {s} [AAA - {sweep_type} + Squeeze]\n💧 سحب سيولة {sweep_type} + ارتداد\n💵 نقطة الدخول اللحظية: {p:.2f}$\n📊 RSI: {rsi:.0f} | المتوسط 50: {sma50:.2f}$\n📈 RVol: {rvol:.2f}x | VWAP: {vwap:.2f}$\n"
                if gamma and gamma['res_strike']: msg += f"🧱 حاجز المقاومة/غاما: {gamma['res_strike']:.2f}$\n"
                if ob_low and ob_high: msg += f"🧱 منطقة OB: {ob_low:.2f}$ - {ob_high:.2f}$\n"
                msg += f"\n🛑 وقف الخسارة: {stop}$\n🎯 الهدف الأول: {t1}$\n🎯 الهدف الثاني: {t2}$\n🎯 الهدف الثالث: {t3}$\n"
                if d:
                    whale_txt = " 🐋 دخول حيتان" if d['whale'] else ""
                    msg += f"\n📅 عقد يومي (0-3 أيام) ({d['exp']}){whale_txt}\n🔹 سترايك: {d['strike']}$ | السعر: {d['last']}$\n📈 فوليوم: {d['vol']} | OI: {d['oi']} | الفرق: {d['spread']:.1%}\n"
                if w:
                    msg += f"\n🛡️ عقد أسبوعي/آمن ({w['exp']})\n🔹 سترايك: {w['strike']}$ | السعر: {w['last']}$\n📈 فوليوم: {w['vol']} | OI: {w['oi']} | الفرق: {w['spread']:.1%}\n"
                if m:
                    msg += f"\n📆 عقد شهري ({m['exp']})\n🔹 سترايك: {m['strike']}$ | السعر: {m['last']}$\n📈 فوليوم: {m['vol']} | OI: {m['oi']} | الفرق: {m['spread']:.1%}\n"
                send(msg); sent_signals.add(sig_key); time.sleep(1.1)
            except Exception as e: print(f'LOOP ERR {s}:{e}'); time.sleep(1.1)
        time.sleep(30)
Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
