"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            M.Genat 5.0 Pro  ·  master_agent.py (Terminal İnterfeysi)         ║
║  YENİLİKLƏR: Pinecone RAG İnteqrasiyası və /learn (Öyrənən Yaddaş) Əmri      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
import os
import threading
import time
import datetime
from typing import Optional

import requests
import schedule
import telebot
from flask import Flask, request as flask_request
from google import genai
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import data_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  MÜHİT DƏYİŞƏNLƏRİ VƏ QLOBAL STATUSLAR
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN",      "")
CHAT_ID         = os.getenv("CHAT_ID",             "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY",      "")
WEBHOOK_URL     = os.getenv("WEBHOOK_URL",         "").rstrip("/")
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_TOKEN",   "")
PORT            = int(os.environ.get("PORT", 10000))

def _env_list(var: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(var, "").strip()
    if not raw: return list(defaults)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

_portfolio_lock  = threading.Lock()
DINAMIK_PORTFEL  = _env_list("DINAMIK_PORTFEL",  ["BTCUSDT", "ETHUSDT"])
STRATEJI_PORTFEL = _env_list("STRATEJI_PORTFEL", ["SPY", "GC=F"])

user_states = {} 
SCOUT_AUTO_ACTIVE = False 

# ══════════════════════════════════════════════════════════════════════════════
#  GEMİNİ VƏ YADDAŞ (MEMORY) MÜHƏRRİKLƏRİ
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
    strict_prompt = "DİQQƏT: Özündən heç bir rəqəm uydurma. Real dataya əsaslan.\n\n" + prompt

    for attempt in range(1, retries + 1):
        try:
            model_name = model if model.startswith("models/") else f"models/{model}"
            resp = client.models.generate_content(model=model_name, contents=strict_prompt)
            if hasattr(resp, "text") and resp.text: return resp.text.strip()
            if hasattr(resp, "candidates") and resp.candidates:
                parts = resp.candidates[0].content.parts
                if parts: return "".join(getattr(p, "text", "") for p in parts).strip()
            return "⚠️ Gemini boş cavab verdi."
        except Exception as exc:
            if attempt < retries: time.sleep(wait); wait *= 2; continue
            return f"🚨 XƏTA: {exc}"

# Qlobal RAG Yaddaş Agentini başladırıq
memory_engine = data_engine.MemoryAgent()

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

def get_utc_timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ══════════════════════════════════════════════════════════════════════════════
#  HESABAT GENERATİRƏSİ 
# ══════════════════════════════════════════════════════════════════════════════
def generate_report(report_type: str = "ANİ ANALİZ", chat_id: str | int = None, custom_symbols: list[str] = None) -> None:
    target = chat_id or CHAT_ID
    if not target: return
    try:
        if custom_symbols is not None: symbols = custom_symbols
        else:
            with _portfolio_lock: symbols = DINAMIK_PORTFEL + STRATEJI_PORTFEL
            
        if not symbols:
            safe_send(target, "⚠️ Seçilmiş portfel boşdur. Əvvəlcə panelden aktiv əlavə edin.")
            return

        context = data_engine.aggregate_context(symbols=symbols, cryptopanic_token=CRYPTOPANIC_KEY, llm_callback=gemini_call)
        prompt = data_engine.build_gemini_prompt(context=context)
        analysis = gemini_call(prompt)

        header = f"🏛 **{report_type}** — M.Genat 5.0 Pro\n{'─'*40}\n"
        safe_send(target, header + analysis, parse_mode="Markdown")

    except Exception as exc:
        safe_send(target, f"⚠️ Xəta baş verdi: {str(exc)[:200]}")

# ══════════════════════════════════════════════════════════════════════════════
#  İNTERAKTİV İDARƏETMƏ PANELİ (UI)
# ══════════════════════════════════════════════════════════════════════════════
def main_menu() -> InlineKeyboardMarkup:
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("🔭 Kripto Radarı", callback_data="menu_crypto"),
          InlineKeyboardButton("🏛 Səhm/ETF Radarı", callback_data="menu_stocks"))
    m.add(InlineKeyboardButton("🔬 Scout Analizi (Kripto)", callback_data="run_scout"),
          InlineKeyboardButton("🌍 Master Analizi (Səhm)", callback_data="run_master"))
    m.add(InlineKeyboardButton("⚖️ Tam Hakim Analizi (Ümumi)", callback_data="run_judge"))
    
    status_icon = "🟢 AÇIQ" if SCOUT_AUTO_ACTIVE else "🔴 BAĞLI"
    m.add(InlineKeyboardButton(f"🤖 Avtopilot Scout: {status_icon}", callback_data="toggle_scout"))
    m.add(InlineKeyboardButton("ℹ️ Məlumat / Yardım", callback_data="show_help"))
    return m

def portfolio_menu(ptype: str) -> InlineKeyboardMarkup:
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("➕ Əlavə et", callback_data=f"add_{ptype}"),
          InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{ptype}"))
    m.add(InlineKeyboardButton("⬅️ Ana Panel", callback_data="main_menu"))
    return m

def build_delete_keyboard(ptype: str) -> InlineKeyboardMarkup:
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
    welcome = "🏛 **M.Genat 5.0 Pro İdarəetmə Mərkəzi**\nXoş gəldiniz. Mühərrik aktivdir.\nZəhmət olmasa, əməliyyat seçin:"
    bot.send_message(message.chat.id, welcome, reply_markup=main_menu(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call: telebot.types.CallbackQuery) -> None:
    if not _auth(call.message.chat.id): return
    data, cid, msg_id = call.data, call.message.chat.id, call.message.message_id

    if data == "main_menu":
        user_states.pop(cid, None)
        bot.edit_message_text("🎛 **Ana Panel**", chat_id=cid, message_id=msg_id, reply_markup=main_menu(), parse_mode="Markdown")

    elif data == "menu_crypto":
        with _portfolio_lock: lst = list(DINAMIK_PORTFEL)
        bot.edit_message_text("🔭 **Kripto Radarı:**\n`" + ("`\n`".join(lst) if lst else "Boşdur.") + "`", chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu("crypto"), parse_mode="Markdown")

    elif data == "menu_stocks":
        with _portfolio_lock: lst = list(STRATEJI_PORTFEL)
        bot.edit_message_text("🏛 **Səhm Radarı:**\n`" + ("`\n`".join(lst) if lst else "Boşdur.") + "`", chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu("stocks"), parse_mode="Markdown")

    elif data.startswith("add_"):
        ptype = data.split("_")[1]
        user_states[cid] = f"wait_add_{ptype}"
        bot.edit_message_text("✍️ **Tikeri yazın:** (Məsələn: `BTCUSDT` və ya `NNE`)", chat_id=cid, message_id=msg_id, parse_mode="Markdown")

    elif data.startswith("del_"):
        ptype = data.split("_")[1]
        bot.edit_message_text("🗑 **Hansı aktivi silmək istəyirsiniz?**", chat_id=cid, message_id=msg_id, reply_markup=build_delete_keyboard(ptype), parse_mode="Markdown")

    elif data.startswith("rm_"):
        parts = data.split("_")
        ptype, sym = parts[1], parts[2]
        with _portfolio_lock:
            if ptype == "crypto" and sym in DINAMIK_PORTFEL: DINAMIK_PORTFEL.remove(sym)
            elif ptype == "stocks" and sym in STRATEJI_PORTFEL: STRATEJI_PORTFEL.remove(sym)
        lst = DINAMIK_PORTFEL if ptype == "crypto" else STRATEJI_PORTFEL
        bot.edit_message_text(f"✅ {sym} silindi!\n🔭 **Radar:**\n`" + ("`\n`".join(lst) if lst else "Boşdur.") + "`", chat_id=cid, message_id=msg_id, reply_markup=portfolio_menu(ptype), parse_mode="Markdown")

    elif data == "run_scout":
        with _portfolio_lock: syms = list(DINAMIK_PORTFEL)
        bot.edit_message_text("⏳ 🔬 SCOUT REJİMİ başladılır...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Kripto (Scout) Analizi", cid, syms), daemon=True).start()

    elif data == "run_master":
        with _portfolio_lock: syms = list(STRATEJI_PORTFEL)
        bot.edit_message_text("⏳ 🌍 MASTER REJİMİ başladılır...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Səhm/Makro (Master) Analizi", cid, syms), daemon=True).start()

    elif data == "run_judge":
        bot.edit_message_text("⏳ ⚖️ HAKİM REJİMİ başladılır...", chat_id=cid, message_id=msg_id, parse_mode="Markdown")
        threading.Thread(target=generate_report, args=("Tam Hakim Analizi (Ümumi)", cid, None), daemon=True).start()

    elif data == "toggle_scout":
        global SCOUT_AUTO_ACTIVE
        SCOUT_AUTO_ACTIVE = not SCOUT_AUTO_ACTIVE
        status = "Aktivləşdirildi 🟢" if SCOUT_AUTO_ACTIVE else "Deaktiv edildi 🔴"
        bot.answer_callback_query(call.id, f"Avtopilot {status}", show_alert=True)
        bot.edit_message_reply_markup(chat_id=cid, message_id=msg_id, reply_markup=main_menu())

    elif data == "show_help":
        help_txt = "📖 *Yardım Mərkəzi*\n`/judge NNE` yazaraq dərin analiz edə bilərsiniz.\n`/learn Mətn` yazaraq bota dərs keçə bilərsiniz."
        m = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Ana Panel", callback_data="main_menu"))
        bot.edit_message_text(help_txt, chat_id=cid, message_id=msg_id, reply_markup=m, parse_mode="Markdown")
    try: bot.answer_callback_query(call.id)
    except Exception: pass

@bot.message_handler(func=lambda m: True)
def handle_message(message: telebot.types.Message) -> None:
    if not _auth(message): return
    text, cid = (message.text or "").strip(), message.chat.id

    if cid in user_states:
        state = user_states[cid]
        if text.startswith("/"): user_states.pop(cid, None)
        elif state.startswith("wait_add_"):
            ptype = state.split("_")[2]
            syms = [s.strip().upper() for s in text.split(",")]
            with _portfolio_lock:
                added = []
                for s in syms:
                    if s:
                        if ptype == "crypto" and s not in DINAMIK_PORTFEL: DINAMIK_PORTFEL.append(s); added.append(s)
                        elif ptype == "stocks" and s not in STRATEJI_PORTFEL: STRATEJI_PORTFEL.append(s); added.append(s)
            user_states.pop(cid, None) 
            bot.send_message(cid, f"✅ Uğurla əlavə edildi: `{', '.join(added)}`", reply_markup=main_menu(), parse_mode="Markdown")
            return

    lower = text.lower()
    
    # ⚖️ HAKİM ƏMRİ
    if lower.startswith("/judge"):
        symbols_str = text.replace("/judge", "").strip()
        custom_symbols = [s.strip().upper() for s in symbols_str.split(",")] if symbols_str else None
        safe_reply(message, "⏳ ⚖️ *HAKİM REJİMİ:* Xüsusi analiz başladılır...", parse_mode="Markdown")
        threading.Thread(target=generate_report, args=(f"HAKİM ANALİZİ ({symbols_str or 'Portfel'})", cid, custom_symbols), daemon=True).start()
        return

    # 🧠 ÖYRƏNƏN YADDAŞ (LEARN) ƏMRİ
    if lower.startswith("/learn"):
        lesson_text = text.replace("/learn", "", 1).strip()
        if not lesson_text:
            safe_reply(message, "⚠️ Nəyi öyrənməli olduğumu yazmadın. Məsələn: `/learn 2024-də DXY qalxanda Qızıl düşmədi çünki...`", parse_mode="Markdown")
            return
            
        safe_reply(message, "⏳ 🧠 **Yaddaşa Yazılır:** Dərs vektorlaşdırılıb Pinecone bazasına göndərilir...", parse_mode="Markdown")
        
        def _write_memory():
            try:
                if not memory_engine.index:
                    safe_reply(message, "⚠️ Pinecone bazası aktiv deyil. API Key yoxla.")
                    return
                
                vector = memory_engine._get_embedding(lesson_text)
                if not vector:
                    safe_reply(message, "⚠️ Mətni riyazi vektora çevirə bilmədim (Embedding xətası).")
                    return
                
                lesson_id = f"lesson_{int(time.time())}"
                
                memory_engine.index.upsert(
                    vectors=[{
                        "id": lesson_id, 
                        "values": vector, 
                        "metadata": {"text": lesson_text, "type": "user_lesson", "date": get_utc_timestamp()}
                    }]
                )
                safe_reply(message, f"✅ **Dərs Uğurla Yaddaşa Həkk Olundu!** (ID: `{lesson_id}`)\nBundan sonra Hakim (Judge) bu ssenari ilə qarşılaşanda sənin dərsinə istinad edəcək.", parse_mode="Markdown")
            except Exception as e:
                safe_reply(message, f"🚨 Yaddaşa yazma xətası: {e}")
                
        threading.Thread(target=_write_memory, daemon=True).start()
        return

    if lower in ("/start", "/menu", "menu", "panel"):
        bot.send_message(cid, "🎛 **M.Genat 5.0 Pro Paneli**", reply_markup=main_menu(), parse_mode="Markdown")
        return

    def _bg_chat() -> None:
        safe_reply(message, gemini_call("Sən M.Genat 5.0 Pro-san. Qısa və peşəkar cavab ver:\n\n" + text), parse_mode="Markdown")
    threading.Thread(target=_bg_chat, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  SERVER VƏ MÜHƏRRİKLƏR
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def health_check(): return "M.Genat 5.0 Pro Panel — Live ✅", 200

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if flask_request.headers.get('content-type') == 'application/json':
        json_string = flask_request.get_data().decode('utf-8')
        upd = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([upd])
        return '', 200
    return 'Forbidden', 403

def _sched_wrapper(report_type: str) -> None:
    try: generate_report(report_type, CHAT_ID, None)
    except Exception as e: log.error("Schedule xəta: %s", e)

def schedule_loop() -> None:
    schedule.every().day.at("06:00").do(_sched_wrapper, "SƏHƏR HESABATI (Tam Hakim)")
    schedule.every().day.at("14:00").do(_sched_wrapper, "GÜNORTA HESABATI (Tam Hakim)")
    schedule.every().day.at("19:00").do(_sched_wrapper, "AXŞAM HESABATI (Tam Hakim)")
    while True:
        schedule.run_pending()
        time.sleep(30)

def keep_alive_loop():
    while True:
        if WEBHOOK_URL:
            try: requests.get(WEBHOOK_URL, timeout=10)
            except Exception: pass
        time.sleep(10 * 60)

def autonomous_scout_loop():
    global SCOUT_AUTO_ACTIVE
    while True:
        if not SCOUT_AUTO_ACTIVE:
            time.sleep(60) 
            continue
            
        now = datetime.datetime.utcnow()
        is_active_market = (8 <= now.hour <= 21) and (now.weekday() < 5)
        sleep_interval = (5 * 60) if is_active_market else (30 * 60)
        
        try:
            if now.minute < 5: 
                macro_report = data_engine.build_consensus_report(asset=None, llm_callback=gemini_call)
                safe_send(CHAT_ID, f"🌍 **ÜMUMİ BAZAR KONSENSUSU**\n{'─'*30}\n{macro_report}", parse_mode="Markdown")

            with _portfolio_lock:
                syms = list(DINAMIK_PORTFEL) + list(STRATEJI_PORTFEL)
                
            anomalies = data_engine.check_anomalies(syms)
            
            if anomalies:
                for anomaly in anomalies:
                    asset_name = anomaly.split(" - ")[0].replace("⚠️", "").replace("**", "").strip()
                    micro_report = data_engine.build_consensus_report(asset=asset_name, llm_callback=gemini_call)
                    msg = f"🚨 **SİQNAL: AKTİV TƏSİRİ ({asset_name})** 🚨\n{anomaly}\n{'─'*30}\n{micro_report}"
                    safe_send(CHAT_ID, msg, parse_mode="Markdown")
                
        except Exception as e:
            log.error(f"Avtopilot xətası: {e}")
            
        time.sleep(sleep_interval)

def setup_connection() -> None:
    try:
        bot.remove_webhook()
        time.sleep(1)
        if WEBHOOK_URL:
            wh_url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
            bot.set_webhook(url=wh_url, drop_pending_updates=True)
            log.info(f"✅ Webhook yenidən quruldu: {wh_url}")
        else:
            threading.Thread(target=bot.infinity_polling, daemon=True).start()
    except Exception as e: log.error(f"Bağlantı xətası: {e}")

if __name__ == "__main__":
    setup_connection()
    threading.Thread(target=schedule_loop, daemon=True).start()
    threading.Thread(target=autonomous_scout_loop, daemon=True).start()
    threading.Thread(target=keep_alive_loop, daemon=True).start() 
    app.run(host="0.0.0.0", port=PORT, debug=False)
