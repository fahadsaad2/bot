from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app=Flask(__name__)
@app.route('/')
def home(): return "HERO ZERO + WHALE WALLETS V4 LIVE"

TOKEN=os.getenv("BOT_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")
WALLETS=[w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
MORALIS=os.getenv("MORALIS_API","").strip()
TICKERS=["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","NFLX","AMD","AVGO","PLTR","MSTR","COIN","INTK","SNDK","SMCI"]
NAMES={"^GSPC":"SPX"}

seen_tx=set()
seen_wallets={}

def send(t):
 try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"},timeout=15)
 except: pass

def get_options():
 hero=[]; sweeps=[]; golden=[]; gamma=[]; power=[]
 today=datetime.now().date()
 now_str=datetime.now().strftime("%m/%d %I:%M%p")
 for sym in TICKERS:
  try:
   ysym="^SPX" if sym=="^GSPC" else sym
   tk=yf.Ticker(ysym)
   if not tk.options: continue
   try:
    ch0=tk.option_chain(tk.options[0]).calls
    if not ch0.empty:
     g=ch0.sort_values(by='openInterest',ascending=False).iloc[0]
     prem=g['openInterest']*g['lastPrice']*100
     gamma.append((g['openInterest'],f"💥 <b>{NAMES.get(sym,sym)} {g['strike']:.0f}C</b> ${g['lastPrice']:.2f} | OI:{int(g['openInterest']):,} | ${prem:,.0f} | {tk.options[0]} | {now_str}\n🎯 هدف1 ${g['lastPrice']*1.3:.2f} هدف2 ${g['lastPrice']*1.6:.2f}\n"))
   except: pass
   for exp in tk.options[:4]:
    try:
     exp_date=datetime.strptime(exp,"%Y-%m-%d").date()
     chain=tk.option_chain(exp).calls
     chain=chain[(chain['openInterest']>150)&(chain['lastPrice']>=0.15)]
     if chain.empty: continue
     chain=chain.copy()
     chain['premium']=chain['openInterest']*chain['lastPrice']*100
     chain['sweep_ratio']=chain['volume']/chain['openInterest'].replace(0,1)
     chain['demand']=chain['volume']*chain['lastPrice']
     if exp_date==today:
      h=chain[chain['lastPrice']<=1.5].sort_values(by='demand',ascending=False).head(1)
      for _,r in h.iterrows():
       hero.append((r['demand'],f"🚀 <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> 0DTE Vol:{int(r['volume']):,} {exp} {now_str}\n🎯 +35% ${r['lastPrice']*1.35:.2f} +80% ${r['lastPrice']*1.8:.2f}\n"))
     sw=chain[(chain['volume']>800)&(chain['sweep_ratio']>1.2)].sort_values(by='premium',ascending=False).head(1)
     for _,r in sw.iterrows():
      msg=f"<b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} x{r['sweep_ratio']:.1f} Vol:{int(r['volume']):,} ${r['premium']:,.0f} {now_str}\n🎯 ${r['lastPrice']*1.3:.2f} / ${r['lastPrice']*1.6:.2f}\n"
      if r['premium']>1000000 and r['volume']>3000: golden.append((r['premium'],f"👑 {msg}"))
      else: sweeps.append((r['premium'],f"🐋 {msg}"))
     if (exp_date-today).days<=4:
      ph=chain[(chain['lastPrice']>=0.3)&(chain['lastPrice']<=2.0)].sort_values(by='demand',ascending=False).head(1)
      for _,r in ph.iterrows(): power.append((r['demand'],f"⏰ <b>{NAMES.get(sym,sym)} {r['strike']:.0f}C ${r['lastPrice']:.2f}</b> {exp} Vol:{int(r['volume']):,} {now_str}\n"))
    except: continue
  except: continue
 for lst in [hero,sweeps,golden,gamma,power]: lst.sort(key=lambda x:x[0],reverse=True)
 return hero[:5],golden[:5],sweeps[:5],gamma[:5],power[:5]

def check_wallets():
 if not MORALIS or not WALLETS: return []
 alerts=[]
 for w in WALLETS:
  try:
   url=f"https://deep-index.moralis.io/api/v2.2/{w}/history?chain=bsc&order=DESC&limit=5"
   r=requests.get(url,headers={"X-API-Key":MORALIS},timeout=15).json()
   for tx in r.get("result",[]):
    h=tx.get("hash")
    if h in seen_tx: continue
    seen_tx.add(h)
    val=int(tx.get("value","0"))/1e18
    if val<0.05: continue
    to_addr=tx.get("to_address","")[:10]
    time_str=datetime.now().strftime("%m/%d %I:%M%p")
    alerts.append(f"💰 <b>محفظة حوت</b> {w[:6]}...{w[-4:]}\n🏢 دخلت: {to_addr}... (BSC)\n💵 قيمة الدخول: {val:.3f} BNB (${val*600:.0f})\n📅 التاريخ: {time_str}\n🔗 Strike/Hash: {h[:12]}...\n")
  except Exception as e: print(f"WALLET ERR {e}")
  time.sleep(1)
 return alerts

def loop():
 time.sleep(3)
 send("✅ <b>Hero V4 جاهز - 19 شركة + 5 محافظ</b>\n🚀 HERO\n👑 GOLDEN\n🐋 SWEEPS\n💥 GAMMA\n⏰ POWER\n💰 محافظ\nارسل /strikes")
 off=0
 last_wallet_check=0
 while True:
  try:
   # فحص محافظ كل دقيقتين تلقائي
   if time.time()-last_wallet_check>120:
    wa=check_wallets()
    for a in wa: send(a)
    last_wallet_check=time.time()

   r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={off+1}&timeout=20",timeout=25).json()
   for u in r.get("result",[]):
    off=u["update_id"]; txt=u.get("message",{}).get("text","").lower()
    if "/strikes" in txt or "/start" in txt:
     send(f"⏳ اجيب السترايكات والمحافظ... {datetime.now().strftime('%H:%M')}")
     h,g,s,ga,p=get_options()
     if h: send("🚀 <b>HERO ZERO:</b>\n\n"+"".join([x[1] for x in h]))
     if g: send("👑 <b>GOLDEN:</b>\n\n"+"".join([x[1] for x in g]))
     if s: send("🐋 <b>SWEEPS:</b>\n\n"+"".join([x[1] for x in s]))
     if ga: send("💥 <b>GAMMA WALL:</b>\n\n"+"".join([x[1] for x in ga]))
     if p: send("⏰ <b>POWER HOUR:</b>\n\n"+"".join([x[1] for x in p]))
     # محافظ
     wa=check_wallets()
     if wa: send("💰 <b>محافظ الحيتان (اخر دخول):</b>\n\n"+"\n".join(wa[:5]))
     else: send("💰 <b>المحافظ:</b> ما فيه دخول جديد اخر 5 دقايق - 5 محافظ تحت المراقبة")
    elif "/wallets" in txt:
     wa=check_wallets()
     if wa: send("💰 <b>تقرير المحافظ:</b>\n\n"+"\n".join(wa))
     else: send(f"💰 يراقب {len(WALLETS)} محافظ، لا يوجد حركة جديدة")
  except Exception as e:
   print(f"LOOP ERR {e}"); time.sleep(3)

threading.Thread(target=loop,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
