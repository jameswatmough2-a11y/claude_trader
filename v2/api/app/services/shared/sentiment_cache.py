"""In-memory + DB-backed sentiment cache.

Tenant cycles call `get(symbol)` — never `fetch()`. Fetching is owned by
sentiment_refresher. Cache entries expire based on source:
  - fear_greed  ~30min
  - rss/reddit  ~15min
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock


@dataclass
class SentimentEntry:
    source: str
    symbol: str
    score: float | None
    payload: dict | None
    refreshed_at: datetime

    def is_fresh(self, ttl: timedelta) -> bool:
        return datetime.now(timezone.utc) - self.refreshed_at < ttl


class SentimentCache:
    def __init__(self) -> None:
        self._lock = RLock()
        # (source, symbol) -> entry
        self._entries: dict[tuple[str, str], SentimentEntry] = {}

    def put(self, entry: SentimentEntry) -> None:
        with self._lock:
            self._entries[(entry.source, entry.symbol.upper())] = entry

    def get(self, source: str, symbol: str = "GLOBAL") -> SentimentEntry | None:
        with self._lock:
            return self._entries.get((source, symbol.upper()))

    def hydrate_from_db(self, db) -> None:
        """Load the latest entries from `sentiment_cache` table on startup.

        TODO: query SentimentCacheEntry rows, populate self._entries.
        """
        pass
