from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .hourly_market_snapshot import HourlyMarketSnapshot
    from .position import Position


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(10), nullable=False)

    snapshots: Mapped[list["HourlyMarketSnapshot"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    positions: Mapped[list["Position"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
