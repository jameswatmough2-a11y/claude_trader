"""Open-to-close lifecycle view of a position.

A Trade row is created on the entry BUY and updated on the exit SELL.
`exit_reason` tells the UI why it closed: stop_loss | take_profit | ai_sell.

This is what the "Trades history" screen reads from — it's the user-facing
record of how the bot performed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("trading_sessions.id"), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open | closed

    # Entry
    entry_execution_id: Mapped[int | None] = mapped_column(ForeignKey("executions.id"), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_fee_usdt: Mapped[float] = mapped_column(Float, default=0.0)
    size_pct: Mapped[float] = mapped_column(Float)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Exit (null while open)
    exit_execution_id: Mapped[int | None] = mapped_column(ForeignKey("executions.id"), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_fee_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    realized_pnl_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
