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

# Model adını təmizləyirik
_raw = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash').strip().replace('"', '').replace("'", "")
GEMINI_MODEL = _raw.replace('models/', '')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR   = []

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI — SELF-HEALING (404) & RETRY (429) MEXANİZMİ
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
            
            # 404 Xətasında "Self-Healing": Model səhvdirsə, standart modelə məcburi keçid edir
            if '404' in err_str or 'NOT_FOUND' in err_str:
                print(f"⚠️ [404 Xətası] '{current_model}' tapılmadı! Təhlükəsiz modelə (gemini-1.5-flash) keçilir...")
                current_model = 'gemini-1.5-flash'
                continue # Dərhal yeni model ilə təkrar yoxlayır
                
            # 429 Limit xətası
            elif '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait = int(float(match.group(1))) + 5 if match else delay
                print(f"⏳ [Gemini 429] {wait} saniyə gözlənilir... (Cəhd: {attempt+1})")
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                print(f"Bilinməyən Gemini Xətası: {err_str}")
                return f"❌ M.Genat xəta ilə qarşılaşdı: {err_str[:50]}..."
                
    return "⏳ Sistem həddindən artıq yüklüdür. Lütfən biraz sonra yenidən cəhd edin."

# ═══════════════════════════════════════════════════════════════════════
#  SERVER & WEBHOOK
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return f"M.Genat 1.3.5 AKTİVDİR. Mühərrik: {GEMINI_MODEL}"

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
#  AĞILLI ZAMANLAMA (ADAPTIVE SCOUTING)
# ═══════════════════════════════════════════════════════════════════════
def get_scout_interval():
    """Bakı vaxtına əsasən ABŞ bazarı üçün dinamik axtarış intervalı (Kvota qorunması üçün uzadıldı)"""
    hour = datetime.now().hour
    if 21 <= hour < 23: return 600       # 10 dəqiqə: FED & Kritik saatlar
    elif 17 <= hour < 21: return 1200    # 20 dəqiqə: ABŞ Bazar Açılışı
    elif 12 <= hour < 17: return 1800    # 30 dəqiqə: Pre-market
    elif 0 <= hour < 4: return 3600      # 1 saat: After-hours
    else: return 7200                    # 2 saat: ABŞ Gecəsi / Durğun vaxt

def scout_loop():
    seen_news = deque(maxlen=500)
    rss_urls = ["https://finance.yahoo.com/news/rssindex", "http://feeds.marketwatch.com/marketwatch/topstories/"]
    while True:
        with _lock:
            keywords = [w.lower() for w in DINAMIK_PORTFEL] + ["fed", "rate", "inflation", "tariff", "crypto"]
        
        # Axtarışdan əvvəl statusu yoxlayır
        interval = get_scout_interval()
        
        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    if entry.title not in seen_news and any(k in entry.title.lower() for k in keywords):
                        seen_news.append(entry.title)
                        prompt = f"Xəbər: '{entry.title}'. Analiz et. Kritikdirsə '🚨 KRİTİK' yazaraq cavaba başla."
                        result = gemini_call(prompt)
                        if "🚨 KRİTİK" in result.upper():
                            bot.send_message(CHAT_ID, f"{result}\n\n🔗 {entry.link}", parse_mode="Markdown")
                        time.sleep(20) # 5 saniyədən 20 saniyəyə qaldırıldı ki, kvota dolmasın
            except: continue
        
        # Dinamik yuxu rejimi
        print(f"Scout agent {interval//60} dəqiqəlik gözləməyə keçdi...")
        time.sleep(interval)

# ═══════════════════════════════════════════════════════════════════════
#  HESABAT VƏ XATIRLATMA
# ═══════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "GUNLUK"):
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = f"M.Genat 1.3.5. {report_type} strateji hesabat. Aktivlər: {portfel_str}. Dil: Azərbaycan."
    try:
        text = gemini_call(prompt)
        bot.send_message(CHAT_ID, f"🏛️ **{report_type} HESABAT**\n\n{text}", parse_mode="Markdown")
    except: pass

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
    print(f"Gələn Mesaj ID: {message.chat.id}")
    if str(message.chat.id) != str(CHAT_ID): 
        print("XƏBƏRDARLIQ: CHAT_ID uyğun gəlmir, mesaj bloklandı.")
        return
    text = message.text or ""
    msg_lower = text.lower()
    
    if msg_lower.startswith("skan əlavə et:"):
        parts = text.split(":", 1)
        if len(parts) == 2:
            yeni = parts[1].strip().upper()
            with _lock:
                if yeni not in DINAMIK_PORTFEL:
                    DINAMIK_PORTFEL.append(yeni)
                    bot.reply_to(message, f"✅ Əlavə edildi: `{yeni}`")
    elif msg_lower == "portfel":
        with _lock: bot.reply_to(message, f"📊 Radar: {', '.join(DINAMIK_PORTFEL)}")
    elif msg_lower.startswith("xatırlat"):
        parts = text.split(" ", 2)
        if len(parts) >= 3:
            with _lock: XATIRLATMALAR.append({"zaman": parts[1], "mesaj": parts[2]})
            bot.reply_to(message, f"✅ {parts[1]} üçün qeyd edildi.")
    elif msg_lower == "hesabat":
        bot.reply_to(message, "⏳ Analiz aparılır...")
        threading.Thread(target=generate_report, args=("ANİ",), daemon=True).start()
    else:
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            result = gemini_call(f"Sən M.Genat-san. Phill-in sualı: {text}")
            bot.reply_to(message, result, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)[:50]}")

if __name__ == '__main__':
    print(f"M.Genat 1.3.5 işə düşür (Model: {GEMINI_MODEL})")
    register_webhook()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
