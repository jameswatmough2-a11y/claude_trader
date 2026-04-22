from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import AIDecision

from .base import Base



class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("ai_decision_id", name="uq_execution_ai_decision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ai_decision_id: Mapped[int] = mapped_column(
        ForeignKey("ai_decisions.id"),
        nullable=False,
        unique=True,
    )

    executed_action: Mapped[str] = mapped_column(String(20), nullable=False)
    executed_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    execution_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    fees_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    slippage: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    execution_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # filled, rejected, none

    ai_decision: Mapped["AIDecision"] = relationship(back_populates="execution")