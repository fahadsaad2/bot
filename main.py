import yfinance as yf, requests, os, time, threading
from flask import Flask

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TICKERS = ["SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","SNDK","SMCI","AVGO","PLTR","^GSPC"]

NAMES = {
"SPY":"SPY","QQQ":"Nasdaq","AAPL":"Apple","NVDA":"Nvidia","MSFT":"Microsoft",
"GOOGL":"Google","AMZN":"Amazon","TSLA":"Tesla","META":"Meta","NFLX":"Netflix",
"AMD":"AMD","SNDK":"SanDisk","SMCI":"Supermicro","AVGO":"Broadcom","PLTR":"Palantir","^GSPC":"SPX"
}

app = Flask(__name__)
@app.route('/')
def home(): return "All 16 + SPX Flow Bot running!"

def send(msg):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=15)
    except: pass

def get_hot_strikes(ticker):
    real_ticker = "SPY" if ticker=="^GSPC" else ticker
    try:
        tk = yf.Ticker(real_ticker)
        exps = tk.options[:2]
        hot = []
        for exp in exps:
            chain = tk.option_chain(exp)
            for df, typ in [(chain.calls, "CALL"), (chain.puts, "PUT")]:
                filtered = df[(df['volume']>300) & (df['volume'] > df['openInterest']*0.5)]
                filtered = filtered.sort_values('volume', ascending=False).head(1)
                for _, r in filtered.iterrows():
                    label = "SPX" if ticker=="^GSPC" else ticker
                    hot.append(f"🔥 <b>{NAMES.get(ticker,ticker)} ({label}) ${r['strike']:.0f} {typ}</b>\n📅 {exp} | Vol:{int(r['volume'])} OI:{int(r['openInterest'])} | ${r['lastPrice']:.2f}\n👉 دخول: كسر وثبات فوق ${r['strike']:.0f}\n👉 خروج: +25% / وقف -15%")
       
