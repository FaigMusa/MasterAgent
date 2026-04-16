import time
import threading
import schedule
import requests
import re
import logging
from datetime import datetime
from collections import deque
from google import genai
from flask import Flask, request as flask_request, abort
import os
import feedparser
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ─────────────────────────── LOGGING ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

# ─────────────────────────── TƏNZİMLƏMƏLƏR ────────────────────────────
TELEGRAM_TOKEN  = os.getenv('TELEGRAM_TOKEN')
CHAT_ID         = os.getenv('CHAT_ID')
GEMINI_API_KEY  = os.getenv('GEMINI_API_KEY')
WEBHOOK_URL     = os.getenv('WEBHOOK_URL', '').rstrip('/')
_raw = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash').strip().replace('"', '').replace("'", "")
GEMINI_MODEL = _raw.replace('models/', '')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

_lock           = threading.Lock()
DINAMIK_PORTFEL = ["ETH"]
STRATEJI_PORTFEL= ["AI", "URAN", "ROBOTICS"]
XATIRLATMALAR   = []
TAPSIRIQLAR     = []

# ═══════════════════════════════════════════════════════════════════════
#  KÖMƏKÇİ: TƏHLÜKƏSİZ MESAJ GÖNDƏR
# ═══════════════════════════════════════════════════════════════════════
def safe_send(chat_id, text, parse_mode="Markdown"):
    """
    FIX #1 & #3: Markdown parse xətasını handle edir.
    Əvvəlcə Markdown ilə cəhd edir, uğursuz olsa düz mətn göndərir.
    """
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode)
    except Exception as e:
        if "can't parse" in str(e).lower() or "parse" in str(e).lower():
            # Markdown xətasıdırsa, formatsız göndər
            try:
                plain = text.replace("**", "").replace("*", "").replace("`", "").replace("_", "")
                bot.send_message(chat_id, plain)
            except Exception as e2:
                log.error(f"safe_send tam uğursuz: {e2}")
        else:
            log.error(f"safe_send xətası: {e}")

def safe_reply(message, text, parse_mode="Markdown"):
    """
    FIX #1: reply_to üçün eyni təhlükəsiz wrapper.
    """
    try:
        bot.reply_to(message, text, parse_mode=parse_mode)
    except Exception as e:
        if "can't parse" in str(e).lower() or "parse" in str(e).lower():
            try:
                plain = text.replace("**", "").replace("*", "").replace("`", "").replace("_", "")
                bot.reply_to(message, plain)
            except Exception as e2:
                log.error(f"safe_reply tam uğursuz: {e2}")
        else:
            log.error(f"safe_reply xətası: {e}")

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI & WEBHOOK
# ═══════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 3) -> str:
    """
    FIX #2: 
    - Bütün xəta növləri üçün retry əlavə edildi (yalnız 429 deyil)
    - Xəta logu əlavə edildi
    - Fallback model daha etibarlı işləyir
    """
    delay = 15
    current_model = GEMINI_MODEL

    for attempt in range(retries):
        try:
            log.info(f"Gemini çağırılır: model={current_model}, attempt={attempt+1}")
            resp = client.models.generate_content(model=current_model, contents=prompt)
            return resp.text

        except Exception as e:
            err_str = str(e)
            log.warning(f"Gemini xətası (attempt {attempt+1}): {err_str[:200]}")

            if '404' in err_str or 'NOT_FOUND' in err_str:
                log.warning(f"Model tapılmadı ({current_model}), gemini-1.5-flash-a keçilir")
                current_model = 'gemini-1.5-flash'
                time.sleep(2)
                continue

            elif '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait = int(float(match.group(1))) + 5 if match else delay
                log.warning(f"Rate limit, {wait}s gözlənilir...")
                time.sleep(wait)
                # continue — döngü özü növbəti cəhdə keçəcək

            elif '401' in err_str or '403' in err_str or 'API_KEY' in err_str.upper():
                # Auth xətası — retry faydasız
                log.error("Gemini API açarı yanlışdır və ya səlahiyyətsizdir!")
                return "❌ API açarı xətası. Zəhmət olmasa GEMINI_API_KEY dəyişənini yoxlayın."

            else:
                # Şəbəkə xətası, server xətası — retry et
                log.warning(f"Naməlum xəta, {delay}s sonra yenidən cəhd...")
                time.sleep(delay)

    return "⏳ Kvota dolub və ya Gemini əlçatmazdır. Bir az sonra yenidən cəhd edin."


app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return "M.Genat 2.0 Aktivdir."

@app.route(f'/webhook/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if flask_request.headers.get('content-type') != 'application/json':
        abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return 'ok', 200

def register_webhook():
    if not WEBHOOK_URL:
        log.warning("WEBHOOK_URL təyin edilməyib, polling rejimə keçilir")
        return False
    url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "true"}, timeout=10
        )
        time.sleep(2)
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": url, "drop_pending_updates": True}, timeout=10
        )
        log.info(f"Webhook qeydiyyatı: {r.json()}")
    except Exception as e:
        log.error(f"Webhook xətası: {e}")

# ═══════════════════════════════════════════════════════════════════════
#  MAKRO-KƏŞFİYYAT VƏ STRATEGİYA
# ═══════════════════════════════════════════════════════════════════════
def scout_loop():
    seen_news = deque(maxlen=500)
    rss_urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://www.investing.com/rss/news_25.rss"
    ]
    while True:
        try:
            with _lock:
                keywords = [w.lower() for w in DINAMIK_PORTFEL] + [
                    "oil", "gold", "fed", "iran", "ceasefire"
                ]

            for url in rss_urls:
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:5]:
                        title = getattr(entry, 'title', '')
                        link  = getattr(entry, 'link', '')
                        if title and title not in seen_news and any(
                            k in title.lower() for k in keywords
                        ):
                            seen_news.append(title)
                            prompt = f"""
                            Xəbər: '{title}'. 
                            Mənim portfelim: {', '.join(DINAMIK_PORTFEL)}.
                            Nəzərə al ki, qlobal geosiyasi gərginliklər neftin, qızılın və DXY-nin qiymətinə təsir edir.
                            Bu xəbərin birbaşa və ya dolaylı yolla mənim portfelimə təsirini analiz et.
                            Əgər vəziyyət təcilidirsə, '🚨 KRİTİK' yazaraq cavaba başla.
                            """
                            result = gemini_call(prompt)
                            if "🚨 KRİTİK" in result.upper():
                                safe_send(CHAT_ID, f"{result}\n\n🔗 {link}")
                            time.sleep(20)
                except Exception as e:
                    log.warning(f"RSS xətası ({url}): {e}")

        except Exception as e:
            log.error(f"scout_loop kritik xəta: {e}")

        time.sleep(1200)


def generate_report(report_type="GÜNLÜK"):
    """FIX #3: Səssiz uğursuzluq əvəzinə xəta mesajı göndərir."""
    try:
        with _lock:
            s_portfel = ", ".join(STRATEJI_PORTFEL)
            d_portfel = ", ".join(DINAMIK_PORTFEL)

        prompt = (
            f"M.Genat 2.0. {report_type} hesabat. "
            f"Qısaqüddətli aktivlər: {d_portfel}. "
            f"Uzunmüddətli strateji izləmə: {s_portfel}. "
            f"Geosiyasi makro-mənzərəni də nəzərə al. Dil: Azərbaycan."
        )
        text = gemini_call(prompt)
        safe_send(CHAT_ID, f"🏛️ **{report_type} STRATEJİ HESABAT**\n\n{text}")

    except Exception as e:
        log.error(f"generate_report xətası: {e}")
        safe_send(CHAT_ID, f"⚠️ Hesabat hazırlanarkən xəta baş verdi: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  AĞILLI ASSİSTENT
# ═══════════════════════════════════════════════════════════════════════
def reminder_loop():
    while True:
        try:
            now_time = datetime.now().strftime("%H:%M")
            with _lock:
                due = [x for x in XATIRLATMALAR if x["zaman"] == now_time]
                for x in due:
                    XATIRLATMALAR.remove(x)
            for x in due:
                safe_send(CHAT_ID, f"⏰ **XATIRLATMA:** {x['mesaj']}")
        except Exception as e:
            log.error(f"reminder_loop xətası: {e}")
        time.sleep(30)


def uncompleted_tasks_reminder():
    try:
        with _lock:
            if not TAPSIRIQLAR:
                return
            msg = "📝 **HƏLL EDİLMƏMİŞ TAPŞIRIQLAR:**\n"
            for i, t in enumerate(TAPSIRIQLAR, 1):
                msg += f"{i}. {t}\n"
        safe_send(CHAT_ID, msg)
    except Exception as e:
        log.error(f"uncompleted_tasks_reminder xətası: {e}")


def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR")
    schedule.every().day.at("10:00").do(uncompleted_tasks_reminder)
    schedule.every().day.at("18:00").do(uncompleted_tasks_reminder)
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error(f"schedule_loop xətası: {e}")
        time.sleep(30)


# ═══════════════════════════════════════════════════════════════════════
#  İNTERAKTİV İDARƏETMƏ PANElİ (UI)
# ═══════════════════════════════════════════════════════════════════════
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔭 Scout Radarı",       callback_data="show_scout"),
        InlineKeyboardButton("🏛️ Strateji Sektorlar", callback_data="show_strat")
    )
    markup.row(
        InlineKeyboardButton("📝 Tapşırıqlar",  callback_data="show_tasks"),
        InlineKeyboardButton("📊 Anlıq Hesabat", callback_data="run_report")
    )
    return markup


@bot.message_handler(commands=['menu', 'start'])
def send_menu(message):
    if str(message.chat.id) != str(CHAT_ID):
        return
    safe_send(message.chat.id, "🎛 **M.Genat İdarəetmə Paneli**")
    bot.send_message(message.chat.id, "Əmr seçin:", reply_markup=main_menu_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.message.chat.id) != str(CHAT_ID):
        return

    try:
        if call.data == "show_scout":
            with _lock:
                txt = ", ".join(DINAMIK_PORTFEL)
            safe_send(call.message.chat.id,
                      f"🔭 **Scout Radarı:** {txt}\nƏlavə etmək üçün yaz: `skan əlavə et: BTC`")

        elif call.data == "show_strat":
            with _lock:
                txt = ", ".join(STRATEJI_PORTFEL)
            safe_send(call.message.chat.id,
                      f"🏛️ **Strateji Sektorlar:** {txt}\nƏlavə etmək üçün yaz: `strat əlavə et: QIZIL`")

        elif call.data == "show_tasks":
            with _lock:
                txt = "\n".join([f"- {t}" for t in TAPSIRIQLAR]) if TAPSIRIQLAR else "Tapşırıq yoxdur."
            safe_send(call.message.chat.id,
                      f"📝 **Üzən Tapşırıqlar:**\n{txt}\nƏlavə etmək üçün yaz: `tapşırıq: Kitab al`")

        elif call.data == "run_report":
            safe_send(call.message.chat.id, "⏳ Makro-Analiz aparılır...")
            threading.Thread(target=generate_report, args=("ANİ",), daemon=True).start()

        bot.answer_callback_query(call.id)  # FIX: callback sorğusunu cavabsız qoymamaq

    except Exception as e:
        log.error(f"handle_query xətası: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  MƏTN ANALİZİ VƏ NLP YÖNLƏNDİRMƏ
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID):
        return
    text  = message.text or ""
    msg_l = text.lower().strip()

    if msg_l == "menu":
        send_menu(message)
        return

    # 1. SCOUT İDARƏSİ
    if msg_l.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.append(yeni)
        safe_reply(message, f"✅ Scout-a əlavə edildi: `{yeni}`")

    elif msg_l.startswith("skan sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.remove(sil)
        safe_reply(message, f"🗑️ Scout-dan silindi: `{sil}`")

    # 2. STRATEJİ SEKTOR İDARƏSİ
    elif msg_l.startswith("strat əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in STRATEJI_PORTFEL:
                STRATEJI_PORTFEL.append(yeni)
        safe_reply(message, f"🏛️ Strateji siyahıya əlavə edildi: `{yeni}`")

    # 3. ASSİSTENT — Vaxtlı Xatırlatma
    elif msg_l.startswith("xatırlat"):
        try:
            parts = text.split(" ", 2)
            if len(parts) < 3:
                raise ValueError("Az arqument")
            with _lock:
                XATIRLATMALAR.append({"zaman": parts[1], "mesaj": parts[2]})
            safe_reply(message, f"✅ Saat {parts[1]} üçün qeyd edildi.")
        except Exception:
            safe_reply(message, "Format: `xatırlat 16:00 İş görüşməsi`")

    # 4. ASSİSTENT — Üzən Tapşırıq
    elif msg_l.startswith("tapşırıq:"):
        yeni = text.split(":", 1)[1].strip()
        with _lock:
            TAPSIRIQLAR.append(yeni)
        safe_reply(message, f"📝 Üzən tapşırıq qeyd edildi: {yeni}")

    elif msg_l.startswith("həll edildi:"):
        sil = text.split(":", 1)[1].strip()
        with _lock:
            if sil in TAPSIRIQLAR:
                TAPSIRIQLAR.remove(sil)
                safe_reply(message, "✅ Tapşırıq siyahıdan çıxarıldı.")
            else:
                safe_reply(message, f"⚠️ `{sil}` tapşırıq siyahısında tapılmadı.")

    # 5. ÜMUMİ SÖHBƏT — GEMINI NLP
    else:
        def gemini_arxa_plan():
            """
            FIX #1 (Əsas): 
            - except pass → istifadəçiyə xəta mesajı göndərir
            - Markdown parse xətası ayrıca handle olunur (safe_reply vasitəsilə)
            """
            try:
                bot.send_chat_action(message.chat.id, 'typing')
                res = gemini_call(f"Sən M.Genat-san. Phill sənə yazır: {text}")
                safe_reply(message, res)   # ← safe_reply Markdown xətasını özü həll edir
            except Exception as e:
                log.error(f"gemini_arxa_plan xətası: {e}")
                try:
                    bot.reply_to(message, f"⚠️ Xəta baş verdi: {str(e)[:200]}")
                except Exception:
                    pass

        threading.Thread(target=gemini_arxa_plan, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════
#  BAŞLANĞIC
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    log.info("M.Genat 2.0 işə düşür...")

    # Mühit dəyişənlərini yoxla
    for var in ['TELEGRAM_TOKEN', 'CHAT_ID', 'GEMINI_API_KEY']:
        if not os.getenv(var):
            log.error(f"❌ {var} mühit dəyişəni təyin edilməyib!")

    register_webhook()

    threading.Thread(target=scout_loop,     daemon=True).start()
    threading.Thread(target=reminder_loop,  daemon=True).start()
    threading.Thread(target=schedule_loop,  daemon=True).start()

    port = int(os.environ.get('PORT', 10000))
    log.info(f"Flask serveri port {port}-da işə düşür")
    app.run(host='0.0.0.0', port=port, use_reloader=False)
