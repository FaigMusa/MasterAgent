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
import telebot # YENİ ƏLAVƏ: Dinləmə funksiyası üçün

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_JSON = os.getenv('GOOGLE_JSON') # YENİ ƏLAVƏ: Baza açarı

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# Botu inisializasiya edirik
bot = telebot.TeleBot(TELEGRAM_TOKEN)

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

# ================= AVTOMATİK FUNKSİYALAR =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Telegram göndərmə xətası: {e}")

def generate_report(type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    prompt = f"""
    Sən M.Genat 1.2-sən. Goldman Sachs, J.P. Morgan və Ray Dalio (Bridgewater) analitiklərindən ibarət komitəsən.
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

# ================= İNTERAKTİV DİNLƏMƏ (HQ AGENT) =================
@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    
    user_text = message.text
    text_lower = user_text.lower()

    # 1. PERSONAL NOTEBOOK: Tapşırıq əlavə etmək
    if text_lower.startswith("insert task"):
        task = user_text[11:].strip()
        if task and sheet:
            now = datetime.now().strftime("%d-%m-%Y %H:%M")
            try:
                sheet.append_row([now, task, "Gözləyir ⏳"])
                bot.reply_to(message, f"✅ Yaddaşa yazıldı: {task}")
            except Exception as e:
                bot.reply_to(message, f"Baza xətası: {e}")
        elif not sheet:
            bot.reply_to(message, "⚠️ Google bazasına qoşulma yoxdur.")
        return

    # 2. PERSONAL NOTEBOOK: Günlük hesabatı çağırmaq
    elif text_lower == "daily":
        if sheet:
            try:
                rows = sheet.get_all_values()
                if len(rows) <= 1:
                    bot.reply_to(message, "📭 Yaddaşda aktiv tapşırıq yoxdur.")
                    return
                
                reply = "📅 **M.Genat 1.2 - Günlük Tapşırıqlar:**\n\n"
                for idx, row in enumerate(rows[1:], start=2): # Başlığı ötürürük
                    status = row[2] if len(row) > 2 else "Gözləyir ⏳"
                    reply += f"🔹 {row[1]} [{status}]\n"
                bot.reply_to(message, reply)
            except Exception as e:
                bot.reply_to(message, f"Baza oxunarkən xəta oldu: {e}")
        else:
            bot.reply_to(message, "⚠️ Google bazasına qoşulma yoxdur.")
        return

    # 3. HQ AGENT: Dərin Araşdırma (Sərbəst söhbət)
    else:
        prompt = f"""
        Sən M.Genat 1.2-sən. Süni İntellekt İnvestisiya Fondu və Phill-in Şəxsi Asistanısan.
        İstifadəçi sualı: {user_text}
        Portfel: {PORTFOLIO}
        Qeyd: Phill-in Almaniya AI Engineering hədəfləri və MİDA layihəsindəki avtomatlaşdırma təcrübəsini nəzərə alaraq peşəkar cavab ver.
        """
        try:
            bot.send_chat_action(message.chat.id, 'typing') # Yazır işarəsi
            response = model.generate_content(prompt).text
            bot.reply_to(message, response, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, "Bağışla, beynimdə qısaqapanma oldu. Yenidən soruş.")

# ================= MASTER START =================
if __name__ == '__main__':
    print("Sistem isidilir, 10 saniyə gözləyin...")
    time.sleep(10)
    
    # Arxa plan modullarını işə salırıq
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # Yeni: Botun səni dinləməsi üçün Polling funksiyası
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    
    try:
        send_tg("🚀 **M.GENAT v1.2 İŞƏ DÜŞDÜ!**\n\nSistem artıq həm kəşfiyyat aparır, həm bazaya yazır, həm də sizi dinləyir.")
    except Exception as e:
        print(f"Telegram-a mesaj gedərkən xəta: {e}")
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
