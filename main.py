from flask import Flask
from threading import Thread
import os, time, requests, finnhub, yfinance as yf
from datetime import datetime
app = Flask(__name__)
@app.route('/')
def home(): return "البوت شغال"
def run_web():
 port = int(os.environ.get("PORT", 10000))
 app.run(host='0.0.0.0', port=port)
Thread(target=run_web, daemon=True).start()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None
SYMBOLS = ["NVDA","TSLA","SMCI","MSTR","COIN","AAPL","GOOGL","META","AMD","AMZN","MSFT","PLTR","APP","ARM","AVGO","MU","LITE","SNDK","RDDT"]
sent = set()
RES_CACHE = {}
def send(msg):
 try:
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
  print(f"Telegram {r.status_code}: {r.text[:200]}")
 except Exception as e:
  print(f"Send error: {e}")
def update_resistances():
 now = int(time.time())
 frm = now - 60*60*24*30
 for sym in SYMBOLS:
  try:
   c = finnhub_client.stock_candles(sym, 'D', frm, now)
   if c.get('s') == 'ok' and len(c['h']) >= 20:
    RES_CACHE[sym] = max(c['h'][-20:])
   time.sleep(1.2)
  except Exception as e:
   print(f"Res error {sym}: {e}")
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
  return {"strike": best['strike'], "last": best['lastPrice'], "vol": int(best['volume'] or 0), "oi": int(best['openInterest'] or 0), "exp": exp}
 except: return None
def loop():
 while True:
  if not RES_CACHE:
   update_resistances()
  today = datetime.now().strftime("%Y-%m-%d")
  for s in SYMBOLS:
   try:
    res = RES_CACHE.get(s)
    if not res: continue
    q = finnhub_client.quote(s)
    p = float(q.get('c', 0))
    if p == 0: continue
    key = f"{s}_{today}"
    if p >= res * 0.995 and key not in sent:
     entry, stop = res, round(res*0.985,2)
     t1,t2,t3 = round(entry*1.02,2), round(entry*1.04,2), round(entry*1.06,2)
     d = get_opt(s,p,"daily")
     w = get_opt(s,p,"weekly")
     if not d and not w: continue
     msg = f"🟢 *اختراق لحظي - {s}*\n💰 {p:.2f}$\n📊 مقاومة: {res:.2f}$\n\n🎯 دخول: {entry:.2f}$\n🛑 وقف: {stop:.2f}$\n✅ {t1}$ | {t2}$ | {t3}$\n"
     if d: msg += f"\n🔥 *يومي* ({d['exp']}) سترايك {d['strike']}$ سعر {d['last']}$\n"
     if w: msg += f"\n🛡️ *اسبوعي* ({w['exp']}) سترايك {w['strike']}$ سعر {w['last']}$\n"
     send(msg)
     sent.add(key)
    time.sleep(1)
   except Exception as e:
    print(f"Loop {s}: {e}")
  time.sleep(15)
send("✅ البوت اشتغل - تم اصلاح الخطأ")
Thread(target=loop, daemon=True).start()
while True: time.sleep(3600)
