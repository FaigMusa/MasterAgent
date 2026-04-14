import json
import gspread
from google.oauth2.service_account import Credentials
import time
import threading
import schedule
from datetime import datetime
from google import genai
from flask import Flask
import os
import feedparser
import telebot

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_JSON = os.getenv('GOOGLE_JSON')

client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ================= DİNAMİK YADDAŞ (YENİ DNT) =================
# Sabit portfeli ləğv etdik, artıq bu canlı bir siyahıdır
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR = [] # Saat və mesajları tutacaq yaddaş

# ================= RENDER ÜÇÜN XİLASKAR VEB SERVER =================
app = Flask(__name__)

@app.route('/')
def home():
    return "M.Genat 1.2 Mühərriki Açıqdır və Port Təsdiqləndi!"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    # Flask serveri qurduq ki, Render "Port" xətası verməsin
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ================= SCOUT & REPORT FUNKSİYALARI =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except: pass

def generate_report(report_type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y")
    portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = f"""
    Sən M.Genat 1.2-sən. Phill üçün {report_type} strateji hesabat hazırla.
    Radardakı Aktivlər: {portfel_str}
    Sektorlar: 1. Chip (SMH) 2. Energy/Uranium (NLR, URA) 3. Robotics (BOTZ, TSLA) 4. Crypto.
    Hər sektor üçün bazara ən xırda təsir edən amilləri araşdır, qiymət hərəkəti və strategiya ver.
    """
    try:
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        send_tg(f"🏛️ **{report_type} STRATEJİ HESABAT** ({now})\n\n{response.text}")
    except Exception as e:
        print(f"Report xətası: {e}")

def scout_loop():
    seen_news = set()
    while True:
        # Kəşfiyyat artıq sənin canlı portfelinə əsaslanır
        keywords = [word.lower() for word in DINAMIK_PORTFEL] + ["fed", "rate", "inflation"]
        for url in [
            "https://finance.yahoo.com/news/rssindex",
            "http://feeds.marketwatch.com/marketwatch/topstories/"
        ]:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title_lower = entry.title.lower()
                    if entry.title not in seen_news and any(k in title_lower for k in keywords):
                        seen_news.add(entry.title)
                        portfel_str = ", ".join(DINAMIK_PORTFEL)
                        prompt = f"Xəbər: '{entry.title}'. Radardakı portfel: {portfel_str}. Bu xəbərin radardakı aktivlərə gizli və ya birbaşa təsiri varsa, '🚨 KRİTİK:' yazaraq qısa izah et."
                        
                        try:
                            response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
                            if "🚨 KRİTİK" in response.text.upper():
                                send_tg(f"{response.text}\n\n🔗 {entry.link}")
                            time.sleep(5) # Rate limit qorunması
                        except:
                            time.sleep(10)
            except: continue
        time.sleep(600)

def reminder_loop():
    """Hər 60 saniyədən bir saatı yoxlayan Ağıllı Xatırlatma mühərriki"""
    while True:
        now_time = datetime.now().strftime("%H:%M")
        for xatirlatma in XATIRLATMALAR[:]: # Siyahının kopyası üzərində gəzirik
            if xatirlatma["zaman"] == now_time:
                send_tg(f"⏰ **PHİLL ÜÇÜN XATIRLATMA:**\n\n🎯 {xatirlatma['mesaj']}")
                XATIRLATMALAR.remove(xatirlatma) # Mesajı göndərdikdən sonra silir
        time.sleep(60)

def run_scheduler():
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR AÇILIŞI")
    schedule.every().day.at("17:00").do(generate_report, report_type="AXŞAM YEKUNU")
    while True:
        schedule.run_pending()
        time.sleep(60)

# ================= İNTERAKTİV DİNLƏMƏ =================
@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    
    msg = message.text.lower()

    # --- 1. DİNAMİK PORTFEL RADARI ---
    if msg.startswith("skan əlavə et:"):
        yeni_aktiv = message.text.split(":")[1].strip().upper()
        if yeni_aktiv and yeni_aktiv not in DINAMIK_PORTFEL:
            DINAMIK_PORTFEL.append(yeni_aktiv)
            bot.reply_to(message, f"🎯 Radara uğurla əlavə edildi: **{yeni_aktiv}**\nMövcud Radar: {', '.join(DINAMIK_PORTFEL)}")
        return
        
    elif msg == "skan siyahısı":
        bot.reply_to(message, f"📡 **Hazırkı Kəşfiyyat Radarı:**\n{', '.join(DINAMIK_PORTFEL)}")
        return

    # --- 2. AĞILLI XATIRLATMA ---
    elif msg.startswith("xatırlat"):
        try:
            hisseler = message.text.split(" ", 2)
            zaman = hisseler[1] # "20:00"
            tapsiriq = hisseler[2] # "Futbolum var"
            XATIRLATMALAR.append({"zaman": zaman, "mesaj": tapsiriq})
            bot.reply_to(message, f"✅ Qəbul edildi, Phill. Saat {zaman} olanda sənə xəbər edəcəm.")
        except:
            bot.reply_to(message, "⚠️ Səhv format. Zəhmət olmasa belə yaz: `xatırlat 20:00 Futbolum var`")
        return

    # --- 3. DİGƏR KOMANDALAR VƏ GEMINI ---
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            prompt = f"Sən M.Genat 1.2-sən. Phill sənə yazır: {message.text}"
            response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)}")

# ================= MASTER START =================
if __name__ == '__main__':
    # 1. Təmizlik
    bot.remove_webhook(drop_pending_updates=True)
    time.sleep(2)
    
    # 2. RENDER ÜÇÜN VEB SERVERİ İŞƏ SALIRIQ (Bu 404/Port xətasını bloklayır)
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # 3. Kəşfiyyat və Xatırlatma mühərriklərini işə salırıq
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start() # Yeni Xatırlatma mühərriki
    
    print("M.Genat 1.2.1 aktivdir!")
    
    # 4. Telegram botun dinləmə rejimi (Sonda gəlməlidir)
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
