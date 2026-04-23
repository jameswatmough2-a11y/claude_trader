from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .asset import Asset
    from .position import Position
    from .ai_decision import AIDecision


class HourlyMarketSnapshot(Base):
    __tablename__ = "hourly_market_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_id", "snapshot_time", name="uq_asset_snapshot_time"),
        Index("ix_snapshot_asset_time", "asset_id", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)

    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    volume: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)

    price_change_1h_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    price_change_since_entry_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    asset: Mapped["Asset"] = relationship(back_populates="snapshots")
    position: Mapped[Optional["Position"]] = relationship(
        back_populates="snapshot",
        uselist=False,
        cascade="all, delete-orphan",
    )
    ai_decision: Mapped[Optional["AIDecision"]] = relationship(
        back_populates="snapshot",
        uselist=False,
        cascade="all, delete-orphan",
    )