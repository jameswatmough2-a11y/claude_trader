from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .hourly_market_snapshot import HourlyMarketSnapshot
    from .execution import Execution


class AIDecision(Base):
    __tablename__ = "ai_decisions"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_ai_decision_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("hourly_market_snapshots.id"),
        nullable=False,
        unique=True,
    )

    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    action: Mapped[str] = mapped_column(String(20), nullable=False)  # buy, sell, hold, close
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)

    reasoning_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    recommended_stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    recommended_take_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_source: Mapped[str] = mapped_column(String(20), nullable=False, default="ai")

    snapshot: Mapped["HourlyMarketSnapshot"] = relationship(back_populates="ai_decision")
    execution: Mapped[Optional["Execution"]] = relationship(
        back_populates="ai_decision",
        uselist=False,
        cascade="all, delete-orphan",
    )