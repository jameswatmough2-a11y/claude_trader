"""Market snapshot — SHARED across tenants (no tenant_id).

Written once per symbol per hour (approximately) by the first tenant cycle
that needs fresh data. Subsequent tenant cycles in the same hour read this
instead of re-fetching. Dramatically cuts REST/CCXT calls at scale.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HourlyMarketSnapshot(Base):
    __tablename__ = "hourly_market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)

    last_price: Mapped[float] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_24h_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_24h: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Candle tail (JSON-encoded list of [ts, o, h, l, c, v]). Keeps the
    # AI prompt stable across tenants viewing the same hour.
    candles_json: Mapped[str | None] = mapped_column(String, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
