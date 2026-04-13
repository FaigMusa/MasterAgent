import requests
import feedparser
import time
import threading
from datetime import datetime
import google.generativeai as genai

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = '8702831719:AAF1JfrZRaXT1c1M2147W-NEu-q1IyRkzMc'
CHAT_ID = '7018381058'
GEMINI_API_KEY = 'AIzaSyAKopAYo4sW7VnUj6or3-XxGwiACLFmryk'

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

PORTFOLIO = "ETH, NVIDIA (NVDA), AMD, URA (Nüvə Enerjisi ETF), ICLN (Yenilənəbilən Enerji ETF)"

# Çoxlu xəbər mənbələri
NEWS_SOURCES = [
    "https://finance.yahoo.com/news/rssindex",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "http://feeds.marketwatch.com/marketwatch/topstories/"
]

# ================= TELEGRAM MODULU =================
def send_telegram_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i+max_length]
        response = requests.post(url, json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "Markdown"})
        if response.status_code != 200:
            requests.post(url, json={"chat_id": CHAT_ID, "text": chunk})

# ================= 1. MULTI-SOURCE SCOUT =================
def scout_loop():
    print(f"\n[📡 Kəşfiyyatçı] {len(NEWS_SOURCES)} fərqli mənbədən canlı izləmə başladı...\n")
    seen_news = set()
    while True:
        for url in NEWS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]: # Hər mənbədən son 3 xəbər
                    if entry.title not in seen_news:
                        seen_news.add(entry.title)
                        
                        prompt = f"Xəbər: '{entry.title}'. Portfel: {PORTFOLIO}. Əgər bu xəbər portfeli sarsıdacaq gücdədirsə '🚨 TƏCİLİ:' ilə başlayan analiz yaz, yoxsa 'GÖZARDI' yaz."
                        ai_decision = model.generate_content(prompt).text.strip()
                        
                        if "🚨 TƏCİLİ" in ai_decision:
                            msg = f"{ai_decision}\n\n🔗 Mənbə: {entry.title}"
                            send_telegram_alert(msg)
                            print(f"\n[🚨 SİQNAL] {entry.title} - Göndərildi!")
            except: continue
        time.sleep(300)

# ================= 2. HQ CONSILIUM =================
def generate_hq_consilium(user_query):
    canli_tarix = datetime.now().strftime("%d %B %Y, %H:%M")
    prompt = f"""
    Sən Goldman Sachs, J.P. Morgan və BlackRock-un baş analitiklərindən ibarət 'Strateji Komitə'sən.
    TARİX: {canli_tarix}. Portfel: {PORTFOLIO}.
    Phill-in sualı: "{user_query}"
    
    Hesabatı bu struktura uyğun hazırla:
    1. **Goldman Sachs Baxışı:** (Aqressiv maliyyə analizi və hədəf qiymətlər)
    2. **J.P. Morgan Baxışı:** (Makro risklər və ehtiyatlı yanaşma)
    3. **BlackRock Baxışı:** (Uzunmüddətli ETF axınları və institusional mövqe)
    4. **Yekun Konsensus:** (Üç bankın ortaq tövsiyəsi və Phill üçün konkret addım)
    
    Dili peşəkar və Azərbaycan dilində olsun.
    """
    try:
        return model.generate_content(prompt).text
    except:
        return "API Yüklənməsi: Zəhmət olmasa 1 dəqiqə sonra yenidən soruşun."

# ================= MASTER CONTROLLER =================
def main():
    print("="*70)
    print("🏛️ PHILL'İN MULTİ-BANK STRATEGİYA TERMİNALI (v2.0) İŞƏ DÜŞDÜ 🏛️")
    print("="*70)
    
    threading.Thread(target=scout_loop, daemon=True).start()
    
    while True:
        query = input("\n[🏛️ HQ Konsilium] Sualınızı daxil edin (məs. 'Amerika-İran gərginliyi'):\n>> ")
        if query.lower() == 'q': break
        if not query.strip(): continue
        
        print("\n⏳ Banklar arası konsilium toplanır... Hesabat hazırlanır...\n")
        result = generate_hq_consilium(query)
        print(f"\n{'='*70}\n{result}\n{'='*70}")
        send_telegram_alert(f"🏛️ **HQ KONSİLİUM HESABATI** 🏛️\n\n{result}")

if __name__ == '__main__':
    main()