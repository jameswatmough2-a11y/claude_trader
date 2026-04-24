"""One row per symbol per cycle — the AI's verdict + reasoning.

Tenant-scoped: the same Claude call may produce different decisions per tenant
because each tenant's prompt includes their own positions + risk thresholds.

References an HourlyMarketSnapshot (shared, no tenant_id) so multiple tenants'
decisions at the same hour can share the same market data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AIDecision(Base):
    __tablename__ = "ai_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("trading_sessions.id"), nullable=True, index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("hourly_market_snapshots.id"), nullable=True)

    symbol: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)             # BUY | SELL | HOLD
    confidence: Mapped[float] = mapped_column(Float)
    recommended_size: Mapped[float] = mapped_column(Float, default=0.0)  # % of portfolio
    reasoning: Mapped[str] = mapped_column(Text, default="")

    # If the risk layer forced a HOLD, the original AI verdict lives here.
    raw_action: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_override_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    decision_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
