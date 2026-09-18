import yfinance as yf, requests, os, time, math
from flask import Flask
import threading
from scipy.stats import norm

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","TSLA","META","AMZN","GOOGL","MU","AMD","NFLX","AVGO","SMCI","PLTR","SNDK"]

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def calc_delta(S,K,T,r,sigma,opt="call"):
    try:
        d1 = (math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
        return norm.cdf(d1) if opt=="call" else norm.cdf(d1)-1
    except: return 0.6

def get_opt(ticker, sig):
    try:
        stock=yf.Ticker(ticker)
        exps=stock.options
        if not exps: return None
        exp=exps[2] if len(exps)>2 else exps[0]
        chain=stock.option_chain(exp)
        opts=chain.calls if "CALL" in sig else chain.puts
        price=stock.history(period="1d")['Close'].iloc[-1]
        for _,row in opts.iterrows():
            iv=row['impliedVolatility'] or 0.5
            d=calc_delta(price,row['strike'],21/365,0.05,iv,"call" if "CALL" in sig else "put")
            vol=row['volume'] or 0
            if "CALL" in sig and 0.6<=d<=0.75 and vol>50: return (exp,row,d)
            if "PUT" in sig and -0.75<=d<=-0.6 and vol>50: return (exp,row,d)
        atm=opts.iloc[(opts['strike']-price).abs().argsort()[:1]]
        return (exp,atm.iloc[0],0.5)
    except: return None

def analyze():
    for t in TICKERS:
        try:
            data=yf.download(t,period="6mo",interval="1d",progress=False,auto_adjust=True)
            close=data['Close']
            if len(close)<200: continue
            e20=close.ewm(span=20).mean().iloc[-1]
            e50=close.ewm(span=50).mean().iloc[-1]
            e200=close.ewm(span=200).mean().iloc[-1]
            price=close.iloc[-1]
            dlt=close.diff()
            gain=dlt.where(dlt>0,0).rolling(14).mean()
            loss=-dlt.where(dlt<0,0).rolling(14).mean()
            rsi=100-(100/(1+gain/loss))
            rsi_now=rsi.iloc[-1]
            vol=data['Volume'].iloc[-1]
            avg=data['Volume'].rolling(20).mean().iloc[-1]
            sig=None
            if price>e20>e50>e200 and 50<rsi_now<70 and vol>avg*1.3: sig="CALL_SWING"
            elif price<e20<e50<e200 and 30<rsi_now<50: sig="PUT_SWING"
            if sig:
                b=get_opt(t,sig)
                if b:
                    exp,row,delta=b
                    send(f"🚀 *{t}* {'CALL 📈' if 'CALL' in sig else 'PUT 📉'}\nالسعر: {price:.2f}\nاكسبايري: {exp}\nسترايك: ${row['strike']} دلتا {delta:.2f}\nالعقد: ${row['lastPrice']:.2f}\nRSI: {rsi_now:.1f}")
                    time.sleep(10)
        except: pass

app=Flask('')
@app.route('/')
def home(): return "16 Companies Bot Running"
threading.Thread(target=lambda: app.run(host='0.0.0.0',port=int(os.environ.get("PORT",8080)))).start()
send("✅ *بوت 16 شركة اشتغل (مع SNDK)*")
while True:
    analyze()
    time.sleep(1800)
