from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import feedparser
import requests
from textblob import TextBlob

logger = logging.getLogger(__name__)

# ── TTL cache ──────────────────────────────────────────────────────────────────

_SENTIMENT_TTL_SECONDS = 1800  # 30 minutes
_FEAR_GREED_TTL_SECONDS = 1800

_sentiment_cache: dict[str, tuple[float, "AssetSentiment"]] = {}
_fear_greed_cache: tuple[float, Optional[int]] = (0.0, None)

# ── Config ─────────────────────────────────────────────────────────────────────

ASSET_KEYWORDS: dict[str, list[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "SOLUSDT": ["solana", "sol"],
}

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

REDDIT_HEADERS = {"User-Agent": "crypto_sentiment_bot/1.0"}


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AssetSentiment:
    symbol: str
    score: float
    headline_count: int
    top_headlines: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)
    fear_greed_index: Optional[int] = None


# ── Text sanitization ──────────────────────────────────────────────────────────

def _sanitize(text: str, max_len: int = 200) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[*_`#\[\]()\\|]', ' ', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0]
    return text


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _polarity(text: str) -> float:
    return float(TextBlob(text).sentiment.polarity)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _matches(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)


# ── Data sources ───────────────────────────────────────────────────────────────

def _fetch_rss(keywords: list[str]) -> tuple[float, list[str]]:
    scores: list[float] = []
    headlines: list[tuple[float, str]] = []

    for url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=RSS_HEADERS, timeout=8)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                continue
            for entry in feed.entries[:40]:
                title = _sanitize(entry.get("title", ""), 180)
                summary = _sanitize(entry.get("summary", ""), 200)
                combined = f"{title} {summary}"
                if not _matches(combined, keywords):
                    continue
                score = _clamp(_polarity(combined))
                scores.append(score)
                headlines.append((abs(score), title))
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", url, exc)

    if not scores:
        return 0.0, []

    headlines.sort(reverse=True)
    return _clamp(_mean(scores)), [h for _, h in headlines[:3]]


def _fetch_reddit(keywords: list[str]) -> tuple[float, list[str]]:
    primary_kw = keywords[0]
    weighted_scores: list[tuple[float, float]] = []
    headlines: list[tuple[float, str]] = []

    for sub in ["cryptocurrency", "bitcoin", "ethtrader"]:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {"q": primary_kw, "sort": "hot", "t": "day", "limit": 25, "restrict_sr": "true"}
        try:
            resp = requests.get(url, params=params, headers=REDDIT_HEADERS, timeout=8)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            for post in posts:
                data = post.get("data", {})
                title = _sanitize(data.get("title", ""), 180)
                upvotes = max(float(data.get("score", 1)), 1.0)
                score = _clamp(_polarity(title))
                weighted_scores.append((score, upvotes))
                headlines.append((abs(score), title))
        except Exception as exc:
            logger.warning("Reddit fetch failed for r/%s: %s", sub, exc)

    if not weighted_scores:
        return 0.0, []

    total_weight = sum(w for _, w in weighted_scores)
    mean_score = _clamp(sum(s * w for s, w in weighted_scores) / total_weight)
    headlines.sort(reverse=True)
    return mean_score, [h for _, h in headlines[:3]]


def _fetch_fear_greed() -> Optional[int]:
    global _fear_greed_cache
    ts, cached_value = _fear_greed_cache
    if time.time() - ts < _FEAR_GREED_TTL_SECONDS:
        return cached_value

    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        resp.raise_for_status()
        value = int(resp.json()["data"][0]["value"])
        _fear_greed_cache = (time.time(), value)
        return value
    except Exception as exc:
        logger.warning("Fear & Greed fetch failed: %s", exc)
        _fear_greed_cache = (time.time(), None)
        return None


def _fear_greed_to_score(value: int) -> float:
    return _clamp((value - 50) / 50.0)


# ── Per-asset sentiment ────────────────────────────────────────────────────────

def _compute_asset_sentiment(symbol: str, fear_greed: Optional[int]) -> AssetSentiment:
    symbol = symbol.upper()
    keywords = ASSET_KEYWORDS.get(symbol, [symbol.replace("USDT", "").lower()])

    # Fetch RSS and Reddit concurrently
    with ThreadPoolExecutor(max_workers=2) as pool:
        rss_future = pool.submit(_fetch_rss, keywords)
        reddit_future = pool.submit(_fetch_reddit, keywords)
        rss_score, rss_headlines = rss_future.result()
        reddit_score, reddit_headlines = reddit_future.result()

    source_scores: dict[str, float] = {"rss": rss_score, "reddit": reddit_score}
    if fear_greed is not None:
        source_scores["fear_greed"] = _fear_greed_to_score(fear_greed)

    blended = _clamp(_mean(list(source_scores.values())))

    all_headlines = rss_headlines + reddit_headlines
    seen: set[str] = set()
    unique_headlines: list[str] = []
    for headline in all_headlines:
        key = headline[:60].lower()
        if key not in seen:
            seen.add(key)
            unique_headlines.append(headline)
        if len(unique_headlines) == 3:
            break

    return AssetSentiment(
        symbol=symbol,
        score=blended,
        headline_count=len(all_headlines),
        top_headlines=unique_headlines,
        source_scores=source_scores,
        fear_greed_index=fear_greed,
    )


def get_asset_sentiment(symbol: str, fear_greed: Optional[int] = None) -> AssetSentiment:
    symbol = symbol.upper()
    ts, cached = _sentiment_cache.get(symbol, (0.0, None))
    if cached is not None and time.time() - ts < _SENTIMENT_TTL_SECONDS:
        logger.info("Sentiment cache hit for %s (age %.0fs)", symbol, time.time() - ts)
        return cached

    logger.info("Fetching fresh sentiment for %s", symbol)
    result = _compute_asset_sentiment(symbol, fear_greed)
    _sentiment_cache[symbol] = (time.time(), result)
    return result


def get_all_sentiment(symbols: list[str]) -> dict[str, AssetSentiment]:
    # Fear & Greed is global — fetch once and share
    fear_greed = _fetch_fear_greed()

    # Fetch all symbols concurrently (each symbol's sources are already concurrent inside)
    results: dict[str, AssetSentiment] = {}
    with ThreadPoolExecutor(max_workers=min(len(symbols), 4)) as pool:
        futures = {pool.submit(get_asset_sentiment, sym, fear_greed): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                results[sym.upper()] = future.result()
            except Exception as exc:
                logger.warning("Sentiment failed for %s: %s", sym, exc)

    return results


def get_sentiment_cache_status() -> dict:
    now = time.time()
    return {
        s: {"cached": True, "age_seconds": round(now - ts)}
        for s, (ts, data) in _sentiment_cache.items()
        if data is not None
    }


def clear_sentiment_cache() -> None:
    _sentiment_cache.clear()
