"""
sentiment.py — Aggregate sentiment scores and top headlines from
CryptoPanic, Twitter/X v2, and Reddit (PRAW) for BTC, ETH, and SOL.

Each source contributes a score in [-1.0, +1.0] and up to 3 headlines.
The final per-asset score is the mean across available sources.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import praw
import requests
import tweepy
from dotenv import load_dotenv
from textblob import TextBlob

load_dotenv()

logger = logging.getLogger(__name__)

# ── Asset keyword maps ────────────────────────────────────────────────────────

ASSET_KEYWORDS: dict[str, list[str]] = {
    "BTC/USDT": ["bitcoin", "BTC", "#bitcoin", "#BTC"],
    "ETH/USDT": ["ethereum", "ETH", "#ethereum", "#ETH"],
    "SOL/USDT": ["solana", "SOL", "#solana", "#SOL"],
}

REDDIT_SUBS = ["cryptocurrency", "bitcoin", "ethtrader"]

# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class AssetSentiment:
    symbol: str
    score: float  # [-1.0, +1.0]
    headline_count: int
    top_headlines: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _polarity(text: str) -> float:
    """TextBlob polarity: -1.0 (negative) to +1.0 (positive)."""
    return TextBlob(text).sentiment.polarity  # type: ignore[return-value]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ── CryptoPanic ───────────────────────────────────────────────────────────────


def _cryptopanic_sentiment(keywords: list[str]) -> tuple[float, list[str]]:
    """
    Query CryptoPanic for news items matching *keywords*.
    Returns (mean_score, top_3_headlines).
    CryptoPanic votes.positive/negative give a simple ratio signal.
    We blend that with TextBlob polarity on the headline text.
    """
    api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
    if not api_key:
        logger.warning("CRYPTOPANIC_API_KEY not set — skipping CryptoPanic")
        return 0.0, []

    # Use primary keyword (e.g. "bitcoin") for the filter
    currencies = ",".join(k for k in keywords if not k.startswith("#"))[:50]
    url = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": api_key,
        "currencies": currencies,
        "kind": "news",
        "filter": "hot",
        "public": "true",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("CryptoPanic request failed: %s", exc)
        return 0.0, []

    results = data.get("results", [])[:20]
    scores: list[float] = []
    headlines: list[tuple[float, str]] = []

    for item in results:
        title: str = item.get("title", "")
        if not title:
            continue
        votes = item.get("votes", {})
        pos = votes.get("positive", 0)
        neg = votes.get("negative", 0)
        total = pos + neg
        vote_score = (pos - neg) / total if total else 0.0
        text_score = _polarity(title)
        combined = _clamp((vote_score + text_score) / 2)
        scores.append(combined)
        headlines.append((combined, title))

    if not scores:
        return 0.0, []

    headlines.sort(key=lambda x: abs(x[0]), reverse=True)
    top_3 = [h for _, h in headlines[:3]]
    mean_score = _clamp(_mean(scores))
    logger.info("CryptoPanic [%s]: score=%.3f from %d items", currencies, mean_score, len(scores))
    return mean_score, top_3


# ── Twitter / X API v2 ───────────────────────────────────────────────────────


def _twitter_sentiment(keywords: list[str]) -> tuple[float, list[str]]:
    """
    Search recent tweets for *keywords* and return mean TextBlob polarity.
    Uses Tweepy's Client (v2 bearer-token auth).
    """
    token = os.getenv("TWITTER_BEARER_TOKEN", "")
    if not token:
        logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter")
        return 0.0, []

    client = tweepy.Client(bearer_token=token, wait_on_rate_limit=True)
    query = " OR ".join(keywords[:4]) + " lang:en -is:retweet"

    try:
        response = client.search_recent_tweets(
            query=query,
            max_results=100,
            tweet_fields=["public_metrics", "created_at"],
        )
    except tweepy.TweepyException as exc:
        logger.error("Twitter search failed: %s", exc)
        return 0.0, []

    tweets = response.data or []
    if not tweets:
        logger.info("Twitter [%s]: no tweets found", query[:60])
        return 0.0, []

    scores: list[float] = []
    headlines: list[tuple[float, str]] = []

    for tweet in tweets:
        text: str = tweet.text
        score = _clamp(_polarity(text))
        scores.append(score)
        if abs(score) > 0.1:
            headlines.append((score, text[:140]))

    headlines.sort(key=lambda x: abs(x[0]), reverse=True)
    top_3 = [h for _, h in headlines[:3]]
    mean_score = _clamp(_mean(scores))
    logger.info("Twitter [%s]: score=%.3f from %d tweets", keywords[0], mean_score, len(scores))
    return mean_score, top_3


# ── Reddit PRAW ──────────────────────────────────────────────────────────────


def _reddit_sentiment(keywords: list[str]) -> tuple[float, list[str]]:
    """
    Search r/cryptocurrency, r/bitcoin, r/ethtrader for *keywords*.
    Score = TextBlob polarity weighted by post score (upvotes).
    """
    client_id = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    user_agent = os.getenv("REDDIT_USER_AGENT", "crypto_bot/1.0")

    if not client_id or not client_secret:
        logger.warning("REDDIT credentials not set — skipping Reddit")
        return 0.0, []

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )

    primary_kw = keywords[0].lower()
    weighted_scores: list[tuple[float, float]] = []  # (score, weight)
    headlines: list[tuple[float, str]] = []

    for sub_name in REDDIT_SUBS:
        try:
            subreddit = reddit.subreddit(sub_name)
            posts = list(subreddit.search(primary_kw, sort="hot", time_filter="day", limit=30))
        except Exception as exc:  # noqa: BLE001
            logger.error("Reddit search failed on r/%s: %s", sub_name, exc)
            continue

        for post in posts:
            title: str = post.title
            post_score = max(post.score, 1)  # avoid zero weight
            polarity = _clamp(_polarity(title))
            weighted_scores.append((polarity, post_score))
            headlines.append((abs(polarity), title))

        # PRAW is synchronous; small sleep to be polite
        time.sleep(0.5)

    if not weighted_scores:
        return 0.0, []

    total_weight = sum(w for _, w in weighted_scores)
    mean_score = _clamp(sum(s * w for s, w in weighted_scores) / total_weight)

    headlines.sort(reverse=True)
    top_3 = [h for _, h in headlines[:3]]
    logger.info("Reddit [%s]: score=%.3f from %d posts", primary_kw, mean_score, len(weighted_scores))
    return mean_score, top_3


# ── Public API ────────────────────────────────────────────────────────────────


def get_asset_sentiment(symbol: str) -> AssetSentiment:
    """
    Aggregate sentiment for one asset (e.g. "BTC/USDT") across all sources.
    Returns an AssetSentiment with a blended score and top headlines.
    """
    keywords = ASSET_KEYWORDS.get(symbol, [symbol.split("/")[0]])
    logger.info("sentiment: gathering for %s (keywords: %s)", symbol, keywords)

    source_scores: dict[str, float] = {}
    all_headlines: list[str] = []

    cp_score, cp_headlines = _cryptopanic_sentiment(keywords)
    source_scores["cryptopanic"] = cp_score
    all_headlines.extend(cp_headlines)

    tw_score, tw_headlines = _twitter_sentiment(keywords)
    source_scores["twitter"] = tw_score
    all_headlines.extend(tw_headlines)

    rd_score, rd_headlines = _reddit_sentiment(keywords)
    source_scores["reddit"] = rd_score
    all_headlines.extend(rd_headlines)

    blended = _clamp(_mean(list(source_scores.values())))
    # De-duplicate and keep top 3 unique headlines
    seen: set[str] = set()
    unique_headlines: list[str] = []
    for h in all_headlines:
        key = h[:60].lower()
        if key not in seen:
            seen.add(key)
            unique_headlines.append(h)
        if len(unique_headlines) == 3:
            break

    logger.info(
        "sentiment: %s blended=%.3f sources=%s",
        symbol,
        blended,
        {k: round(v, 3) for k, v in source_scores.items()},
    )

    return AssetSentiment(
        symbol=symbol,
        score=blended,
        headline_count=len(all_headlines),
        top_headlines=unique_headlines,
        source_scores=source_scores,
    )


def get_all_sentiment(symbols: list[str]) -> dict[str, AssetSentiment]:
    """Return sentiment for every symbol in *symbols*."""
    return {sym: get_asset_sentiment(sym) for sym in symbols}
