from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import HourlyMarketSnapshot, Asset

from .base import Base



class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_position_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("hourly_market_snapshots.id"),
        nullable=False,
        unique=True,
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)

    side: Mapped[str] = mapped_column(String(10), nullable=False)  # flat, long, short
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    unrealized_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    wallet_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    snapshot: Mapped["HourlyMarketSnapshot"] = relationship(back_populates="position")
    asset: Mapped["Asset"] = relationship(back_populates="positions")