from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .trading_session import TradingSession
    from .execution import Execution


class Trade(Base):
    """A single trade lifecycle: from BUY entry to SELL/SL/TP exit."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=1)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Entry
    entry_execution_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("executions.id"), nullable=True
    )
    entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    entry_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    entry_fee_usdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    size_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Risk levels at entry
    stop_loss_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    # Exit
    exit_execution_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("executions.id"), nullable=True
    )
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    exit_fee_usdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # ai_sell | stop_loss | take_profit | session_end
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Computed on close
    realized_pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    realized_pnl_usdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    # open | closed

    session: Mapped["TradingSession"] = relationship(back_populates="trades")
    entry_execution: Mapped[Optional["Execution"]] = relationship(
        foreign_keys=[entry_execution_id],
        primaryjoin="Trade.entry_execution_id == Execution.id",
    )
    exit_execution: Mapped[Optional["Execution"]] = relationship(
        foreign_keys=[exit_execution_id],
        primaryjoin="Trade.exit_execution_id == Execution.id",
    )
