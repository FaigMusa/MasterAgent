import time
import threading
import schedule
import requests
import re
import os
from google import genai
from flask import Flask, request as flask_request, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# BİZİM YENİ MƏLUMAT KARXANAMIZ
import data_engine

# ─────────────────────────── TƏNZİMLƏMƏLƏR ────────────────────────────
TELEGRAM_TOKEN  = os.getenv('TELEGRAM_TOKEN')
CHAT_ID         = os.getenv('CHAT_ID')
GEMINI_API_KEY  = os.getenv('GEMINI_API_KEY')
WEBHOOK_URL     = os.getenv('WEBHOOK_URL', '').rstrip('/')
CRYPTOPANIC_KEY = os.getenv('CRYPTOPANIC_TOKEN') # Əgər yoxdursa, problem deyil.

_raw = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash').strip().replace('"', '').replace("'", "")
GEMINI_MODEL = _raw.replace('models/', '')

client = genai.Client(api_key=GEMINI_API_KEY)
bot    = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
_lock  = threading.Lock()

# ─────────────────────────── RADARLAR (İzlənilən Aktivlər) ────────────────────
def get_env_list(var_name, default_values):
    """Environment variable-dan vergüllə ayrılmış siyahını oxuyur."""
    raw_val = os.getenv(var_name)
    if not raw_val:
        return default_values
    # Boşluqları təmizləyir və siyahıya çevirir
    return [x.strip().upper() for x in raw_val.split(',')]

# Əgər Env Var tapılmasa, mötərizədəki default-lar aktiv olacaq
DINAMIK_PORTFEL = get_env_list('DINAMIK_PORTFEL', ["BTCUSDT", "ETHUSDT"])
STRATEJI_PORTFEL = get_env_list('STRATEJI_PORTFEL', ["SPY", "GC=F"])

# ═══════════════════════════════════════════════════════════════════════
#  GEMINI CALL (Analiz Mühərriki)
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
            if '404' in err_str or 'NOT_FOUND' in err_str:
                current_model = 'gemini-1.5-flash'
                continue
            elif '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                match = re.search(r'retryDelay.*?(\d+(?:\.\d+)?)\s*s', err_str)
                wait = int(float(match.group(1))) + 5 if match else delay
                time.sleep(wait)
            else:
                return f"⏳ Sistem müvəqqəti yüklüdür."
    return "⏳ Kvota dolub, lütfən gözləyin."

# ═══════════════════════════════════════════════════════════════════════
#  FLASK SERVER & WEBHOOK
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health(): return f"M.Genat 3.0 Aktivdir (Data Engine qoşulub)."

@app.route(f'/webhook/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if flask_request.headers.get('content-type') != 'application/json': abort(403)
    update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
    bot.process_new_updates([update])
    return 'ok', 200

def register_webhook():
    if not WEBHOOK_URL: return False
    url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", params={"drop_pending_updates": "true"}, timeout=10)
        time.sleep(2)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": url, "drop_pending_updates": True}, timeout=10)
    except: pass

# ═══════════════════════════════════════════════════════════════════════
#  DATA ENGINE İLƏ HESABATLARIN HAZIRLANMASI
# ═══════════════════════════════════════════════════════════════════════
def generate_report(report_type="SƏHƏR"):
    with _lock:
        bütün_aktivlər = DINAMIK_PORTFEL + STRATEJI_PORTFEL
    
    try:
        # 1. Python rəqəmləri və xəbərləri yığır (API-lərdən)
        faktiki_data_json = data_engine.aggregate_context(symbols=bütün_aktivlər, cryptopanic_key=CRYPTOPANIC_KEY)
        
        # 2. Rəqəmlər Gemini üçün "Deep Research" promptuna salınır
        sert_prompt = data_engine.build_gemini_prompt(context_json=faktiki_data_json, report_type=report_type)
        
        # 3. Gemini faktiki rəqəmləri analiz edir
        analiz_metni = gemini_call(sert_prompt)
        
        # 4. Telegram-a göndərir
        bot.send_message(CHAT_ID, f"🏛️ **{report_type} STRATEJİ HESABAT (M.Genat 3.0)**\n\n{analiz_metni}", parse_mode="Markdown")
    except Exception as e:
        print(f"Hesabat Xətası: {e}")

def schedule_loop():
    # Günlük avtomatik hesabatlar
    schedule.every().day.at("08:00").do(generate_report, report_type="SƏHƏR")
    schedule.every().day.at("17:00").do(generate_report, report_type="AXŞAM (PRE-MARKET)")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ═══════════════════════════════════════════════════════════════════════
#  İNTERAKTİV İDARƏETMƏ PANElİ (UI)
# ═══════════════════════════════════════════════════════════════════════
def main_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔭 Kripto (Dinamik) Radar", callback_data="show_crypto"),
               InlineKeyboardButton("🏛️ Səhm (Strateji) Radar", callback_data="show_stocks"))
    markup.row(InlineKeyboardButton("📊 Dərin Analiz Hesabatı (Anlıq)", callback_data="run_report"))
    return markup

@bot.message_handler(commands=['menu', 'start'])
def send_menu(message):
    if str(message.chat.id) != str(CHAT_ID): return
    bot.send_message(message.chat.id, "🎛 **M.Genat 3.0 İdarəetmə Paneli**", reply_markup=main_menu_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.message.chat.id) != str(CHAT_ID): return
    
    if call.data == "show_crypto":
        with _lock: txt = ", ".join(DINAMIK_PORTFEL)
        bot.send_message(call.message.chat.id, f"🔭 **Dinamik (Kripto) Radar:** {txt}\nƏlavə etmək üçün: `skan əlavə et: SOLUSDT`")
    
    elif call.data == "show_stocks":
        with _lock: txt = ", ".join(STRATEJI_PORTFEL)
        bot.send_message(call.message.chat.id, f"🏛️ **Strateji (Səhm) Radar:** {txt}\nƏlavə etmək üçün: `strat əlavə et: AAPL`")
        
    elif call.data == "run_report":
        bot.send_message(call.message.chat.id, "⏳ Real vaxt məlumatları (Binance/Yahoo) çəkilir... Zəhmət olmasa 1 dəqiqə gözləyin.")
        threading.Thread(target=generate_report, args=("ANİ (DEEP RESEARCH)",), daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════
#  MƏTN ANALİZİ VƏ NLP YÖNLƏNDİRMƏ
# ═══════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID): return
    text = message.text or ""
    msg_l = text.lower()

    if msg_l == "menu":
        send_menu(message)
        return

    # 1. DİNAMİK PORTFEL (Kripto)
    if msg_l.startswith("skan əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in DINAMIK_PORTFEL: DINAMIK_PORTFEL.append(yeni)
        bot.reply_to(message, f"✅ Kripto Radara əlavə edildi: {yeni} (USDT formati mütləqdir)")
    
    elif msg_l.startswith("skan sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in DINAMIK_PORTFEL: DINAMIK_PORTFEL.remove(sil)
        bot.reply_to(message, f"🗑️ Kripto Radardan silindi: {sil}")

    # 2. STRATEJİ PORTFEL (Səhm)
    elif msg_l.startswith("strat əlavə et:"):
        yeni = text.split(":", 1)[1].strip().upper()
        with _lock:
            if yeni not in STRATEJI_PORTFEL: STRATEJI_PORTFEL.append(yeni)
        bot.reply_to(message, f"🏛️ Səhm Radarına əlavə edildi: {yeni}")

    elif msg_l.startswith("strat sil:"):
        sil = text.split(":", 1)[1].strip().upper()
        with _lock:
            if sil in STRATEJI_PORTFEL: STRATEJI_PORTFEL.remove(sil)
        bot.reply_to(message, f"🗑️ Səhm Radardan silindi: {sil}")

    # 3. ÜMUMİ SÖHBƏT (Sərbəst Gemini Dialoqu)
    else:
        def gemini_arxa_plan():
            try:
                bot.send_chat_action(message.chat.id, 'typing')
                res = gemini_call(f"Sən M.Genat 3.0-san. Phill sənə yazır: {text}")
                bot.reply_to(message, res, parse_mode="Markdown")
            except: pass
        threading.Thread(target=gemini_arxa_plan, daemon=True).start()

if __name__ == '__main__':
    print("M.Genat 3.0 (Kvant Analitik Modu) işə düşür...")
    register_webhook()
    threading.Thread(target=schedule_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
