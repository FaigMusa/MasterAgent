import time
import threading
import schedule
import requests
import re
from datetime import datetime
from collections import deque
from google import genai
from flask import Flask, request as flask_request, abort
import os
import feedparser
import telebot

# ─────────────────────────── TƏNZİMLƏMƏLƏR ────────────────────────────
TELEGRAM_TOKEN  = os.getenv('TELEGRAM_TOKEN')
CHAT_ID         = os.getenv('CHAT_ID')
GEMINI_API_KEY  = os.getenv('GEMINI_API_KEY')
WEBHOOK_URL     = os.getenv('WEBHOOK_URL', '').rstrip('/')
GEMINI_MODEL    = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash').strip().replace('"', '')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR   = []

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI — SELF-HEALING & RETRY
# ═══════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 3) -> str:
    delay = 10
    current_model = GEMINI_MODEL
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=current_model, contents=prompt)
            return resp.text
        except Exception as e:
            err_str = str(e)
            if '404' in err_str or 'NOT_FOUND' in err_str:
                current_model = 'gemini-1.5-flash'
                continue
            elif '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait = int(float(match.group(1))) + 5 if match else delay
                time.sleep(wait)
            else:
                return f"⏳ Sistem müvəqqəti yüklüdür. Lütfən biraz sonra yoxlayın."
    return "⏳ Kvota dolub, 1-2 dəqiqəyə yenidən cəhd edin."

# ═══════════════════════════════════════════════════════════════════════
#  SERVER & WEBHOOK
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return f"M.Genat 1.3.6 Aktivdir. Model: {GEMINI_MODEL}"

@app.route(f'/webhook/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if flask_request.headers.get('content-type') != 'application/json':
        abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return 'ok', 200

def register_webhook():
    if not WEBHOOK_URL: return False
    url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", params={"drop_pending_updates": "true"}, timeout=10)
        time.sleep(2)
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": url, "drop_pending_updates": True}, timeout=10)
        return resp.json().get("ok")
    except: return False

# ═══════════════════════════════════════════════════════════════════════
#  DİNAMİK PROSESLƏR (SCOUT & REPORT)
# ═══════════════════════════════════════════════════════════════════════
def scout_loop():
    seen_news = deque(maxlen=500)
    while True:
        with _lock:
            keywords = [w.lower() for w in DINAMIK_PORTFEL] + ["fed", "inflation", "crypto"]
        try:
            feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")
            for entry in feed.entries[:5]:
                if entry.title not in seen_news and any(k in entry.title.lower() for k in keywords):
                    seen_news.append(entry.title)
                    result = gemini_call(f"Xəbər: '{entry.title}'. Kritikdirsə '🚨 KRİTİK' yazaraq analiz et.")
                    if "🚨 KRİTİK" in result.upper():
                        bot.send_message(CHAT_ID, f"{result}\n\n🔗 {entry.link}", parse_mode="Markdown")
                    time.sleep(20)
        except: pass
        time.sleep(1200)

def generate_report(report_type="GUNLUK"):
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    try:
        text = gemini_call(f"M.Genat 1.3.6. {report_type} strateji hesabat. Aktivlər: {portfel_str}. Dil: Azərbaycan.")
        bot.send_message(CHAT_ID, f"🏛️ **{report_type} HESABAT**\n\n{text}", parse_mode="Markdown")
    except: pass

def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="GÜNLÜK")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ═══════════════════════════════════════════════════════════════════════
#  MESAJ İDARƏETMƏSİ
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    print(f"DEBUG: Mesaj gəldi! ID: {message.chat.id}, Text: {message.text}")
    if str(message.chat.id) != str(CHAT_ID): return

    text = message.text or ""
    msg_l = text.lower()

    if msg_l == "test":
        bot.reply_to(message, "🚀 Bağlantı MÜKƏMMƏLDİR!")
    elif msg_l.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL: DINAMIK_PORTFEL.append(yeni)
        bot.reply_to(message, f"✅ Əlavə edildi: {yeni}")
    elif msg_l == "portfel":
        with _lock: bot.reply_to(message, f"📊 Radar: {', '.join(DINAMIK_PORTFEL)}")
    elif msg_l == "hesabat":
        bot.reply_to(message, "⏳ Analiz aparılır...")
        threading.Thread(target=generate_report, args=("ANİ",), daemon=True).start()
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            res = gemini_call(f"Sən M.Genat-san. Phill yazır: {text}")
            bot.reply_to(message, res, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)[:30]}")

# ═══════════════════════════════════════════════════════════════════════
#  START
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("M.Genat 1.3.6 işə düşür...")
    register_webhook()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
