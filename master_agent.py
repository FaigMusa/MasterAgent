"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           M.Genat 3.1 Pro  ·  master_agent.py                              ║
║                                                                              ║
║   Telegram Bot · Flask Webhook · Gemini 1.5 Flash                          ║
║   Zamanlanmış hesabatlar · Scout/Master analiz · Portfel idarəetməsi       ║
╚══════════════════════════════════════════════════════════════════════════════╝

Mühit dəyişənləri (.env / Render dashboard):
  TELEGRAM_TOKEN     — Bot tokeni (@BotFather-dən)
  CHAT_ID            — Yeganə icazəli chat ID
  GEMINI_API_KEY     — Google AI Studio açarı
  CRYPTOPANIC_TOKEN  — CryptoPanic API açarı
  WEBHOOK_URL        — https://yourapp.onrender.com   (son slash olmadan)
  DINAMIK_PORTFEL    — isteğe bağlı, məs: BTCUSDT,ETHUSDT
  STRATEJI_PORTFEL   — isteğe bağlı, məs: SPY,GC=F
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import requests
import schedule
import telebot
from flask import Flask, request as flask_request
from google import genai
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import data_engine

# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  MÜHİT DƏYİŞƏNLƏRİ
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN",      "")
CHAT_ID         = os.getenv("CHAT_ID",             "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY",      "")
WEBHOOK_URL     = os.getenv("WEBHOOK_URL",         "").rstrip("/")
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_TOKEN",   "")
PORT            = int(os.environ.get("PORT", 10000))

# Portfellər — mühit dəyişənindən oxu, yoxsa default
def _env_list(var: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(var, "").strip()
    if not raw:
        return list(defaults)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


_portfolio_lock  = threading.Lock()
_DINAMIK_DEFAULT = ["BTCUSDT", "ETHUSDT"]
_STRATEJI_DEFAULT = ["SPY", "GC=F"]

DINAMIK_PORTFEL  = _env_list("DINAMIK_PORTFEL",  _DINAMIK_DEFAULT)
STRATEJI_PORTFEL = _env_list("STRATEJI_PORTFEL", _STRATEJI_DEFAULT)

# ══════════════════════════════════════════════════════════════════════════════
#  BAŞLANĞIC YOXLAMALARI
# ══════════════════════════════════════════════════════════════════════════════

_missing = [v for v in ("TELEGRAM_TOKEN", "CHAT_ID", "GEMINI_API_KEY")
            if not os.getenv(v)]
if _missing:
    log.critical("Lazımi mühit dəyişənləri təyin edilməyib: %s", _missing)

# ══════════════════════════════════════════════════════════════════════════════
#  GEMİNİ MÜŞTƏRİSİ
# ══════════════════════════════════════════════════════════════════════════════

_gemini_client: Optional[genai.Client] = None

def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def gemini_call(prompt: str,
                model:   str = "gemini-2.5-flash",
                retries: int = 3) -> str:
    """Gemini API-yə prompt göndərir."""
    if not GEMINI_API_KEY:
        return "❌ GEMINI_API_KEY təyin edilməyib."

    client  = _get_gemini()
    wait    = 5  # ilk gözləmə (saniyə)
    last_error = "Bilinməyən xəta" # 🚨 XƏTANI TUTMAQ ÜÇÜN TƏLƏMİZ

    for attempt in range(1, retries + 1):
        try:
            # Bəzi regionlarda və layihələrdə 'models/' prefixi mütləq tələb olunur
            model_name = model if model.startswith("models/") else f"models/{model}"
            
            resp = client.models.generate_content(
                model=model_name, # Tam adı göndəririk: models/gemini-1.5-flash
                contents=prompt,
            )
            # Cavab məzmununu çıxar
            text = getattr(resp, "text", None)
            if text:
                return text.strip()
            # Alternativ struktur
            if hasattr(resp, "candidates") and resp.candidates:
                parts = resp.candidates[0].content.parts
                if parts:
                    return "".join(getattr(p, "text", "") for p in parts).strip()
            return "⚠️ Gemini boş cavab qaytardı."

        except Exception as exc:
            last_error = str(exc) # 🚨 XƏTANI BURADA YADDAŞA YAZIRIQ
            err = last_error.lower()
            log.warning("Gemini cəhd %d/%d xəta: %s", attempt, retries, exc)

            # Açar / icazə xətası
            if any(code in err for code in ("api_key", "400", "403", "invalid")):
                return f"❌ Gemini API açarı xətası. Təfərrüat: {last_error}"

            # Kvota / rate-limit xətası
            if any(code in err for code in ("429", "quota", "exhausted", "resource")):
                if attempt < retries:
                    log.info("Kvota — %ds gözlənilir...", wait)
                    time.sleep(wait)
                    wait *= 2   # eksponensial artım
                continue

            # Şəbəkə/server xətası
            if attempt < retries:
                time.sleep(wait)
                wait *= 2
            continue

    # 🚨 VƏ ƏN SONDA HƏQİQİ XƏTANI ÇAP EDİRİK
    return f"🚨 GİZLİ XƏTA AŞKARLANDI: {last_error}"

# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM YARDİMÇILARI
# ══════════════════════════════════════════════════════════════════════════════

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
app = Flask(__name__)

_MAX_MSG = 4000   # Telegram 4096 hərflik limit; ehtiyatlı olmaq üçün 4000


def _plain(text: str) -> str:
    """Markdown simvollarını kənara qoyur — sadə mətn göndərişi üçün."""
    for ch in ("**", "*", "`", "```", "_", "~", ">"):
        text = text.replace(ch, "")
    return text


def _chunks(text: str, size: int = _MAX_MSG) -> list[str]:
    """Uzun mətni Telegram limitinə uyğun parçalara bölür."""
    parts = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


def safe_send(chat_id: str | int, text: str) -> None:
    """Uzun mesajı parçalara bölüb sadə mətn kimi göndərir."""
    plain_text = _plain(text)
    for chunk in _chunks(plain_text):
        try:
            bot.send_message(chat_id, chunk)
        except Exception as e:
            log.error("Telegram göndərmə xətası: %s", e)
            try:
                bot.send_message(chat_id, chunk[:400] + "\n...(kəsildi)")
            except Exception:
                pass


def safe_reply(message: telebot.types.Message, text: str) -> None:
    plain_text = _plain(text)
    for i, chunk in enumerate(_chunks(plain_text)):
        try:
            if i == 0:
                bot.reply_to(message, chunk)
            else:
                bot.send_message(message.chat.id, chunk)
        except Exception as e:
            log.error("Telegram cavab xətası: %s", e)


def _auth(message_or_chat_id) -> bool:
    """Yalnız icazəli CHAT_ID-ə cavab verir."""
    if not CHAT_ID:
        return True   # CHAT_ID təyin edilməyibsə hamıya açıqdır (test rejimi)
    cid = getattr(message_or_chat_id, "chat", None)
    cid = str(cid.id) if cid else str(message_or_chat_id)
    return cid == str(CHAT_ID)


# ══════════════════════════════════════════════════════════════════════════════
#  HESABAT GENERATİRƏSİ
# ══════════════════════════════════════════════════════════════════════════════

def generate_report(report_type: str = "ANİ ANALİZ",
                    chat_id:     str | int = None) -> None:
    """
    Bütün addımları ardıcıl icra edir:
      1. Portfeli oxu
      2. data_engine.aggregate_context() — Scout + Master
      3. data_engine.build_gemini_prompt() — prompt yarat
      4. gemini_call() — analiz al
      5. Telegram-a göndər
    """
    target = chat_id or CHAT_ID
    if not target:
        log.error("CHAT_ID bilinmir — hesabat göndərilmir.")
        return

    log.info("Hesabat başladı: %s → chat %s", report_type, target)

    try:
        # Portfeli thread-safe oxu
        with _portfolio_lock:
            symbols = DINAMIK_PORTFEL + STRATEJI_PORTFEL

        if not symbols:
            safe_send(target, "⚠️ Portfel boşdur. 'skan əlavə et:BTCUSDT' komandasını istifadə et.")
            return

        # Məlumat toplama
        context = data_engine.aggregate_context(
            symbols=symbols,
            cryptopanic_token=CRYPTOPANIC_KEY,
            news_currencies="BTC,ETH",
        )

        # Prompt yarat
        prompt = data_engine.build_gemini_prompt(context=context)

        # Gemini analizi
        analysis = gemini_call(prompt)

        # Göndər
        header = f"🏛 {report_type} — M.Genat 3.1 Pro\n{'─'*40}\n"
        safe_send(target, header + analysis)

    except (ValueError, RuntimeError) as exc:
        # data_engine tərəfindən atılan gözlənilən xətalar
        log.error("Hesabat data xətası: %s", exc)
        safe_send(target, f"⚠️ Data toplanarkən xəta:\n{str(exc)[:300]}")
    except Exception as exc:
        log.error("Hesabat gözlənilməz xəta: %s", exc, exc_info=True)
        safe_send(target, f"⚠️ Gözlənilməz xəta baş verdi: {str(exc)[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
#  MENİU
# ══════════════════════════════════════════════════════════════════════════════

def main_menu() -> InlineKeyboardMarkup:
    m = InlineKeyboardMarkup(row_width=2)
    m.row(
        InlineKeyboardButton("🔭 Kripto Radar",    callback_data="show_crypto"),
        InlineKeyboardButton("🏛 Səhm Radar",     callback_data="show_stocks"),
    )
    m.row(
        InlineKeyboardButton("📊 Anlıq Analiz",   callback_data="run_report"),
        InlineKeyboardButton("ℹ️ Yardım",         callback_data="show_help"),
    )
    return m


def help_text() -> str:
    return (
        "📖 M.Genat 3.1 Pro — Komandalar\n"
        "─────────────────────────────\n"
        "/start  /menu   — Ana panel\n"
        "analiz          — Anlıq hesabat\n\n"
        "── Kripto Radar ──\n"
        "skan əlavə et:BTCUSDT\n"
        "skan sil:BTCUSDT\n\n"
        "── Səhm/ETF Radar ──\n"
        "strat əlavə et:SPY\n"
        "strat sil:SPY\n\n"
        "── Digər ──\n"
        "portfel         — Cari siyahı\n"
        "(istənilən sual → Gemini-yə göndərilir)"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM HANDLERLƏRİ
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call: telebot.types.CallbackQuery) -> None:
    if not _auth(call.message.chat.id):
        return

    data = call.data
    cid  = call.message.chat.id

    if data == "show_crypto":
        with _portfolio_lock:
            lst = list(DINAMIK_PORTFEL)
        safe_send(cid, "🔭 Kripto Radar:\n" + (", ".join(lst) if lst else "(boş)"))

    elif data == "show_stocks":
        with _portfolio_lock:
            lst = list(STRATEJI_PORTFEL)
        safe_send(cid, "🏛 Səhm/ETF Radar:\n" + (", ".join(lst) if lst else "(boş)"))

    elif data == "run_report":
        safe_send(cid, "⏳ Məlumatlar toplanır... (15–30 saniyə)")
        threading.Thread(
            target=generate_report,
            args=("ANİ ANALİZ", cid),
            daemon=True,
        ).start()

    elif data == "show_help":
        safe_send(cid, help_text())

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


@bot.message_handler(func=lambda m: True)
def handle_message(message: telebot.types.Message) -> None:
    if not _auth(message):
        return

    text  = (message.text or "").strip()
    lower = text.lower()
    cid   = message.chat.id

    # ── Menyu/Komanda ──────────────────────────────────────────────────────
    if lower in ("/start", "/menu", "menu", "radar"):
        bot.send_message(cid, "🎛 M.Genat 3.1 Pro Paneli", reply_markup=main_menu())
        return

    if lower in ("/help", "yardım", "help"):
        safe_send(cid, help_text())
        return

    if lower in ("analiz", "/analiz", "hesabat"):
        safe_reply(message, "⏳ Məlumatlar toplanır... (15–30 saniyə)")
        threading.Thread(
            target=generate_report,
            args=("ANİ ANALİZ", cid),
            daemon=True,
        ).start()
        return

    if lower == "portfel":
        with _portfolio_lock:
            d = list(DINAMIK_PORTFEL)
            s = list(STRATEJI_PORTFEL)
        safe_reply(message,
                   f"🔭 Kripto: {', '.join(d) or '(boş)'}\n"
                   f"🏛 Səhm : {', '.join(s) or '(boş)'}")
        return

    # ── Portfel idarəetməsi ────────────────────────────────────────────────
    if lower.startswith("skan əlavə et:"):
        sym = text.split(":", 1)[1].strip().upper()
        if not sym:
            safe_reply(message, "❌ Ticker boşdur."); return
        with _portfolio_lock:
            if sym not in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.append(sym)
        safe_reply(message, f"✅ Kripto Radara əlavə edildi: {sym}")
        return

    if lower.startswith("skan sil:"):
        sym = text.split(":", 1)[1].strip().upper()
        with _portfolio_lock:
            if sym in DINAMIK_PORTFEL:
                DINAMIK_PORTFEL.remove(sym)
        safe_reply(message, f"🗑 Kripto Radarından silindi: {sym}")
        return

    if lower.startswith("strat əlavə et:"):
        sym = text.split(":", 1)[1].strip().upper()
        if not sym:
            safe_reply(message, "❌ Ticker boşdur."); return
        with _portfolio_lock:
            if sym not in STRATEJI_PORTFEL:
                STRATEJI_PORTFEL.append(sym)
        safe_reply(message, f"🏛 Səhm Radarına əlavə edildi: {sym}")
        return

    if lower.startswith("strat sil:"):
        sym = text.split(":", 1)[1].strip().upper()
        with _portfolio_lock:
            if sym in STRATEJI_PORTFEL:
                STRATEJI_PORTFEL.remove(sym)
        safe_reply(message, f"🗑 Səhm Radarından silindi: {sym}")
        return

    # ── Sərbəst söhbət → Gemini ────────────────────────────────────────────
    def _bg_chat() -> None:
        persona = (
            "Sən M.Genat 3.1 Pro-san — peşəkar maliyyə analitiki. "
            "İstifadəçi sənə yazır:\n\n"
        )
        result = gemini_call(persona + text)
        safe_reply(message, result)

    threading.Thread(target=_bg_chat, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health_check():
    return "M.Genat 3.1 Pro — Live ✅", 200


@app.route("/check-models", methods=["GET"])
def check_models():
    try:
        client = _get_gemini()
        models = client.models.list()
        model_names = [m.name for m in models]
        return {"visible_models": model_names, "count": len(model_names)}, 200
    except Exception as e:
        return {"error": str(e)}, 500


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if not TELEGRAM_TOKEN:
        return "no token", 400
    raw  = flask_request.get_data(as_text=True)
    upd  = telebot.types.Update.de_json(raw)
    bot.process_new_updates([upd])
    return "ok", 200


# ══════════════════════════════════════════════════════════════════════════════
#  ZAMANLANMIŞ HESABATLAR
# ══════════════════════════════════════════════════════════════════════════════

def _sched_wrapper(report_type: str) -> None:
    """Schedule-dan çağırılan wrapper — exception-ı udmur."""
    try:
        generate_report(report_type, CHAT_ID)
    except Exception as e:
        log.error("Schedule xəta [%s]: %s", report_type, e)


def schedule_loop() -> None:
    schedule.every().day.at("06:00").do(_sched_wrapper, "SƏHƏR HESABATI")
    schedule.every().day.at("14:00").do(_sched_wrapper, "GÜNORTA HESABATI")
    schedule.every().day.at("19:00").do(_sched_wrapper, "AXŞAM HESABATI")
    log.info("Schedule hazır: 06:00 · 14:00 · 19:00 UTC")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK QURULUM
# ══════════════════════════════════════════════════════════════════════════════

def setup_webhook() -> None:
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        log.warning("TELEGRAM_TOKEN və ya WEBHOOK_URL yoxdur — webhook qurulmadı.")
        return

    wh_url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": wh_url, "drop_pending_updates": True},
            timeout=15,
        )
        data = r.json()
        if data.get("ok"):
            log.info("Webhook quruldu: %s", wh_url)
        else:
            log.error("Webhook xətası: %s", data)
    except Exception as e:
        log.error("Webhook qurulum xətası: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  BAŞLANĞIC
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("═" * 60)
    log.info("  M.Genat 3.1 Pro başlayır")
    log.info("  Port      : %d", PORT)
    log.info("  Chat ID   : %s", CHAT_ID or "⚠️ təyin edilməyib")
    log.info("  Gemini    : %s", "✅" if GEMINI_API_KEY  else "❌ yoxdur")
    log.info("  CryptoPan : %s", "✅" if CRYPTOPANIC_KEY else "❌ yoxdur")
    with _portfolio_lock:
        log.info("  Kripto    : %s", DINAMIK_PORTFEL)
        log.info("  Səhm/ETF  : %s", STRATEJI_PORTFEL)
    log.info("═" * 60)

    # Webhook qur
    setup_webhook()

    # Zamanlanmış hesabatlar ayrı thread-də
    threading.Thread(target=schedule_loop, daemon=True).start()

    # Flask serveri başlat
    app.run(host="0.0.0.0", port=PORT, debug=False)
