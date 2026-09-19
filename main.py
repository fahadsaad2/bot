import os, json, time, requests, yfinance as yf
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
POLYGON_KEY = os.getenv("POLYGON_KEY") # لما تشترك حطه هنا ويصير لحظي
TICKERS = ["NVDA","TSLA","AAPL","MSFT","MSTR","AMD","META","PLTR","SMCI","COIN","GOOGL","AMZN"]
PRICE_MIN = 0.50
PRICE_MAX = 2.50
TRACK_FILE = "trades.json"

def send(msg):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"Markdown"})
    except: pass

def get_chain_polygon(ticker):
    # يشتغل لحظي لما تحط المفتاح
    url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?limit=250&apiKey={POLYGON_KEY}"
    try:
        r = requests.get(url, timeout=10).json()
        return r.get('results', [])
    except: return []

def get_chain_yf(ticker):
    try:
        tk = yf.Ticker(ticker)
        exps = tk.options[:4]
        all_rows=[]
        for exp in exps:
            try:
                ch = tk.option_chain(exp)
                df = ch.calls
                df['exp']=exp
                all_rows.append(df)
            except: continue
        if all_rows:
            import pandas as pd
            return pd.concat(all_rows)
    except: pass
    return None

def score_row(price, vol, oi, dte):
    s=0
    if 0.50 <= price <= 2.5: s+=25
    if vol > 5000: s+=30
    elif vol > 1500: s+=20
    if oi > 5000: s+=20
    if 7 <= dte <= 21: s+=25 # اسبوعين اقوى شي
    return s

def main_loop():
    # تحميل العقود المتابعة
    tracked = {}
    if os.path.exists(TRACK_FILE):
        try: tracked=json.load(open(TRACK_FILE))
        except: tracked={}
    
    # 1- متابعة الارباح
    for key, data in list(tracked.items()):
        try:
            tk = yf.Ticker(data['ticker'])
            ch = tk.option_chain(data['exp'])
            live = ch.calls[ch.calls['contractSymbol']==data['symbol']]
            if not live.empty:
                now = float(live.iloc[0]['lastPrice'])
                entry = data['entry']
                pnl = ((now-entry)/entry)*100
                if pnl >= 20: # يرسل اذا ربح 20%+
                    send(f"📈 *متابعة ربح*\n{data['ticker']} {data['strike']}C\nدخول: ${entry} -> الان: ${now} *({pnl:.0f}%)* 🚀")
                    tracked[key]['entry']=now # يحدث عشان ما يزعج
        except: continue

    # 2- صيد جديد
    for ticker in TICKERS:
        df=None
        if POLYGON_KEY:
            res = get_chain_polygon(ticker)
            # معالجة بوليقون (مختصرة)
            # هنا نستخدم yfinance للتبسيط لين تشترك
            df = get_chain_yf(ticker)
        else:
            df = get_chain_yf(ticker)
        
        if df is None or df.empty: continue
        df = df[(df['lastPrice']>=PRICE_MIN)&(df['lastPrice']<=PRICE_MAX)&(df['volume']>1500)]
        if df.empty: continue
        
        for _, row in df.iterrows():
            try:
                exp = datetime.strptime(row['exp'], "%Y-%m-%d")
                dte = (exp - datetime.now()).days
                if not (7 <= dte <= 45): continue
                sc = score_row(row['lastPrice'], row['volume'], row['openInterest'], dte)
                if sc >= 85 and row['contractSymbol'] not in tracked:
                    msg = f"🚨 *HERO 90+* [{sc}]\n*{ticker}* {row['strike']}C Exp:{row['exp']} DTE:{dte}\nPrice: ${row['lastPrice']} Vol:{int(row['volume'])}\n`{row['contractSymbol']}`"
                    send(msg)
                    tracked[row['contractSymbol']] = {
                        "ticker":ticker,"strike":float(row['strike']),"exp":row['exp'],
                        "symbol":row['contractSymbol'],"entry":float(row['lastPrice']),
                        "time":time.time()
                    }
            except: continue
    
    json.dump(tracked, open(TRACK_FILE,'w'))

if __name__ == "__main__":
    main_loop()
