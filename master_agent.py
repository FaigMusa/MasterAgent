"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            M.Genat 4.1 Pro  ·  master_agent.py (Terminal İnterfeysi)         ║
║                                                                              ║
║   Telegram Bot · Flask Webhook · Gemini API                                  ║
║   Zamanlanmış hesabatlar · Scout/Master analiz · İNTERAKTİV PORTFEL          ║
╚══════════════════════════════════════════════════════════════════════════════╝
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

# Botun hansı əməliyyatı gözlədiyini yadda saxlamaq üçün (State)
user_states = {} 

# ══════════════════════════════════════════════════════════════════════════════
#  GEMİNİ VƏ YARDIMÇILAR
# ══════════════════════════════════════════════════════════════════════════════
_gemini_client: Optional[genai.Client] = None

def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client

def gemini_call(prompt: str, model: str = "gemini-2.5-flash", retries: int = 3) -> str:
    if not GEMINI_API_KEY: return "❌ GEMINI_API_KEY yoxdur."
    client = _get_gemini()
    wait = 5
    last_error = ""

    for attempt in range(1, retries + 1):
        try:
            model_name = model if model.startswith("models/") else f"models/{model}"
            resp = client.models.generate_content(model=model_name, contents=prompt)
            if hasattr(resp, "text") and resp.text: return resp.text.strip()
            if hasattr(resp, "candidates") and resp.candidates:
                parts = resp.candidates[0].content.parts
                if parts: return "".join(getattr(p, "text", "") for p in parts).strip()
            return "⚠️ Gemini boş cavab verdi."
        except Exception as exc:
            last_error = str(exc)
            err = last_error.lower()
            if any(code in err for code in ("api_key", "400", "403")): return f"❌ API Xətası: {last_error}"
            if any(code in err for code in ("429", "quota")):
                if attempt < retries: time.sleep(wait); wait *= 2; continue
            if attempt < retries: time.sleep(wait); wait *= 2; continue
    return f"🚨 XƏTA: {last_error}"

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
app = Flask(__name__)

def _chunks(text: str, size: int = 4000) -> list[str]:
    parts = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2: cut = size
        parts.append(text[:cut])
        text = text[cut:]
    if text: parts.append(text)
    return parts

def safe_send(chat_id: str | int, text: str, parse_mode: str = None) -> None:
    for chunk in _chunks(text):
        try: bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception: bot.send_message(chat_id, chunk.replace("*", "").replace("_", ""))

def safe_reply(message: telebot.types.Message, text: str, parse_mode: str = None) -> None:
    for i, chunk in enumerate(_chunks(text)):
        try:
            if i == 0: bot.reply_to(message, chunk, parse_mode=parse_mode)
            else: bot.send_message(message.chat.id, chunk, parse_mode=parse_mode)
        except Exception: bot.send_message(message.chat.id, chunk.replace("*", "").replace("_", ""))

def _auth(message_or_chat_id) -> bool:
    if not CHAT_ID: return True
    cid = getattr(message_or_chat_id, "chat", None)
    cid = str(cid.id) if cid else str(message_or_chat_id)
    return cid == str(CHAT_ID)

# ══════════════════════════════════════════════════════════════════════════════
#  HESABAT GENERATİRƏSİ
# ══════════════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "ANİ ANALİZ", chat_id: str | int = None, custom_symbols: list[str] = None) -> None:
    target = chat_id or CHAT_ID
    if not target: return

    try:
        # custom_symbols göndərilibsə onu istifadə et, yoxsa hamısını götür (Hakim Rejimi üçün)
        if custom_symbols is not None: 
            symbols = custom_symbols
        else:
            with _portfolio_lock: symbols = DINAMIK_PORTFEL + STRATEJI_PORTFEL
            
        if not symbols:
            safe_send(target, "⚠️ Seçilmiş portfel boşdur. Əvvəlcə panelden aktiv əlavə edin.")
            return

        context = data_engine.aggregate_context(symbols=symbols, cryptopanic_token=CRYPTOPANIC_KEY)
        prompt = data_engine.build_gemini_prompt(context=context)
        analysis = gemini_call(prompt)

        header = f"🏛 **{report_type}** — M.Genat 4.0 Pro\n{'─'*40}\n"
        safe_send(target, header + analysis, parse_mode="Markdown")

    except Exception as exc:
        safe_send(target, f"⚠️ Xəta baş verdi: {str(exc)[:200]}")

# ══════════════════════════════════════════════════════════════════════════════
#  İNTERAKTİV İDARƏETMƏ PANELİ (UI)
# ══════════════════════════════════════════════════════════════════════════════

def main_menu() -> InlineKeyboardMarkup:
    """Əsas İdarəetmə Paneli"""
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("🔭 Kripto Radarı", callback_data="menu_crypto"),
        InlineKeyboardButton("🏛 Səhm/ETF Radarı", callback_data="menu_stocks")
    )
    # Yeni Ayrılmış Analiz Düymələri
    m.add(
        InlineKeyboardButton("🔬 Scout Analizi (Kripto)", callback_data="run_scout"),
        InlineKeyboardButton("🌍 Master Analizi (Səhm)", callback_data="run_master")
    )
    # Ümumi (Hakim) Analiz Düyməsi
    m.add(
        InlineKeyboardButton("⚖️ Tam Hakim Analizi (Ümumi Portfel)", callback_data="run_judge")
    )
    m.add(
        InlineKeyboardButton("ℹ️ Məlumat / Yardım", callback_data="show_help")
    )
    return m

def portfolio_menu(ptype: str) -> InlineKeyboardMarkup:
    """Alt menyu: Siyahı, Əlavə et, Sil, Geri"""
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("➕ Əlavə et", callback_data=f"add_{ptype}"),
        InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{ptype}")
    )
    m.add(InlineKeyboardButton("⬅️ Ana Panelə Qayıt", callback_data="main_menu"))
    return m

def build_delete_keyboard(ptype: str) -> InlineKeyboardMarkup:
    """Silmək üçün mövcud aktivlərin düymələrini yaradır."""
    m = InlineKeyboardMarkup(row_width=3)
    with _portfolio_lock:
        lst = DINAMIK_PORTFEL if ptype == "crypto" else STRATEJI_PORTFEL
    
    buttons = [InlineKeyboardButton(sym, callback_data=f"rm_{ptype}_{sym}") for sym in lst]
    m.add(*buttons)
    m.add(InlineKeyboardButton("⬅️ Geri", callback_data=f"menu_{ptype}"))
    return m

# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM HANDLERLƏRİ
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['start', 'menu', 'panel'])
def send_panel(message):
    if not _auth(message): return
    user_states.pop(message.chat.id, None)
    
    welcome = (
        "🏛 **M.Genat 4.0 Pro İdarəetmə Mərkəzi**\n"
        "Xoş gəldiniz. Mühərrik aktivdir.\n"
        "Zəhmət olmasa, əməliyyat seçin:"
    )
    bot.send_message(message.chat.id, welcome, reply_markup=main_menu(), parse_mode="Markdown")


@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call: telebot.types.CallbackQuery) -> None:
    if not _auth(call.message.chat.id): return
    
    data = call.data
    cid = call.message.chat.id
    msg_id = call.message.message_id

    # ── ANA MENYULAR ──
    if data == "main_menu":
        user_states.pop(cid, None)
        bot.edit_message_text("🎛 **Ana Panel**", chat_id=cid, message_id=msg_id, reply_markup=main_menu(), parse_mode="Markdown")

    elif data == "menu_crypto":
        with _portfolio_lock: lst = list(DINAMIK_PORTFEL)
        text = "🔭 **Kripto Radarı (Aktiv İzlənilənlər):**\n`" + ("`\n`".join(lst) if lst else "Siyahı boşdur.") + "`\n\nNə etmək istəyirsiniz?"
        bot.edit_message_text(text, chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu("crypto"), parse_mode="Markdown")

    elif data == "menu_stocks":
        with _portfolio_lock: lst = list(STRATEJI_PORTFEL)
        text = "🏛 **Səhm/ETF Radarı (Aktiv İzlənilənlər):**\n`" + ("`\n`".join(lst) if lst else "Siyahı boşdur.") + "`\n\nNə etmək istəyirsiniz?"
        bot.edit_message_text(text, chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu("stocks"), parse_mode="Markdown")

    # ── ƏLAVƏ ETMƏK ──
    elif data.startswith("add_"):
        ptype = data.split("_")[1]
        user_states[cid] = f"wait_add_{ptype}"
        text = (
            "✍️ **Əlavə etmək istədiyiniz tikeri yazın.**\n"
            "(Məsələn: `BTCUSDT` və ya `SOLUSDT`)\n"
            "_Təxirə salmaq üçün /menu yazın._"
        )
        bot.edit_message_text(text, chat_id=cid, message_id=msg_id, parse_mode="Markdown")

    # ── SİLMƏK ÜÇÜN MENYU ──
    elif data.startswith("del_"):
        ptype = data.split("_")[1]
        bot.edit_message_text("🗑 **Hansı aktivi silmək istəyirsiniz?** Aşağıdan seçin:", chat_id=cid, message_id=msg_id, reply_markup=build_delete_keyboard(ptype), parse_mode="Markdown")

    # ── FİKTİKİ SİLMƏ ƏMƏLİYYATI ──
    elif data.startswith("rm_"):
        parts = data.split("_")
        ptype = parts[1]
        sym = parts[2]
        
        with _portfolio_lock:
            if ptype == "crypto" and sym in DINAMIK_PORTFEL: DINAMIK_PORTFEL.remove(sym)
            elif ptype == "stocks" and sym in STRATEJI_PORTFEL: STRATEJI_PORTFEL.remove(sym)
                
        bot.answer_callback_query(call.id, f"✅ {sym} radarından silindi!")
        lst = DINAMIK_PORTFEL if ptype == "crypto" else STRATEJI_PORTFEL
        bot.edit_message_text(f"🔭 **Yenilənmiş Radar:**\n`" + ("`\n`".join(lst) if lst else "Siyahı boşdur.") + "`", chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu(ptype), parse_mode="Markdown")

    # ── AYRILMIŞ ANALİZLƏR (YENİ) ──
    elif data == "run_scout":
        with _portfolio_lock: syms = list(DINAMIK_PORTFEL)
        bot.edit_message_text("⏳ 🔬 *SCOUT REJİMİ:* Yalnız Kripto aktivləri üçün texniki analiz başladılır...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Kripto (Scout) Analizi", cid, syms), daemon=True).start()

    elif data == "run_master":
        with _portfolio_lock: syms = list(STRATEJI_PORTFEL)
        bot.edit_message_text("⏳ 🌍 *MASTER REJİMİ:* Yalnız Səhm və Makro aktivləri üçün analiz başladılır...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Səhm/Makro (Master) Analizi", cid, syms), daemon=True).start()

    elif data == "run_judge":
        bot.edit_message_text("⏳ ⚖️ *HAKİM REJİMİ:* Bütün portfel (Kripto + Səhm) və makro korelyasiyalar oxunur...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Tam Hakim Analizi (Ümumi)", cid, None), daemon=True).start()

    # ── YARDIM ──
    elif data == "show_help":
        help_txt = (
            "📖 *Yardım Mərkəzi*\n"
            "Siz düymələrlə portfelinizi tam idarə edə bilərsiniz.\n\n"
            "Spesifik analizlər üçün birbaşa mesaj yaza bilərsiniz:\n"
            "`/judge SOLUSDT` — Yalnız SOL üçün Hakim Analizi.\n\n"
            "Və ya M.Genat ilə birbaşa dərdləşmək üçün normal cümlə yazın."
        )
        m = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Ana Panelə Qayıt", callback_data="main_menu"))
        bot.edit_message_text(help_txt, chat_id=cid, message_id=msg_id, reply_markup=m, parse_mode="Markdown")

    try: bot.answer_callback_query(call.id)
    except Exception: pass


@bot.message_handler(func=lambda m: True)
def handle_message(message: telebot.types.Message) -> None:
    if not _auth(message): return
    text  = (message.text or "").strip()
    cid   = message.chat.id

    # ── 1. STATE YOXLAMASI (Əlavə etmə rejimi) ──
    if cid in user_states:
        state = user_states[cid]
        if text.startswith("/"):
            user_states.pop(cid, None)
        elif state.startswith("wait_add_"):
            ptype = state.split("_")[2]
            syms = [s.strip().upper() for s in text.split(",")]
            
            with _portfolio_lock:
                added = []
                for s in syms:
                    if s:
                        if ptype == "crypto" and s not in DINAMIK_PORTFEL:
                            DINAMIK_PORTFEL.append(s)
                            added.append(s)
                        elif ptype == "stocks" and s not in STRATEJI_PORTFEL:
                            STRATEJI_PORTFEL.append(s)
                            added.append(s)
            
            user_states.pop(cid, None) 
            bot.send_message(cid, f"✅ Uğurla əlavə edildi: `{', '.join(added)}`", reply_markup=main_menu(), parse_mode="Markdown")
            return

    lower = text.lower()
    
    # ── STANDART KOMANDALAR ──
    if lower.startswith("/judge"):
        symbols_str = text.replace("/judge", "").strip()
        custom_symbols = [s.strip().upper() for s in symbols_str.split(",")] if symbols_str else None
        safe_reply(message, "⏳ ⚖️ *HAKİM REJİMİ:* Xüsusi analiz başladılır...", parse_mode="Markdown")
        threading.Thread(target=generate_report, args=(f"HAKİM ANALİZİ ({symbols_str or 'Portfel'})", cid, custom_symbols), daemon=True).start()
        return

    if lower in ("/start", "/menu", "menu", "panel"):
        bot.send_message(cid, "🎛 **M.Genat 4.0 Pro Paneli**", reply_markup=main_menu(), parse_mode="Markdown")
        return

    # ── 3. SƏRBƏST SÖHBƏT (Gemini) ──
    def _bg_chat() -> None:
        persona = "Sən M.Genat 4.0 Pro-san. Qısa və peşəkar cavab ver:\n\n"
        result = gemini_call(persona + text)
        safe_reply(message, result, parse_mode="Markdown")

    threading.Thread(target=_bg_chat, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  SERVER VƏ ZAMANLANMIŞ HESABATLAR
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health_check(): return "M.Genat 4.0 Pro Panel — Live ✅", 200

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if not TELEGRAM_TOKEN: return "no token", 400
    raw  = flask_request.get_data(as_text=True)
    upd  = telebot.types.Update.de_json(raw)
    bot.process_new_updates([upd])
    return "ok", 200

def _sched_wrapper(report_type: str) -> None:
    try: generate_report(report_type, CHAT_ID, None) # Zamanlanmışlar həmişə Ümumi (Hakim) olur
    except Exception as e: log.error("Schedule xəta: %s", e)

def schedule_loop() -> None:
    schedule.every().day.at("06:00").do(_sched_wrapper, "SƏHƏR HESABATI (Tam Hakim)")
    schedule.every().day.at("14:00").do(_sched_wrapper, "GÜNORTA HESABATI (Tam Hakim)")
    schedule.every().day.at("19:00").do(_sched_wrapper, "AXŞAM HESABATI (Tam Hakim)")
    while True:
        schedule.run_pending()
        time.sleep(30)

def setup_webhook() -> None:
    if not TELEGRAM_TOKEN or not WEBHOOK_URL: return
    wh_url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": wh_url, "drop_pending_updates": True}, timeout=15)
        log.info("Webhook quruldu" if r.json().get("ok") else f"Webhook xətası: {r.json()}")
    except Exception as e: log.error("Webhook xəta: %s", e)

if __name__ == "__main__":
    setup_webhook()
    threading.Thread(target=schedule_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
