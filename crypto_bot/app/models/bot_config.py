from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BotConfig(Base):
    """Single-row table that persists the user-configured trading parameters.

    Always accessed as id=1 via upsert — only one row ever exists.
    """

    __tablename__ = "bot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_position_pct: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    max_total_exposure_pct: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    stop_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    take_profit_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tracked_symbols: Mapped[str] = mapped_column(String, nullable=False, default="BTCUSDT,ETHUSDT,SOLUSDT")
    paper_balance_usdt: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    chart_interval: Mapped[str] = mapped_column(String(10), nullable=False, default="1m")
    model_name: Mapped[str] = mapped_column(String(100), nullable=False, default="claude-sonnet-4-6")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    display_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    ohlcv_interval: Mapped[str] = mapped_column(String(10), nullable=False, default="1h")
    ohlcv_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    taker_fee_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.001)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @staticmethod
    def default_config() -> dict:
        return {
            "interval_minutes": 60,
            "min_confidence": 0.7,
            "max_position_pct": 20.0,
            "max_total_exposure_pct": 60.0,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 0.0,
            "tracked_symbols": "BTCUSDT,ETHUSDT,SOLUSDT",
            "paper_balance_usdt": 10000.0,
            "chart_interval": "1m",
            "model_name": "claude-sonnet-4-6",
            "timezone": "UTC",
            "display_currency": "USD",
            "ohlcv_interval": "1h",
            "ohlcv_limit": 50,
            "taker_fee_rate": 0.001,
        }

    def to_dict(self) -> dict:
        return {
            "interval_minutes": self.interval_minutes,
            "min_confidence": self.min_confidence,
            "max_position_pct": self.max_position_pct,
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "tracked_symbols": self.tracked_symbols,
            "paper_balance_usdt": self.paper_balance_usdt,
            "chart_interval": self.chart_interval,
            "model_name": self.model_name,
            "timezone": self.timezone,
            "display_currency": self.display_currency,
            "ohlcv_interval": self.ohlcv_interval,
            "ohlcv_limit": self.ohlcv_limit,
            "taker_fee_rate": self.taker_fee_rate,
            "updated_at": self.updated_at.isoformat(),
        }
