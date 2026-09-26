import requests, time
from datetime import datetime, date, timedelta, timezone

TICKERS = ["NVDA","TSLA","META","AMD","AMZN","MSFT","PLTR","AVGO","SNDK","APP","MU","QCOM","LITE"]
BOT_TOKEN = "حط_التوكن_هنا"
CHAT_ID = "حط_الايدي_هنا"

CONFIG = {
    "MIN_PREMIUM": 25000,
    "MIN_VOL": 150,
    "MIN_VOL_OI_RATIO": 1.5,
    "MIN_SCORE": 5,
    "MIN_DTE": 2,
    "MAX_DTE": 14,
    "MIN_DELTA": 0.45,
}

KSA = timezone(timedelta(hours=3))
def now_ksa(): return datetime.now(KSA)

def calc_dte(expiry_str):
    try:
        exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        return (exp - now_ksa().date()).days
    except: return 0

def send_tg(text):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def explosion_score(t):
    s=0
    r=t['volume']/max(t['openInterest'],1)
    if r>=5: s+=4
    elif r>=3: s+=3
    elif r>=1.5: s+=1
    if t['premium']>=100000: s+=3
    elif t['premium']>=50000: s+=2
    elif t['premium']>=25000: s+=1
    if t.get('ask_pct',0)>=80: s+=2
    elif t.get('ask_pct',0)>=50: s+=1
    if t['openInterest']<1500 and t['volume']>150: s+=1
    return min(s,10)

def is_valid(t):
    if t['ticker'] not in TICKERS: return False
    if not (CONFIG["MIN_DTE"] <= t.get('dte',0) <= CONFIG["MAX_DTE"]): return False
    if t.get('delta',0) < CONFIG["MIN_DELTA"]: return False
    if t['premium'] < CONFIG["MIN_PREMIUM"]: return False
    if t['volume'] < CONFIG["MIN_VOL"]: return False
    if t['volume']/max(t['openInterest'],1) < CONFIG["MIN_VOL_OI_RATIO"]: return False
    if explosion_score(t) < CONFIG["MIN_SCORE"]: return False
    return True

def build_msg(t):
    score = explosion_score(t)
    if score >= 9: icon, desc = "💎", "حوت كبير جدا - لا تفوته"
    elif score >= 7: icon, desc = "🔥", "انفجار قوي"
    else: icon, desc = "💥", "انفجار متوسط"

    نوع = "شراء" if t['type']=="C" else "بيع"
    return f"""{icon} *{desc} {score}/10*
*الشركة:* `{t['ticker']}` - عقد {نوع}
*السعر المستهدف:* `{t['strike']}` - ينتهي بعد `{t.get('dte',0)} يوم`
*قوة العقد:* دلتا `{t.get('delta',0):.2f}` - ثابت ويرتفع

*💰 حجم السيولة:* `${t['premium']:,.0f}`
*📊 الحجم:* `{t['volume']}` / المفتوح `{t['openInterest']}` = `{t['volume']/max(t['openInterest'],1):.1f} ضعف`
*⚡ الشراء:* فوق سعر الطلب `{t.get('ask_pct',0)}%` - مستعجل
*💵 سعر العقد الان:* `${t['price']:.2f}`

*⏰ {now_ksa().strftime('%I:%M:%S %p')} بتوقيت الرياض 🇸🇦*"""

def handle_trade(raw):
    raw['dte'] = calc_dte(raw.get('expiry',''))
    if not is_valid(raw): return
    msg = build_msg(raw)
    print(msg)
    send_tg(msg)

if __name__ == "__main__":
    send_tg(f"✅ *البوت اشتغل - رسائل عربية*\nالوقت: {now_ksa().strftime('%I:%M %p')} الرياض\nالشركات: {', '.join(TICKERS)}\nبيجيك 15-20 انفجار بالساعة وتختار اللي يعجبك")
    while True: time.sleep(1)
