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

# Render-dəki Environment Variable-dan oxuyur, yoxdursa flash-1.5-ə keçir
GEMINI_MODEL    = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR   = []

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI — 429-a qarşı retry mexanizmi
# ═══════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 3) -> str:
    delay = 10
    for attempt in range(retries):
        try:
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
    return "⏳ Hazırda sistem çox yüklüdür (Kvota dolub). Lütfən bir az sonra yenidən cəhd edin."

# ═══════════════════════════════════════════════════════════════════════
#  FLASK — webhook server
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return f"M.Genat 1.3.1 - {GEMINI_MODEL} Aktivdir"

@app.route(f'/webhook/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if flask_request.headers.get('content-type') != 'application/json':
        abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return 'ok', 200

def register_webhook():
    if not WEBHOOK_URL:
        print("XƏBƏRDARLIQ: WEBHOOK_URL yoxdur!")
        return False
    url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", params={"drop_pending_updates": "true"}, timeout=10)
        time.sleep(1)
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": url, "drop_pending_updates": True, "max_connections": 1}, timeout=10)
        return resp.json().get("ok")
    except Exception as e:
        print(f"Webhook xətası: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════
#  MİSKROSKOP VƏ TELESKOP ANALİZLƏRİ
# ═══════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "GUNLUK"):
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    
    prompt = f"""
    Sən M.Genat 1.3.1-sən. Phill üçün {report_type} strateji maliyyə hesabatı hazırla. 
    Aktivlər: {portfel_str}. 
    Tarix: {datetime.now().strftime('%d %B %Y')}.
    
    Analiz Tələbləri:
    1. MİKROSKOP: Səhmlərə təsir edən ən xırda, gizli amilləri (insayder hərəkətləri, tədarük zənciri dəyişiklikləri) tap.
    2. TELESKOP: Gələcək vəd edən sektorlar (AI, Uran, Robototexnika) üzrə yeni layihələri araşdır.
    3. STRATEGİYA: Portfel üçün konkret giriş/çıxış və ya gözləmə tövsiyəsi ver.
    Dil: Azərbaycan.
    """
    try:
        text = gemini_call(prompt)
        bot.send_message(CHAT_ID, f"🏛️ **{report_type} STRATEJİ HESABAT**\n\n{text}", parse_mode="Markdown")
    except Exception as e:
        print(f"Hesabat xətası: {e}")

def scout_loop():
    seen_news = deque(maxlen=500)
    rss_urls = ["https://finance.yahoo.com/news/rssindex", "http://feeds.marketwatch.com/marketwatch/topstories/"]
    while True:
        with _lock:
            keywords = [w.lower() for w in DINAMIK_PORTFEL] + ["fed", "rate", "inflation", "tariff", "crypto", "bitcoin"]
        
        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title_lower = entry.title.lower()
                    if entry.title not in seen_news and any(k in title_lower for k in keywords):
                        seen_news.append(entry.title)
                        prompt = f"Xəbər: '{entry.title}'.\nPortfel: {', '.join(DINAMIK_PORTFEL)}. Bu xəbərin gizli təsirlərini analiz et. Əgər hərəkət lazımdırsa, cavaba '🚨 KRİTİK' ilə başla."
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
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR AÇILIŞI")
    schedule.every().monday.at("09:00").do(generate_report, report_type="HƏFTƏLİK")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ═══════════════════════════════════════════════════════════════════════
#  MESAJ İDARƏETMƏSİ
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    text = message.text or ""
    msg_lower = text.lower()

    if msg_lower.startswith("skan elave et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.append(yeni)
                bot.reply_to(message, f"✅ Radara əlavə edildi: `{yeni}`")
    elif msg_lower == "portfel":
        with _lock:
            bot.reply_to(message, f"📊 **Cari Kəşfiyyat Radarı:**\n{', '.join(DINAMIK_PORTFEL)}")
    elif msg_lower.startswith("xatirlat"):
        parts = text.split(" ", 2)
        if len(parts) >= 3:
            with _lock: XATIRLATMALAR.append({"zaman": parts[1], "mesaj": parts[2]})
            bot.reply_to(message, f"✅ {parts[1]} üçün qeyd edildi.")
    elif msg_lower == "hesabat":
        bot.reply_to(message, "⏳ Strateji analiz aparılır...")
        threading.Thread(target=generate_report, args=("ANI",), daemon=True).start()
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            result = gemini_call(f"Sən M.Genat-san. Phill-in sualı: {text}")
            bot.reply_to(message, result, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)[:50]}...")

# ═══════════════════════════════════════════════════════════════════════
#  MASTER START
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print(f"M.Genat 1.3.1 işə düşür (Model: {GEMINI_MODEL})")
    register_webhook()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
