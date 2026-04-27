"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            M.Genat 5.0 Pro  ·  data_engine.py  (Zirehli Versiya)             ║
║                                                                              ║
║   SCOUT  →  Binance (Vision) / yfinance · 5m/1h/4h/1d                        ║
║   MASTER →  CryptoPanic · RSS · Investing.com · Google Research Fallback     ║
║   ENGINE →  aggregate_context()  ·  build_gemini_prompt()                    ║
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
#  SABITLƏR VƏ QLOBAL MƏNBƏLƏR
# ══════════════════════════════════════════════════════════════════════════════

BINANCE_BASE     = "https://data-api.binance.vision" 
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
HTTP_TIMEOUT     = 12
THREAD_TIMEOUT   = 30

SCOUT_TIMEFRAMES: dict[str, tuple[str, str, str]] = {
    "5m": ("5m",  "5m",  "1d"),
    "1h": ("1h",  "60m", "5d"),
    "4h": ("4h",  "1h",  "5d"),   
    "1d": ("1d",  "1d",  "60d"),
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

BIG_FISH_KEYWORDS: dict[str, int] = {
    "jpmorgan": 3, "blackrock": 3, "goldman sachs": 3, "goldman": 2,
    "federal reserve": 3, "fed": 3, "fomc": 3,
    "rate hike": 3, "rate cut": 3, "inflation": 2, "cpi": 2, 
    "dollar index": 3, "dxy": 3, "middle east": 3, "war": 2,
    "semiconductor": 2, "ai investments": 2, "bitcoin etf": 3,
}

MASTER_CRYPTO_COUNT = 3
MASTER_MACRO_COUNT  = 4

# YENİ: SCOUT ÜÇÜN MULTİ-MƏNBƏ RSS
MULTI_NEWS_SOURCES = {
    "Investing_Global": "https://www.investing.com/rss/news_285.rss",
    "Investing_Crypto": "https://www.investing.com/rss/news_301.rss",
    "Yahoo_Finance": "https://finance.yahoo.com/news/rssindex"
}

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
    volume_status:    str  
    error:            Optional[str] = None
    def to_dict(self) -> dict: return asdict(self)

@dataclass
class ScoutResult:
    symbol:     str
    asset_type: str
    scanned_at: str
    timeframes: dict[str, dict]
    scout_ok:   bool
    errors:     list[str] = field(default_factory=list)
    def to_dict(self) -> dict: return asdict(self)

@dataclass
class NewsItem:
    category:     str 
    title:        str
    source:       str
    published_at: str
    url:          str
    score:        int = 0
    def to_dict(self) -> dict: return asdict(self)

# ══════════════════════════════════════════════════════════════════════════════
#  YARDIMÇI FUNKSİYALAR
# ══════════════════════════════════════════════════════════════════════════════

def _utc_now() -> str: return datetime.now(timezone.utc).strftime
