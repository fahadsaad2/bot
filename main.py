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
@app.route("/")
def home():
    return "OPTIONS V4.1 - 7-30 DTE - 500 VOL - KSA"
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except Exception as e:
        print(e)
        return False

KSA = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")
def now_ksa(): return datetime.now(KSA)
def now_us(): return datetime.now(US_EASTERN)

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]

# ===== V4.1 FINAL =====
MAX_SIGNALS_PER_TICKER = 5
MIN_VOLUME = 500
MIN_OI = 100
MIN_DTE = 7
MAX_DTE = 30
MAX_SPREAD_PCT = 15.0
MIN_OPTION_PRICE = 0.10
MIN_STRIKE_DISTANCE = -0.10
MAX_STRIKE_DISTANCE = 0.10
MIN_SCORE = 70
MIN_CONFIRMATIONS = 3
SCAN_INTERVAL = 45
MAX_EXPIRATIONS = 5
# ======================

STATE_FILE = "bot_state_v4.json"
daily_count = defaultdict(int)
sent_contracts = set()
last_state_date = now_ksa().date()

def load_state():
    global last_state_date
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == str(now_ksa().date()):
                daily_count.update(data.get("daily_count", {}))
                sent_contracts.update(data.get("sent_contracts", []))
                last_state_date = now_ksa().date()
    except Exception as e: print(e)

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": str(now_ksa().date()), "daily_count": dict(daily_count), "sent_contracts": list(sent_contracts)}, f, ensure_ascii=False, indent=2)
    except Exception as e: print(e)

load_state()

def safe_float(v,d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except: return d
def safe_int(v,d=0):
    try:
        if pd.isna(v): return d
        return int(float(v))
    except: return d
def clamp(v,l,h): return max(l, min(h, v))
def nth_weekday(year,month,weekday,n):
    d=date(year,month,1); offset=(weekday-d.weekday())%7
    return d+timedelta(days=offset+(n-1)*7)
def last_weekday(year,month,weekday):
    d=date(year+1,1,1)-timedelta(days=1) if month==12 else date(year,month+1,1)-timedelta(days=1)
    offset=(d.weekday()-weekday)%7
    return d-timedelta(days=offset)
def easter_sunday(year):
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4; f=(b+8)//25; g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30; i=c//4; k=c%4; l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31; day=((h+l-7*m+114)%31)+1
    return date(year,month,day)
def observed_date(d):
    if d.weekday()==5: return d-timedelta(days=1)
    if d.weekday()==6: return d+timedelta(days=1)
    return d
def us_market_holidays(year):
    holidays=set()
    holidays.add(observed_date(date(year,1,1))); holidays.add(nth_weekday(year,1,0,3))
    holidays.add(nth_weekday(year,2,0,3)); easter=easter_sunday(year)
    holidays.add(easter-timedelta(days=2)); holidays.add(last_weekday(year,5,0))
    holidays.add(observed_date(date(year,6,19))); holidays.add(observed_date(date(year,7,4)))
    holidays.add(nth_weekday(year,9,0,1)); holidays.add(nth_weekday(year,11,3,4))
    holidays.add(observed_date(date(year,12,25)))
    return holidays
def is_us_market_open():
    current=now_us()
    if current.weekday()>=5: return False
    if current.date() in us_market_holidays(current.year): return False
    start=datetime(current.year,current.month,current.day,9,30,tzinfo=US_EASTERN).time()
    end=datetime(current.year,current.month,current.day,16,0,tzinfo=US_EASTERN).time()
    return start <= current.time() < end
def market_time_message():
    us=now_us(); ksa=now_ksa()
    return f"🇺🇸 السوق: {us.strftime('%H:%M')} ET\n🇸🇦 السعودية: {ksa.strftime('%H:%M')}"
def calculate_dte(expiration):
    try: exp_date=datetime.strptime(expiration,"%Y-%m-%d").date(); return (exp_date-now_us().date()).days
    except: return 0
def calculate_rsi(series,period=14):
    try:
        delta=series.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
        avg_gain=gain.rolling(period).mean(); avg_loss=loss.rolling(period).mean()
        rs=avg_gain/avg_loss.replace(0,math.nan); return 100-(100/(1+rs))
    except: return pd.Series(index=series.index,dtype=float)
def technical_analysis(hist):
    result={"trend":"NEUTRAL","momentum":"NEUTRAL","rsi":50.0,"ema20":0,"ema50":0,"support":0,"resistance":0,"volume_ratio":1.0}
    try:
        close=hist["Close"].dropna(); volume=hist["Volume"].fillna(0)
        if len(close)<50: return result
        ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean()
        rsi=calculate_rsi(close,14); current=safe_float(close.iloc[-1]); e20=safe_float(ema20.iloc[-1]); e50=safe_float(ema50.iloc[-1])
        current_rsi=safe_float(rsi.iloc[-1],50); recent=close.tail(20)
        support=safe_float(recent.min()); resistance=safe_float(recent.max())
        avg_volume=safe_float(volume.tail(20).mean()); current_volume=safe_float(volume.iloc[-1])
        volume_ratio=current_volume/avg_volume if avg_volume>0 else 1
        trend="BULLISH" if current>e20>e50 else "BEARISH" if current<e20<e50 else "NEUTRAL"
        momentum="BULLISH" if current_rsi>=55 and current>e20 else "BEARISH" if current_rsi<=45 and current<e20 else "NEUTRAL"
        result={"trend":trend,"momentum":momentum,"rsi":current_rsi,"ema20":e20,"ema50":e50,"support":support,"resistance":resistance,"volume_ratio":volume_ratio}
    except Exception as e: print(e)
    return result
def norm_cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def norm_pdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
def calculate_greeks(stock_price,strike,iv,dte,option_type):
    try:
        if stock_price<=0 or strike<=0 or iv<=0 or dte<=0: return {}
        S=stock_price; K=strike; sigma=iv; T=dte/365; r=0.04; sqrt_t=math.sqrt(T)
        d1=(math.log(S/K)+(r+0.5*sigma*sigma)*T)/(sigma*sqrt_t); d2=d1-sigma*sqrt_t
        gamma=norm_pdf(d1)/(S*sigma*sqrt_t); vega=S*norm_pdf(d1)*sqrt_t/100
        if option_type=="CALL":
            delta=norm_cdf(d1); theta=(-(S*norm_pdf(d1)*sigma/(2*sqrt_t))-r*K*math.exp(-r*T)*norm_cdf(d2))/365
        else:
            delta=norm_cdf(d1)-1; theta=(-(S*norm_pdf(d1)*sigma/(2*sqrt_t))+r*K*math.exp(-r*T)*norm_cdf(-d2))/365
        return {"delta":delta,"gamma":gamma,"theta":theta,"vega":vega}
    except: return {}
def calculate_walls(calls,puts):
    result={"call_wall":0,"call_wall_oi":0,"put_wall":0,"put_wall_oi":0}
    try:
        if not calls.empty:
            c=calls.copy(); c["openInterest"]=pd.to_numeric(c["openInterest"],errors="coerce").fillna(0)
            row=c.loc[c["openInterest"].idxmax(); result["call_wall"]=safe_float(row["strike"]); result["call_wall_oi"]=safe_int(row["openInterest"])
    except: pass
    try:
        if not puts.empty:
            p=puts.copy(); p["openInterest"]=pd.to_numeric(p["openInterest"],errors="coerce").fillna(0)
            row=p.loc[p["openInterest"].idxmax(); result["put_wall"]=safe_float(row["strike"]); result["put_wall_oi"]=safe_int(row["openInterest"])
    except: pass
    return result
def normalize_iv(iv):
    iv=safe_float(iv)
    if iv<=0: return 0
    if iv>5: iv/=100
    return iv
def iv_score(iv):
    iv_pct=iv*100
    if 15<=iv_pct<=55: return 10
    if 10<=iv_pct<=70: return 6
    if iv_pct<=90: return 2
    return 0
def analyze_contract(ticker,stock_price,row,expiration,option_type,walls,technical):
    try:
        strike=safe_float(row.get("strike")); volume=safe_int(row.get("volume")); oi=safe_int(row.get("openInterest"))
        if strike<=0 or volume<MIN_VOLUME or oi<MIN_OI: return None
        bid=safe_float(row.get("bid")); ask=safe_float(row.get("ask")); last_price=safe_float(row.get("lastPrice"))
        premium=(bid+ask)/2 if bid>0 and ask>0 and ask>=bid else last_price
        if premium<MIN_OPTION_PRICE: return None
        spread_pct=((ask-bid)/premium)*100 if bid>0 and ask>0 else 999
        if spread_pct>MAX_SPREAD_PCT: return None
        dte=calculate_dte(expiration)
        if dte<MIN_DTE or dte>MAX_DTE: return None
        strike_distance=(strike-stock_price)/stock_price
        if strike_distance<MIN_STRIKE_DISTANCE or strike_distance>MAX_STRIKE_DISTANCE: return None
        iv=normalize_iv(row.get("impliedVolatility"))
        if iv<=0: return None
        vol_oi=volume/max(oi,1); greeks=calculate_greeks(stock_price,strike,iv,dte,option_type)
        delta=abs(safe_float(greeks.get("delta"))); score=0; confirmations=0; reasons=[]; warnings=[]
        if option_type=="CALL":
            if technical["trend"]=="BULLISH": score+=15; confirmations+=1; reasons.append("اتجاه صاعد")
            elif technical["trend"]=="BEARISH": score-=15; warnings.append("الاتجاه ضد CALL")
            if technical["momentum"]=="BULLISH": score+=10; confirmations+=1; reasons.append("زخم صاعد")
            elif technical["momentum"]=="BEARISH": score-=10; warnings.append("الزخم ضد CALL")
        else:
            if technical["trend"]=="BEARISH": score+=15; confirmations+=1; reasons.append("اتجاه هابط")
            elif technical["trend"]=="BULLISH": score-=15; warnings.append("الاتجاه ضد PUT")
            if technical["momentum"]=="BEARISH": score+=10; confirmations+=1; reasons.append("زخم هابط")
            elif technical["momentum"]=="BULLISH": score-=10; warnings.append("الزخم ضد PUT")
        rsi=technical["rsi"]
        if option_type=="CALL":
            if 50<=rsi<=68: score+=8; reasons.append(f"RSI مناسب {rsi:.0f}")
            elif rsi>75: score-=5; warnings.append("RSI مرتفع")
        else:
            if 32<=rsi<=50: score+=8; reasons.append(f"RSI مناسب {rsi:.0f}")
            elif rsi<25: score-=5; warnings.append("RSI منخفض جدا")
        volume_ratio=technical["volume_ratio"]
        if volume_ratio>=2: score+=10; confirmations+=1; reasons.append(f"Volume قوي {volume_ratio:.1f}x")
        elif volume_ratio>=1.3: score+=6
        if vol_oi>=5: score+=12; confirmations+=1; reasons.append(f"Volume/OI {vol_oi:.1f}x")
        elif vol_oi>=3: score+=9
        elif vol_oi>=2: score+=5
        if volume>=10000: score+=10
        elif volume>=5000: score+=7
        elif volume>=1500: score+=4
        if oi>=10000: score+=8
        elif oi>=5000: score+=6
        elif oi>=2000: score+=4
        if spread_pct<=3: score+=12; confirmations+=1; reasons.append("سيولة ممتازة")
        elif spread_pct<=6: score+=8
        elif spread_pct<=10: score+=4
        abs_distance=abs(strike_distance)
        if abs_distance<=0.02: score+=10; reasons.append("Strike قريب من السعر")
        elif abs_distance<=0.04: score+=7
        elif abs_distance<=0.06: score+=4
        if 10<=dte<=30: score+=8
        elif 7<=dte<=30: score+=5
        if 0.35<=delta<=0.65: score+=10; confirmations+=1; reasons.append(f"Delta مناسب {delta:.2f}")
        elif 0.25<=delta<=0.75: score+=5
        else: warnings.append(f"Delta بعيد {delta:.2f}")
        iv_points=iv_score(iv); score+=iv_points
        if iv_points>=6: reasons.append(f"IV مناسب {iv*100:.1f}%")
        support=technical["support"]; resistance=technical["resistance"]
        if option_type=="CALL" and resistance>0:
            distance=(resistance-stock_price)/stock_price
            if 0<=distance<=0.03: score+=5; reasons.append("قريب من مقاومة")
        if option_type=="PUT" and support>0:
            distance=(stock_price-support)/stock_price
            if 0<=distance<=0.03: score+=5; reasons.append("قريب من دعم")
        if option_type=="CALL":
            wall=walls["call_wall"]
            if wall>0 and abs(strike-wall)/stock_price<=0.03: score+=5; reasons.append(f"قرب Call Wall {wall:g}")
        else:
            wall=walls["put_wall"]
            if wall>0 and abs(strike-wall)/stock_price<=0.03: score+=5; reasons.append(f"قرب Put Wall {wall:g}")
        if option_type=="CALL" and technical["trend"]=="BEARISH" and technical["momentum"]=="BEARISH": return None
        if option_type=="PUT" and technical["trend"]=="BULLISH" and technical["momentum"]=="BULLISH": return None
        score=int(clamp(score,0,100))
        if score<MIN_SCORE or confirmations<MIN_CONFIRMATIONS: return None
        cid=f"{ticker}_{expiration}_{option_type}_{strike}"
        return {"id":cid,"ticker":ticker,"type":option_type,"expiration":expiration,"strike":strike,"stock_price":stock_price,"premium":premium,"bid":bid,"ask":ask,"spread_pct":spread_pct,"volume":volume,"oi":oi,"vol_oi":vol_oi,"iv":iv,"dte":dte,"score":score,"confirmations":confirmations,"delta":greeks.get("delta",0),"gamma":greeks.get("gamma",0),"theta":greeks.get("theta",0),"vega":greeks.get("vega",0),"rsi":rsi,"trend":technical["trend"],"momentum":technical["momentum"],"volume_ratio":volume_ratio,"call_wall":walls["call_wall"],"call_wall_oi":walls["call_wall_oi"],"put_wall":walls["put_wall"],"put_wall_oi":walls["put_wall_oi"],"support":support,"resistance":resistance,"reasons":reasons,"warnings":warnings}
    except Exception as e:
        print(f"Contract error {ticker}:",e); return None

def scan_ticker(ticker):
    try:
        stock=yf.Ticker(ticker); hist=stock.history(period="3mo",interval="1d",auto_adjust=False)
        if hist.empty:
            print(f"DEBUG {ticker}: hist empty - yfinance blocked"); return []
        stock_price=safe_float(hist["Close"].dropna().iloc[-1])
        if stock_price<=0: return []
        technical=technical_analysis(hist); expirations=stock.options
        if not expirations: return []
        valid_expirations=[]
        for exp in expirations:
            dte=calculate_dte(exp)
            if MIN_DTE<=dte<=MAX_DTE: valid_expirations.append(exp)
        valid_expirations=valid_expirations[:MAX_EXPIRATIONS]; results=[]
        for expiration in valid_expirations:
            try:
                chain=stock.option_chain(expiration); calls=chain.calls; puts=chain.puts
                if calls.empty and puts.empty: continue
                walls=calculate_walls(calls,puts)
                if not calls.empty:
                    for _,row in calls.iterrows():
                        r=analyze_contract(ticker,stock_price,row,expiration,"CALL",walls,technical)
                        if r: results.append(r)
                if not puts.empty:
                    for _,row in puts.iterrows():
                        r=analyze_contract(ticker,stock_price,row,expiration,"PUT",walls,technical)
                        if r: results.append(r)
            except Exception as e: print(f"Exp error {ticker} {expiration}: {e}")
        if results:
            best = sorted(results, key=lambda x: x["score"], reverse=True)[0]
            print(f"DEBUG {ticker}: found {len(results)} - best {best['ticker']} {best['type']} {best['strike']} score {best['score']}")
        else:
            print(f"DEBUG {ticker}: found 0 contracts that pass filters")
        return results
    except Exception as e: print(f"Scan error {ticker}: {e}"); return []

def format_signal(x):
    is_call=x["type"]=="CALL"; emoji="🟢" if is_call else "🔴"; option_name="CALL" if is_call else "PUT"
    breakeven=x["strike"]+x["premium"] if is_call else x["strike"]-x["premium"]
    reasons="\n".join(f"• {r}" for r in x["reasons"]); warnings=""
    if x["warnings"]: warnings="\n\n⚠️ ملاحظات:\n"+"\n".join(f"• {w}" for w in x["warnings"])
    text=(f"{emoji} <b>OPTIONS V4.1 — {option_name}</b>\n\n📌 السهم: <b>{x['ticker']}</b>\n💰 سعر السهم: ${x['stock_price']:.2f}\n\n🎯 العقد: <b>{x['strike']:g}{'C' if is_call else 'P'}</b>\n📅 الانتهاء: {x['expiration']}\n⏳ DTE: {x['dte']} يوم (7-30)\n\n💵 Premium: ${x['premium']:.2f}\n↔️ Bid/Ask: ${x['bid']:.2f} / ${x['ask']:.2f}\n📐 Spread: {x['spread_pct']:.1f}%\n\n📊 Volume: {x['volume']:,}\n📦 OI: {x['oi']:,}\n🔥 Volume/OI: {x['vol_oi']:.1f}x\n🌡️ IV: {x['iv']*100:.1f}%\n\n📈 الاتجاه: {x['trend']}\n⚡ الزخم: {x['momentum']}\nRSI: {x['rsi']:.1f}\n📊 Volume Ratio: {x['volume_ratio']:.1f}x\n\n🧮 <b>Greeks</b>\nDelta: {x['delta']:.2f}\nGamma: {x['gamma']:.4f}\nTheta: {x['theta']:.4f}\nVega: {x['vega']:.4f}\n\n🧱 Call Wall: {x['call_wall']:g} ({x['call_wall_oi']:,})\n🧱 Put Wall: {x['put_wall']:g} ({x['put_wall_oi']:,})\n\n🟦 Support: ${x['support']:.2f}\n🟥 Resistance: ${x['resistance']:.2f}\n\n🎯 Break-even: ${breakeven:.2f}\n\n⭐ <b>قوة الإشارة: {x['score']}/100</b>\n✅ التأكيدات: {x['confirmations']}/{MIN_CONFIRMATIONS}\n\n🔎 <b>أسباب الإشارة:</b>\n{reasons}{warnings}\n\n🕐 السعودية: {now_ksa().strftime('%Y-%m-%d %H:%M:%S')}\n🇺🇸 السوق: {now_us().strftime('%H:%M ET')}\n\n⚠️ <i>تنبيه آلي تعليمي، وليس توصية استثمارية.</i>")
    return text

def process_ticker(ticker):
    if daily_count[ticker]>=MAX_SIGNALS_PER_TICKER: return
    results=scan_ticker(ticker)
    if not results: return
    results.sort(key=lambda x: (x["score"],x["confirmations"],x["vol_oi"],x["volume"]), reverse=True)
    for result in results:
        if daily_count[ticker]>=MAX_SIGNALS_PER_TICKER: break
        cid=result["id"]
        if cid in sent_contracts: continue
        message=format_signal(result)
        if not send_tg(message): continue
        sent_contracts.add(cid); daily_count[ticker]+=1; save_state()
        print("تم إرسال:",ticker,result["type"],result["strike"],result["score"])
        time.sleep(2); break

def startup_message():
    text=(f"🚀 <b>OPTIONS V4.1 اشتغل - 500 VOL - 70 SCORE</b>\n\n🇸🇦 التوقيت: السعودية\n🇺🇸 السوق: الولايات المتحدة\n\n{market_time_message()}\n\n🧠 <b>V4.1 FILTERS</b>\n📊 Volume: {MIN_VOLUME}+\n⭐ الحد الأدنى: {MIN_SCORE}/100\n✅ التأكيدات: {MIN_CONFIRMATIONS}\n⏳ DTE: {MIN_DTE}-{MAX_DTE} يوم\n\n⚠️ Scanner فقط.")
    send_tg(text)

last_market_status=None
def send_market_status_if_changed():
    global last_market_status
    status=is_us_market_open()
    if status==last_market_status: return
    last_market_status=status
    send_tg(f"{'🟢 <b>السوق مفتوح</b>' if status else '🔴 <b>السوق مغلق</b>'}\n\n{market_time_message()}")

def reset_daily():
    global last_state_date
    today=now_ksa().date()
    if today!=last_state_date:
        daily_count.clear(); sent_contracts.clear(); last_state_date=today; save_state()

startup_message()
while True:
    try:
        reset_daily(); send_market_status_if_changed()
        if not is_us_market_open():
            time.sleep(60); continue
        print(f"\nOPTIONS V4.1 SCAN 7-30 500VOL - {now_ksa()}")
        for ticker in TICKERS:
            try:
                if daily_count[ticker]>=MAX_SIGNALS_PER_TICKER: continue
                process_ticker(ticker); time.sleep(2)
            except Exception as e: print(f"خطأ {ticker}: {e}")
        time.sleep(SCAN_INTERVAL)
    except Exception as e:
        print("MAIN ERROR:",e); time.sleep(15)
