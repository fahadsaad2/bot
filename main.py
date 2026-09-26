from flask import Flask
import threading
import yfinance as yf
import requests
import time
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import pandas as pd

app = Flask(__name__)
@app.route('/')
def home():
    return "V35 FREE OK - ENV MODE"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

KSA = timezone(timedelta(hours=3))
TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]
daily_count = defaultdict(int)
last_reset = datetime.now(KSA).day
sent_contracts = set()

def now_ksa():
    return datetime.now(KSA)

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text}, timeout=15)
    except:
        pass

def scan_ticker(tk):
    try:
        stock = yf.Ticker(tk)
        hist = stock.history(period="1d")
        if hist.empty:
            return []
        price = float(hist["Close"].iloc[-1])
        max_oi = 0
        wall_strike = 0
        for exp in stock.options[:2]:
            try:
                chain = stock.option_chain(exp)
                if chain.calls.empty:
                    continue
                best = chain.calls.loc[chain.calls["openInterest"].idxmax()]
                if best["openInterest"] > max_oi:
                    max_oi = int(best["openInterest"])
                    wall_strike = float(best["strike"])
            except:
                continue
        if max_oi == 0:
            return []
        ops = []
        for exp in stock.options[:2]:
            try:
                chain = stock.option_chain(exp)
                calls = chain.calls
                if calls.empty:
                    continue
                calls = calls[(calls["strike"] >= price*0.95) & (calls["strike"] <= price*1.08)]
                for _, c in calls.iterrows():
                    vol = int(c["volume"]) if pd.notna(c["volume"]) else 0
                    oi = int(c["openInterest"]) if pd.notna(c["openInterest"]) else 1
                    if oi == 0:
                        oi = 1
                    if vol < 800:
                        continue
                    vol_oi = vol / oi
                    cid = f"{tk}_{exp}_{c['strike']}"
                    if cid in sent_contracts:
                        continue
                    score = 0
                    reasons = []
                    if vol_oi >= 5:
                        score += 4
                        reasons.append(f"INF {vol_oi:.1f}x")
                    elif vol_oi >= 3:
                        score += 3
                        reasons.append(f"{vol_oi:.1f}x")
                    dist = ((c["strike"] - price)/price*100)
                    if 0.1 < dist < 2.5 and max_oi > 15000:
                        score += 4
                        reasons.append(f"WALL {wall_strike:.0f} {dist:.1f}%")
                    if score >= 6:
                        ops.append({"tk":tk,"strike":float(c["strike"]),"price":float(c["lastPrice"]) if pd.notna(c["lastPrice"]) else 0,"vol":vol,"oi":oi,"wall":wall_strike,"wall_oi":max_oi,"dist":dist,"score":score,"reasons":reasons,"id":cid,"stock_price":price})
            except:
                continue
        return ops
    except Exception as e:
        print(e)
        return []

send_tg(f"V35 START ENV {now_ksa().strftime('%H:%M')}")

while True:
    try:
        n = now_ksa()
        if n.day!= last_reset and n.hour == 0:
            daily_count.clear()
            sent_contracts.clear()
            last_reset = n.day
        if not (n.weekday() < 5 and 16 <= n.hour <= 23):
            time.sleep(60)
            continue
        for tk in TICKERS:
            if daily_count[tk] >= 5:
                continue
            items = scan_ticker(tk)
            if not items:
                continue
            best = sorted(items, key=lambda x: x["score"], reverse=True)[0]
            if best["score"] >= 7:
                forced = best["wall_oi"]*50
                msg = f"LVL4 {best['score']}/10 {best['tk']} | {' | '.join(best['reasons'])} | Vol {best['vol']} OI {best['oi']} | {best['strike']}C @ {best['price']} | WALL {best['wall']} {best['wall_oi']} | {best['stock_price']} {best['dist']:.1f}% | {forced} shares"
                send_tg(msg)
                daily_count[tk] += 1
                sent_contracts.add(best["id"])
                time.sleep(3)
        time.sleep(30)
    except Exception as e:
        print(e)
        time.sleep(10)
