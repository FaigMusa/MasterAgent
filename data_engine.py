"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            M.Genat 5.0 Pro  ·  data_engine.py  (Callback Versiyası)          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Callable

import feedparser
import pandas as pd
import requests
import ta
import yfinance as yf

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  SABİTLƏR
# ══════════════════════════════════════════════════════════════════════════════
BINANCE_BASE     = "https://data-api.binance.vision" 
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
HTTP_TIMEOUT     = 12
THREAD_TIMEOUT   = 30

SCOUT_TIMEFRAMES: dict[str, tuple[str, str, str]] = {
    "5m": ("5m",  "5m",  "5d"),   # 5 dəqiqəlik data maksimum 5 gün geriyə baxır
    "1h": ("1h",  "60m", "1mo"),  # EMA 200 üçün 1 aya qaldırdıq
    "4h": ("4h",  "1h",  "1mo"),  
    "1d": ("1d",  "1d",  "1y"),   # Günlük EMA 200 üçün mütləq 1 il lazımdır!
}
KLINE_LIMIT          = 210   
LIQUIDITY_MULTIPLIER = 1.5
VOLUME_LOOKBACK      = 24

MASTER_RSS_FEEDS = [
    ("Yahoo Finance",   "https://finance.yahoo.com/news/rssindex"),
    ("CNBC Finance",    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("Reuters Biz",     "https://feeds.reuters.com/reuters/businessNews"),
    ("MarketWatch",     "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing.com",   "https://www.investing.com/rss/news.rss"),
    ("FT Markets",      "https://www.ft.com/rss/markets"),
]

MULTI_NEWS_SOURCES = {
    "Investing_Global": "https://www.investing.com/rss/news_285.rss",
    "Investing_Crypto": "https://www.investing.com/rss/news_301.rss",
    "Yahoo_Finance": "https://finance.yahoo.com/news/rssindex"
}

BIG_FISH_KEYWORDS: dict[str, int] = {
    "jpmorgan": 3, "blackrock": 3, "goldman sachs": 3, "goldman": 2,
    "federal reserve": 3, "fed": 3, "fomc": 3, "rate hike": 3, "rate cut": 3, 
    "inflation": 2, "cpi": 2, "dollar index": 3, "dxy": 3, "middle east": 3, 
    "war": 2, "semiconductor": 2, "ai investments": 2, "bitcoin etf": 3,
}

MASTER_CRYPTO_COUNT = 3
MASTER_MACRO_COUNT  = 4

# ══════════════════════════════════════════════════════════════════════════════
#  DATA SINIFLARI
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class TimeframeSnapshot:
    tf: str; source: str; last_close: Optional[float]; last_candle_time: str
    rsi_14: Optional[float]; ema_50: Optional[float]; ema_100: Optional[float]
    ema_200: Optional[float]; volume_last: Optional[float]; volume_avg24: Optional[float]
    volume_status: str; error: Optional[str] = None
    def to_dict(self) -> dict: return asdict(self)

@dataclass
class ScoutResult:
    symbol: str; asset_type: str; scanned_at: str; timeframes: dict[str, dict]
    scout_ok: bool; errors: list[str] = field(default_factory=list)
    def to_dict(self) -> dict: return asdict(self)

@dataclass
class NewsItem:
    category: str; title: str; source: str; published_at: str; url: str; score: int = 0
    def to_dict(self) -> dict: return asdict(self)

# ══════════════════════════════════════════════════════════════════════════════
#  YARDIMÇI FUNKSİYALAR
# ══════════════════════════════════════════════════════════════════════════════
def _utc_now() -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _safe_round(val: Any, n: int = 4) -> Optional[float]:
    try: return None if pd.isna(float(val)) else round(float(val), n)
    except: return None

def _is_crypto(symbol: str) -> bool:
    s = symbol.upper().strip()
    if any(s.endswith(sfx) for sfx in ("USDT", "BUSD", "BTC", "ETH", "BNB", "USDC")): return True
    return bool(re.fullmatch(r"[A-Z0-9]{5,12}", s))

def _vol_status(last: Optional[float], avg: Optional[float]) -> str:
    if last is None or avg is None or avg == 0: return "UNKNOWN"
    r = last / avg
    if r >= LIQUIDITY_MULTIPLIER: return "SPIKE"
    return "NORMAL" if r >= 0.8 else "LOW"

def _rss_time(entry: Any) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try: return datetime(*t[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except: pass
    return _utc_now()

# ══════════════════════════════════════════════════════════════════════════════
#  GOOGLE RESEARCH VƏ KONSENSUS (CALLBACK İLƏ)
# ══════════════════════════════════════════════════════════════════════════════

def research_missing_intel(topic: str, llm_callback: Callable) -> str:
    """Gemini funksiyasını parametr kimi qəbul edib işlədir."""
    if not llm_callback: return "Axtarış mühərriki qoşulmayıb."
    prompt = f"Sən peşəkar maliyyə analitikisən. QƏTİ QADAĞANDIR uydurmaq. Yalnız REAL mənbələrdən bu mövzunun datalarını tap: {topic}"
    return llm_callback(prompt)

def fetch_multi_source_news() -> str:
    compiled_news = ""
    for source_name, url in MULTI_NEWS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            compiled_news += f"\n📰 **MƏNBƏ: {source_name}**\n"
            for entry in feed.entries[:3]: compiled_news += f"- {entry.title}\n"
        except Exception as e: log.error(f"Xəbər xətası ({source_name}): {e}")
    return compiled_news

def build_consensus_report(asset: str = None, llm_callback: Callable = None) -> str:
    if not llm_callback: return "Analizator qoşulmayıb."
    raw_news = fetch_multi_source_news()
    
    if not raw_news.strip(): 
        raw_news = research_missing_intel("Bazarın bugünkü ümumi vəziyyəti", llm_callback)

    prompt = "Sən M.Genat Hedge Fund analitikisən. Xəbərləri çarpaz yoxla, trendi tap. Uydurma qadağandır."
    if asset: prompt += f"\n👉 TƏLƏB: Bu xəbərlərin **{asset}** aktivinə təsirini (Al/Sat) analiz et."
    else: prompt += "\n👉 TƏLƏB: Bazardakı fürsəti/riski vurğula."
    prompt += f"\n\n🚨 BAZA:\n{raw_news}"
    return llm_callback(prompt)

def check_anomalies(symbols: list[str]) -> list[str]:
    anomalies = []
    for sym in symbols:
        try:
            yf_sym = f"{sym}-USD" if "USDT" in sym else sym
            ticker = yf.Ticker(yf_sym)
            hist = ticker.history(period="5d", interval="5m") 
            if hist.empty or len(hist) < 2: continue
            
            vol_mean, last_vol = hist['Volume'][:-1].mean(), hist['Volume'].iloc[-1]
            prev_close, last_close = hist['Close'].iloc[-2], hist['Close'].iloc[-1]
            price_change = ((last_close - prev_close) / prev_close) * 100
            
            if (last_vol > (vol_mean * 2) and vol_mean > 0) or abs(price_change) > 1.5:
                trend = "🟢 QALXMA" if price_change > 0 else "🔴 DÜŞMƏ"
                anomalies.append(f"⚠️ **{sym}** - {trend} | Dəyişim: {price_change:.2f}% | Həcm: {last_vol/vol_mean if vol_mean>0 else 0:.1f}x")
        except: pass 
    return anomalies

# ══════════════════════════════════════════════════════════════════════════════
#  SCOUT & MASTER AGENTS
# ══════════════════════════════════════════════════════════════════════════════

class ScoutAgent:
    def _binance_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        resp = requests.get(f"{BINANCE_BASE}/api/v3/klines", params={"symbol": symbol.upper(), "interval": interval, "limit": KLINE_LIMIT}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json(), columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"): df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    def _yf_ohlcv(self, symbol: str, iv: str, period: str) -> pd.DataFrame:
        clean_symbol = symbol.replace("USDT", "-USD") if _is_crypto(symbol) and "USDT" in symbol else symbol
        df = yf.download(clean_symbol, period=period, interval=iv, progress=False, auto_adjust=True)
        if df.empty: raise ValueError("Boş")
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        df.index.name = "open_time"
        df = df.reset_index()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    def _resample_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.set_index("open_time").sort_index()
        return pd.DataFrame({"open": df["open"].resample("4h").first(), "high": df["high"].resample("4h").max(), "low": df["low"].resample("4h").min(), "close": df["close"].resample("4h").last(), "volume": df["volume"].resample("4h").sum()}).dropna(subset=["close"]).reset_index()

    def _indicators(self, df: pd.DataFrame) -> dict:
        close, n = df["close"].dropna(), len(df["close"].dropna())
        out = {"rsi_14": None, "ema_50": None, "ema_100": None, "ema_200": None}
        if n >= 15: out["rsi_14"] = _safe_round(ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1])
        if n >= 50: out["ema_50"] = _safe_round(ta.trend.EMAIndicator(close=close, window=50).ema_indicator().iloc[-1])
        if n >= 100: out["ema_100"] = _safe_round(ta.trend.EMAIndicator(close=close, window=100).ema_indicator().iloc[-1])
        if n >= 200: out["ema_200"] = _safe_round(ta.trend.EMAIndicator(close=close, window=200).ema_indicator().iloc[-1])
        return out

    def _volume(self, df: pd.DataFrame, symbol: str) -> tuple[Optional[float], Optional[float], str]:
    # Əgər aktiv İndeksdirsə (DXY və s.) həcm hesablama
    if symbol.startswith("^") or symbol == "DX-Y.NYB":
        return None, None, "İNDEX (Tətbiq Olunmur)"
        
    if "volume" not in df.columns or len(df) < 2: return None, None, "UNKNOWN"
    vols = df["volume"].dropna()
    # Yalnız həcm > 0 olanları yoxlayırıq
    if vols.sum() == 0: return None, None, "İNDEX (Tətbiq Olunmur)"
    
    last = _safe_round(vols.iloc[-1], 2)
    lookbk = vols.iloc[-(VOLUME_LOOKBACK + 1):-1]
    avg = _safe_round(lookbk.mean(), 2) if len(lookbk) >= 3 else None
    return last, avg, _vol_status(last, avg)

    def _snap(self, symbol: str, tf: str, is_crypto: bool) -> TimeframeSnapshot:
        b_iv, yf_iv, yf_period = SCOUT_TIMEFRAMES[tf]
        source = "unknown"
        try:
            if is_crypto:
                try: 
                    df, source = self._binance_klines(symbol, b_iv), "binance"
                except Exception:
                    if tf == "4h": 
                        df = self._resample_4h(self._yf_ohlcv(symbol, SCOUT_TIMEFRAMES["1h"][1], SCOUT_TIMEFRAMES["1h"][2]))
                    else: 
                        df = self._yf_ohlcv(symbol, yf_iv, yf_period)
                    source = "yfinance (fallback)"
            else: # Ənənəvi aktivlər üçün (XLU, COPX, SMH və s.)
                if tf == "4h": 
                    df = self._resample_4h(self._yf_ohlcv(symbol, SCOUT_TIMEFRAMES["1h"][1], SCOUT_TIMEFRAMES["1h"][2]))
                else: 
                    try:
                        df = self._yf_ohlcv(symbol, yf_iv, yf_period)
                    except ValueError:
                        # FALLBACK: Əgər 5m çökərsə, 15m ilə bir daha sına (XLU xətası üçün həll)
                        log.warning(f"⚠️ {symbol} üçün {tf} tapılmadı. Fallback (15m) işə düşür...")
                        if tf == "5m": 
                            df = self._yf_ohlcv(symbol, "15m", "5d")
                        else: 
                            raise # Digər TF-lərdə xətanı burax
                source = "yfinance"

            if df.empty: raise ValueError("Boş")
            last = df.iloc[-1]
            
            try: 
                ctime = pd.Timestamp(last.get("open_time")).tz_localize("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception: 
                ctime = _utc_now()
                
            ind = self._indicators(df)
            
            # Yeni Həcm (Volume) çağırışı (DXY probleminin həlli)
            vl, va, vs = self._volume(df, symbol)
            
            return TimeframeSnapshot(tf=tf, source=source, last_close=_safe_round(last["close"]), last_candle_time=ctime, **ind, volume_last=vl, volume_avg24=va, volume_status=vs)
            
        except Exception as exc:
            return TimeframeSnapshot(tf=tf, source=source, last_close=None, last_candle_time=_utc_now(), rsi_14=None, ema_50=None, ema_100=None, ema_200=None, volume_last=None, volume_avg24=None, volume_status="UNKNOWN", error=str(exc))
    def scan(self, symbol: str) -> ScoutResult:
        sym, crypto, tfs, snaps, errors = symbol.upper(), _is_crypto(symbol), list(SCOUT_TIMEFRAMES), {}, []
        with ThreadPoolExecutor(max_workers=4) as pool:
            fmap = {pool.submit(self._snap, sym, tf, crypto): tf for tf in tfs}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT):
                tf = fmap[fut]
                try:
                    snap = fut.result()
                    snaps[tf] = snap.to_dict()
                    if snap.error: errors.append(f"{tf}: {snap.error}")
                except Exception as e: errors.append(f"{tf}: {e}")
        ok = any(snaps.get(t, {}).get("last_close") is not None for t in tfs)
        return ScoutResult(symbol=sym, asset_type="crypto" if crypto else "traditional", scanned_at=_utc_now(), timeframes=snaps, scout_ok=ok, errors=errors)

    def scan_multiple(self, symbols: list[str]) -> list[dict]:
        results = []
        with ThreadPoolExecutor(max_workers=min(len(symbols), 6)) as pool:
            fmap = {pool.submit(self.scan, s): s for s in symbols}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT * 2):
                sym = fmap[fut]
                try: results.append(fut.result().to_dict())
                except Exception as e: results.append({"symbol": sym.upper(), "scout_ok": False, "scanned_at": _utc_now(), "error": str(e), "timeframes": {}, "errors": [str(e)]})
        return results

class MasterAgent:
    def __init__(self, cryptopanic_token: str): self.cp_token = cryptopanic_token
    @staticmethod
    def _score(text: str) -> int: return sum(w for kw, w in BIG_FISH_KEYWORDS.items() if kw in text.lower())

    def _crypto_news(self, currencies: str, count: int) -> list[NewsItem]:
        if not self.cp_token: return []
        try:
            resp = requests.get(f"{CRYPTOPANIC_BASE}/posts/", params={"auth_token": self.cp_token, "currencies": currencies.upper(), "kind": "news", "public": "true"}, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json().get("results", [])
        except: return []
        return [NewsItem(category="crypto", title=e.get("title", "").strip(), source=e.get("source", {}).get("title", "CryptoPanic"), published_at=e.get("published_at", _utc_now()), url=e.get("url", ""), score=self._score(e.get("title", ""))) for e in raw[:count] if e.get("title", "").strip()]

    def _one_rss(self, label: str, url: str) -> list[NewsItem]:
        items = []
        try:
            feed = feedparser.parse(url)
            src = getattr(feed.feed, "title", None) or label
            for e in feed.entries:
                title, summary = getattr(e, "title", "").strip(), getattr(e, "summary", "").strip()
                if not title: continue
                score = self._score(f"{title} {summary}")
                if score > 0: items.append(NewsItem(category="macro", title=title, source=src, published_at=_rss_time(e), url=getattr(e, "link", ""), score=score))
        except: pass
        return items

    def _macro_news(self, count: int) -> list[NewsItem]:
        all_, seen, unique = [], set(), []
        with ThreadPoolExecutor(max_workers=len(MASTER_RSS_FEEDS)) as pool:
            fmap = {pool.submit(self._one_rss, lbl, url): lbl for lbl, url in MASTER_RSS_FEEDS}
            for fut in as_completed(fmap, timeout=THREAD_TIMEOUT):
                try: all_.extend(fut.result())
                except: pass
        for item in all_:
            key = item.title.lower()[:80]
            if key not in seen: seen.add(key); unique.append(item)
        unique.sort(key=lambda x: x.score, reverse=True)
        return unique[:count]

    def collect(self, currencies: str = "BTC,ETH", crypto_count: int = MASTER_CRYPTO_COUNT, macro_count: int = MASTER_MACRO_COUNT) -> dict:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fc, fm = pool.submit(self._crypto_news, currencies, crypto_count), pool.submit(self._macro_news, macro_count)
            try: crypto = fc.result(timeout=THREAD_TIMEOUT)
            except: crypto = []
            try: macro = fm.result(timeout=THREAD_TIMEOUT)
            except: macro = []
        combined = sorted(crypto + macro, key=lambda x: x.score, reverse=True)
        return {"collected_at": _utc_now(), "crypto_news": [i.to_dict() for i in crypto], "macro_news": [i.to_dict() for i in macro], "top_signals": [i.to_dict() for i in combined[:5]], "master_ok": len(combined) > 0}

import os
import PyPDF2
class MemoryAgent:
    def __init__(self, kb_path="knowledge_base"):
        self.kb_path = kb_path
        if not os.path.exists(self.kb_path): os.makedirs(self.kb_path)
    def read_reports(self, max_chars=15000) -> str:
        if not os.path.exists(self.kb_path): return "Yaddaş yoxdur."
        files = [f for f in os.listdir(self.kb_path) if f.endswith(('.pdf', '.txt'))]
        if not files: return "Yaddaş boşdur."
        compiled_text = ""
        for file in files:
            file_path = os.path.join(self.kb_path, file)
            try:
                if file.endswith('.pdf'):
                    with open(file_path, 'rb') as f:
                        for page in PyPDF2.PdfReader(f).pages[:3]: compiled_text += page.extract_text() + "\n"
                elif file.endswith('.txt'):
                    with open(file_path, 'r', encoding='utf-8') as f: compiled_text += f"\n--- {file} ---\n" + f.read(5000) + "\n"
            except: pass
        if len(compiled_text) > max_chars: compiled_text = compiled_text[:max_chars] + "\n...[Kəsildi]..."
        return compiled_text

# ══════════════════════════════════════════════════════════════════════════════
#  AGGREGATOR 
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_context(symbols: list[str], cryptopanic_token: str, news_currencies: str = "BTC,ETH", llm_callback: Callable = None) -> dict:
    if not symbols: raise ValueError("Ticker tələb olunur.")
    macro_symbols = ["DX-Y.NYB", "GC=F"] 
    combined_symbols = list(set([s.upper() for s in symbols] + macro_symbols))

    scout, master, memory = ScoutAgent(), MasterAgent(cryptopanic_token), MemoryAgent() 

    with ThreadPoolExecutor(max_workers=3) as pool: 
        fs, fm, fmem = pool.submit(scout.scan_multiple, combined_symbols), pool.submit(master.collect, news_currencies), pool.submit(memory.read_reports)
        try: scout_res = fs.result(timeout=THREAD_TIMEOUT * 2)
        except: scout_res = []
        try: master_res = fm.result(timeout=THREAD_TIMEOUT * 2)
        except: master_res = {"collected_at": _utc_now(), "crypto_news": [], "macro_news": [], "top_signals": [], "master_ok": False}
        try: memory_res = fmem.result(timeout=THREAD_TIMEOUT)
        except: memory_res = "Yaddaş oxuna bilmədi."

    # Callback vasitəsilə Google Research (Master_agent-i idxal etmədən)
    google_research_fallback = {}
    if llm_callback:
        for res in scout_res:
            if not res.get("scout_ok"):
                sym = res.get("symbol")
                google_research_fallback[sym] = research_missing_intel(f"{sym} ticker current price and macro impact", llm_callback)

    quality = {
        "scout_ok":        any(r.get("scout_ok", False) for r in scout_res),
        "crypto_news_ok":  len(master_res.get("crypto_news", [])) > 0,
        "macro_news_ok":   len(master_res.get("macro_news",  [])) > 0,
        "memory_ok":       len(memory_res) > 50, 
        "symbols_scanned": [r.get("symbol") for r in scout_res],
        "tfs_available":   list(SCOUT_TIMEFRAMES),
    }

    return {
        "engine":       "M.Genat 5.0 Pro",
        "generated_at": _utc_now(),
        "scout":        {"symbols": symbols, "results": scout_res},
        "master":       master_res,
        "memory":       memory_res, 
        "google_research": google_research_fallback, 
        "data_quality": quality,
    }

def build_gemini_prompt(context: dict) -> str:
    if not context: raise ValueError("Boş kontekst.")
    json_block = json.dumps(context, ensure_ascii=False)
    return f"""Sən M.Genat 5.0 Pro-san.
MÜTLƏQ QAYDALAR (Zero Hallucination):
1. Heç bir qiyməti uydurma! YALNIZ aşağıdakı JSON-dakı API datasına və ya 'google_research' blokuna istinad et.
2. [HAKİM MƏNTİQİ]: Ziddiyyəti açıqla.
3. [KORELYASİYA]: DXY və Qızılın trendi ilə müqayisə et.

--- CANLI DATA (JSON) ---
{json_block}
--- DATA SONU ---

Real məlumatlara əsasən Azərbaycan dilində hesabat hazırla.
"""
