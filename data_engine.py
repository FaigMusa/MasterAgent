"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              M.Genat 3.1 Pro  —  data_engine.py                            ║
║                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────┐    ║
║  │  SCOUT AGENT   │  Day-trading · Multi-TF · RSI · EMA · Vol Spike   │    ║
║  │  MASTER AGENT  │  Macro · Geopolitical · Institutional Flow         │    ║
║  │  AGGREGATOR    │  aggregate_context()  →  build_gemini_prompt()     │    ║
║  └─────────────────────────────────────────────────────────────────────┘    ║
║                                                                              ║
║  Kitabxanalar:  requests · yfinance · ta · pandas · feedparser              ║
║  Python 3.11+  |  Uydurma data qəti yoxdur  |  Thread-pool parallelism     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import feedparser
import pandas as pd
import requests
import ta                    # TA-Lib wrapper (pip install ta) — pandas-ta DEYİL
import yfinance as yf

# ──────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)

# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL SABİTLƏR
# ══════════════════════════════════════════════════════════════════════════════

BINANCE_BASE      = "https://api.binance.com"
CRYPTOPANIC_BASE  = "https://cryptopanic.com/api/v1"
HTTP_TIMEOUT      = 12          # saniyə
THREAD_TIMEOUT    = 25          # futures üçün maksimum gözləmə

# Scout — zaman dilimləri  {display_label: (binance_interval, yf_interval, yf_period)}
SCOUT_TIMEFRAMES: dict[str, tuple[str, str, str]] = {
    "5m":  ("5m",  "5m",  "1d"),
    "1h":  ("1h",  "60m", "5d"),
    "4h":  ("4h",  "1h",  "5d"),   # yfinance 4h yoxdur → 1h çəkib resample edirik
    "1d":  ("1d",  "1d",  "60d"),
}
SCOUT_KLINE_LIMIT    = 210       # EMA-200 üçün ən azı 200 şam lazımdır
LIQUIDITY_MULTIPLIER = 1.5       # həcm bu qədər artarsa → Spike
VOLUME_LOOKBACK      = 24        # neçə şamın ortalaması alınır

# Master — xəbər mənbələri
MASTER_RSS_FEEDS = [
    ("Yahoo Finance · Markets",
     "https://finance.yahoo.com/news/rssindex"),
    ("CNBC · Finance",
     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("Reuters · Business",
     "https://feeds.reuters.com/reuters/businessNews"),
    ("Bloomberg · Markets",
     "https://feeds.bloomberg.com/markets/news.rss"),
    ("MarketWatch · Top Stories",
     "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing.com · News",
     "https://www.investing.com/rss/news.rss"),
    ("FT · Markets",
     "https://www.ft.com/rss/markets"),
]

# Master — "Böyük Balıq" açar sözləri (score hesablanır)
BIG_FISH_KEYWORDS: dict[str, int] = {
    # İnstitusional (ağırlıq 3)
    "jpmorgan":        3, "blackrock":       3, "goldman sachs":  3,
    "goldman":         2, "vanguard":        2, "fidelity":       2,
    "morgan stanley":  2, "citadel":         2, "bridgewater":    2,
    "berkshire":       2, "ray dalio":       2, "warren buffett": 2,
    # Makro / Monetar (ağırlıq 3)
    "federal reserve": 3, "fed":             3, "fomc":           3,
    "rate hike":       3, "rate cut":        3, "interest rate":  2,
    "inflation":       2, "cpi":             2, "ppi":            2,
    "gdp":             2, "recession":       2, "yield curve":    3,
    "treasury":        2, "dollar index":    3, "dxy":            3,
    "ecb":             2, "boj":             2, "imf":            2,
    # Geopolitik (ağırlıq 3)
    "hormuz":          3, "middle east":     3, "taiwan":         3,
    "china":           2, "sanctions":       2, "opec":           2,
    "oil":             1, "war":             2, "conflict":       2,
    # Texnoloji / Kripto (ağırlıq 2)
    "semiconductor":   2, "chip":            2, "nvidia":         2,
    "ai investments":  2, "bitcoin etf":     3, "spot etf":       3,
    "crypto":          1, "defi":            1, "sec":            2,
}

MASTER_CRYPTO_COUNT = 6     # CryptoPanic-dən
MASTER_MACRO_COUNT  = 8     # RSS-dən (filtr sonrası)


# ══════════════════════════════════════════════════════════════════════════════
#  YARDIMÇI TİPLƏR
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TimeframeSnapshot:
    """Bir zaman diliminin tam texniki görüntüsü."""
    tf:               str
    source:           str                    # "binance" | "yfinance"
    last_close:       Optional[float]
    last_candle_time: str
    rsi_14:           Optional[float]
    ema_50:           Optional[float]
    ema_100:          Optional[float]
    ema_200:          Optional[float]
    volume_last:      Optional[float]
    volume_avg24:     Optional[float]
    volume_status:    str                    # "SPIKE" | "NORMAL" | "LOW" | "UNKNOWN"
    error:            Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoutResult:
    """Bir aktivin bütün zaman dilimləri üçün Scout nəticəsi."""
    symbol:      str
    asset_type:  str                         # "crypto" | "traditional"
    scanned_at:  str
    timeframes:  dict[str, dict]             # tf → TimeframeSnapshot.to_dict()
    scout_ok:    bool
    errors:      list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NewsItem:
    """Bir xəbər elementi."""
    category:     str                        # "crypto" | "macro"
    title:        str
    source:       str
    published_at: str
    url:          str
    score:        int = 0                    # Big-Fish relevance skoru

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
#  YARDIMÇI FUNKSİYALAR
# ══════════════════════════════════════════════════════════════════════════════

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_round(val: Any, n: int = 4) -> Optional[float]:
    """NaN / None / istənilən uğursuz çevrilmənin öhdəsindən gəlir."""
    try:
        f = float(val)
        return None if pd.isna(f) else round(f, n)
    except (TypeError, ValueError):
        return None


def _is_crypto(symbol: str) -> bool:
    """
    Ticker-in Binance cütü olub-olmadığını müəyyən edir.
    'BTCUSDT' → True  |  'SPY' → False  |  'BTC-USD' → False
    """
    s = symbol.upper().strip()
    crypto_suffixes = ("USDT", "BUSD", "ETH", "BTC", "BNB", "USDC")
    if any(s.endswith(sfx) for sfx in crypto_suffixes):
        return True
    # Binance format: yalnız böyük hərf+rəqəm, tire/nöqtə yoxdur, 5-12 simvol
    return bool(re.fullmatch(r"[A-Z0-9]{5,12}", s))


def _volume_status(last: Optional[float], avg: Optional[float]) -> str:
    if last is None or avg is None or avg == 0:
        return "UNKNOWN"
    ratio = last / avg
    if ratio >= LIQUIDITY_MULTIPLIER:
        return "SPIKE"
    if ratio >= 0.8:
        return "NORMAL"
    return "LOW"


def _parse_rss_time(entry: Any) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
    return _utc_now()


def _domain_from_url(url: str) -> str:
    m = re.search(r"(?:https?://)?(?:www\.|feeds?\.)?([^/\s]+)", url)
    return m.group(1) if m else url


# ══════════════════════════════════════════════════════════════════════════════
#  SCOUT AGENT  ───  Day-Trading / Qısamüddətli Analiz
# ══════════════════════════════════════════════════════════════════════════════

class ScoutAgent:
    """
    Bir aktivi 4 zaman dilimində (5m · 1h · 4h · 1d) skan edir.
    Hər dilim üçün RSI(14), EMA(50/100/200) və həcm anomaliyası hesablanır.

    İstifadə:
        scout = ScoutAgent()
        result = scout.scan("BTCUSDT")   # kripto
        result = scout.scan("SPY")       # səhm/ETF
    """

    # ── OHLCV çəkmə ───────────────────────────────────────────────────────────

    def _fetch_binance_klines(self,
                               symbol: str,
                               interval: str,
                               limit: int = SCOUT_KLINE_LIMIT) -> pd.DataFrame:
        """Binance REST API-dən OHLCV çəkir, DataFrame qaytarır."""
        url    = f"{BINANCE_BASE}/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        resp   = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        df  = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades",
            "taker_base", "taker_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"])

    def _fetch_yf_ohlcv(self,
                         symbol: str,
                         yf_interval: str,
                         yf_period: str) -> pd.DataFrame:
        """yfinance-dən OHLCV çəkir, normallaşdırılmış DataFrame qaytarır."""
        df = yf.download(
            symbol,
            period=yf_period,
            interval=yf_interval,
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            raise ValueError(f"yfinance boş dataframe [{symbol} {yf_interval}]")

        # MultiIndex sütunları düzəlt
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                "_".join(str(c) for c in col if c).lower().split("_")[0]
                for col in df.columns
            ]
        else:
            df.columns = [c.lower() for c in df.columns]

        df.index.name = "open_time"
        df = df.reset_index()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")

        # Sütun adları normallaşdırma
        col_map = {}
        for needed in ("open", "high", "low", "close", "volume"):
            match = next((c for c in df.columns if needed in c.lower()), None)
            if match:
                col_map[match] = needed
        df = df.rename(columns=col_map)

        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"])

    def _resample_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        yfinance 4h interval dəstəkləmir.
        1h verisini 4h-a resample edirik: O=first, H=max, L=min, C=last, V=sum
        """
        df = df.set_index("open_time").sort_index()
        rule = "4h"
        resampled = df["close"].resample(rule).last().rename("close")
        result = pd.DataFrame({"close": resampled})
        result["open"]   = df["open"].resample(rule).first()
        result["high"]   = df["high"].resample(rule).max()
        result["low"]    = df["low"].resample(rule).min()
        result["volume"] = df["volume"].resample(rule).sum()
        result = result.dropna(subset=["close"]).reset_index()
        result = result.rename(columns={"open_time": "open_time"})
        return result

    # ── İndikator hesablaması ──────────────────────────────────────────────────

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        """
        `ta` kitabxanası ilə RSI(14) + EMA(50, 100, 200) hesablayır.
        Minimum şam sayı yetərsizdirsə None qaytarır.
        """
        close = df["close"].dropna()
        n     = len(close)

        rsi_val   = None
        ema50_val = ema100_val = ema200_val = None

        if n >= 15:
            rsi_val = _safe_round(
                ta.momentum.RSIIndicator(close=close, window=14)
                  .rsi().iloc[-1]
            )

        if n >= 50:
            ema50_val = _safe_round(
                ta.trend.EMAIndicator(close=close, window=50)
                  .ema_indicator().iloc[-1]
            )
        if n >= 100:
            ema100_val = _safe_round(
                ta.trend.EMAIndicator(close=close, window=100)
                  .ema_indicator().iloc[-1]
            )
        if n >= 200:
            ema200_val = _safe_round(
                ta.trend.EMAIndicator(close=close, window=200)
                  .ema_indicator().iloc[-1]
            )

        return {
            "rsi_14":  rsi_val,
            "ema_50":  ema50_val,
            "ema_100": ema100_val,
            "ema_200": ema200_val,
        }

    # ── Həcm anomaliyası ───────────────────────────────────────────────────────

    def _volume_analysis(self, df: pd.DataFrame) -> tuple[Optional[float], Optional[float], str]:
        """
        Son şamın həcmini əvvəlki VOLUME_LOOKBACK şamın ortalaması ilə müqayisə edir.
        Qaytarır: (last_volume, avg_volume, status)
        """
        if "volume" not in df.columns or len(df) < 2:
            return None, None, "UNKNOWN"

        vols       = df["volume"].dropna()
        last_vol   = _safe_round(vols.iloc[-1], 2)
        lookback   = vols.iloc[-(VOLUME_LOOKBACK + 1):-1]
        avg_vol    = _safe_round(lookback.mean(), 2) if len(lookback) >= 3 else None
        status     = _volume_status(last_vol, avg_vol)
        return last_vol, avg_vol, status

    # ── Bir zaman dilimi üçün tam snapshot ────────────────────────────────────

    def _scan_single_timeframe(self,
                                symbol:      str,
                                tf_label:    str,
                                is_crypto_f: bool) -> TimeframeSnapshot:
        """
        Bir zaman dilimi (məs. '1h') üçün tam TimeframeSnapshot qaytarır.
        Xəta baş verərsə error sahəli snapshot qaytarır, exception atmır.
        """
        b_interval, yf_interval, yf_period = SCOUT_TIMEFRAMES[tf_label]
        try:
            if is_crypto_f:
                df     = self._fetch_binance_klines(symbol, b_interval)
                source = "binance"
            else:
                if tf_label == "4h":
                    # 1h data çəkib 4h-a resample edirik
                    _, yf_1h, period_1h = SCOUT_TIMEFRAMES["1h"]
                    raw_df = self._fetch_yf_ohlcv(symbol, yf_1h, period_1h)
                    df     = self._resample_4h(raw_df)
                else:
                    df     = self._fetch_yf_ohlcv(symbol, yf_interval, yf_period)
                source = "yfinance"

            if df.empty:
                raise ValueError("Boş dataframe")

            last_row    = df.iloc[-1]
            last_close  = _safe_round(last_row["close"])

            # Son şam vaxtı
            raw_time    = last_row.get("open_time")
            try:
                ts          = pd.Timestamp(raw_time)
                ts          = ts.tz_localize("UTC") if ts.tzinfo is None else ts
                candle_time = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                candle_time = _utc_now()

            indicators  = self._compute_indicators(df)
            vol_last, vol_avg, vol_status = self._volume_analysis(df)

            return TimeframeSnapshot(
                tf               = tf_label,
                source           = source,
                last_close       = last_close,
                last_candle_time = candle_time,
                rsi_14           = indicators["rsi_14"],
                ema_50           = indicators["ema_50"],
                ema_100          = indicators["ema_100"],
                ema_200          = indicators["ema_200"],
                volume_last      = vol_last,
                volume_avg24     = vol_avg,
                volume_status    = vol_status,
            )

        except Exception as exc:
            log.warning("Scout TF xətası [%s %s]: %s", symbol, tf_label, exc)
            return TimeframeSnapshot(
                tf               = tf_label,
                source           = "binance" if is_crypto_f else "yfinance",
                last_close       = None,
                last_candle_time = _utc_now(),
                rsi_14           = None,
                ema_50           = None,
                ema_100          = None,
                ema_200          = None,
                volume_last      = None,
                volume_avg24     = None,
                volume_status    = "UNKNOWN",
                error            = str(exc),
            )

    # ── Açıq API: tam scan ────────────────────────────────────────────────────

    def scan(self, symbol: str) -> ScoutResult:
        """
        Bir aktivi bütün 4 TF-də paralel olaraq skan edir.

        Args:
            symbol: 'BTCUSDT', 'ETHUSDT', 'SPY', 'GLD', 'AAPL' və s.

        Returns:
            ScoutResult — bütün TF-lərin snapshot-larını özündə cəmləyir.
        """
        sym_upper  = symbol.upper()
        is_crypto  = _is_crypto(sym_upper)
        tfs        = list(SCOUT_TIMEFRAMES.keys())
        snapshots  = {}
        errors     = []

        log.info("Scout skan başladı: %s (%s) — %s",
                 sym_upper, "kripto" if is_crypto else "ənənəvi", " · ".join(tfs))

        # Bütün TF-ləri paralel çəkirik
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="scout") as pool:
            future_map = {
                pool.submit(self._scan_single_timeframe, sym_upper, tf, is_crypto): tf
                for tf in tfs
            }
            for future in as_completed(future_map, timeout=THREAD_TIMEOUT):
                tf_label = future_map[future]
                try:
                    snap = future.result()
                    snapshots[tf_label] = snap.to_dict()
                    if snap.error:
                        errors.append(f"{tf_label}: {snap.error}")
                except FuturesTimeout:
                    msg = f"{tf_label}: timeout ({THREAD_TIMEOUT}s)"
                    log.error("Scout timeout: %s %s", sym_upper, msg)
                    errors.append(msg)
                except Exception as exc:
                    msg = f"{tf_label}: {exc}"
                    log.error("Scout future xətası: %s %s", sym_upper, msg)
                    errors.append(msg)

        scout_ok = any(
            snapshots.get(tf, {}).get("last_close") is not None
            for tf in tfs
        )

        return ScoutResult(
            symbol     = sym_upper,
            asset_type = "crypto" if is_crypto else "traditional",
            scanned_at = _utc_now(),
            timeframes = snapshots,
            scout_ok   = scout_ok,
            errors     = errors,
        )

    def scan_multiple(self, symbols: list[str]) -> list[dict]:
        """
        Bir neçə aktivi paralel skan edir.

        Args:
            symbols: ['BTCUSDT', 'ETHUSDT', 'SPY', 'GLD']

        Returns:
            ScoutResult siyahısı (dict formatında)
        """
        results = []
        with ThreadPoolExecutor(max_workers=min(len(symbols), 6),
                                thread_name_prefix="scout_multi") as pool:
            futures = {pool.submit(self.scan, sym): sym for sym in symbols}
            for future in as_completed(futures, timeout=THREAD_TIMEOUT * 2):
                sym = futures[future]
                try:
                    results.append(future.result().to_dict())
                except Exception as exc:
                    log.error("scan_multiple xətası [%s]: %s", sym, exc)
                    results.append({
                        "symbol": sym.upper(), "scout_ok": False,
                        "error": str(exc), "scanned_at": _utc_now(),
                    })
        return results


# ══════════════════════════════════════════════════════════════════════════════
#  MASTER AGENT  ───  Macro / Geopolitical / Institutional Flow
# ══════════════════════════════════════════════════════════════════════════════

class MasterAgent:
    """
    Qlobal makroekonomik, geopolitik və institusional xəbərləri toplayır.

    Mənbələr:
      • CryptoPanic API   → kripto xəbərləri
      • Çoxlu RSS lentlər → makro + institusional xəbərlər

    Ağıllı Süzgəc:
      BIG_FISH_KEYWORDS sözlük ağırlıqlarına əsasən hər xəbərə score verilir.
      JPMorgan / BlackRock / FED / Geopolitik hadisələr yüksək score alır.
    """

    def __init__(self, cryptopanic_token: str):
        self.cp_token = cryptopanic_token

    # ── CryptoPanic ───────────────────────────────────────────────────────────

    def _fetch_crypto_news(self,
                            currencies: str  = "BTC,ETH",
                            count:      int  = MASTER_CRYPTO_COUNT) -> list[NewsItem]:
        url    = f"{CRYPTOPANIC_BASE}/posts/"
        params = {
            "auth_token": self.cp_token,
            "currencies": currencies.upper(),
            "kind":       "news",
            "public":     "true",
        }
        try:
            resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as exc:
            log.warning("CryptoPanic xətası: %s", exc)
            return []

        items = []
        for entry in results[:count]:
            title = entry.get("title", "").strip()
            score = self._score_text(title)
            items.append(NewsItem(
                category     = "crypto",
                title        = title,
                source       = entry.get("source", {}).get("title", "CryptoPanic"),
                published_at = entry.get("published_at", _utc_now()),
                url          = entry.get("url", ""),
                score        = score,
            ))
        return items

    # ── RSS xəbərləri ─────────────────────────────────────────────────────────

    def _fetch_single_rss(self,
                           label: str,
                           url:   str) -> list[NewsItem]:
        """Bir RSS lentini oxuyur, filtr edir, NewsItem siyahısı qaytarır."""
        items = []
        try:
            feed = feedparser.parse(url)
            source_name = getattr(feed.feed, "title", None) or label

            for entry in feed.entries:
                title   = getattr(entry, "title",   "").strip()
                summary = getattr(entry, "summary", "").strip()

                if not title:
                    continue

                score = self._score_text(f"{title} {summary}")
                if score == 0:
                    continue   # heç bir açar söz yoxdur, atla

                items.append(NewsItem(
                    category     = "macro",
                    title        = title,
                    source       = source_name,
                    published_at = _parse_rss_time(entry),
                    url          = getattr(entry, "link", ""),
                    score        = score,
                ))
        except Exception as exc:
            log.warning("RSS xətası [%s]: %s", label, exc)
        return items

    def _fetch_macro_news(self, count: int = MASTER_MACRO_COUNT) -> list[NewsItem]:
        """Bütün RSS lentlərini paralel oxuyur, ən yüksək scorlu-ları qaytarır."""
        all_items: list[NewsItem] = []

        with ThreadPoolExecutor(max_workers=len(MASTER_RSS_FEEDS),
                                thread_name_prefix="master_rss") as pool:
            futures = {
                pool.submit(self._fetch_single_rss, label, url): label
                for label, url in MASTER_RSS_FEEDS
            }
            for future in as_completed(futures, timeout=THREAD_TIMEOUT):
                label = futures[future]
                try:
                    all_items.extend(future.result())
                except Exception as exc:
                    log.warning("RSS future xətası [%s]: %s", label, exc)

        # Dublikat başlıqları sil (eyni başlıq fərqli mənbədən gələ bilər)
        seen   : set[str]      = set()
        unique : list[NewsItem] = []
        for item in all_items:
            key = item.title.lower()[:80]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        # Scorea görə sırala, ən yüksək count-u götür
        unique.sort(key=lambda x: x.score, reverse=True)
        return unique[:count]

    # ── Score hesablaması ─────────────────────────────────────────────────────

    @staticmethod
    def _score_text(text: str) -> int:
        """
        Mətni BIG_FISH_KEYWORDS-lə müqayisə edir, ağırlıqlanmış score qaytarır.
        score = 0 → xəbər uyğun deyil (atlanacaq)
        """
        lower = text.lower()
        return sum(weight for kw, weight in BIG_FISH_KEYWORDS.items() if kw in lower)

    # ── Açıq API ──────────────────────────────────────────────────────────────

    def collect(self,
                currencies:   str = "BTC,ETH",
                crypto_count: int = MASTER_CRYPTO_COUNT,
                macro_count:  int = MASTER_MACRO_COUNT) -> dict:
        """
        Bütün xəbərləri toplayır, kateqoriyaya görə ayırır.

        Returns:
            {
              "collected_at": "...",
              "crypto_news":  [ NewsItem.to_dict(), ... ],
              "macro_news":   [ NewsItem.to_dict(), ... ],
              "top_signals":  [ yüksək scorlu xəbərlər birlikdə ],
              "master_ok":    bool
            }
        """
        log.info("Master agent xəbər toplama başladı ...")

        # Kripto + Makro paralel çəkirik
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="master") as pool:
            f_crypto = pool.submit(self._fetch_crypto_news, currencies, crypto_count)
            f_macro  = pool.submit(self._fetch_macro_news, macro_count)

            try:
                crypto_news = f_crypto.result(timeout=THREAD_TIMEOUT)
            except Exception as exc:
                log.error("CryptoPanic toplama xətası: %s", exc)
                crypto_news = []

            try:
                macro_news = f_macro.result(timeout=THREAD_TIMEOUT)
            except Exception as exc:
                log.error("Makro xəbər toplama xətası: %s", exc)
                macro_news = []

        # Bütün xəbərləri bir yerdə scorea görə sırala → Top siqnallar
        combined = crypto_news + macro_news
        combined.sort(key=lambda x: x.score, reverse=True)
        top_signals = [i.to_dict() for i in combined[:5]]

        log.info("Master: %d kripto + %d makro xəbər toplandı",
                 len(crypto_news), len(macro_news))

        return {
            "collected_at": _utc_now(),
            "crypto_news":  [i.to_dict() for i in crypto_news],
            "macro_news":   [i.to_dict() for i in macro_news],
            "top_signals":  top_signals,
            "master_ok":    len(combined) > 0,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  VAHİD AGGREGATOR
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_context(
    symbols:           list[str],
    cryptopanic_token: str,
    news_currencies:   str  = "BTC,ETH",
) -> dict:
    """
    Scout + Master agentlərini işə salır, nəticələri vahid JSON sözlüyünə yığır.

    Args:
        symbols:           Skan ediləcək tikerlər — ['BTCUSDT', 'ETHUSDT', 'SPY']
        cryptopanic_token: CryptoPanic API açarı
        news_currencies:   CryptoPanic filtr simvolları

    Returns:
        {
          "engine":       "M.Genat 3.1 Pro",
          "generated_at": "...",
          "scout":        { "symbols": [...], "results": [...] },
          "master":       { "crypto_news": [...], "macro_news": [...], ... },
          "data_quality": { ... }
        }
    """
    if not symbols:
        raise ValueError("Ən azı bir ticker tələb olunur.")

    log.info("═" * 60)
    log.info("M.Genat 3.1 Pro — aggregate_context başladı")
    log.info("Tikerlər: %s", ", ".join(symbols))
    log.info("═" * 60)

    scout_agent  = ScoutAgent()
    master_agent = MasterAgent(cryptopanic_token)

    # Scout və Master-i paralel işlədirik
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="engine") as pool:
        f_scout  = pool.submit(scout_agent.scan_multiple, symbols)
        f_master = pool.submit(master_agent.collect, news_currencies)

        try:
            scout_results = f_scout.result(timeout=THREAD_TIMEOUT * 2)
        except Exception as exc:
            log.error("Scout toplama xətası: %s", exc)
            scout_results = []

        try:
            master_results = f_master.result(timeout=THREAD_TIMEOUT * 2)
        except Exception as exc:
            log.error("Master toplama xətası: %s", exc)
            master_results = {
                "collected_at": _utc_now(),
                "crypto_news": [], "macro_news": [],
                "top_signals": [], "master_ok": False,
            }

    quality = {
        "scout_ok":        any(r.get("scout_ok", False) for r in scout_results),
        "crypto_news_ok":  len(master_results.get("crypto_news", [])) > 0,
        "macro_news_ok":   len(master_results.get("macro_news",  [])) > 0,
        "symbols_scanned": [r.get("symbol") for r in scout_results],
        "tfs_available":   list(SCOUT_TIMEFRAMES.keys()),
    }

    if not any([quality["scout_ok"],
                quality["crypto_news_ok"],
                quality["macro_news_ok"]]):
        raise RuntimeError(
            "Bütün data mənbələri uğursuz oldu. "
            "Gemini-yə boş/saxta prompt göndərilmir."
        )

    return {
        "engine":       "M.Genat 3.1 Pro",
        "generated_at": _utc_now(),
        "scout": {
            "symbols": symbols,
            "results": scout_results,
        },
        "master":       master_results,
        "data_quality": quality,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI PROMPT BİLDİRİCİSİ  ───  M.Genat 3.1 Pro
# ══════════════════════════════════════════════════════════════════════════════

def build_gemini_prompt(context: dict) -> str:
    """
    aggregate_context() çıxışını alır, M.Genat 3.1 Pro şəxsiyyəti ilə
    dual-agent (Scout + Master) Gemini promptunu qaytarır.

    Qoruyucular:
      • context None/boşdursa             → ValueError
      • data_quality tamamilə False-dursa → ValueError (boş prompt qadağan)
      • Bəzi mənbə uğursuzsa              → ⚠️ bildiriş prompt içinə əlavə edilir
    """
    if not context:
        raise ValueError("Gemini-yə göndərmək üçün dolu kontekst tələb olunur.")

    quality = context.get("data_quality", {})
    if not quality.get("scout_ok") and \
       not quality.get("crypto_news_ok") and \
       not quality.get("macro_news_ok"):
        raise ValueError(
            "Bütün data mənbələri uğursuz oldu. Boş prompt göndərilmir."
        )

    json_block = json.dumps(context, ensure_ascii=False, indent=2)

    # ── Xəbərdarlıq bloku ─────────────────────────────────────────────────────
    warns = []
    if not quality.get("scout_ok"):
        warns.append("⚠️  Scout texniki datası əlçatmaz — RSI/EMA/Volume analizi məhduddur.")
    if not quality.get("crypto_news_ok"):
        warns.append("⚠️  Kripto xəbərləri boşdur — kripto sentiment analizi mümkün deyil.")
    if not quality.get("macro_news_ok"):
        warns.append("⚠️  Makro RSS lenti boşdur — institusional axın analizi məhduddur.")

    warn_block = ""
    if warns:
        warn_block = (
            "\n[SİSTEM XƏBƏRDARLIĞI]\n"
            + "\n".join(warns)
            + "\n"
        )

    # ── Sessiya (Səhər / Axşam / London / NY açılışı) ─────────────────────────
    hour = datetime.now(timezone.utc).hour
    if   4  <= hour < 8:   session = "Asiya Bağlanışı / Avropa Açılışı Səhər"
    elif 8  <= hour < 12:  session = "London Açılışı"
    elif 12 <= hour < 17:  session = "New York Açılışı"
    elif 17 <= hour < 21:  session = "New York Bağlanışı / After-Hours"
    else:                  session = "Gecə / Asiya Açılışı"

    symbols_str = ", ".join(quality.get("symbols_scanned", ["N/A"]))
    tfs_str     = " · ".join(quality.get("tfs_available",  []))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    prompt = f"""Sən M.Genat 3.1 Pro-san — eyni anda iki fərqli şəxsiyyəti özündə birləşdirən \
peşəkar maliyyə analitikisən:

  🔬 SCOUT (Day Trader)  — Anlıq 5m/1h/4h/1d şam hərəkətlərini, RSI, EMA crosslarını \
və Volume Spike siqnallarını real-time dəyərləndirir.
  🌍 MASTER (Macro Investor)  — Qlobal geopolitik hadisələri, FED / ECB qərarlarını, \
JPMorgan · BlackRock · Goldman Sachs kimi qurumların hərəkətlərini izləyir.
{warn_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSIYA  : {session}
TİKERLƏR : {symbols_str}
TF-LƏR   : {tfs_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MÜTLƏQ QAYDALARIN:

1. Heç bir qiyməti, rəqəmi, tarixi uydurmа. YALNIZ aşağıdakı JSON-dakı \
faktiki API datasına istinad et.
2. Scout datası (RSI, EMA, Volume) ilə Master datası (FED, institusional, \
geopolitik xəbərlər) arasında zəncirvari (causal chain) bağlantı qur.
3. JPMorgan, BlackRock, Goldman Sachs, FED, Dollar Index, geopolitik \
hadisə xəbərləri varsa — hesabatın mərkəzinə o məlumatları qoy.
4. Volume Spike siqnalı varsa — bu şamın həcm anomaliyasını mütləq qeyd et \
və makro səbəbini araşdır.
5. 4 TF-nin (5m · 1h · 4h · 1d) uyğunluğunu yoxla: eyni istiqamətdə olarsa \
"Trend Confluence" qeyd et.

--- CANLI DATA (JSON) ---
{json_block}
--- DATA SONU ---

İndi bu real məlumatlara əsasən {session} üçün Azərbaycan dilində \
M.Genat 3.1 Pro Hesabatını hazırla:

## 🔬 SCOUT ANALİZİ

**Multi-Timeframe Xülasəsi**
Hər TF (5m · 1h · 4h · 1d) üçün: Qiymət · RSI zonu · EMA mövqeyi · Volume statusu.
Cədvəl formatında göstər.

**Trend Confluence**
Bütün TF-lər eyni istiqamətdədirsə → güclü siqnal. Fərqlidirsə → konsolidasiya/qeyri-müəyyənlik.

**Volume Spike Analizi**
SPIKE statusu olan TF varsa: Son şamın həcmi ortalamanın neçə qatıdır? Bu anomaliyanın \
mümkün makro/korporativ səbəbi nədir?

**EMA Mövqe Xəritəsi**
Qiymət EMA50/100/200-ün nə tərəfindədir? Bullish/Bearish alignment varmı?

---

## 🌍 MASTER ANALİZİ

**İnstitusional Siqnallar**
JPMorgan, BlackRock, Goldman Sachs, Vanguard hərəkətləri (əgər varsa).
Bu qurumların mövqeyi bazara necə təsir edə bilər?

**Makro & Monetar Mühit**
FED / ECB qərarları, faiz, inflyasiya, Dollar Index (DXY) dinamikası.
Mövcud monetar mühitin aktiv(lər)ə birbaşa/dolayı təsiri.

**Geopolitik Risk Xəritəsi**
Orta Şərq, Tayvan boğazı, Hormuz, sanksiyalar — aktiv qiymətinə potensial şok effekti.

**Kripto Sentiment**
CryptoPanic xəbərlərinin ümumi tonu: Bullish / Bearish / Mixed.

---

## ⛓️ ZƏNCİRVARİ ƏLAQƏ ANALİZİ

```
Makro Katalizator → İnstitusional Mövqe → Texniki Siqnal → Qiymət Hərəkəti
```
Bu ardıcıllıqla konkret nümunə qur. Hər addımda JSON-dakı real dataya istinad et.

---

## 📊 SENARYO MATRİSİ

| Senaryo    | Tetikləyici Şərt              | Hədəf Zona         | Ehtimal |
|------------|-------------------------------|--------------------|---------|
| 🟢 Bullish | (JSON-dakı datadan doldur)    | (rəqəm uydurmа)    | ?%      |
| 🔴 Bearish | (JSON-dakı datadan doldur)    | (rəqəm uydurmа)    | ?%      |
| 🟡 Base    | (JSON-dakı datadan doldur)    | (rəqəm uydurmа)    | ?%      |

Qiymət hədəflərini yalnız JSON-dakı mövcud EMA dəyərlərindən çıxar. Uydurmа.

---

## 💼 HEDGE-FUND TÖVSİYƏSİ

**Mövqe**: Alış / Satış / Gözlə  
**Əsas Səbəb**: (Scout + Master sintezi — 2-3 cümlə)  
**Risk Faktoru**: 1–5 skala (1=minimal, 5=yüksək)  
**İzlənəcək Növbəti Trigger**: (hansı xəbər/indikator mövqeyi dəyişdirər)

⚠️ Xatırlatma: Yalnız JSON-dakı faktiki API datasına istinad et. \
Kənar fərziyyə, uydurmа qiymət/tarix qəti qadağandır."""

    return prompt


# ══════════════════════════════════════════════════════════════════════════════
#  CLI SINAQ REJIMI  ───  python data_engine.py
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    import sys

    CP_TOKEN = os.getenv("CRYPTOPANIC_TOKEN", "YOUR_TOKEN_HERE")
    if CP_TOKEN == "YOUR_TOKEN_HERE":
        print("❌  CRYPTOPANIC_TOKEN mühit dəyişəni təyin edilməyib.")
        print("    export CRYPTOPANIC_TOKEN=<tokeniniz>")
        sys.exit(1)

    # Test konfiqurasiyası — istədiyiniz tikerlərə dəyişin
    TEST_SYMBOLS = os.getenv("TEST_SYMBOLS", "BTCUSDT,ETHUSDT,SPY").split(",")

    print(f"\n{'═' * 64}")
    print(f"  M.Genat 3.1 Pro — data_engine.py  |  {_utc_now()}")
    print(f"  Tikerlər: {', '.join(TEST_SYMBOLS)}")
    print(f"{'═' * 64}\n")

    t0 = time.perf_counter()
    try:
        ctx    = aggregate_context(TEST_SYMBOLS, CP_TOKEN)
        prompt = build_gemini_prompt(ctx)
        elapsed = time.perf_counter() - t0

        print(f"\n{'─' * 64}")
        print(f"  Data toplama tamamlandı: {elapsed:.2f}s")
        print(f"  Keyfiyyət: {ctx['data_quality']}")
        print(f"{'─' * 64}")

        preview = json.dumps(ctx, ensure_ascii=False, indent=2)
        print("\n[KONTEKST PREVIEW — ilk 2000 simvol]")
        print(preview[:2000], "\n... (kəsildi)")

        print(f"\n{'─' * 64}")
        print("[GEMINI PROMPT PREVIEW — ilk 1000 simvol]")
        print(prompt[:1000], "\n... (kəsildi)")

    except (ValueError, RuntimeError) as e:
        print(f"\n❌  {e}")
        sys.exit(1)
