from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf
app=Flask(__name__)
@app.route('/')
def home(): return "ALL-IN-ONE LIVE"
TOKEN=os.getenv("BOT_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")
TICKERS=["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","AVGO","PLTR","MSTR","COIN","INTK","SNDK","SMCI"]
NAMES={"^GSPC":"SPX"}

def send(t):
 try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"},timeout=15)
 except: pass

def get_all():
 hero=[]; sweeps=[]; golden=[]; gamma=[]; power=[]
 today=datetime.now().date()
 for sym in TICKERS:
  try:
   ysym="^SPX" if sym=="^GSPC" else sym
   tk=yf.Ticker(ysym)
   if not tk.options: continue
   # Gamma - اكبر OI في كل الشركة
   try:
    exp0=tk.options[0]
    ch0=tk.option_chain(exp0).calls
    if not ch0.empty:
     g=ch0.sort_values(by='openInterest',ascending=False).iloc[0]
     gamma.append((g['openInterest'],f"💥 <b>{NAMES.get(sym,sym)} {g['strike']:.0f}C</b> {exp0} OI:{int(g['openInterest']):,} ${g['lastPrice']:.2f} WALL\n"))
   except: pass

   for exp in tk.options[:4]:
    try:
     exp_date=datetime.strptime(exp,"%Y-%m-%d").date()
     is_today= exp_date == today
     chain=tk.option_chain(exp).calls
     chain=chain[(chain['openInterest']>150) & (chain['lastPrice']>=0.15)]
     if chain.empty: continue
     chain=chain.copy()
     chain['premium']=chain['openInterest']*chain['lastPrice']*100
     chain['sweep_ratio']=chain['volume']/chain['openInterest'].replace(0,1)

     # HERO
     if is_today:
      h=chain[chain['lastPrice']<=1.5].sort_values(by='volume',ascending=False).head(1)
      for _,r in h.iterrows():
       hero.append((r['volume'],f"🚀 <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> 0DTE Vol:{int(r['volume']):,}\n"))

     # SWEEPS & GOLDEN
     sw=chain[(chain['volume']>800) & (chain['sweep_ratio']>1.2)].sort_values(by='volume',ascending=False).head(1)
     for _,r in sw.iterrows():
      d=NAMES.get(sym,sym)
      if r['premium']>1000000 and r['volume']>3000:
       golden.append((r['premium'],f"👑 <b>{d} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} Vol:{int(r['volume']):,} ${r['premium']:,.0f} GOLDEN\n"))
      else:
       sweeps.append((r['volume'],f"🐋 <b>{d} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} x{float(r['sweep_ratio']):.1f}\n"))

     # POWER HOUR - عقود الاسبوع رخيصة
     if (exp_date - today).days <= 4:
      ph=chain[(chain['lastPrice']>=0.30) & (chain['lastPrice']<=2.0)].sort_values(by='volume',ascending=False).head(1)
      for _,r in ph.iterrows():
       power.append((r['volume'],f"⏰ <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} Vol:{int(r['volume']):,}\n"))
    except: continue
  except: continue
 hero.sort(key=lambda x:x[0],reverse=True)
 sweeps.sort(key=lambda x:x[0],reverse=True)
 golden.sort(key=lambda x:x[0],reverse=True)
 gamma.sort(key=lambda x:x[0],reverse=True)
 power.sort(key=lambda x:x[0],reverse=True)
 return hero[:6], golden[:6], sweeps[:6], gamma[:6], power[:6]

def loop():
 time.sleep(3); send("✅ بوت 5x جاهز\n🚀 HERO\n👑 GOLDEN\n🐋 SWEEPS\n💥 GAMMA\n⏰ POWER HOUR\nارسل /strikes")
 off=0
 while True:
  try:
   r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
   for u in r.get("result",[]):
    off=u["update_id"]; txt=u.get("message",{}).get("text","")
    if "/strikes" in txt.lower() or "/start" in txt.lower():
     send("⏳ اجيب الـ 5...")
     h,g,s,ga,p=get_all()
     if h: send("🚀 <b>HERO ZERO:</b>\n\n"+"".join([x[1] for x in h]))
     if g: send("👑 <b>GOLDEN SWEEPS (حوت مليوني):</b>\n\n"+"".join([x[1] for x in g]))
     if s: send("🐋 <b>SWEEPS:</b>\n\n"+"".join([x[1] for x in s]))
     if ga: send("💥 <b>GAMMA WALL (اقوى جدار):</b>\n\n"+"".join([x[1] for x in ga[:5]]))
     if p: send("⏰ <b>POWER HOUR (جاهز لاخر ساعة):</b>\n\n"+"".join([x[1] for x in p[:5]]))
     if not any([h,g,s,ga,p]): send("⚠️ السوق مقفل - الاثنين 4:45 العصر")
  except: time.sleep(3)

threading.Thread(target=loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
