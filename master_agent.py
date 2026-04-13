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

# 1.5-FLASH: Günlük 1500 sorğu limiti ilə 429 xətasının qarşısını alır
client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

PORTFOLIO = "ETH, NVIDIA (NVDA), AMD, URA (Nüvə), ICLN (Yenilənəbilən), SOXX, SMH"

app = Flask(__name__)

@app.route('/')
def home():
    return "M.Genat 1.2 Mühərriki Aktivdir!"

# ================= GOOGLE SHEETS BAZASI =================
sheet = None
if GOOGLE_JSON:
    try:
        creds_dict = json.loads(GOOGLE_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open("MGenat_Memory").sheet1
    except Exception as e:
        print(f"Baza xətası: {e}")

# ================= SCOUT & REPORT FUNKSİYALARI =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except: pass

def generate_report(report_type="GÜNLÜK"):
    now = datetime.now().strftime("%d %B %Y")
    # Sənin istədiyin xüsusi sektorların prompota əlavəsi
    prompt = f"""
    Sən M.Genat 1.2-sən. Phill üçün {report_type} strateji hesabat hazırla.
    Sektorlar: 
    1. Chip (NVDA, AMD, SOXX)
    2. Cloud (Storage & AI)
    3. AI Robotics (Boston Dynamics context)
    4. Energy (URA, ICLN, Nuclear/Renewable)
    5. Space & Quantum Computing.
    
    Hər sektor üçün günlük qiymət hərəkəti, təzyiq və perspektiv (🟢/🔴 ikonlarla) qeyd et. 
    Sonda makro analiz və Phill üçün konkret 'Action Plan' ver. Dil: Azərbaycan.
    """
    try:
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        send_tg(f"🏛️ **{report_type} STRATEJİ HESABAT** ({now})\n\n{response.text}")
    except Exception as e:
        print(f"Report xətası: {e}")

def scout_loop():
    seen_news = set()
    # Limitə qənaət üçün açar sözlər
    keywords = ["eth", "ethereum", "fed", "nvidia", "amd", "rate", "inflation", "whale", "binance", "nasdaq", "ura"]
    while True:
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
                        prompt = f"Xəbər: '{entry.title}'. Phill-in portfeli: {PORTFOLIO}. Əgər bu xəbər qiymətə ciddi təsir edərsə, səbəb-nəticə əlaqəsi ilə '🚨 TƏCİLİ:' yazaraq analiz et."
                        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
                        if "🚨 TƏCİLİ" in response.text:
                            send_tg(f"{response.text}\n\n🔗 {entry.link}")
            except: continue
        time.sleep(600) # 10 dəqiqədən bir yoxla

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

    # 1. Notebook: Task əlavə et
    if msg.startswith("insert task"):
        task = message.text[11:].strip()
        if sheet:
            now = datetime.now().strftime("%d-%m-%Y")
            sheet.append_row([now, task, "Gözləyir ⏳"])
            bot.reply_to(message, f"✅ Yaddaşa yazıldı: {task}")
        return

    # 2. Notebook: Task bitir (Done)
    elif msg.startswith("done"):
        task_name = message.text[4:].strip().lower()
        if sheet:
            rows = sheet.get_all_values()
            for i, row in enumerate(rows):
                if task_name in row[1].lower():
                    sheet.update_cell(i+1, 3, "Həll edildi ✅")
                    bot.reply_to(message, f"🎯 Təbriklər, Phill! '{row[1]}' tamamlandı.")
                    return
            bot.reply_to(message, "Belə bir tapşırıq tapılmadı.")
        return

    # 3. Notebook: Siyahıları göstər
    elif msg in ["daily", "weekly", "monthly"]:
        if sheet:
            rows = sheet.get_all_values()
            if len(rows) <= 1:
                bot.reply_to(message, "Siyahı boşdur.")
                return
            reply = f"📅 **M.Genat 1.2 - {msg.capitalize()} Tapşırıqlar:**\n\n"
            for row in rows[1:]:
                reply += f"🔹 {row[1]} [{row[2]}] ({row[0]})\n"
            bot.reply_to(message, reply)
        return

    # 4. HQ Agent: Dərin Araşdırma
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            prompt = f"Sən M.Genat 1.2-sən. HQ Agent kimi dərin araşdırma apar. Sual: {message.text}"
            response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)}")

# ================= MASTER START =================
if __name__ == '__main__':
    bot.remove_webhook()
    time.sleep(2)
    
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    
    print("M.Genat 1.2 aktivdir!")
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
