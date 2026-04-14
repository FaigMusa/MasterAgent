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

# 2026-cı ilin ən stabil modeli: gemini-2.0-flash
client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR = []

app = Flask(__name__)

@app.route('/')
def home():
    return "M.Genat 1.2.2 - Stabil"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ================= FUNKSİYALAR =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except: pass

def generate_report(report_type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y")
    portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = f"M.Genat 1.2. {report_type} hesabat hazırla. Aktivlər: {portfel_str}. Dil: Azərbaycan."
    try:
        # MODEL YENİLƏNDİ: 2.0-flash
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        send_tg(f"🏛️ **{report_type} HESABAT**\n\n{response.text}")
    except Exception as e:
        print(f"Report xətası: {e}")

def scout_loop():
    seen_news = set()
    while True:
        keywords = [word.lower() for word in DINAMIK_PORTFEL] + ["fed", "rate", "inflation"]
        for url in ["https://finance.yahoo.com/news/rssindex", "http://feeds.marketwatch.com/marketwatch/topstories/"]:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title_lower = entry.title.lower()
                    if entry.title not in seen_news and any(k in title_lower for k in keywords):
                        seen_news.add(entry.title)
                        prompt = f"Xəbər: '{entry.title}'. Təsir analizi et (🚨 KRİTİK: yaz)."
                        # MODEL YENİLƏNDİ
                        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                        if "🚨 KRİTİK" in response.text.upper():
                            send_tg(f"{response.text}\n\n🔗 {entry.link}")
                        time.sleep(5)
            except: continue
        time.sleep(600)

def reminder_loop():
    while True:
        now_time = datetime.now().strftime("%H:%M")
        for x in XATIRLATMALAR[:]:
            if x["zaman"] == now_time:
                send_tg(f"⏰ **XATIRLATMA:** {x['mesaj']}")
                XATIRLATMALAR.remove(x)
        time.sleep(60)

# ================= MESAJ İDARƏETMƏSİ =================
@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    msg = message.text.lower()

    if msg.startswith("skan əlavə et:"):
        yeni = message.text.split(":")[1].strip().upper()
        if yeni not in DINAMIK_PORTFEL: DINAMIK_PORTFEL.append(yeni)
        bot.reply_to(message, f"✅ Əlavə edildi: {yeni}")
    elif msg.startswith("xatırlat"):
        try:
            h = message.text.split(" ", 2)
            XATIRLATMALAR.append({"zaman": h[1], "mesaj": h[2]})
            bot.reply_to(message, f"✅ Saat {h[1]} üçün qeyd edildi.")
        except: bot.reply_to(message, "Format: `xatırlat 20:00 Mesaj`")
    else:
        try:
            # MODEL YENİLƏNDİ
            response = client.models.generate_content(model='gemini-2.0-flash', contents=f"Asistent M.Genat: {message.text}")
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)}")

# ================= MASTER START =================
if __name__ == '__main__':
    # 1. KONFLİKTİ ÖLDÜRMƏK ÜÇÜN AGRESSİV TƏMİZLİK
    print("Köhnə bağlantılar təmizlənir...")
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    # 2. Telegram-ın özünə gəlməsi üçün 10 saniyəlik "Susqunluq" fasiləsi
    # Bu, 409 xətasının qarşısını alan əsas hissədir.
    time.sleep(10)
    
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    
    print("M.Genat 1.2.2 işə düşür...")
    # Polling-i daha stabil parametrlərlə başladırıq
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
