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

# Claude-un tapdığı 404-ün qəti həlli: 
# models/ prefiksini silirik, çünki SDK onu özü əlavə edəcək.
_raw = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash').strip().replace('"', '').replace("'", "")
GEMINI_MODEL = _raw.replace('models/', '')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR   = []

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI — 429 Limit Qoruması
# ═══════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 3) -> str:
    delay = 10
    for attempt in range(retries):
        try:
            # DİQQƏT: SDK models/ prefiksini bura özü qoyacaq
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return resp.text
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait = int(float(match.group(1))) + 5 if match else delay
                print(f"[Gemini 429] {wait}s gözlənilir... (cəhd {attempt+1}/{retries})")
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise
    return "⏳ Sistem yüklüdür, kvota dolub. Az sonra yenidən cəhd edin."

# ═══════════════════════════════════════════════════════════════════════
#  SERVER & WEBHOOK (Render-in Sağlamlığı Üçün)
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return f"M.Genat 1.3.4 AKTİVDİR. Model: {GEMINI_MODEL}"

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
        time.sleep(1)
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": url, "drop_pending_updates": True, "max_connections": 1}, timeout=10)
        return resp.json().get("ok")
    except: return False

# ═══════════════════════════════════════════════════════════════════════
#  HESABAT VƏ KƏŞFİYYAT
# ═══════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "GUNLUK"):
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = f"M.Genat 1.3.4. {report_type} strateji hesabat. Aktivlər: {portfel_str}. Dil: Azərbaycan."
    try:
        text = gemini_call(prompt)
        bot.send_message(CHAT_ID, f"🏛️ **{report_type} HESABAT**\n\n{text}", parse_mode="Markdown")
    except: pass

def scout_loop():
    seen_news = deque(maxlen=500)
    rss_urls = ["https://finance.yahoo.com/news/rssindex", "http://feeds.marketwatch.com/marketwatch/topstories/"]
    while True:
        with _lock:
            keywords = [w.lower() for w in DINAMIK_PORTFEL] + ["fed", "rate", "inflation", "tariff", "crypto"]
        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    if entry.title not in seen_news and any(k in entry.title.lower() for k in keywords):
                        seen_news.append(entry.title)
                        prompt = f"Xəbər: '{entry.title}'. Analiz et. Kritikdirsə '🚨 KRİTİK' yaz."
                        result = gemini_call(prompt)
                        if "🚨 KRİTİK" in result.upper():
                            bot.send_message(CHAT_ID, f"{result}\n\n🔗 {entry.link}", parse_mode="Markdown")
                        time.sleep(10)
            except: continue
        time.sleep(600)

def reminder_loop():
    while True:
        now_time = datetime.now().strftime("%H:%M")
        with _lock:
            due = [x for x in XATIRLATMALAR if x["zaman"] == now_time]
            for x in due: XATIRLATMALAR.remove(x)
        for x in due:
            bot.send_message(CHAT_ID, f"⏰ **XATIRLATMA:** {x['mesaj']}")
        time.sleep(30)

def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="GÜNLÜK")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ═══════════════════════════════════════════════════════════════════════
#  MESAJLAR
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    text = message.text or ""
    msg_lower = text.lower()
    
    if msg_lower.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.append(yeni)
                bot.reply_to(message, f"✅ Əlavə edildi: `{yeni}`")
    elif msg_lower == "portfel":
        with _lock: bot.reply_to(message, f"📊 Radar: {', '.join(DINAMIK_PORTFEL)}")
    elif msg_lower == "hesabat":
        bot.reply_to(message, "⏳ Analiz aparılır...")
        threading.Thread(target=generate_report, args=("ANI",), daemon=True).start()
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            result = gemini_call(f"Sən M.Genat-san. Phill-in sualı: {text}")
            bot.reply_to(message, result, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)[:50]}")

if __name__ == '__main__':
    print(f"M.Genat 1.3.4 işə düşür (Model: {GEMINI_MODEL})")
    register_webhook()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
