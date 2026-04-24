"""Sentiment cache — SHARED across tenants.

Refreshed by the sentiment_refresher coroutine on its own cadence, independent
of any tenant cycle. Tenant cycles READ only; never trigger a fetch.

One row per (source, symbol-or-global). `source` is one of:
  - "fear_greed"       (symbol = "GLOBAL")
  - "reddit"           (per-symbol)
  - "rss"              (per-symbol)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SentimentCacheEntry(Base):
    __tablename__ = "sentiment_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True, default="GLOBAL")

    score: Mapped[float | None] = mapped_column(Float, nullable=True)   # -1..+1 or source-native
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # headlines / raw data

    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
