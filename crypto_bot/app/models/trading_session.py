from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User
    from .trade import Trade


class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=1)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # active | stopped | crashed

    starting_balance_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    ending_balance_usdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
    trades: Mapped[list["Trade"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Trade.opened_at",
    )

    @property
    def duration_seconds(self) -> Optional[float]:
        def _as_utc(dt: datetime) -> datetime:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        if self.ended_at and self.started_at:
            return (_as_utc(self.ended_at) - _as_utc(self.started_at)).total_seconds()
        if self.started_at:
            return (datetime.now(timezone.utc) - _as_utc(self.started_at)).total_seconds()
        return None

    @property
    def pnl_usdt(self) -> Optional[float]:
        if self.ending_balance_usdt is not None:
            return float(self.ending_balance_usdt - self.starting_balance_usdt)
        return None

    @property
    def pnl_pct(self) -> Optional[float]:
        start = float(self.starting_balance_usdt)
        if start > 0 and self.ending_balance_usdt is not None:
            return (float(self.ending_balance_usdt) - start) / start * 100
        return None
