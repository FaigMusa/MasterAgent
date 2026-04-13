import json
import gspread
from google.oauth2.service_account import Credentials
import time
import threading
import schedule
from datetime import datetime
import google.generativeai as genai
from flask import Flask
import os
import feedparser
import telebot

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_JSON = os.getenv('GOOGLE_JSON')
genai.configure(api_key=GEMINI_API_KEY)
# ================= UNİKAL DİNAMİK BEYİN SEÇİMİ =================
def initialize_brain():
    print("M.Genat aktiv modelləri axtarır...")
    try:
        # Google-dan əlçatan bütün modelləri çəkirik
        all_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        print(f"Sistemdə tapılan modellər: {all_models}")

        # Ən güclü və müasir olanı seçirik (Priority List)
        # Sənin regionunda hansı aktivdirsə, onu ilk sıraya qoyuruq
        priorities = [
            'models/gemini-2.0-flash', 
            'models/gemini-1.5-flash', 
            'models/gemini-1.5-flash-latest', 
            'models/gemini-pro'
        ]
        
        for p in priorities:
            if p in all_models:
                print(f"Seçilən beyin: {p}")
                return genai.GenerativeModel(p)
        
        # Əgər heç biri tapılmasa, siyahıdakı ilk mövcud modeli götür
        if all_models:
            return genai.GenerativeModel(all_models[0])
            
    except Exception as e:
        print(f"Dinamik seçim xətası: {e}. Standart rejimə keçilir.")
        return genai.GenerativeModel('gemini-1.5-flash')

# Modeli işə salırıq
model = initialize_brain()
# ==============================================================
bot = telebot.TeleBot(TELEGRAM_TOKEN)

PORTFOLIO = "ETH, NVIDIA (NVDA), AMD, URA (Nüvə), ICLN (Yenilənəbilən)"
NEWS_SOURCES = [
    "https://finance.yahoo.com/news/rssindex",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "http://feeds.marketwatch.com/marketwatch/topstories/"
]

app = Flask(__name__)

@app.route('/')
def home():
    return "Mühərrik aktivdir! M.Genat 1.2 bazarı izləyir."

# ================= GOOGLE SHEETS BAZASI =================
sheet = None
if GOOGLE_JSON:
    try:
        creds_dict = json.loads(GOOGLE_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open("MGenat_Memory").sheet1
        print("M.Genat Verilənlər Bazasına uğurla qoşuldu!")
    except Exception as e:
        print(f"Baza xətası: {e}")

# ================= FUNKSİYALAR =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Telegram göndərmə xətası: {e}")

def generate_report(report_type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    prompt = f"Sən M.Genat 1.2-sən. {report_type} hesabat hazırla. Portfel: {PORTFOLIO}. Azərbaycan dilində."
    try:
        report = model.generate_content(prompt).text
        send_tg(f"🏛️ **{report_type} STRATEJİ HESABAT** 🏛️\n\n{report}")
    except Exception as e:
        print(f"Report xətası: {e}")

def run_scheduler():
    schedule.every().day.at("08:30").do(generate_report, report_type="SƏHƏR AÇILIŞI")
    schedule.every().day.at("20:00").do(generate_report, report_type="AXŞAM YEKUNU")
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
            except:
                continue
        time.sleep(300)

# ================= İNTERAKTİV DİNLƏMƏ =================
@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID):
        return
    
    user_text = message.text
    text_lower = user_text.lower()

    if text_lower.startswith("insert task"):
        task = user_text[11:].strip()
        if task and sheet:
            now = datetime.now().strftime("%d-%m-%Y %H:%M")
            try:
                sheet.append_row([now, task, "Gözləyir ⏳"])
                bot.reply_to(message, f"✅ Yaddaşa yazıldı: {task}")
            except Exception as e:
                bot.reply_to(message, f"Baza xətası: {e}")
        return

    elif text_lower == "daily":
        if sheet:
            try:
                rows = sheet.get_all_values()
                if len(rows) <= 1:
                    bot.reply_to(message, "📭 Yaddaşda tapşırıq yoxdur.")
                    return
                reply = "📅 **M.Genat 1.2 - Tapşırıqlar:**\n\n"
                for row in rows[1:]:
                    reply += f"🔹 {row[1]} [{row[2]}]\n"
                bot.reply_to(message, reply)
            except Exception as e:
                bot.reply_to(message, f"Baza xətası: {e}")
        return

    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            prompt = f"Sən M.Genat 1.2-sən. Phill-in asistantısan. Sual: {user_text}"
            response = model.generate_content(prompt).text
            bot.reply_to(message, response, parse_mode="Markdown")
        except Exception as e:
            # XƏTANI GİZLƏTMİRİK, BİRBAŞA TELEGRAMA YAZIRIQ
            error_message = f"❌ **KRİTİK XƏTA DETEKTED:**\n`{str(e)}`"
            bot.reply_to(message, error_message, parse_mode="Markdown")
            print(f"Server Log Xətası: {e}")

# ================= MASTER START =================
if __name__ == '__main__':
    time.sleep(10)
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    
    print("M.Genat 1.2 aktivdir!")
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
