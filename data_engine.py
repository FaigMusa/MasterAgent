"""
M.Genat 3.0 — Data Fetching & Aggregation Module (Data Engine)
-------------------------------------------------
Kripto (USDT) -> Binance API
Ənənəvi Səhmlər (SPY, QIZIL və s.) -> Yahoo Finance (yfinance)
Texniki Analiz -> pandas-ta (RSI, EMA)
Makro Xəbərlər -> CryptoPanic + RSS (Investing/Yahoo)
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import pandas_ta as ta
import requests
import yfinance as yf
import feedparser

logger = logging.getLogger(__name__)

# ── Sabitlər ──────────────────────────────────────────────────────────────────
BINANCE_BASE = "https://api.binance.com"
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
REQUEST_TIMEOUT = 10

# ── 1. Kripto və Ənənəvi Bazar Datası (Birləşdirilmiş) ────────────────────────
def fetch_market_data(symbol: str) -> dict:
    """
    Simvolun növünə görə Binance və ya Yahoo Finance-dan OHLCV datası çəkir, 
    RSI və EMA indikatorlarını hesablayıb vahid formatda qaytarır.
    """
    is_crypto = symbol.endswith("USDT")
    
    try:
        if is_crypto:
            # BINANCE API (Kripto üçün)
            url = f"{BINANCE_BASE}/api/v3/klines"
            resp = requests.get(url, params={"symbol": symbol.upper(), "interval": "1d", "limit": 100}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            df = pd.DataFrame(resp.json(), columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "tb_base", "tb_quote", "ignore"])
            df["close"] = df["close"].astype(float)
            df["volume"] = df["volume"].astype(float)
            df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        else:
            # YAHOO FINANCE (Səhm, ETF və İndekslər üçün, məsələn SPY)
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="6mo", interval="1d")
            if df.empty:
                raise ValueError(f"yfinance {symbol} üçün data tapa bilmədi.")
            df = df.rename(columns={"Close": "close", "Volume": "volume"})
            df["date"] = df.index

        # İndikatorların hesablanması (pandas-ta)
        df.ta.rsi(length=14, append=True)
        df.ta.ema(length=20, append=True)
        df.ta.ema(length=50, append=True)

        last = df.iloc[-1]
        
        # Təhlükəsiz sütun oxumaq üçün köməkçi
        def _safe(col_prefix):
            cands = [c for c in df.columns if c.upper().startswith(col_prefix.upper())]
            return round(float(last[cands[0]]), 2) if cands and pd.notna(last[cands[0]]) else None

        return {
            "symbol": symbol.upper(),
            "current_price": round(float(last["close"]), 2),
            "volume_24h": round(float(last["volume"]), 2),
            "rsi_14": _safe("RSI_14"),
            "ema_20": _safe("EMA_20"),
            "ema_50": _safe("EMA_50"),
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Binance" if is_crypto else "Yahoo Finance"
        }

    except Exception as e:
        logger.error(f"Market Data xətası [{symbol}]: {e}")
        return {"symbol": symbol, "error": str(e)}

# ── 2. Çoxmənbəli Makro və Kripto Xəbərləri ──────────────────────────────────
def fetch_global_news(cryptopanic_token: str = None) -> list:
    """CryptoPanic və qlobal RSS lentlərindən xəbərləri cəmləyir."""
    news_list = []

    # 1. RSS: Makroiqtisadi və Ənənəvi Xəbərlər (Investing/Yahoo/Fed)
    rss_urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664" # CNBC Finance
    ]
    keywords = ["fed", "rate", "inflation", "blackrock", "goldman", "war", "oil"]
    
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]: # Hər mənbədən top 5
                if any(k in entry.title.lower() for k in keywords):
                    news_list.append({"title": entry.title, "source": "Global RSS", "date": entry.get("published", "")})
        except:
            continue

    # 2. CryptoPanic (Kripto spesifik xəbərlər)
    if cryptopanic_token and cryptopanic_token != "YOUR_TOKEN_HERE":
        try:
            url = f"{CRYPTOPANIC_BASE}/posts/"
            resp = requests.get(url, params={"auth_token": cryptopanic_token, "public": "true", "kind": "news"}, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                for item in resp.json().get("results", [])[:5]:
                    news_list.append({"title": item.get("title"), "source": item.get("source", {}).get("title"), "date": item.get("published_at")})
        except:
            pass

    return news_list[:10] # Ən vacib 10 xəbəri saxlayır

# ── 3. Aggregator: Hər şeyi Json-a Yığan Mərkəz ──────────────────────────────
def aggregate_context(symbols: list, cryptopanic_key: str = None) -> str:
    """Siyahıdakı bütün simvolları və xəbərləri çəkib təmiz JSON string qaytarır."""
    assets_data = []
    for sym in symbols:
        data = fetch_market_data(sym)
        assets_data.append(data)
        time.sleep(1) # API limitlərinə düşməmək üçün

    news_data = fetch_global_news(cryptopanic_key)

    context = {
        "report_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets": assets_data,
        "macro_and_crypto_news": news_data
    }
    
    return json.dumps(context, ensure_ascii=False, indent=2)

# ── 4. Gemini Prompt Builder (Sərt Təlimatlı) ────────────────────────────────
def build_gemini_prompt(context_json: str, report_type: str = "GÜNLÜK") -> str:
    prompt = f"""Sən M.Genat 3.0, peşəkar Hedge-Fund analitikisən. 
Aşağıda sənə yalnız API-lərdən çəkilmiş FAKTİKİ, CANLI bazar datası və qlobal xəbərlər (JSON formatında) verilir. 

SƏNİN MÜTLƏQ QAYDALARIN:
1. Heç bir qiyməti, tarixi, həcmi (volume) və ya xəbəri TƏXMİN ETMƏ VƏ UYDURMA. YALNIZ JSON daxilindəki rəqəmlərdən istifadə et.
2. S&P 500 (SPY), Kripto (USDT) və Makro-iqtisadi xəbərləri bir-biri ilə əlaqələndirərək zəncirvari (deep research) analiz et.
3. Təqdim olunan xəbərlər daxilində institusional hesabatlar və ya FED qərarları varsa, hesabatın mərkəzinə onu qoy.
4. Hər bir aktiv üçün RSI və EMA vəziyyətini şərh et (Aşırı alım/satım, trendin yönü).
5. Azərbaycan dilində professional maliyyə terminologiyası ilə cavab ver.

--- CANLI DATA (JSON) ---
{context_json}
--- DATA SONU ---

İndi bu real məlumatlara əsasən {report_type} STRATEJİ HESABATINI hazırla."""

    return prompt
