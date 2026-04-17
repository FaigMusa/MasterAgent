"""
M.Genat 3.0 — Data Fetching & Aggregation Module (Data Engine)
-------------------------------------------------
Kripto (USDT)                -> Binance API
Ənənəvi Səhmlər (SPY, GC=F) -> Yahoo Finance (yfinance)
Texniki Analiz               -> ta kitabxanası (RSI, EMA)
                                (pandas-ta Python 3.11-i dəstəkləmir,
                                 buna görə `ta` ilə əvəz edilib)
Makro Xəbərlər               -> CryptoPanic + RSS (CNBC/Yahoo)
"""

import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf
import feedparser

# ── `ta` kitabxanası (pandas-ta əvəzinə, Python 3.11 uyğun) ─────────────────
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logging.warning("'ta' kitabxanası tapılmadı. RSI/EMA hesablanmayacaq.")

logger = logging.getLogger(__name__)

# ── Sabitlər ──────────────────────────────────────────────────────────────────
BINANCE_BASE     = "https://api.binance.com"
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
REQUEST_TIMEOUT  = 15


# ═══════════════════════════════════════════════════════════════════════════════
#  1. TEXNİKİ İNDİKATORLAR (ta kitabxanası ilə)
# ═══════════════════════════════════════════════════════════════════════════════
def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    DataFrame-ə RSI(14), EMA(20), EMA(50) sütunları əlavə edir.
    `ta` kitabxanası `pandas-ta`-nın Python 3.11-dəki uyğunsuzluğunu həll edir.
    """
    if not TA_AVAILABLE or len(df) < 51:
        df["RSI_14"] = None
        df["EMA_20"] = None
        df["EMA_50"] = None
        return df

    close = df["close"]
    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi().round(2)
    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator().round(2)
    df["EMA_50"] = ta.trend.EMAIndicator(close=close, window=50).ema_indicator().round(2)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  2. KRİPTO VƏ ƏNƏNƏVİ BAZAR DATASI
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_market_data(symbol: str) -> dict:
    """
    Simvolun növünə görə Binance (kripto) və ya Yahoo Finance (səhm/ETF) API-dən
    OHLCV datası çəkir, RSI və EMA hesablayıb vahid dict qaytarır.

    Kripto simvollar: 'BTCUSDT', 'ETHUSDT' (USDT ilə bitən)
    Səhm/ETF simvollar: 'SPY', 'GC=F', 'AAPL'
    """
    is_crypto = symbol.upper().endswith("USDT")

    try:
        if is_crypto:
            # ── Binance REST API ───────────────────────────────────────────────
            url  = f"{BINANCE_BASE}/api/v3/klines"
            resp = requests.get(
                url,
                params={"symbol": symbol.upper(), "interval": "1d", "limit": 100},
                timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()

            cols = [
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "tb_base", "tb_quote", "ignore"
            ]
            df = pd.DataFrame(resp.json(), columns=cols)
            df["close"]  = df["close"].astype(float)
            df["volume"] = df["volume"].astype(float)
            df["date"]   = pd.to_datetime(df["open_time"], unit="ms", utc=True)

        else:
            # ── Yahoo Finance ──────────────────────────────────────────────────
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="6mo", interval="1d")

            if df.empty:
                raise ValueError(f"yfinance '{symbol}' üçün data tapa bilmədi.")

            df = df.rename(columns={"Close": "close", "Volume": "volume"})
            df["date"] = df.index

        # ── İndikatorlar əlavə edilir ──────────────────────────────────────────
        df = _add_indicators(df)

        last = df.iloc[-1]

        def _safe_float(col: str):
            val = last.get(col)
            try:
                return round(float(val), 2) if val is not None and pd.notna(val) else None
            except (TypeError, ValueError):
                return None

        return {
            "symbol":        symbol.upper(),
            "current_price": _safe_float("close"),
            "volume_24h":    _safe_float("volume"),
            "rsi_14":        _safe_float("RSI_14"),
            "ema_20":        _safe_float("EMA_20"),
            "ema_50":        _safe_float("EMA_50"),
            "last_updated":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source":        "Binance" if is_crypto else "Yahoo Finance"
        }

    except Exception as e:
        logger.error(f"fetch_market_data xətası [{symbol}]: {e}")
        return {"symbol": symbol.upper(), "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  3. MAKRO VƏ KRİPTO XƏBƏRLƏRİ
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_global_news(cryptopanic_token: str = None) -> list:
    """CryptoPanic (kripto) və RSS (makro) lentlərindən ən vacib 10 xəbəri cəmləyir."""
    news_list = []

    # ── RSS: Makro/Maliyyə xəbərləri ──────────────────────────────────────────
    rss_sources = [
        "https://finance.yahoo.com/news/rssindex",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"
    ]
    keywords = ["fed", "rate", "inflation", "blackrock", "goldman", "war", "oil", "recession", "gdp"]

    for url in rss_sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:7]:
                title = getattr(entry, "title", "") or ""
                if any(k in title.lower() for k in keywords):
                    news_list.append({
                        "title":  title,
                        "source": "Global RSS",
                        "date":   entry.get("published", "")
                    })
        except Exception as e:
            logger.warning(f"RSS xətası ({url}): {e}")

    # ── CryptoPanic: Kripto xəbərləri ─────────────────────────────────────────
    if cryptopanic_token and cryptopanic_token not in ("", "YOUR_TOKEN_HERE"):
        try:
            resp = requests.get(
                f"{CRYPTOPANIC_BASE}/posts/",
                params={"auth_token": cryptopanic_token, "public": "true", "kind": "news"},
                timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 200:
                for item in resp.json().get("results", [])[:5]:
                    news_list.append({
                        "title":  item.get("title", ""),
                        "source": item.get("source", {}).get("title", "CryptoPanic"),
                        "date":   item.get("published_at", "")
                    })
        except Exception as e:
            logger.warning(f"CryptoPanic xətası: {e}")

    return news_list[:10]


# ═══════════════════════════════════════════════════════════════════════════════
#  4. AGGREGATOR: Bütün Datanı JSON-a Yığan Mərkəz
# ═══════════════════════════════════════════════════════════════════════════════
def aggregate_context(symbols: list, cryptopanic_key: str = None) -> str:
    """
    Siyahıdakı bütün simvolların bazar datasını və qlobal xəbərləri çəkib
    Gemini üçün təmiz JSON string qaytarır.
    """
    assets_data = []
    for sym in symbols:
        logger.info(f"Data çəkilir: {sym}")
        data = fetch_market_data(sym)
        assets_data.append(data)
        time.sleep(1.2)  # API rate-limit qorunması

    news_data = fetch_global_news(cryptopanic_key)

    context = {
        "report_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets":               assets_data,
        "macro_and_crypto_news": news_data
    }

    return json.dumps(context, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. GEMİNİ PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════
def build_gemini_prompt(context_json: str, report_type: str = "GÜNLÜK") -> str:
    return f"""Sən M.Genat 3.0, peşəkar Hedge-Fund analitikisən.
Aşağıda sənə YALNIZ API-lərdən çəkilmiş FAKTİKİ, CANLI bazar datası və qlobal xəbərlər (JSON) verilir.

SƏNİN MÜTLƏQ QAYDALARIN:
1. Heç bir qiyməti, tarixi, həcmi (volume) və ya xəbəri TƏXMİN ETMƏ, UYDURMA. YALNIZ JSON daxilindəki rəqəmlərdən istifadə et.
2. S&P 500 (SPY), Kripto (USDT aktivlər) və makro xəbərləri bir-biri ilə əlaqələndirərək dərin (deep research) analiz apar.
3. FED qərarları, institusional hesabatlar varsa — hesabatın mərkəzinə onu qoy.
4. Hər aktiv üçün RSI və EMA vəziyyətini şərh et (aşırı alım/satım, trendin yönü).
5. Azərbaycan dilində professional maliyyə terminologiyası ilə cavab ver.
6. Cavabda ulduz (*) və ya digər Markdown simvollarından qaçın; düz mətn istifadə et.

--- CANLI DATA (JSON) ---
{context_json}
--- DATA SONU ---

İndi bu real məlumatlara əsasən {report_type} STRATEJİ HESABATINI hazırla."""
