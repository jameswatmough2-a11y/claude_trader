"""Per-tenant runtime config — replaces v1's single-row BotConfig.

Includes scheduling fields (`running`, `next_run_at`) that the tick_scheduler
uses to decide which tenants are due. This moves scheduling state *into the
database* so a server restart doesn't lose the running set.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True, index=True)

    # Runtime state — owned by tick_scheduler
    running: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Cadence + symbols
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    tracked_symbols: Mapped[str] = mapped_column(String, default="BTCUSDT,ETHUSDT,SOLUSDT")

    # Risk params (same shape as v1)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    max_position_pct: Mapped[float] = mapped_column(Float, default=25.0)
    max_total_exposure_pct: Mapped[float] = mapped_column(Float, default=75.0)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    take_profit_pct: Mapped[float] = mapped_column(Float, default=10.0)

    # Paper / display
    paper_balance_usdt: Mapped[float] = mapped_column(Float, default=10_000.0)
    chart_interval: Mapped[str] = mapped_column(String, default="1h")

    # AI provider selection (v2.x: users may pick Claude or a local model)
    model_name: Mapped[str] = mapped_column(String, default="claude-sonnet-4-6")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
