import requests
import feedparser
import time
import threading
import schedule
from datetime import datetime
import google.generativeai as genai
from flask import Flask # YENİ ƏLAVƏ
import os # YENİ ƏLAVƏ

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

PORTFOLIO = "ETH, NVIDIA (NVDA), AMD, URA (Nüvə), ICLN (Yenilənəbilən)"
NEWS_SOURCES = [
    "https://finance.yahoo.com/news/rssindex",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "http://feeds.marketwatch.com/marketwatch/topstories/"
]

# ================= FLASK SERVER (OYAQ QALMAQ ÜÇÜN) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Mühərrik aktivdir! Master Agent 3.0 bazarı izləyir."

# ================= FUNKSİYALAR =================
def send_tg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload)
        if r.status_code != 200:
            requests.post(url, json={"chat_id": CHAT_ID, "text": text})
    except:
        pass

def generate_report(type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    prompt = f"""
    Sən Goldman Sachs, J.P. Morgan və Ray Dalio (Bridgewater) analitiklərindən ibarət komitəsən.
    HESABAT NÖVÜ: {type} | TARİX: {now} | PORTFEL: {PORTFOLIO}
    1. Goldman Sachs: Aqressiv hədəflər.
    2. J.P. Morgan: Makro risklər.
    3. Ray Dalio: Uzunmüddətli iqtisadi dövrlər və diversifikasiya.
    4. Yekun Strategiya: Phill üçün konkret addım.
    Azərbaycan dilində yaz.
    """
    try:
        report = model.generate_content(prompt).text
        send_tg(f"🏛️ **{type} STRATEJİ HESABAT** 🏛️\n\n{report}")
    except:
        pass

def run_scheduler():
    schedule.every().day.at("08:30").do(generate_report, type="SƏHƏR AÇILIŞI")
    schedule.every().day.at("20:00").do(generate_report, type="AXŞAM YEKUNU")
    while True:
        schedule.run_pending()
        time.sleep(60)

def scout_loop():
    seen_news = set()
    while True:
        for url in NEWS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    if entry.title not in seen_news:
                        seen_news.add(entry.title)
                        prompt = f"Xəbər: '{entry.title}'. Portfel: {PORTFOLIO}. Əgər kritikdirsə '🚨 TƏCİLİ:' yaz, yoxsa 'GÖZARDI' yaz."
                        res = model.generate_content(prompt).text.strip()
                        if "🚨 TƏCİLİ" in res:
                            send_tg(f"{res}\n\n🔗 {entry.title}")
            except: continue
        time.sleep(300)

# ================= MASTER START =================
# ================= MASTER START =================
if __name__ == '__main__':
    # 1. Serverin tam aktivləşməsi üçün 10 saniyə gözləyirik (Bulud üçün vacibdir)
    print("Sistem isidilir, 10 saniyə gözləyin...")
    time.sleep(10)
    
    # 2. Modulları işə salırıq
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # 3. İLK SALAM MESAJI (Zəmanətli çatdırılma üçün)
    try:
        send_tg("🚀 **PHILL MASTER AGENT v3.0 İŞƏ DÜŞDÜ!**\n\nSistem artıq Render buludunda 24/7 canlıdır. İlk kəşfiyyat hesabatlarını gözləyin.")
        print("Telegram-a ilk siqnal göndərildi.")
    except Exception as e:
        print(f"Telegram-a mesaj gedərkən xəta: {e}")
    
    # 4. Flask Veb Serveri (Sistemi oyaq saxlayan hissə)
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
