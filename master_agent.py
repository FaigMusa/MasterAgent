"""
M.Genat 3.0 — Master Agent
-------------------------------------------------
Düzəldilmiş Problemlər:
  [FIX-1] gemini_arxa_plan: `except: pass` → istifadəçiyə xəta mesajı göndərir
  [FIX-2] generate_report: `except: pass` → log + xəta mesajı
  [FIX-3] safe_send/safe_reply: Telegram Markdown parse xətasını handle edir
  [FIX-4] gemini_call: auth (401/403) və şəbəkə xətaları üçün düzgün retry məntiqi
  [FIX-5] callback_query: answer_callback_query çağırılır (UI donması aradan qalxır)
  [FIX-6] Başlanğıcda mühit dəyişənləri yoxlanılır
"""

import logging
import os
import re
import threading
import time

import requests
import schedule
import telebot
from flask import Flask, abort
from flask import request as flask_request
from google import genai
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import data_engine

# ─────────────────────────── LOGGING ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────── MÜHİT DƏYİŞƏNLƏRİ ──────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID         = os.getenv("CHAT_ID")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL     = os.getenv("WEBHOOK_URL", "").rstrip("/")
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_TOKEN", "")

_raw         = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip().replace('"', "").replace("'", "")
GEMINI_MODEL = _raw.replace("models/", "")

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
_lock  = threading.Lock()


# ─────────────────────────── RADARLAR ────────────────────────────────────────
def _get_env_list(var_name: str, defaults: list) -> list:
    raw = os.getenv(var_name, "")
    if not raw.strip():
        return defaults
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


DINAMIK_PORTFEL  = _get_env_list("DINAMIK_PORTFEL",  ["BTCUSDT", "ETHUSDT"])
STRATEJI_PORTFEL = _get_env_list("STRATEJI_PORTFEL", ["SPY", "GC=F"])


# ═══════════════════════════════════════════════════════════════════════════════
#  KÖMƏKÇİ: TƏHLÜKƏSİZ MESAJ GÖNDƏR  [FIX-3]
# ═══════════════════════════════════════════════════════════════════════════════
def _strip_markdown(text: str) -> str:
    return text.replace("**", "").replace("*", "").replace("`", "").replace("_", "")


def safe_send(chat_id, text: str, parse_mode: str = None):
    """
    Markdown parse xətası olarsa düz mətnlə yenidən cəhd edir.
    """
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode)
    except Exception as e:
        err = str(e).lower()
        if "parse" in err or "can't parse" in err or "entity" in err:
            try:
                bot.send_message(chat_id, _strip_markdown(text))
            except Exception as e2:
                log.error(f"safe_send tam uğursuz: {e2}")
        else:
            log.error(f"safe_send xətası: {e}")


def safe_reply(message, text: str, parse_mode: str = None):
    """
    reply_to üçün eyni təhlükəsiz wrapper.
    """
    try:
        bot.reply_to(message, text, parse_mode=parse_mode)
    except Exception as e:
        err = str(e).lower()
        if "parse" in err or "can't parse" in err or "entity" in err:
            try:
                bot.reply_to(message, _strip_markdown(text))
            except Exception as e2:
                log.error(f"safe_reply tam uğursuz: {e2}")
        else:
            log.error(f"safe_reply xətası: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  GEMİNİ CALL  [FIX-4]
# ═══════════════════════════════════════════════════════════════════════════════
def gemini_call(prompt: str, retries: int = 3) -> str:
    """
    Bütün xəta növləri üçün retry məntiqi:
      - 404/NOT_FOUND  → fallback modelə keç
      - 429/EXHAUSTED  → API-nin verdiyi gözləmə müddəti
      - 401/403/API_KEY → dərhal izahlı mesajla qayıt (retry faydasız)
      - Digər           → delay ilə retry
    """
    delay         = 15
    current_model = GEMINI_MODEL

    for attempt in range(retries):
        try:
            log.info(f"Gemini çağırılır: model={current_model}, cəhd={attempt + 1}")
            resp = client.models.generate_content(model=current_model, contents=prompt)
            return resp.text

        except Exception as e:
            err = str(e)
            log.warning(f"Gemini xətası (cəhd {attempt + 1}): {err[:250]}")

            if "404" in err or "NOT_FOUND" in err:
                log.warning(f"Model tapılmadı ({current_model}), gemini-1.5-flash-a keçilir")
                current_model = "gemini-1.5-flash"
                time.sleep(2)

            elif "429" in err or "RESOURCE_EXHAUSTED" in err:
                match = re.search(r"retryDelay.*?(\d+(?:\.\d+)?)\s*s", err)
                wait  = int(float(match.group(1))) + 5 if match else delay
                log.info(f"Rate limit — {wait}s gözlənilir")
                time.sleep(wait)

            elif "401" in err or "403" in err or "API_KEY" in err.upper():
                log.error("Gemini API açarı etibarsızdır!")
                return "❌ API açarı xətası. GEMINI_API_KEY mühit dəyişənini yoxlayın."

            else:
                log.warning(f"Naməlum xəta — {delay}s sonra yenidən cəhd")
                time.sleep(delay)

    return "⏳ Gemini əlçatmazdır və ya kvota dolub. Bir az sonra yenidən cəhd edin."


# ═══════════════════════════════════════════════════════════════════════════════
#  FLASK SERVER & WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)


@app.route("/", methods=["GET"])
def health():
    return "M.Genat 3.0 Aktivdir (Data Engine qoşulub)."


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if flask_request.headers.get("content-type") != "application/json":
        abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "ok", 200


def register_webhook():
    if not WEBHOOK_URL:
        log.warning("WEBHOOK_URL təyin edilməyib.")
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
        log.info(f"Webhook: {r.json()}")
    except Exception as e:
        log.error(f"Webhook qeydiyyat xətası: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  HESABAT  [FIX-2]
# ═══════════════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "SƏHƏR"):
    """
    Real vaxt datası çəkib Gemini ilə analiz edir.
    Uğursuzluqda sessiz qalmaq əvəzinə log + Telegram mesajı göndərir.
    """
    try:
        with _lock:
            butun_aktivler = DINAMIK_PORTFEL + STRATEJI_PORTFEL

        log.info(f"Hesabat başlayır: {report_type} | Aktivlər: {butun_aktivler}")

        # 1. Data çəkilir (Binance + Yahoo Finance)
        faktiki_json = data_engine.aggregate_context(
            symbols=butun_aktivler,
            cryptopanic_key=CRYPTOPANIC_KEY
        )

        # 2. Gemini prompta salınır
        prompt = data_engine.build_gemini_prompt(
            context_json=faktiki_json,
            report_type=report_type
        )

        # 3. Gemini analiz edir
        analiz = gemini_call(prompt)

        # 4. Telegram-a göndərilir (safe_send Markdown xətasını handle edir)
        safe_send(
            CHAT_ID,
            f"🏛️ {report_type} STRATEJİ HESABAT (M.Genat 3.0)\n\n{analiz}"
        )
        log.info(f"Hesabat göndərildi: {report_type}")

    except Exception as e:
        log.error(f"generate_report xətası [{report_type}]: {e}")
        try:
            safe_send(CHAT_ID, f"⚠️ Hesabat hazırlanarkən xəta baş verdi:\n{str(e)[:300]}")
        except Exception:
            pass


def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR")
    schedule.every().day.at("17:00").do(generate_report, report_type="AXŞAM (PRE-MARKET)")
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error(f"schedule_loop xətası: {e}")
        time.sleep(30)


# ═══════════════════════════════════════════════════════════════════════════════
#  İNTERAKTİV İDARƏETMƏ PANELİ
# ═══════════════════════════════════════════════════════════════════════════════
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔭 Kripto Radar",        callback_data="show_crypto"),
        InlineKeyboardButton("🏛️ Səhm Radar",          callback_data="show_stocks")
    )
    markup.row(
        InlineKeyboardButton("📊 Dərin Analiz (Anlıq)", callback_data="run_report")
    )
    return markup


@bot.message_handler(commands=["menu", "start"])
def send_menu(message):
    if str(message.chat.id) != str(CHAT_ID):
        return
    safe_send(message.chat.id, "🎛 M.Genat 3.0 İdarəetmə Paneli")
    bot.send_message(message.chat.id, "Əmr seçin:", reply_markup=main_menu_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.message.chat.id) != str(CHAT_ID):
        return

    try:
        if call.data == "show_crypto":
            with _lock:
                txt = ", ".join(DINAMIK_PORTFEL)
            safe_send(
                call.message.chat.id,
                f"🔭 Dinamik (Kripto) Radar: {txt}\n"
                f"Əlavə etmək: skan əlavə et: SOLUSDT\n"
                f"Silmək:      skan sil: ETHUSDT"
            )

        elif call.data == "show_stocks":
            with _lock:
                txt = ", ".join(STRATEJI_PORTFEL)
            safe_send(
                call.message.chat.id,
                f"🏛️ Strateji (Səhm) Radar: {txt}\n"
                f"Əlavə etmək: strat əlavə et: AAPL\n"
                f"Silmək:      strat sil: SPY"
            )

        elif call.data == "run_report":
            safe_send(
                call.message.chat.id,
                "⏳ Real vaxt məlumatları (Binance + Yahoo Finance) çəkilir...\n"
                "Zəhmət olmasa 1-2 dəqiqə gözləyin."
            )
            threading.Thread(
                target=generate_report,
                args=("ANİ (DEEP RESEARCH)",),
                daemon=True
            ).start()

    except Exception as e:
        log.error(f"handle_query xətası: {e}")
    finally:
        # [FIX-5] Callback cavabsız qalmırsa UI donmur
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  MƏTN ANALİZİ VƏ NLP YÖNLƏNDİRMƏ
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID):
        return

    text  = (message.text or "").strip()
    msg_l = text.lower()

    # ── Menyu ─────────────────────────────────────────────────────────────────
    if msg_l in ("menu", "/menu"):
        send_menu(message)
        return

    # ── Kripto Radar İdarəsi ──────────────────────────────────────────────────
    if msg_l.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        if not yeni.endswith("USDT"):
            safe_reply(message, f"⚠️ Kripto simvolu USDT ilə bitməlidir. Məsələn: {yeni}USDT")
            return
        with _lock:
            if yeni not in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.append(yeni)
        safe_reply(message, f"✅ Kripto Radara əlavə edildi: {yeni}")

    elif msg_l.startswith("skan sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.remove(sil)
                safe_reply(message, f"🗑️ Kripto Radardan silindi: {sil}")
            else:
                safe_reply(message, f"⚠️ {sil} Kripto Radarında tapılmadı.")

    # ── Səhm Radar İdarəsi ────────────────────────────────────────────────────
    elif msg_l.startswith("strat əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in STRATEJI_PORTFEL:
                STRATEJI_PORTFEL.append(yeni)
        safe_reply(message, f"🏛️ Səhm Radarına əlavə edildi: {yeni}")

    elif msg_l.startswith("strat sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in STRATEJI_PORTFEL:
                STRATEJI_PORTFEL.remove(sil)
                safe_reply(message, f"🗑️ Səhm Radardan silindi: {sil}")
            else:
                safe_reply(message, f"⚠️ {sil} Səhm Radarında tapılmadı.")

    # ── Ümumi Söhbət — Gemini NLP  [FIX-1] ────────────────────────────────────
    else:
        def gemini_arxa_plan():
            try:
                bot.send_chat_action(message.chat.id, "typing")
                res = gemini_call(f"Sən M.Genat 3.0-san. Phill sənə yazır: {text}")
                safe_reply(message, res)   # safe_reply Markdown xətasını özü həll edir
            except Exception as e:
                log.error(f"gemini_arxa_plan xətası: {e}")
                try:
                    bot.reply_to(message, f"⚠️ Xəta baş verdi: {str(e)[:200]}")
                except Exception:
                    pass

        threading.Thread(target=gemini_arxa_plan, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  BAŞLANĞIC  [FIX-6]
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Mühit dəyişənlərini yoxla
    missing = [v for v in ("TELEGRAM_TOKEN", "CHAT_ID", "GEMINI_API_KEY") if not os.getenv(v)]
    if missing:
        for m in missing:
            log.error(f"❌ Mühit dəyişəni tapılmadı: {m}")
        raise SystemExit("Tələb olunan mühit dəyişənləri təyin edilməyib. Bot dayandırıldı.")

    log.info("M.Genat 3.0 (Kvant Analitik Modu) işə düşür...")
    log.info(f"Kripto Radar:  {DINAMIK_PORTFEL}")
    log.info(f"Səhm Radar:    {STRATEJI_PORTFEL}")
    log.info(f"Gemini Model:  {GEMINI_MODEL}")

    register_webhook()
    threading.Thread(target=schedule_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    log.info(f"Flask serveri port {port}-da başlayır")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
