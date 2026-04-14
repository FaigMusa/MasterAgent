import time
import threading
import schedule
import requests
from datetime import datetime
from collections import deque
from google import genai
from flask import Flask
import os
import feedparser
import telebot

# ================= TƏNZİMLƏMƏLƏR =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# Thread-safe kilid
_lock = threading.Lock()

DINAMIK_PORTFEL = ["ETH", "BTC", "NVDA", "AMD", "SMH", "NLR", "URA", "BOTZ", "TSLA"]
XATIRLATMALAR = []

app = Flask(__name__)

@app.route('/')
def home():
    return "M.Genat 1.2.2 - Stabil"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)


# ================= FUNKSİYALAR =================
def send_tg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Telegram göndərmə xətası: {e}")


def generate_report(report_type="GÜNLÜK"):
    """Gemini ilə hesabat yaradır və Telegram-a göndərir."""
    with _lock:
        portfel_str = ", ".join(DINAMIK_PORTFEL)
    prompt = (
        f"M.Genat 1.2. {report_type} hesabat hazırla. "
        f"Aktivlər: {portfel_str}. "
        f"Tarix: {datetime.now().strftime('%d %B %Y')}. Dil: Azərbaycan."
    )
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash', contents=prompt
        )
        send_tg(f"🏛️ **{report_type} HESABAT**\n\n{response.text}")
    except Exception as e:
        print(f"Hesabat xətası: {e}")


def schedule_loop():
    """schedule kitabxanasını işlədir — bütün planlaşdırılmış tapşırıqlar buraya əlavə edilir."""
    # Günlük saat 08:00-da hesabat
    schedule.every().day.at("08:00").do(generate_report, report_type="GÜNLÜK")
    # Hər bazar ertəsi saat 09:00-da həftəlik hesabat
    schedule.every().monday.at("09:00").do(generate_report, report_type="HƏFTƏLİK")

    while True:
        schedule.run_pending()
        time.sleep(30)


def scout_loop():
    """RSS lentlərini izləyir, portfelə aid kritik xəbərləri göndərir."""
    # deque ilə yaddaş sızmasının qarşısı alınır (maksimum 500 xəbər saxlanır)
    seen_news = deque(maxlen=500)

    while True:
        with _lock:
            keywords = [word.lower() for word in DINAMIK_PORTFEL] + [
                "fed", "rate", "inflation"
            ]

        rss_urls = [
            "https://finance.yahoo.com/news/rssindex",
            "http://feeds.marketwatch.com/marketwatch/topstories/",
        ]

        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title_lower = entry.title.lower()
                    if (
                        entry.title not in seen_news
                        and any(k in title_lower for k in keywords)
                    ):
                        seen_news.append(entry.title)
                        prompt = (
                            f"Xəbər: '{entry.title}'. "
                            f"Portfelə (ETH, BTC, NVDA...) təsirini analiz et. "
                            f"Kritikdirsə cavabın əvvəlinə '🚨 KRİTİK' yaz."
                        )
                        try:
                            response = client.models.generate_content(
                                model='gemini-2.0-flash', contents=prompt
                            )
                            if "🚨 KRİTİK" in response.text:
                                send_tg(f"{response.text}\n\n🔗 {entry.link}")
                        except Exception as e:
                            print(f"Scout Gemini xətası: {e}")
                        time.sleep(5)
            except Exception as e:
                print(f"RSS xətası ({url}): {e}")
                continue

        time.sleep(600)


def reminder_loop():
    """Qeydə alınmış xatırlatmaları vaxtında göndərir."""
    while True:
        now_time = datetime.now().strftime("%H:%M")
        with _lock:
            due = [x for x in XATIRLATMALAR if x["zaman"] == now_time]
            for x in due:
                XATIRLATMALAR.remove(x)

        for x in due:
            send_tg(f"⏰ **XATIRLATMA:** {x['mesaj']}")

        time.sleep(30)


# ================= MESAJ İDARƏETMƏSİ =================
@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if str(message.chat.id) != str(CHAT_ID):
        return

    text = message.text or ""
    msg_lower = text.lower()

    # "skan əlavə et: TICKER" — split(":", 1) istifadə edirik ki
    # mümkün ikinci ":" olan tickerlar itməsin
    if msg_lower.startswith("skan əlavə et:"):
        parts = text.split(":", 1)
        if len(parts) == 2:
            yeni = parts[1].strip().upper()
            with _lock:
                if yeni and yeni not in DINAMIK_PORTFEL:
                    DINAMIK_PORTFEL.append(yeni)
                    bot.reply_to(message, f"✅ Əlavə edildi: {yeni}")
                else:
                    bot.reply_to(message, f"⚠️ `{yeni}` artıq siyahıdadır.")
        else:
            bot.reply_to(message, "Format: `skan əlavə et: TICKER`")

    # "skan sil: TICKER"
    elif msg_lower.startswith("skan sil:"):
        parts = text.split(":", 1)
        if len(parts) == 2:
            sil = parts[1].strip().upper()
            with _lock:
                if sil in DINAMIK_PORTFEL:
                    DINAMIK_PORTFEL.remove(sil)
                    bot.reply_to(message, f"🗑️ Silindi: {sil}")
                else:
                    bot.reply_to(message, f"⚠️ `{sil}` siyahıda tapılmadı.")

    # "portfel" — cari siyahını göstər
    elif msg_lower == "portfel":
        with _lock:
            siyahi = ", ".join(DINAMIK_PORTFEL)
        bot.reply_to(message, f"📊 **Cari portfel:** {siyahi}")

    # "xatırlat HH:MM mesaj"
    elif msg_lower.startswith("xatırlat"):
        try:
            parts = text.split(" ", 2)
            if len(parts) < 3:
                raise ValueError
            XATIRLATMALAR.append({"zaman": parts[1], "mesaj": parts[2]})
            bot.reply_to(message, f"✅ Saat {parts[1]} üçün qeyd edildi.")
        except (ValueError, IndexError):
            bot.reply_to(message, "Format: `xatırlat 20:00 Mesaj mətni`")

    # "hesabat" — əl ilə anlıq hesabat
    elif msg_lower == "hesabat":
        bot.reply_to(message, "⏳ Hesabat hazırlanır...")
        threading.Thread(target=generate_report, args=("ANI",), daemon=True).start()

    # Ümumi söhbət — Gemini-yə yönləndir
    else:
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=f"Sən M.Genat adlı maliyyə assistentisən. İstifadəçi: {text}"
            )
            bot.reply_to(message, response.text, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Xəta: {str(e)}")


# ================= 409 XƏTASİNİN ƏSAS HƏLLİ =================
def kill_existing_sessions():
    """
    Telegram tərəfindəki bütün köhnə polling sessiyalarını zorla bağlayır.
    Bu, 409 Conflict xətasının əsas həllidir.
    Yalnız delete_webhook yetərli deyil — getUpdates sorğusu da lazımdır.
    """
    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    for attempt in range(3):
        try:
            # 1. Webhook sil (varsa)
            requests.get(
                f"{base_url}/deleteWebhook",
                params={"drop_pending_updates": "true"},
                timeout=10
            )
            # 2. Köhnə long-polling sessiyasını force-close et:
            #    offset=-1 ilə boş sorğu göndərmək Telegram-ın açıq saxladığı
            #    əvvəlki getUpdates bağlantısını dərhal kəsir.
            requests.get(
                f"{base_url}/getUpdates",
                params={"offset": -1, "timeout": 0},
                timeout=10
            )
            print(f"Köhnə sessiya təmizləndi (cəhd {attempt + 1})")
            time.sleep(3)
        except Exception as e:
            print(f"Sessiya təmizləmə xətası (cəhd {attempt + 1}): {e}")

    # Telegram serverinə köhnə bağlantını tamamilə bağlamaq üçün vaxt ver
    print("Telegram-ın köhnə sessiyaları bağlaması üçün 15 saniyə gözlənilir...")
    time.sleep(15)


# ================= MASTER START =================
if __name__ == '__main__':
    print("M.Genat 1.2.2 başlayır...")

    # Köhnə sessiyaları məhv et — 409 xətasının yeganə etibarlı həlli
    kill_existing_sessions()

    # Bütün threadlər daemon=True ilə işləyir (ana proses dayananda avtomatik dayanır)
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=scout_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()

    print("Bütün threadlər işə düşdü. Polling başlayır...")

    # none_stop=True: müvəqqəti şəbəkə xətalarında polling dayanmır
    # restart_on_change=False: fayl dəyişdikdə yenidən başlamır (Render üçün vacib)
    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=15,
        none_stop=True,
        restart_on_change=False,
        allowed_updates=[]
    )
