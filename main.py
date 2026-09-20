from flask import Flask
import os, requests, threading, time
from datetime import datetime
import yfinance as yf

app = Flask(__name__)
@app.route('/')
def home(): return "SPX V10 DOUBLE MONSTER - 20 HALAL - LIVE"

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WALLETS = [w.strip() for w in os.getenv("MONITORED_WALLETS","").split(",") if w.strip()]
MORALIS = os.getenv("MORALIS_API","").strip()
ETHERSCAN_API = os.getenv("ETHERSCAN_API","").strip()

TICKERS = ["^GSPC","SPY","QQQ","AAPL","NVDA","MSFT","GOOGL","AMZN","TSLA","META","AMD","AVGO","MSTR","COIN","MU","SMCI","ARM","QCOM","RKLB","SNDK"]
NAMES = {"^GSPC":"SPX"}
sent = set()
seen_tx = set()
monster_memory = {"GOLDEN": {}, "GAMMA": {}, "HERO": {}, "SWEEPS": {}, "POWER": {}}
double_sent = {} # 🔒 جديد لمنع تكرار نفس الدخول

def send(t):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id":CHAT_ID, "text":t, "parse_mode":"HTML"}, timeout=15)
    except: pass

def check_double_monster(ticker, typ, vol_k, strike=0, exp="", price=0, opt_type="C", premium=0):
    # خزن
    monster_memory[typ][ticker] = {"time": time.time(), "vol": vol_k, "strike": strike, "exp": exp, "price": price, "type": opt_type, "premium": premium}
    for k in list(monster_memory.keys()):
        for tk in list(monster_memory[k].keys()):
            if time.time() - monster_memory[k][tk]["time"] > 3600:
                del monster_memory[k][tk]

    combos = [
        (["GOLDEN","POWER"], "👑⚡ GOLDEN+POWER تدبيلة بور هور"),
        (["GOLDEN","GAMMA"], "🔥🔥 GOLDEN+GAMMA انفجار جاما"),
        (["HERO","SWEEPS"], "🚀🌊 HERO+SWEEPS صندوق يخفي دخول"),
        (["HERO","GAMMA"], "💣💥 HERO+GAMMA انفجار لحظي"),
        (["GOLDEN","HERO"], "💎🚀 GOLDEN+HERO أقوى دخول"),
    ]
    for combo, desc in combos:
        if typ not in combo: # 🔒 فقط اذا النوع الجديد جزء من الكومبو
            continue
        if all(ticker in monster_memory[c] for c in combo):
            times = [monster_memory[c][ticker]["time"] for c in combo]
            if max(times)-min(times) < 3600:
                ref = monster_memory["GOLDEN"][ticker] if "GOLDEN" in combo else monster_memory[combo[0]][ticker]
                last = monster_memory[combo[-1]][ticker]

                # 🔒 مفتاح يمنع التكرار: نفس السهم + كومبو + سترايك + تاريخ + مبلغ مقرب
                # اذا نفس المبلغ بالضبط ما يرسل مرة ثانية
                prem_bucket = int(ref['premium'] / 50000) * 50000 # قرب المبلغ لـ 50 الف
                base_key = f"DOUBLE_{ticker}_{'_'.join(combo)}_{int(ref['strike'])}{ref['type']}_{ref['exp']}_{prem_bucket}"

                if base_key in double_sent:
                    continue # نفس الدخول نفس المبلغ ارسلناه قبل - سكيب

                total = sum(monster_memory[c][ticker]["vol"] for c in combo)
                double_sent[base_key] = time.time()

                send(f"🚨🚨🚨 <b>الوحش المزدوج V10</b> 🚨🚨🚨\n\n"
                     f"🎯 <b>{ticker} - {' + '.join(combo)}</b>\n"
                     f"📝 {desc}\n\n"
                     f"💥 <b>سترايك: {ref['strike']:.0f}{ref['type']}</b>\n"
                     f"📅 تاريخ الانتهاء: {ref['exp']}\n"
                     f"💵 سعر العقد: ${ref['price']:.2f}\n"
                     f"💰 مبلغ الدخول: ${ref['premium']:,.0f}\n"
                     f"💰 سيولة مجمعة: ${total:,.0f}k\n"
                     f"⏰ خلال ساعة\n"
                     f"🔥 ادخل NOW")

                # احذف النوع الثاني عشان ما يكرر مباشرة
                if combo[0]!= typ and ticker in monster_memory[combo[0]]:
                    del monster_memory[combo[0]][ticker]

def score(vol, oi, price, prem):
    s=0; r=vol/max(oi,1)
    if r>3: s+=40
    elif r>1.5: s+=30
    if prem>1500000: s+=30
    elif prem>500000: s+=20
    if 0.90 <= price <= 2.5: s+=20
    if vol>5000: s+=10
    return min(s,99)

def scan_option(tk_symbol, exp, opt_type="calls"):
    try:
        tk = yf.Ticker("^SPX" if tk_symbol=="^GSPC" else tk_symbol)
        chain = getattr(tk.option_chain(exp), opt_type)
        return chain
    except:
        return None

def sniper_loop():
    while True:
        try:
            today = datetime.now().date()
            for sym in TICKERS:
                try:
                    ysym = "^SPX" if sym=="^GSPC" else sym
                    tk = yf.Ticker(ysym)
                    if not tk.options: continue
                    dname = NAMES.get(sym,sym)

                    try:
                        exp0 = tk.options[0]
                        for otype in ["calls","puts"]:
                            ch = scan_option(sym, exp0, otype)
                            if ch is None or ch.empty: continue
                            g = ch.sort_values(by='openInterest',ascending=False).iloc[0]
                            if int(g['openInterest'])>3000 and float(g['lastPrice'])>=0.3:
                                otype_s = "C" if otype=="calls" else "P"
                                prem = float(g['openInterest']*g['lastPrice']*100)
                                key = f"GAMMA{sym}{g['strike']}{exp0}{otype_s}"
                                if key not in sent:
                                    sent.add(key)
                                    send(f"💥 <b>GAMMA WALL لحظي</b>\n\n<b>{dname} {g['strike']:.0f}{otype_s}</b>\n📅 {exp0}\n💵 ${g['lastPrice']:.2f}\n💰 دخول: ${prem:,.0f}\n📊 OI:{int(g['openInterest']):,}\n🎯 هدف ${g['lastPrice']*1.3:.2f}")
                                    check_double_monster(dname, "GAMMA", prem/1000, float(g['strike']), exp0, float(g['lastPrice']), otype_s, prem)
                    except: pass

                    for exp in tk.options[:3]:
                        try:
                            ed = datetime.strptime(exp,"%Y-%m-%d").date()
                            for otype in ["calls","puts"]:
                                chain = scan_option(sym, exp, otype)
                                if chain is None or chain.empty: continue
                                chain = chain[(chain['openInterest']>100) & (chain['lastPrice']>=0.30)]
                                if chain.empty: continue
                                otype_s = "C" if otype=="calls" else "P"
                                for _,r in chain.iterrows():
                                    vol = int(r['volume']); oi = int(r['openInterest']); price = float(r['lastPrice'])
                                    if vol < 800: continue
                                    prem = float(r['openInterest']*price*100) if oi>0 else float(vol*price*100)
                                    prem_vol = float(vol*price*100)
                                    sc = score(vol, oi, price, prem)

                                    if prem>1000000 and vol>3000:
                                        key = f"GOLDEN{sym}{r['strike']}{exp}{otype_s}{int(vol/100)*100}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"👑 <b>GOLDEN لحظي SCORE {sc}</b>\n\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 تاريخ: {exp}\n💵 عقد: ${price:.2f}\n💰 مبلغ الدخول: ${prem:,.0f} (Vol ${prem_vol:,.0f})\n📊 Vol:{vol:,} OI:{oi:,}\n🎯 دخول ${price:.2f} هدف ${price*1.8:.2f}")
                                            check_double_monster(dname, "GOLDEN", prem/1000, float(r['strike']), exp, price, otype_s, prem)
                                    elif vol/ max(oi,1) >1.2 and vol>800:
                                        key = f"SWEEPS{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🐋 <b>SWEEPS لحظي</b>\n\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}\n📊 Vol:{vol:,} OI:{oi:} x{vol/max(oi,1):.1f}\n🎯 ${price*1.3:.2f}")
                                            check_double_monster(dname, "SWEEPS", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if ed==today and 0.90 <= price <= 2.5 and vol>1000:
                                        key = f"HERO{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"🚀 <b>HERO ZERO لحظي</b>\n\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp} 0DTE\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}\n📊 Vol:{vol:,}\n🎯 +35% ${price*1.35:.2f} +80% ${price*1.8:.2f}")
                                            check_double_monster(dname, "HERO", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                                    if (ed-today).days<=4 and 0.3 <= price <= 2.0 and vol>1000:
                                        key = f"POWER{sym}{r['strike']}{exp}{otype_s}{vol}"
                                        if key not in sent:
                                            sent.add(key)
                                            send(f"⏰ <b>POWER HOUR لحظي</b>\n\n<b>{dname} {r['strike']:.0f}{otype_s}</b>\n📅 {exp}\n💵 ${price:.2f}\n💰 دخول: ${prem_vol:,.0f}\n📊 Vol:{vol:,}\n🎯 ${price*1.4:.2f}")
                                            check_double_monster(dname, "POWER", prem_vol/1000, float(r['strike']), exp, price, otype_s, prem_vol)
                        except: continue
                except: continue
            time.sleep(45)
        except: time.sleep(20)

def check_wallets():
    alerts=[]
    if not WALLETS: return alerts
    if ETHERSCAN_API:
        SPX = "0xE0
