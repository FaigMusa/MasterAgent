import logging
import os
import threading
import time
import requests
import schedule
import telebot
from flask import Flask, request as flask_request
from google import genai
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
import data_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID         = os.getenv("CHAT_ID")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL     = os.getenv("WEBHOOK_URL", "").rstrip("/")
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_TOKEN", "")

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
app    = Flask(__name__)
_lock  = threading.Lock()

def get_env_list(var_name, defaults):
    raw = os.getenv(var_name, "")
    if not raw.strip(): return defaults
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

DINAMIK_PORTFEL  = get_env_list("DINAMIK_PORTFEL",  ["BTCUSDT", "ETHUSDT"])
STRATEJI_PORTFEL = get_env_list("STRATEJI_PORTFEL", ["SPY", "GC=F"])

def strip_md(text):
    return text.replace("**", "").replace("*", "").replace("`", "").replace("_", "")

def safe_send(chat_id, text):
    try: bot.send_message(chat_id, text)
    except Exception as e:
        if "parse" in str(e).lower(): bot.send_message(chat_id, strip_md(text))

def safe_reply(message, text):
    try: bot.reply_to(message, text)
    except Exception as e:
        if "parse" in str(e).lower(): bot.reply_to(message, strip_md(text))

def gemini_call(prompt, retries=2):
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            return resp.text
        except Exception as e:
            err = str(e).lower()
            log.error(f"Gemini xətası: {err}")
            if "429" in err or "quota" in err or "exhausted" in err: 
                time.sleep(5)
            elif "api_key" in err or "400" in err or "403" in err: 
                return "❌ API açarı xətası."
            else: 
                time.sleep(5)
    return "⏳ Gemini API cavab vermir. Daha sonra cəhd edin."

@app.route("/", methods=["GET"])
def health(): return "M.Genat 3.1 Pro Live"

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "ok", 200

def generate_report(report_type="ANİ ANALİZ", chat_id=CHAT_ID):
    try:
        with _lock: symbols = DINAMIK_PORTFEL + STRATEJI_PORTFEL
        data_dict = data_engine.aggregate_context(symbols=symbols, cryptopanic_token=CRYPTOPANIC_KEY)
        prompt = data_engine.build_gemini_prompt(context=data_dict)
        analiz = gemini_call(prompt)
        safe_send(chat_id, f"🏛️ {report_type} (M.Genat 3.1 Pro)\n\n{analiz}")
    except Exception as e:
        log.error(f"Hesabat xətası: {e}")
        safe_send(chat_id, f"⚠️ Məlumat toplanarkən xəta: {str(e)[:250]}")

def schedule_loop():
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR HESABATI")
    schedule.every().day.at("17:00").do(generate_report, report_type="AXŞAM HESABATI")
    while True:
        schedule.run_pending()
        time.sleep(30)

def main_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🔭 Kripto Radar", callback_data="show_crypto"),
          InlineKeyboardButton("🏛️ Səhm Radar", callback_data="show_stocks"))
    m.row(InlineKeyboardButton("📊 Dərin Analiz (Anlıq)", callback_data="run_report"))
    return m

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.message.chat.id) != str(CHAT_ID): return
    
    if call.data == "show_crypto":
        safe_send(call.message.chat.id, f"🔭 Kripto: {', '.join(DINAMIK_PORTFEL)}")
    elif call.data == "show_stocks":
        safe_send(call.message.chat.id, f"🏛️ Səhm: {', '.join(STRATEJI_PORTFEL)}")
    elif call.data == "run_report":
        safe_send(call.message.chat.id, "⏳ Məlumatlar toplanır... (15-20 saniyə)")
        threading.Thread(target=generate_report, args=("ANİ ANALİZ", call.message.chat.id), daemon=True).start()
    
    try: bot.answer_callback_query(call.id)
    except: pass

# DÜZƏLİŞ: BÜTÜN MESAJLARI VƏ KOMANDALARI TƏK BİR BLOKDA İDARƏ EDİRİK
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    
    text = message.text.strip()
    msg_l = text.lower()

    # 1. Menyu Komandaları
    if msg_l in ("/start", "/menu", "radar"):
        bot.send_message(message.chat.id, "🎛 M.Genat 3.1 Pro Paneli", reply_markup=main_menu())
        return

    # 2. Analiz Əmri
    if msg_l == "analiz":
        safe_reply(message, "⏳ Məlumatlar toplanır... (15-20 saniyə)")
        threading.Thread(target=generate_report, args=("ANİ ANALİZ", message.chat.id), daemon=True).start()
        return

    # 3. Radar İdarəetməsi
    if msg_l.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL: DINAMIK_PORTFEL.append(yeni)
        safe_reply(message, f"✅ Kripto Radara əlavə edildi: {yeni}")
        return

    if msg_l.startswith("skan sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in DINAMIK_PORTFEL: DINAMIK_PORTFEL.remove(sil)
        safe_reply(message, f"🗑️ Kripto Radarından silindi: {sil}")
        return

    if msg_l.startswith("strat əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in STRATEJI_PORTFEL: STRATEJI_PORTFEL.append(yeni)
        safe_reply(message, f"🏛️ Səhm Radarına əlavə edildi: {yeni}")
        return

    if msg_l.startswith("strat sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in STRATEJI_PORTFEL: STRATEJI_PORTFEL.remove(sil)
        safe_reply(message, f"🗑️ Səhm Radarından silindi: {sil}")
        return

    # 4. Əgər yuxarıdakıların heç biridirsə, Gemini-yə göndər (Sərbəst Söhbət)
    def _bg():
        res = gemini_call(f"Sən M.Genat 3.1 Pro-san. Phill sənə yazır: {text}")
        safe_reply(message, res)
    
    threading.Thread(target=_bg, daemon=True).start()

if __name__ == '__main__':
    print("M.Genat 3.1 Pro işə düşür...")
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"})
    threading.Thread(target=schedule_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
