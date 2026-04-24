"""One row per order sent (paper or live). Used to:
  - Reconstruct paper balance on startup (replay BUY/SELL cost + fees)
  - Audit trail
  - Populate the trades table (open + close rows reference executions)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ai_decision_id: Mapped[int | None] = mapped_column(ForeignKey("ai_decisions.id"), nullable=True)

    symbol: Mapped[str] = mapped_column(String, index=True)
    executed_action: Mapped[str] = mapped_column(String)      # BUY | SELL
    executed_size: Mapped[float] = mapped_column(Float)       # qty in base asset
    execution_price: Mapped[float] = mapped_column(Float)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String)               # filled | paper_filled | rejected | error
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)

    execution_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
