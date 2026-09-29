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
    return "Bot V4.5 Running"
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

def now_ksa():
    return datetime.now(KSA)
def now_us():
    return datetime.now(US_EASTERN)

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","LITE","MU","QCOM","APP"]
MAX_SIGNALS_PER_TICKER = 3
MIN_VOLUME = 500
MIN_OI = 500
MIN_DTE = 7
MAX_DTE = 21
MAX_SPREAD_PCT = 8.0
MIN_OPTION_PRICE = 0.40
MIN_STRIKE_DISTANCE = -0.06
MAX_STRIKE_DISTANCE = 0.06
MIN_SCORE = 72
SCAN_INTERVAL = 45
MAX_EXPIRATIONS = 4
EARNINGS_BUFFER_DAYS = 3

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
    except:
        pass

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": str(now_ksa().date()), "daily_count": dict(daily_count), "sent_contracts": list(sent_contracts)}, f, ensure_ascii=False, indent=2)
    except:
        pass

load_state()

def safe_float(v,d=0.0):
    try:
        if pd.isna(v):
            return d
        return float(v)
    except:
        return d

def safe_int(v,d=0):
    try:
        if pd.isna(v):
            return d
        return int(float(v))
    except:
        return d

def clamp(v,l,h):
    return max(l, min(h, v))

def nth_weekday(y,m,wd,n):
    d=date(y,m,1)
    offset=(wd-d.weekday())%7
    return d+timedelta(days=offset+(n-1)*7)

def last_weekday(y,m,wd):
    if m==12:
        d=date(y+1,1,1)-timedelta(days=1)
    else:
        d=date(y,m+1,1)-timedelta(days=1)
    offset=(d.weekday()-wd)%7
    return d-timedelta(days=offset)

def easter_sunday(y):
    a=y%19
    b=y//100
    c=y%100
    d=b//4
    e=b%4
    f=(b+8)//25
    g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30
    i=c//4
    k=c%4
    l=(32+2*e+2*i-h-k)%7
    m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31
    day=((h+l-7*m+114)%31)+1
    return date(y,month,day)

def observed_date(d):
    if d.weekday()==5:
        return d-timedelta(days=1)
    if d.weekday()==6:
        return d+timedelta(days=1)
    return d

def us_market_holidays(y):
    holidays=set()
    holidays.add(observed_date(date(y,1,1)))
    holidays.add(nth_weekday(y,1,0,3))
    holidays.add(nth_weekday(y,2,0,3))
    easter=easter_sunday(y)
    holidays.add(easter-timedelta(days=2))
    holidays.add(last_weekday(y,5,0))
    holidays.add(observed_date(date(y,6,19)))
    holidays.add(observed_date(date(y,7,4)))
    holidays.add(nth_weekday(y,9,0,1))
    holidays.add(nth_weekday(y,11,3,4))
    holidays.add(observed_date(date(y,12,25)))
    return holidays

def is_us_market_open():
    cur=now_us()
    if cur.weekday()>=5:
        return False
    if cur.date() in us_market_holidays(cur.year):
        return False
    start=datetime(cur.year,cur.month,cur.day,9,30,tzinfo=US_EASTERN).time()
    end=datetime(cur.year,cur.month,cur.day,16,0,tzinfo=US_EASTERN).time()
    return start <= cur.time() < end

def market_time_message():
    us=now_us()
    ksa=now_ksa()
    return f"توقيت امريكا: {us.strftime('%H:%M')} | السعودية: {ksa.strftime('%H:%M')}"

def calculate_dte(exp):
    try:
        return (datetime.strptime(exp,"%Y-%m-%d").date()-now_us().date()).days
    except:
        return 0

def get_spy_trend():
    try:
        df = yf.Ticker("SPY").history(period="1d", interval="5m", auto_adjust=False)
        if df.empty or len(df) < 20:
            return "NEUTRAL"
        close = df['Close']
        vol = df['Volume']
        vwap = (close * vol).cumsum() / vol.cumsum()
        ema20 = close.ewm(span=20, adjust=False).mean()
        price = safe_float(close.iloc[-1])
        v = safe_float(vwap.iloc[-1])
        e = safe_float(ema20.iloc[-1])
        if price > v and price > e:
            return "BULLISH"
        if price < v and price < e:
            return "BEARISH"
        return "NEUTRAL"
    except Exception as ex:
        print(ex)
        return "NEUTRAL"

def is_near_earnings(ticker):
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is not None and not cal.empty and 'Earnings Date' in cal:
            for edate in cal['Earnings Date']:
                if isinstance(edate, date):
                    diff=(edate-now_us().date()).days
                    if 0 <= diff <= EARNINGS_BUFFER_DAYS:
                        return True, diff
    except:
        pass
    return False, 0

def calculate_atr(hist, period=14):
    try:
        df=hist.copy()
        df['H-L']=df['High']-df['Low']
        df['H-PC']=abs(df['High']-df['Close'].shift(1))
        df['L-PC']=abs(df['Low']-df['Close'].shift(1))
        df['TR']=df[['H-L','H-PC','L-PC']].max(axis=1)
        return safe_float(df['TR'].rolling(period).mean().iloc[-1],1.0)
    except:
        return 1.0

def get_intraday_levels(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m", auto_adjust=False)
        if df.empty or len(df) < 5:
            return None
        pv=df['Close']*df['Volume']
        vwap=(pv.cumsum()/df['Volume'].cumsum()).iloc[-1]
        return {"vwap":safe_float(vwap),"day_low":safe_float(df['Low'].min()),"day_high":safe_float(df['High'].max()),"current":safe_float(df['Close'].iloc[-1])}
    except:
        return None

def technical_analysis(hist):
    result={"trend":"NEUTRAL","momentum":"NEUTRAL","rsi":50.0,"support":0,"resistance":0,"volume_ratio":1.0,"atr":1.0}
    try:
        close=hist["Close"].dropna()
        volume=hist["Volume"].fillna(0)
        if len(close)<20:
            return result
        ema20=close.ewm(span=20,adjust=False).mean()
        ema50=close.ewm(span=min(50,len(close)),adjust=False).mean()
        delta=close.diff()
        gain=delta.clip(lower=0).rolling(14).mean()
        loss=(-delta.clip(upper=0)).rolling(14).mean()
        rs=gain/loss.replace(0, float("nan"))
        rsi=100-(100/(1+rs))
        current=safe_float(close.iloc[-1])
        e20=safe_float(ema20.iloc[-1])
        e50=safe_float(ema50.iloc[-1])
        crsi=safe_float(rsi.iloc[-1],50)
        recent=close.tail(20)
        avg_vol=safe_float(volume.tail(20).mean())
        cur_vol=safe_float(volume.iloc[-1])
        if current>e20 and e20>e50:
            trend="BULLISH"
        elif current<e20 and e20<e50:
            trend="BEARISH"
        else:
            trend="NEUTRAL"
        if crsi>=52 and current>e20:
            momentum="BULLISH"
        elif crsi<=48 and current<e20:
            momentum="BEARISH"
        else:
            momentum="NEUTRAL"
        vr=cur_vol/avg_vol if avg_vol>0 else 1
        result={"trend":trend,"momentum":momentum,"rsi":crsi,"support":safe_float(recent.min()),"resistance":safe_float(recent.max()),"volume_ratio":vr,"atr":calculate_atr(hist)}
    except:
        pass
    return result

def norm_cdf(x):
    return 0.5*(1+math.erf(x/math.sqrt(2)))

def norm_pdf(x):
    return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)

def calculate_greeks(S,K,iv,dte,typ):
    try:
        if S<=0 or K<=0 or iv<=0 or dte<=0:
            return {}
        T=dte/365
        r=0.04
        sqrt_t=math.sqrt(T)
        d1=(math.log(S/K)+(r+0.5*iv*iv)*T)/(iv*sqrt_t)
        d2=d1-iv*sqrt_t
        gamma=norm_pdf(d1)/(S*iv*sqrt_t)
        vega=S*norm_pdf(d1)*sqrt_t/100
        if typ=="CALL":
            delta=norm_cdf(d1)
        else:
            delta=norm_cdf(d1)-1
        return {"delta":delta,"gamma":gamma,"vega":vega}
    except:
        return {}

def normalize_iv(iv):
    iv=safe_float(iv)
    if iv>5:
        iv=iv/100
    return iv

def iv_score(iv):
    p=iv*100
    if 15<=p<=55:
        return 10
    if 10<=p<=70:
        return 6
    if p<=90:
        return 2
    return 0

def analyze_contract(ticker,stock_price,row,expiration,option_type,technical,intraday,spy_trend):
    try:
        if spy_trend == "BEARISH" and option_type == "CALL":
            return None
        if spy_trend == "BULLISH" and option_type == "PUT":
            return None
        strike=safe_float(row.get("strike"))
        volume=safe_int(row.get("volume"))
        oi=safe_int(row.get("openInterest"))
        if strike<=0 or volume<MIN_VOLUME or oi<MIN_OI:
            return None
        bid=safe_float(row.get("bid"))
        ask=safe_float(row.get("ask"))
        last=safe_float(row.get("lastPrice"))
        if bid>0 and ask>0 and ask>=bid:
            premium=(bid+ask)/2
        else:
            premium=last
        if premium<MIN_OPTION_PRICE:
            return None
        if bid>0 and ask>0:
            spread=(ask-bid)/premium*100
        else:
            spread=999
        if spread>MAX_SPREAD_PCT:
            return None
        dte=calculate_dte(expiration)
        if dte<MIN_DTE or dte>MAX_DTE:
            return None
        dist=(strike-stock_price)/stock_price
        if dist<MIN_STRIKE_DISTANCE or dist>MAX_STRIKE_DISTANCE:
            return None
        iv=normalize_iv(row.get("impliedVolatility"))
        if iv<=0:
            return None
        vwap=intraday["vwap"] if intraday else 0
        if vwap>0:
            if option_type=="CALL" and stock_price < vwap:
                return None
            if option_type=="PUT" and stock_price > vwap:
                return None
        greeks=calculate_greeks(stock_price,strike,iv,dte,option_type)
        delta=abs(safe_float(greeks.get("delta")))
        score=0
        conf=0
        reasons=[]
        if spy_trend!="NEUTRAL":
            score=score+10
            conf=conf+1
            reasons.append("سوق متوافق " + spy_trend)
        if vwap>0:
            score=score+15
            conf=conf+1
            if option_type=="CALL":
                reasons.append("فوق VWAP")
            else:
                reasons.append("تحت VWAP")
        if technical["trend"]=="BULLISH" and option_type=="CALL":
            score=score+15
            conf=conf+1
            reasons.append("اتجاه صاعد قوي")
        if technical["trend"]=="BEARISH" and option_type=="PUT":
            score=score+15
            conf=conf+1
            reasons.append("اتجاه هابط قوي")
        if technical["momentum"]==("BULLISH" if option_type=="CALL" else "BEARISH"):
            score=score+10
            conf=conf+1
            if option_type=="CALL":
                reasons.append("زخم صاعد")
            else:
                reasons.append("زخم هابط")
        rsi=technical["rsi"]
        if 48<=rsi<=68 and option_type=="CALL":
            score=score+8
            reasons.append("RSI مناسب")
        if 32<=rsi<=52 and option_type=="PUT":
            score=score+8
            reasons.append("RSI مناسب")
        if technical["volume_ratio"]>=1.8:
            score=score+8
            reasons.append("فوليوم عالي")
        vol_oi=volume/max(oi,1)
        if vol_oi>=3:
            score=score+10
            conf=conf+1
            reasons.append("فوليوم قوي")
        if spread<=4:
            score=score+8
            conf=conf+1
            reasons.append("سبريد ضيق")
        if 0.35<=delta<=0.65:
            score=score+10
            conf=conf+1
            reasons.append("دلتا ممتازة")
        score=score+iv_score(iv)
        score=int(clamp(score,0,100))
        if score<MIN_SCORE or conf<3:
            return None
        atr=technical["atr"]
        if option_type=="CALL":
            stop_stock=stock_price-atr*1.2
            target_stock=stock_price+atr*1.8
        else:
            stop_stock=stock_price+atr*1.2
            target_stock=stock_price-atr*1.8
        risk=abs((stock_price-stop_stock)/stock_price*100)
        if risk<0.9:
            return None
        cid=f"{ticker}_{expiration}_{option_type}_{strike}"
        return {"id":cid,"ticker":ticker,"type":option_type,"expiration":expiration,"strike":strike,"stock_price":stock_price,"premium":premium,"spread":spread,"volume":volume,"oi":oi,"iv":iv,"dte":dte,"score":score,"conf":conf,"delta":greeks.get("delta",0),"rsi":rsi,"vwap":vwap,"stop_stock":stop_stock,"target_stock":target_stock,"risk":risk,"reasons":reasons,"atr":atr,"spy":spy_trend}
    except Exception as ex:
        print(ex)
        return None

def scan_ticker(ticker, spy_trend):
    try:
        has_earn,days=is_near_earnings(ticker)
        if has_earn:
            return []
        stock=yf.Ticker(ticker)
        hist=stock.history(period="1mo",interval="1d",auto_adjust=False)
        if hist.empty:
            return []
        price=safe_float(hist["Close"].dropna().iloc[-1])
        tech=technical_analysis(hist)
        intra=get_intraday_levels(ticker)
        if not intra:
            return []
        results=[]
        for exp in stock.options[:MAX_EXPIRATIONS]:
            if calculate_dte(exp)<MIN_DTE or calculate_dte(exp)>MAX_DTE:
                continue
            try:
                chain=stock.option_chain(exp)
                for _, row in chain.calls.iterrows():
                    r=analyze_contract(ticker,price,row,exp,"CALL",tech,intra,spy_trend)
                    if r:
                        results.append(r)
                for _, row in chain.puts.iterrows():
                    r=analyze_contract(ticker,price,row,exp,"PUT",tech,intra,spy_trend)
                    if r:
                        results.append(r)
            except:
                pass
        return results
    except:
        return []

def format_signal(x):
    is_call = x["type"]=="CALL"
    if is_call:
        tipo = "شراء"
        icon = "🟢"
        vwap_txt = "فوق"
        cp = "C"
    else:
        tipo = "بيع"
        icon = "🔴"
        vwap_txt = "تحت"
        cp = "P"
    entry=x['premium']
    stop_o=round(entry*0.60,2)
    target_o=round(entry*1.80,2)
    if is_call:
        be=x['strike']+entry
    else:
        be=x['strike']-entry
    txt_reasons = ""
    for rr in x['reasons']:
        txt_reasons = txt_reasons + "• " + rr + "\n"
    text=(
        f"{icon} <b>صفقة {tipo} قوية</b> | سوق {x['spy']}\n\n"
        f"📌 <b>{x['ticker']}</b> ${x['stock_price']:.2f}\n"
        f"📊 VWAP ${x['vwap']:.2f} {vwap_txt} | ATR ${x['atr']:.2f}\n\n"
        f"🎯 <b>{x['strike']:g}{cp}</b> | ينتهي {x['expiration']} متبقي {x['dte']} يوم\n\n"
        f"💰 دخول ${x['stock_price']:.2f} هدف ${x['target_stock']:.2f} وقف ${x['stop_stock']:.2f}\n"
        f"💵 عقد ${entry:.2f} وقف ${stop_o} هدف ${target_o}\n"
        f"🎯 تعادل ${be:.2f}\n\n"
        f"📊 فوليوم {x['volume']:,} عقود {x['oi']:,} سبريد {x['spread']:.1f}%\n"
        f"⭐ قوة {x['score']}/100 ثقة {x['conf']}\n"
        f"{txt_reasons}\n"
        f"🕐 {now_ksa().strftime('%H:%M')}"
    )
    return text

def process_ticker(ticker, spy_trend):
    if daily_count[ticker]>=MAX_SIGNALS_PER_TICKER:
        return
    res=scan_ticker(ticker, spy_trend)
    if not res:
        return
    res.sort(key=lambda xx: (xx["score"],xx["volume"]), reverse=True)
    for r_item in res:
        if r_item["id"] in sent_contracts:
            continue
        if send_tg(format_signal(r_item)):
            sent_contracts.add(r_item["id"])
            daily_count[ticker]=daily_count[ticker]+1
            save_state()
            print(f"OK {ticker} {r_item['type']} {r_item['strike']} {r_item['score']}")
            break

def startup_message(spy):
    send_tg(f"🚀 <b>البوت V4.5 جاهز</b>\n📋 {', '.join(TICKERS)}\n📈 SPY: {spy}\n\n{market_time_message()}")

spy_now = get_spy_trend()
startup_message(spy_now)

while True:
    try:
        if now_ksa().date()!=last_state_date:
            daily_count.clear()
            sent_contracts.clear()
            last_state_date=now_ksa().date()
            save_state()
        if not is_us_market_open():
            time.sleep(60)
            continue
        spy_trend = get_spy_trend()
        print(f"SCAN SPY {spy_trend} {now_ksa().strftime('%H:%M:%S')}")
        for t in TICKERS:
            try:
                if daily_count[t]<MAX_SIGNALS_PER_TICKER:
                    process_ticker(t, spy_trend)
                    time.sleep(1.5)
            except Exception as e:
                print(e)
        time.sleep(SCAN_INTERVAL)
    except Exception as e:
        print(e)
        time.sleep(15)
