"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            M.Genat 4.0 Pro  ·  data_engine.py  (Zirehli Versiya)             ║
║                                                                              ║
║   SCOUT  →  Binance (Vision) / yfinance · 4 TF · RSI · EMA · Volume Spike    ║
║   MASTER →  CryptoPanic · RSS (Yahoo/CNBC/Reuters…) · Big-Fish filter        ║
║   ENGINE →  aggregate_context()  ·  build_gemini_prompt()                    ║
║                                                                              ║
║   Python 3.11+  ·  pip install requests yfinance ta pandas feedparser        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    TimeoutError as FuturesTimeout,
)
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import feedparser
import pandas as pd
import requests
import ta
import yfinance as yf

# ──────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  SABITLƏR
# ══════════════════════════════════════════════════════════════════════════════

# MƏRHƏLƏ 0 FİX: ABŞ IP-lərində 451 xətası almamaq üçün qlobal API-yə keçid.
BINANCE_BASE     = "https://data-api.binance.vision" 
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
HTTP_TIMEOUT     = 12
THREAD_TIMEOUT   = 30

# Scout — {label: (binance_iv, yf_iv, yf_period)}
SCOUT_TIMEFRAMES: dict[str, tuple[str, str, str]] = {
    "5m": ("5m",  "5m",  "1d"),
    "1h": ("1h",  "60m", "5d"),
    "4h": ("4h",  "1h",  "5d"),   # yfinance 4h yoxdur → 1h → resample
    "1d": ("1d",  "1d",  "60d"),
}
KLINE_LIMIT          = 210   # EMA-200 üçün
LIQUIDITY_MULTIPLIER = 1.5
VOLUME_LOOKBACK      = 24

MASTER_RSS_FEEDS = [
    ("Yahoo Finance",   "https://finance.yahoo.com/news/rssindex"),
    ("CNBC Finance",    "https://search.cnbc.com/rs/search/combinedcms/view.xml"
                        "?partnerId=wrss01&id=10000664"),
    ("Reuters Biz",     "https://feeds.reuters.com/reuters/businessNews"),
    ("MarketWatch",     "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing.com",   "https://www.investing.com/rss/news.rss"),
    ("FT Markets",      "https://www.ft.com/rss/markets"),
]

BIG_FISH_KEYWORDS: dict[str, int] = {
    "jpmorgan": 3, "blackrock": 3, "goldman sachs": 3, "goldman": 2,
    "vanguard": 2, "fidelity": 2, "morgan stanley": 2, "citadel": 2,
    "bridgewater": 2, "berkshire": 2, "ray dalio": 2, "warren buffett": 2,
    "federal reserve": 3, "fed": 3, "fomc": 3,
    "rate hike": 3, "rate cut": 3, "interest rate": 2,
    "inflation": 2, "cpi": 2, "ppi": 2, "gdp": 2, "recession": 2,
    "yield curve": 3, "treasury": 2, "dollar index": 3, "dxy": 3,
    "ecb": 2, "boj": 2, "imf": 2,
    "hormuz": 3, "middle east": 3, "taiwan": 3, "china": 2,
    "sanctions": 2, "opec": 2, "war": 2, "conflict": 2,
    "semiconductor": 2, "chip": 2, "nvidia": 2, "ai investments": 2,
    "bitcoin etf": 3, "spot etf": 3, "crypto": 1, "sec": 2,
}

MASTER_CRYPTO_COUNT = 3
MASTER_MACRO_COUNT  = 4


# ══════════════════════════════════════════════════════════════════════════════
#  DATA SINIFLAR
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TimeframeSnapshot:
    tf:               str
    source:           str
    last_close:       Optional[float]
    last_candle_time: str
    rsi_14:           Optional[float]
    ema_50:           Optional[float]
    ema_100:          Optional[float]
    ema_200:          Optional[float]
    volume_last:      Optional[float]
    volume_avg24:     Optional[float]
    volume_status:    str   # SPIKE | NORMAL | LOW | UNKNOWN
    error:            Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoutResult:
    symbol:     str
    asset_type: str
    scanned_at: str
    timeframes: dict[str, dict]
    scout_ok:   bool
    errors:     list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NewsItem:
    category:     str   # crypto | macro
    title:        str
    source:       str
    published_at: str
    url:          str
    score:        int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
#  YARDIMÇI FUNKSİYALAR
# ══════════════════════════════════════════════════════════════════════════════

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_round(val: Any, n: int = 4) -> Optional[float]:
    try:
        f = float(val)
        return None if pd.isna(f) else round(f, n)
    except (TypeError, ValueError):
        return None


def _is_crypto(symbol: str) -> bool:
    s = symbol.upper().strip()
    if any(s.endswith(sfx) for sfx in ("USDT", "BUSD", "BTC", "ETH", "BNB", "USDC")):
        return True
    return bool(re.fullmatch(r"[A-Z0-9]{5,12}", s))


def _vol_status(last: Optional[float], avg: Optional[float]) -> str:
    if last is None or avg is None or avg == 0:
        return "UNKNOWN"
    r = last / avg
    if r >= LIQUIDITY_MULTIPLIER:
        return "SPIKE"
    return "NORMAL" if r >= 0.8 else "LOW"


def _rss_time(entry: Any) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
    return _utc_now()


# ══════════════════════════════════════════════════════════════════════════════
#  SCOUT AGENT
# ══════════════════════════════════════════════════════════════════════════════

class ScoutAgent:
    """
    4 zaman dilimini (5m·1h·4h·1d) paralel skan edir.
    Kripto → Binance REST  |  Ənənəvi → yfinance
    """

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def _binance_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        url  = f"{BINANCE_BASE}/api/v3/klines"
        resp = requests.get(
            url,
            params={"symbol": symbol.upper(), "interval": interval,
                    "limit": KLINE_LIMIT},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        df = pd.DataFrame(resp.json(), columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    def _yf_ohlcv(self, symbol: str, iv: str, period: str) -> pd.DataFrame:
        # MƏRHƏLƏ 0 FİX: Kripto "yfinance" fallback formatı (BTCUSDT -> BTC-USD)
        clean_symbol = symbol
        if _is_crypto(symbol) and "USDT" in symbol:
             clean_symbol = symbol.replace("USDT", "-USD")

        df = yf.download(clean_symbol, period=period, interval=iv,
                         progress=False, auto_adjust=True)
        if df.empty:
            raise ValueError(f"yfinance boş [{clean_symbol} {iv}]")

        # MƏRHƏLƏ 0 FİX: Pandas MultiIndex 'arg must be a list' qatili
        if isinstance(df.columns, pd.MultiIndex):
            # Yalnız birinci səviyyəni götürürük (Open, High, Low, Close, Volume)
            df.columns = df.columns.get_level_values(0)
        
        # Sütunları kiçik hərfə çevir
        df.columns = [str(c).lower() for c in df.columns]

        # Datetime index-i sütuna çevir
        df.index.name = "open_time"
        df = df.reset_index()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")

        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    def _resample_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        """1h → 4h resample (yfinance 4h dəstəkləmir)."""
        df = df.set_index("open_time").sort_index()
        result = pd.DataFrame({
            "open":   df["open"].resample("4h").first(),
            "high":   df["high"].resample("4h").max(),
            "low":    df["low"].resample("4h").min(),
            "close":  df["close"].resample("4h").last(),
            "volume": df["volume"].resample("4h").sum(),
        }).dropna(subset=["close"]).reset_index()
        return result

    # ── İndikatorlar ──────────────────────────────────────────────────────────

    def _indicators(self, df: pd.DataFrame) -> dict:
        close = df["close"].dropna()
        n     = len(close)
        out   = {"rsi_14": None, "ema_50": None, "ema_100": None, "ema_200": None}

        if n >= 15:
            out["rsi_14"] = _safe_round(
                ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1])
        if n >= 50:
            out["ema_50"] = _safe_round(
                ta.trend.EMAIndicator(close=close, window=50).ema_indicator().iloc[-1])
        if n >= 100:
            out["ema_100"] = _safe_round(
                ta.trend.EMAIndicator(close=close, window=100).ema_indicator().iloc[-1])
        if n >= 200:
            out["ema_200"] = _safe_round(
                ta.trend.EMAIndicator(close=close, window=200).ema_indicator().iloc[-1])
        return out

    def _volume(self, df: pd.DataFrame) -> tuple[Optional[float], Optional[float], str]:
        if "volume" not in df.columns or len(df) < 2:
            return None, None, "UNKNOWN"
        vols    = df["volume"].dropna()
        last    = _safe_round(vols.iloc[-1], 2)
        lookbk  = vols.iloc[-(VOLUME_LOOKBACK + 1):-1]
        avg     = _safe_round(lookbk.mean(), 2) if len(lookbk) >= 3 else None
        return last, avg, _vol_status(last, avg)

    # ── Tek TF snapshot ───────────────────────────────────────────────────────

    def _snap(self, symbol: str, tf: str, is_crypto: bool) -> TimeframeSnapshot:
        b_iv, yf_iv, yf_period = SCOUT_TIMEFRAMES[tf]
        source = "unknown"
        df = pd.DataFrame()
        try:
            if is_crypto:
                try:
                    df     = self._binance_klines(symbol, b_iv)
                    source = "binance"
                except Exception as e:
                    # MƏRHƏLƏ 0 FİX: Binance 451 verərsə, yfinance-ə fallback et
                    log.warning(f"Binance fail, yfinance-ə keçilir: {symbol}. Səbəb: {e}")
                    if tf == "4h":
                        _, iv1h, p1h = SCOUT_TIMEFRAMES["1h"]
                        df = self._resample_4h(self._yf_ohlcv(symbol, iv1h, p1h))
                    else:
                        df = self._yf_ohlcv(symbol, yf_iv, yf_period)
                    source = "yfinance (fallback)"
            else:
                if tf == "4h":
                    _, iv1h, p1h = SCOUT_TIMEFRAMES["1h"]
                    df = self._resample_4h(self._yf_ohlcv(symbol, iv1h, p1h))
                else:
                    df = self._yf_ohlcv(symbol, yf_iv, yf_period)
                source = "yfinance"

            if df.empty:
                raise ValueError("Boş dataframe")

            last      = df.iloc[-1]
            raw_time  = last.get("open_time")
            try:
                ts   = pd.Timestamp(raw_time)
                ts   = ts.tz_localize("UTC") if ts.tzinfo is None else ts
                ctime = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                ctime = _utc_now()

            ind  = self._indicators(df)
            vl, va, vs = self._volume(df)

            return TimeframeSnapshot(
                tf=tf, source=source,
                last_close=_safe_round(last["close"]),
                last_candle_time=ctime,
                rsi_14=ind["rsi_14"], ema_50=ind["ema_50"],
                ema_100=ind["ema_100"], ema_200=ind["ema_200"],
                volume_last=vl, volume_avg24=va, volume_status=vs,
            )
        except Exception as exc:
            log.warning("Scout TF xəta [%s %s]: %s", symbol, tf, exc)
            return TimeframeSnapshot(
                tf=tf, source=source,
                last_close=None, last_candle_time=_utc_now(),
                rsi_14=None, ema_50=None, ema_100=None, ema_200=None,
                volume_last=None, volume_avg24=None, volume_status="UNKNOWN",
                error=str(exc),
            )

    # ── Açıq API ──────────────────────────────────────────────────────────────

    def scan(self, symbol: str) -> ScoutResult:
        sym       = symbol.upper()
        crypto    = _is_crypto(sym)
        tfs       = list(SCOUT_TIMEFRAMES)
        snaps     : dict[str, dict] = {}
        errors    : list[str] = []

        log.info("Scout → %s (%s) [%s]", sym,
                 "kripto" if crypto else "ənənəvi", " · ".join(tfs))

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="scout") as pool:
            fmap = {pool.submit(self._snap, sym, tf, crypto): tf for tf in tfs}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT):
                tf = fmap[fut]
                try:
                    snap = fut.result()
                    snaps[tf] = snap.to_dict()
                    if snap.error:
                        errors.append(f"{tf}: {snap.error}")
                except FuturesTimeout:
                    errors.append(f"{tf}: timeout")
                except Exception as e:
                    errors.append(f"{tf}: {e}")

        ok = any(snaps.get(t, {}).get("last_close") is not None for t in tfs)
        return ScoutResult(symbol=sym,
                           asset_type="crypto" if crypto else "traditional",
                           scanned_at=_utc_now(),
                           timeframes=snaps, scout_ok=ok, errors=errors)

    def scan_multiple(self, symbols: list[str]) -> list[dict]:
        results: list[dict] = []
        workers = min(len(symbols), 6)
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="scout_m") as pool:
            fmap = {pool.submit(self.scan, s): s for s in symbols}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT * 2):
                sym = fmap[fut]
                try:
                    results.append(fut.result().to_dict())
                except Exception as e:
                    log.error("scan_multiple xəta [%s]: %s", sym, e)
                    results.append({
                        "symbol": sym.upper(), "scout_ok": False,
                        "scanned_at": _utc_now(), "error": str(e),
                        "timeframes": {}, "errors": [str(e)],
                    })
        return results


# ══════════════════════════════════════════════════════════════════════════════
#  MASTER AGENT
# ══════════════════════════════════════════════════════════════════════════════

class MasterAgent:
    """
    Qlobal makro + institusional xəbərləri toplayır.
    CryptoPanic → kripto  |  RSS lentlər → makro
    BIG_FISH_KEYWORDS ilə weighted score verilir.
    """

    def __init__(self, cryptopanic_token: str):
        self.cp_token = cryptopanic_token

    # ── Score ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _score(text: str) -> int:
        t = text.lower()
        return sum(w for kw, w in BIG_FISH_KEYWORDS.items() if kw in t)

    # ── CryptoPanic ───────────────────────────────────────────────────────────

    def _crypto_news(self, currencies: str, count: int) -> list[NewsItem]:
        if not self.cp_token:
            log.warning("CRYPTOPANIC_TOKEN təyin edilməyib — kripto xəbəri yoxdur.")
            return []
        try:
            resp = requests.get(
                f"{CRYPTOPANIC_BASE}/posts/",
                params={"auth_token": self.cp_token,
                        "currencies": currencies.upper(),
                        "kind": "news", "public": "true"},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("results", [])
        except Exception as exc:
            log.warning("CryptoPanic xəta: %s", exc)
            return []

        return [
            NewsItem(
                category="crypto",
                title=e.get("title", "").strip(),
                source=e.get("source", {}).get("title", "CryptoPanic"),
                published_at=e.get("published_at", _utc_now()),
                url=e.get("url", ""),
                score=self._score(e.get("title", "")),
            )
            for e in raw[:count]
            if e.get("title", "").strip()
        ]

    # ── RSS ───────────────────────────────────────────────────────────────────

    def _one_rss(self, label: str, url: str) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            feed   = feedparser.parse(url)
            src    = getattr(feed.feed, "title", None) or label
            for e in feed.entries:
                title   = getattr(e, "title",   "").strip()
                summary = getattr(e, "summary", "").strip()
                if not title:
                    continue
                score = self._score(f"{title} {summary}")
                if score == 0:
                    continue
                items.append(NewsItem(
                    category="macro", title=title, source=src,
                    published_at=_rss_time(e),
                    url=getattr(e, "link", ""), score=score,
                ))
        except Exception as exc:
            log.warning("RSS xəta [%s]: %s", label, exc)
        return items

    def _macro_news(self, count: int) -> list[NewsItem]:
        all_: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=len(MASTER_RSS_FEEDS),
                                thread_name_prefix="rss") as pool:
            fmap = {pool.submit(self._one_rss, lbl, url): lbl
                    for lbl, url in MASTER_RSS_FEEDS}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT):
                try:
                    all_.extend(fut.result())
                except Exception as e:
                    log.warning("RSS future xəta: %s", e)

        # Dublikat sil
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in all_:
            key = item.title.lower()[:80]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        unique.sort(key=lambda x: x.score, reverse=True)
        return unique[:count]

    # ── Açıq API ──────────────────────────────────────────────────────────────

    def collect(self,
                currencies:   str = "BTC,ETH",
                crypto_count: int = MASTER_CRYPTO_COUNT,
                macro_count:  int = MASTER_MACRO_COUNT) -> dict:
        log.info("Master → xəbər toplama başladı")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="master") as pool:
            fc = pool.submit(self._crypto_news, currencies, crypto_count)
            fm = pool.submit(self._macro_news,  macro_count)
            try:
                crypto = fc.result(timeout=THREAD_TIMEOUT)
            except Exception as e:
                log.error("CryptoPanic timeout: %s", e)
                crypto = []
            try:
                macro = fm.result(timeout=THREAD_TIMEOUT)
            except Exception as e:
                log.error("RSS timeout: %s", e)
                macro = []

        combined = crypto + macro
        combined.sort(key=lambda x: x.score, reverse=True)
        top5 = [i.to_dict() for i in combined[:5]]

        log.info("Master → %d kripto + %d makro xəbər", len(crypto), len(macro))
        return {
            "collected_at": _utc_now(),
            "crypto_news":  [i.to_dict() for i in crypto],
            "macro_news":   [i.to_dict() for i in macro],
            "top_signals":  top5,
            "master_ok":    len(combined) > 0,
        }

# ══════════════════════════════════════════════════════════════════════════════
#  MEMORY AGENT (RAG - Dərin Yaddaş)
# ══════════════════════════════════════════════════════════════════════════════
import os
import PyPDF2

class MemoryAgent:
    """
    knowledge_base/ qovluğundakı PDF və TXT hesabatları oxuyur.
    M.Genat-a institusional strategiyaları 'əzbərlədir'.
    """
    def __init__(self, kb_path="knowledge_base"):
        self.kb_path = kb_path
        if not os.path.exists(self.kb_path):
            os.makedirs(self.kb_path)
            log.info(f"📁 Qovluq yaradıldı: {self.kb_path}. PDF-ləri bura atın.")

    def read_reports(self, max_chars=15000) -> str:
        """Qovluqdakı sənədləri oxuyur və birləşdirir."""
        compiled_text = ""
        
        if not os.path.exists(self.kb_path):
            return "Dərin yaddaş qovluğu tapılmadı."

        files = [f for f in os.listdir(self.kb_path) if f.endswith(('.pdf', '.txt'))]
        if not files:
            return "Məlumat bazasında aktiv hesabat yoxdur."

        log.info(f"Memory → {len(files)} sənəd oxunur: {', '.join(files)}")

        for file in files:
            file_path = os.path.join(self.kb_path, file)
            try:
                if file.endswith('.pdf'):
                    with open(file_path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        text = f"\n--- SƏNƏD: {file} ---\n"
                        # İlk 3 səhifəni oxuyuruq (Token limitinə qənaət üçün)
                        for page in reader.pages[:3]: 
                            text += page.extract_text() + "\n"
                        compiled_text += text
                
                elif file.endswith('.txt'):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        compiled_text += f"\n--- SƏNƏD: {file} ---\n" + f.read(5000) + "\n"
            
            except Exception as e:
                log.error(f"Sənəd oxunma xətası [{file}]: {e}")

        # Əgər mətn çox uzundursa, modelin beyni qarışmasın deyə kəsirik
        if len(compiled_text) > max_chars:
            compiled_text = compiled_text[:max_chars] + "\n...[Mətn çox uzun olduğu üçün kəsildi]..."
            
        return compiled_text

# ══════════════════════════════════════════════════════════════════════════════
#  AGGREGATOR (Hakim, Yaddaş və Korelyasiya daxil)
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_context(
    symbols:           list[str],
    cryptopanic_token: str,
    news_currencies:   str = "BTC,ETH",
) -> dict:
    if not symbols:
        raise ValueError("Ən azı bir ticker tələb olunur.")

    log.info("Engine → %s", ", ".join(s.upper() for s in symbols))

    # YENİ (CƏRRAHİYYƏ 1): Görünməz İplər (Korelyasiya) - DXY və Qızılı gizlicə əlavə edirik
    macro_symbols = ["DX-Y.NYB", "GC=F"] 
    combined_symbols = list(set([s.upper() for s in symbols] + macro_symbols))

    scout  = ScoutAgent()
    master = MasterAgent(cryptopanic_token)
    memory = MemoryAgent() 

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="engine") as pool: 
        # İndi Scout həm sənin istədiyin (məs: BTC), həm də DXY-ni skan edəcək
        fs = pool.submit(scout.scan_multiple, combined_symbols)
        fm = pool.submit(master.collect, news_currencies)
        fmem = pool.submit(memory.read_reports)

        try:
            scout_res = fs.result(timeout=THREAD_TIMEOUT * 2)
        except Exception as e:
            log.error("Scout engine xəta: %s", e)
            scout_res = []

        try:
            master_res = fm.result(timeout=THREAD_TIMEOUT * 2)
        except Exception as e:
            log.error("Master engine xəta: %s", e)
            master_res = {
                "collected_at": _utc_now(),
                "crypto_news": [], "macro_news": [],
                "top_signals": [], "master_ok": False,
            }
            
        try:
            memory_res = fmem.result(timeout=THREAD_TIMEOUT)
        except Exception as e:
            log.error("Memory engine xəta: %s", e)
            memory_res = "Yaddaş oxuna bilmədi."

    quality = {
        "scout_ok":        any(r.get("scout_ok", False) for r in scout_res),
        "crypto_news_ok":  len(master_res.get("crypto_news", [])) > 0,
        "macro_news_ok":   len(master_res.get("macro_news",  [])) > 0,
        "memory_ok":       len(memory_res) > 50, 
        "symbols_scanned": [r.get("symbol") for r in scout_res],
        "tfs_available":   list(SCOUT_TIMEFRAMES),
    }

    if not any([quality["scout_ok"],
                quality["crypto_news_ok"],
                quality["macro_news_ok"]]):
        raise RuntimeError(
            "Bütün data mənbələri uğursuz oldu. Gemini-yə boş prompt göndərilmir.")

    return {
        "engine":       "M.Genat 4.0 Pro",
        "generated_at": _utc_now(),
        # Sənə sadəcə öz axtardığın tickerlərin siyahısını göstəririk ki, beynin qarışmasın
        "scout":        {"symbols": symbols, "results": scout_res},
        "master":       master_res,
        "memory":       memory_res, 
        "data_quality": quality,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDER (Judge & Korelyasiya Məntiqi ilə)
# ══════════════════════════════════════════════════════════════════════════════

def build_gemini_prompt(context: dict) -> str:
    if not context:
        raise ValueError("Boş kontekst. Prompt yaradılmır.")

    quality = context.get("data_quality", {})
    if not any([quality.get("scout_ok"),
                quality.get("crypto_news_ok"),
                quality.get("macro_news_ok")]):
        raise ValueError("Bütün data mənbələri uğursuz. Boş prompt göndərilmir.")

    json_block = json.dumps(context, ensure_ascii=False)

    warns = []
    if not quality.get("scout_ok"):
        warns.append("⚠️ Scout texniki datası əlçatmaz — RSI/EMA/Volume məhduddur.")
    if not quality.get("crypto_news_ok"):
        warns.append("⚠️ Kripto xəbərləri boşdur.")
    if not quality.get("macro_news_ok"):
        warns.append("⚠️ Makro RSS lenti boşdur.")
    warn_block = ("\n[SİSTEM XƏBƏRDARLIĞI]\n" + "\n".join(warns) + "\n") if warns else ""

    h = datetime.now(timezone.utc).hour
    if   4  <= h < 8:  session = "Asiya Bağlanışı / Avropa Açılışı"
    elif 8  <= h < 12: session = "London Açılışı"
    elif 12 <= h < 17: session = "New York Açılışı"
    elif 17 <= h < 21: session = "NY Bağlanışı / After-Hours"
    else:              session = "Gecə / Asiya Açılışı"

    symbols_str = ", ".join(str(s) for s in quality.get("symbols_scanned", ["N/A"]))
    tfs_str     = " · ".join(quality.get("tfs_available", []))

    return f"""Sən M.Genat 4.0 Pro-san — eyni anda 3 fərqli şəxsiyyəti özündə birləşdirən \
peşəkar maliyyə ekosistemisən (Multi-Agent):

🔬 SCOUT (Day Trader) — 5m/1h/4h/1d RSI · EMA crossları · Volume Spike real-time.
🌍 MASTER (Macro Investor) — FED/ECB qərarları · CryptoPanic · Geopolitik xəbərlər.
🧠 JUDGE & MEMORY (Hedge Fund Manager) — Xüsusi bazadakı institusional PDF-ləri oxuyur və texniki xəbərlərlə toqquşdurur.
{warn_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSIYA  : {session}
TİKERLƏR : {symbols_str}
TF-LƏR   : {tfs_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MÜTLƏQ QAYDALARIN (Zero Hallucination Protocol):
1. Heç bir qiyməti, rəqəmi uydurma. YALNIZ aşağıdakı JSON-dakı faktiki API datasına istinad et.
2. [HAKİM MƏNTİQİ]: Əgər Scout "Al" deyirsə, amma Master xəbərləri (və ya Memory hesabatları) böhran siqnalı verirsə, sən Hakimsən! Ziddiyyəti açıqla və riskin çox olduğunu bildir.
3. [KORELYASİYA MƏNTİQİ]: JSON-da gizli şəkildə DXY (Dollar İndeksi - DX-Y.NYB) və Qızıl (GC=F) dataları var. Əsas aktivin vəziyyətini mütləq DXY və Qızılın trendi ilə müqayisə et (məs: "DXY qalxır, bu aktiv üçün riskdir").
4. Volume SPIKE varsa — anomaliyanı qeyd et.

--- CANLI DATA (JSON) ---
{json_block}
--- DATA SONU ---

İndi bu real məlumatlara əsasən {session} üçün Azərbaycan dilində \
M.Genat 4.0 Pro Hesabatını hazırla:

## 🔬 SCOUT ANALİZİ (Texniki Kəşfiyyat)
Hər TF üçün: Qiymət · RSI zonu · EMA mövqeyi · Volume statusu (Xüsusilə Spike olanlar).

## 🌍 MASTER & MEMORY ANALİZİ (İnstitusional Düşüncə)
Xəbərlərdən və JSON-dakı "memory" (Yaddaş) bloku daxilindəki sənədlərdən çıxan əsas qlobal mənzərə. İnstitusional oyunçular bazara necə baxır? (Yaddaşdakı hesabatları xüsusi qeyd et).

## ⚖️ JUDGE: ÇARPAZ TOQQUŞMA VƏ KORELYASİYA
Scout-un rəqəmləri ilə Master-in xəbərləri toqquşurmu? DXY (Dollar) və Qızılın hərəkəti sənin əsas aktivinə necə təsir edir? Ziddiyyət və ya tam uzlaşma (Confluence) varmı?

## 📊 SENARYO MATRİSİ
| Senaryo    | Tetikləyici Şərt            | Hədəf (EMA-dan) | Ehtimal |
|------------|----------------------------|-----------------|---------|
| 🟢 Bullish | JSON-dakı datadan doldur   | uydurma         | ?%      |
| 🔴 Bearish | JSON-dakı datadan doldur   | uydurma         | ?%      |
| 🟡 Base    | JSON-dakı datadan doldur   | uydurma         | ?%      |

## 💼 HEDGE-FUND YEKUN TÖVSİYƏSİ
Mövqe: Alış / Satış / Gözlə
Əsas Səbəb: Judge Agentin yekun hökmü.
Risk Faktoru: 1–5
Növbəti Trigger: Hansı data/xəbər mövqeyi dəyişər?

⚠️ Yalnız JSON-dakı dataya istinad et. Uydurma qadağandır."""


# ══════════════════════════════════════════════════════════════════════════════
#  CLI SINAQ
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os, sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s │ %(levelname)-8s │ %(message)s")

    CP = os.getenv("CRYPTOPANIC_TOKEN", "")
    if not CP:
        print("❌  export CRYPTOPANIC_TOKEN=<token>"); sys.exit(1)

    SYMS = os.getenv("TEST_SYMBOLS", "BTCUSDT,SPY").split(",")
    t0   = time.perf_counter()
    ctx  = aggregate_context(SYMS, CP)
    pmt  = build_gemini_prompt(ctx)
    print(f"\nTamamlandı: {time.perf_counter()-t0:.1f}s")
    print(f"Keyfiyyət : {ctx['data_quality']}")
    print("\n--- PROMPT PREVIEW (ilk 600 simvol) ---")
    print(pmt[:600])
