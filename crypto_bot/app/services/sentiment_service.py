from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import feedparser
import requests
from textblob import TextBlob

logger = logging.getLogger(__name__)

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

REDDIT_SUBS = ["cryptocurrency", "bitcoin", "ethtrader"]
REDDIT_HEADERS = {"User-Agent": "crypto_sentiment_bot/1.0"}


@dataclass
class AssetSentiment:
    symbol: str
    score: float
    headline_count: int
    top_headlines: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)
    fear_greed_index: int | None = None


def _polarity(text: str) -> float:
    return float(TextBlob(text).sentiment.polarity)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _matches(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)


def _rss_sentiment(keywords: list[str]) -> tuple[float, list[str]]:
    scores: list[float] = []
    headlines: list[tuple[float, str]] = []

    for url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=RSS_HEADERS, timeout=10)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                logger.warning("RSS: no entries from %s", url)
                continue
            for entry in feed.entries[:40]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                combined = f"{title} {summary}"
                if not _matches(combined, keywords):
                    continue
                score = _clamp(_polarity(combined))
                scores.append(score)
                headlines.append((abs(score), title))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RSS fetch failed for %s: %s", url, exc)

    if not scores:
        return 0.0, []

    headlines.sort(reverse=True)
    return _clamp(_mean(scores)), [h for _, h in headlines[:3]]


def _reddit_sentiment(keywords: list[str]) -> tuple[float, list[str]]:
    primary_kw = keywords[0]
    weighted_scores: list[tuple[float, float]] = []
    headlines: list[tuple[float, str]] = []

    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {
            "q": primary_kw,
            "sort": "hot",
            "t": "day",
            "limit": 25,
            "restrict_sr": "true",
        }

        try:
            resp = requests.get(url, params=params, headers=REDDIT_HEADERS, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reddit fetch failed for r/%s: %s", sub, exc)
            time.sleep(1)
            continue

        for post in posts:
            data = post.get("data", {})
            title = data.get("title", "")
            upvotes = max(float(data.get("score", 1)), 1.0)
            score = _clamp(_polarity(title))
            weighted_scores.append((score, upvotes))
            headlines.append((abs(score), title))

        time.sleep(1)

    if not weighted_scores:
        return 0.0, []

    total_weight = sum(w for _, w in weighted_scores)
    mean_score = _clamp(sum(s * w for s, w in weighted_scores) / total_weight)
    headlines.sort(reverse=True)
    return mean_score, [h for _, h in headlines[:3]]


def _fear_greed_index() -> int | None:
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        resp.raise_for_status()
        return int(resp.json()["data"][0]["value"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fear & Greed fetch failed: %s", exc)
        return None


def _fear_greed_to_score(value: int) -> float:
    return _clamp((value - 50) / 50.0)


def get_asset_sentiment(symbol: str, fear_greed: int | None = None) -> AssetSentiment:
    symbol = symbol.upper()
    keywords = ASSET_KEYWORDS.get(symbol, [symbol.replace("USDT", "").lower()])

    source_scores: dict[str, float] = {}
    all_headlines: list[str] = []

    rss_score, rss_headlines = _rss_sentiment(keywords)
    source_scores["rss"] = rss_score
    all_headlines.extend(rss_headlines)

    reddit_score, reddit_headlines = _reddit_sentiment(keywords)
    source_scores["reddit"] = reddit_score
    all_headlines.extend(reddit_headlines)

    if fear_greed is not None:
        source_scores["fear_greed"] = _fear_greed_to_score(fear_greed)

    blended = _clamp(_mean(list(source_scores.values())))

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


def get_all_sentiment(symbols: list[str]) -> dict[str, AssetSentiment]:
    fear_greed = _fear_greed_index()
    return {symbol.upper(): get_asset_sentiment(symbol, fear_greed) for symbol in symbols}