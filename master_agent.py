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
 
# gemini-1.5-flash: pulsuz → 15 req/dəq, 1500 req/gün
# gemini-2.0-flash: pulsuz kvota çox kiçikdir, 429 verir
GEMINI_MODEL = 'gemini-1.5-flash'
 
client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
 
_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR   = []
 
 
# ═══════════════════════════════════════════════════════════════════════
#  GEMINI — 429-a qarşı retry mexanizmi
# ═══════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 4) -> str:
    """
    429 RESOURCE_EXHAUSTED alındıqda:
      - Cavabdakı retryDelay-i oxuyur (məs. "51s") və gözləyir
      - Sonra exponential backoff ilə yenidən cəhd edir
    """
    delay = 10
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )
            return resp.text
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait  = int(float(match.group(1))) + 5 if match else delay
                print(f"[Gemini 429] {wait}s gözlənilir... (cəhd {attempt+1}/{retries})")
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise
    raise ValueError(f"Gemini {retries} cəhddən sonra cavab vermədi.")
 
 
# ═══════════════════════════════════════════════════════════════════════
#  FLASK — webhook + sağlamlıq yoxlaması
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)
 
@app.route('/', methods=['GET'])
def health():
    return "M.Genat 1.3.0 - Webhook aktiv"
 
@app.route(f'/webhook/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if flask_request.headers.get('content-type') != 'application/json':
        abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return 'ok', 200
 
 
def register_webhook():
    """
    409 Conflict-in ƏSAS HƏLLİ:
    Webhook rejimində Telegram getUpdates çağırmır — polling yoxdur.
    İki proses eyni anda işləsə də heç bir konflikt olmur.
    Yeni deploy sadəcə webhook URL-i yenilər.
    """
    if not WEBHOOK_URL:
        print("XƏBƏRDARLIQ: WEBHOOK_URL mühit dəyişəni yoxdur!")
        print("  Render → Environment → WEBHOOK_URL=https://<app>.onrender.com")
        return False
 
    url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "true"}, timeout=10
        )
        time.sleep(1)
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": url, "drop_pending_updates": True, "max_connections": 1},
            timeout=10
        )
        data = resp.json()
        if data.get("ok"):
            print(f"Webhook qeydiyyatdan kecdi: {url}")
            return True
        else:
            print(f"Webhook xetasi: {data}")
            return False
    except Exception as e:
        print(f"Webhook istisna: {e}")
        return False
 
 
# ═══════════════════════════════════════════════════════════════════════
#  TELEGRAM YARDIMÇI
# ═══════════════════════════════════════════════════════════════════════
def send_tg(text: str):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except Exception as e:
        print(f"[Telegram] gondermə xetasi: {e}")
 
 
# ═══════════════════════════════════════════════════════════════════════
#  HESABAT
# ═══════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "GUNLUK"):
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = (
        f"M.Genat 1.3. {report_type} maliyye hesabati hazirla. "
        f"Aktivler: {portfel_str}. "
        f"Tarix: {datetime.now().strftime('%d %B %Y')}. Dil: Azerbaycan."
    )
    try:
        text = gemini_call(prompt)
        send_tg(f"🏛️ **{report_type} HESABAT**\n\n{text}")
    except Exception as e:
        print(f"[Hesabat] xeta: {e}")
        send_tg(f"⚠️ Hesabat yaradila bilmedi: {e}")
 
 
def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="GUNLUK")
    schedule.every().monday.at("09:00").do(generate_report, report_type="HEFTELIK")
    while True:
        schedule.run_pending()
        time.sleep(30)
 
 
# ═══════════════════════════════════════════════════════════════════════
#  SCOUT
# ═══════════════════════════════════════════════════════════════════════
def scout_loop():
    seen_news = deque(maxlen=500)
    rss_urls  = [
        "https://finance.yahoo.com/news/rssindex",
        "http://feeds.marketwatch.com/marketwatch/topstories/",
    ]
 
    while True:
        with _lock:
            keywords = [w.lower() for w in DINAMIK_PORTFEL] + [
                "fed", "rate", "inflation", "tariff", "crypto", "bitcoin"
            ]
 
        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title_lower = entry.title.lower()
                    if entry.title in seen_news:
                        continue
                    if not any(k in title_lower for k in keywords):
                        continue
                    seen_news.append(entry.title)
 
                    prompt = (
                        f"Xeber basliqi: '{entry.title}'.\n"
                        f"ETH, BTC, NVDA, AMD portfelinə potensial tesiri analiz et. "
                        f"Yalniz kritikdirse cavabinin birinci sozu '🚨 KRİTİK' olsun."
                    )
                    try:
                        result = gemini_call(prompt)
                        if "🚨 KRİTİK" in result:
                            send_tg(f"{result}\n\n🔗 {entry.link}")
                    except Exception as e:
                        print(f"[Scout] Gemini xeta: {e}")
 
                    time.sleep(10)   # sorğular arasi fasilə — kvota qorumasi
 
            except Exception as e:
                print(f"[Scout] RSS xeta ({url}): {e}")
 
        time.sleep(600)
 
 
# ═══════════════════════════════════════════════════════════════════════
#  XATIRLATMA
# ═══════════════════════════════════════════════════════════════════════
def reminder_loop():
    while True:
        now_time = datetime.now().strftime("%H:%M")
        with _lock:
            due = [x for x in XATIRLATMALAR if x["zaman"] == now_time]
            for x in due:
                XATIRLATMALAR.remove(x)
        for x in due:
            send_tg(f"⏰ **XATIRLATMA:** {x['mesaj']}")
        time.sleep(30)
 
 
# ═══════════════════════════════════════════════════════════════════════
#  MESAJ İDARƏETMƏSİ
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID):
        return
 
    text      = message.text or ""
    msg_lower = text.lower()
 
    if msg_lower.startswith("skan elave et:"):
        parts = text.split(":", 1)
        if len(parts) == 2:
            yeni = parts[1].strip().upper()
            with _lock:
                if yeni and yeni not in DINAMIK_PORTFEL:
                    DINAMIK_PORTFEL.append(yeni)
                    bot.reply_to(message, f"✅ Elave edildi: `{yeni}`")
                else:
                    bot.reply_to(message, f"⚠️ `{yeni}` artiq siyahidadir.")
        else:
            bot.reply_to(message, "Format: `skan elave et: TICKER`")
 
    elif msg_lower.startswith("skan sil:"):
        parts = text.split(":", 1)
        if len(parts) == 2:
            sil = parts[1].strip().upper()
            with _lock:
                if sil in DINAMIK_PORTFEL:
                    DINAMIK_PORTFEL.remove(sil)
                    bot.reply_to(message, f"🗑️ Silindi: `{sil}`")
                else:
                    bot.reply_to(message, f"⚠️ `{sil}` tapilmadi.")
 
    elif msg_lower == "portfel":
        with _lock:
            siyahi = ", ".join(DINAMIK_PORTFEL)
        bot.reply_to(message, f"📊 **Cari portfel:**\n{siyahi}")
 
    elif msg_lower.startswith("xatirlatma") or msg_lower.startswith("xatırlat"):
        parts = text.split(" ", 2)
        if len(parts) < 3 or ":" not in parts[1]:
            bot.reply_to(message, "Format: `xatirlatma 20:00 Mesaj metni`")
        else:
            with _lock:
                XATIRLATMALAR.append({"zaman": parts[1], "mesaj": parts[2]})
            bot.reply_to(message, f"✅ Saat {parts[1]} ucun qeyd edildi.")
 
    elif msg_lower == "hesabat":
        bot.reply_to(message, "⏳ Hesabat hazirlanir...")
        threading.Thread(target=generate_report, args=("ANI",), daemon=True).start()
 
    else:
        try:
            result = gemini_call(
                f"Sen M.Genat adli maliyye assistentisen. "
                f"Istifadeci suali: {text}"
            )
            bot.reply_to(message, result, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xeta: {e}")
 
 
# ═══════════════════════════════════════════════════════════════════════
#  MASTER START
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("M.Genat 1.3.0 bashlayir (webhook rejimi)...")
 
    register_webhook()
 
    threading.Thread(target=scout_loop,    daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
 
    print("Threadler ise dushdu. Flask webhook serveri bashlayir...")
    port = int(os.environ.get('PORT', 10000))
    # use_reloader=False — Flask reloader ikinci proses acar ve 409 yaradir
    app.run(host='0.0.0.0', port=port, use_reloader=False, debug=False)
