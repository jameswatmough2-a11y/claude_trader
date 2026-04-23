from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.models.bot_config import BotConfig
from app.models.execution import Execution
from app.models.ai_decision import AIDecision
from app.models.position import Position
from app.models.hourly_market_snapshot import HourlyMarketSnapshot
from app.models.asset import Asset
from app.state import bot_state, scheduler, run_hourly_cycle, risk_service, execution_service, ws_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"])


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def status() -> dict:
    return {
        "running": bot_state.running,
        "paper_trading": settings.paper_trading,
    }


# ── Config ────────────────────────────────────────────────────────────────────

class ConfigPayload(BaseModel):
    interval_minutes: int = Field(ge=1, le=1440)
    min_confidence: float = Field(ge=0.0, le=1.0)
    max_position_pct: float = Field(ge=1.0, le=100.0)
    max_total_exposure_pct: float = Field(ge=1.0, le=100.0)
    stop_loss_pct: float = Field(ge=0.0, le=50.0)
    take_profit_pct: float = Field(ge=0.0, le=100.0)
    tracked_symbols: str = Field(default="BTCUSDT,ETHUSDT,SOLUSDT")
    paper_balance_usdt: float = Field(ge=100.0, le=10_000_000.0)
    chart_interval: str = Field(default="1m")
    model_name: str = Field(default="claude-sonnet-4-6")
    timezone: str = Field(default="UTC")
    display_currency: str = Field(default="USD")


@router.get("/config")
def get_config(db: Session = Depends(get_db)) -> dict:
    row = db.get(BotConfig, 1)
    if row is None:
        return BotConfig.default_config()
    return row.to_dict()


@router.put("/config")
async def put_config(payload: ConfigPayload, db: Session = Depends(get_db)) -> dict:
    row = db.get(BotConfig, 1)
    if row is None:
        row = BotConfig(id=1, updated_at=datetime.now(timezone.utc))
        db.add(row)

    old_symbols = row.tracked_symbols if row.id else None
    row.interval_minutes = payload.interval_minutes
    row.min_confidence = payload.min_confidence
    row.max_position_pct = payload.max_position_pct
    row.max_total_exposure_pct = payload.max_total_exposure_pct
    row.stop_loss_pct = payload.stop_loss_pct
    row.take_profit_pct = payload.take_profit_pct
    row.tracked_symbols = _normalise_symbols(payload.tracked_symbols)
    row.paper_balance_usdt = payload.paper_balance_usdt
    row.chart_interval = payload.chart_interval
    row.model_name = payload.model_name.strip()
    row.timezone = payload.timezone.strip() or "UTC"
    row.display_currency = payload.display_currency.strip().upper() or "USD"
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)

    _apply_settings(row)
    if bot_state.running:
        _reschedule(row.interval_minutes)

    if row.tracked_symbols != old_symbols:
        asyncio.create_task(ws_service.reconnect())

    return row.to_dict()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@router.post("/start")
async def start(db: Session = Depends(get_db)) -> dict:
    if bot_state.running:
        return {"running": True, "message": "Already running"}

    row = db.get(BotConfig, 1)
    interval = row.interval_minutes if row else 60
    if row:
        _apply_settings(row)

    scheduler.add_job(
        run_hourly_cycle,
        trigger=IntervalTrigger(minutes=interval),
        id="hourly_trading_cycle",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5),
    )
    bot_state.running = True
    logger.info("Bot started — interval=%d min", interval)
    return {"running": True, "message": "Bot started"}


@router.post("/stop")
def stop() -> dict:
    if not bot_state.running:
        return {"running": False, "message": "Not running"}

    try:
        scheduler.remove_job("hourly_trading_cycle")
    except Exception:
        pass
    bot_state.running = False
    logger.info("Bot stopped by user")
    return {"running": False, "message": "Bot stopped"}


@router.post("/reset")
def reset(db: Session = Depends(get_db)) -> dict:
    if bot_state.running:
        try:
            scheduler.remove_job("hourly_trading_cycle")
        except Exception:
            pass
        bot_state.running = False

    # Clear all trading data in FK-safe cascade order
    db.query(Execution).delete()
    db.query(AIDecision).delete()
    db.query(Position).delete()
    db.query(HourlyMarketSnapshot).delete()
    db.query(Asset).delete()
    db.commit()

    # Reset in-memory state
    risk_service._positions.clear()
    execution_service._paper_usdt = settings.paper_balance_usdt

    logger.info("Database reset — all trading records cleared")
    return {"reset": True, "running": False, "message": "Database cleared"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_symbols(raw: str) -> str:
    """Uppercase, strip whitespace, deduplicate, rejoin."""
    seen = []
    for s in raw.split(","):
        s = s.strip().upper()
        if s and s not in seen:
            seen.append(s)
    return ",".join(seen)


def _apply_settings(row: BotConfig) -> None:
    settings.min_confidence = row.min_confidence
    settings.max_position_pct = row.max_position_pct
    settings.max_total_exposure_pct = row.max_total_exposure_pct
    settings.stop_loss_pct = row.stop_loss_pct
    settings.take_profit_pct = row.take_profit_pct
    settings.paper_balance_usdt = row.paper_balance_usdt
    settings.model_name = row.model_name
    if row.tracked_symbols:
        settings.tracked_symbols = [s.strip().upper() for s in row.tracked_symbols.split(",") if s.strip()]


def _reschedule(interval_minutes: int) -> None:
    scheduler.reschedule_job(
        "hourly_trading_cycle",
        trigger=IntervalTrigger(minutes=interval_minutes),
    )
