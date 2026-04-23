from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends, HTTPException
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
from app.state import bot_state, scheduler, run_hourly_cycle, risk_service, execution_service, ws_service, session_service, market_store

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
    ohlcv_interval: str = Field(default="1h")
    ohlcv_limit: int = Field(ge=10, le=500, default=50)
    taker_fee_rate: float = Field(ge=0.0, le=0.05, default=0.001)


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
    row.ohlcv_interval = payload.ohlcv_interval.strip() or "1h"
    row.ohlcv_limit = payload.ohlcv_limit
    row.taker_fee_rate = payload.taker_fee_rate
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

    # Create a new trading session
    sess = session_service.create_session(db, starting_balance_usdt=execution_service.paper_usdt)
    bot_state.current_session_id = sess.id

    logger.info("Bot started — interval=%d min session=%d", interval, sess.id)
    from app.services import db_logger
    db_logger.log_info("system", "bot_start", f"Bot started — interval={interval} min session={sess.id}")
    return {"running": True, "message": "Bot started", "session_id": sess.id}


@router.post("/stop")
async def stop(db: Session = Depends(get_db)) -> dict:
    if not bot_state.running:
        return {"running": False, "message": "Not running"}

    # Halt the scheduler first so no new cycle can start
    try:
        scheduler.remove_job("hourly_trading_cycle")
    except Exception:
        pass
    bot_state.running = False

    # Wait for any in-flight cycle to finish before touching the DB.
    # The cycle holds a write transaction between flush and commit; writing
    # concurrently causes "database is locked" even with WAL mode.
    for _ in range(120):
        if not bot_state.cycle_active:
            break
        await asyncio.sleep(0.5)

    if bot_state.current_session_id:
        from app.state import trade_service
        from app.models.trade import Trade

        open_trades = db.query(Trade).filter(
            Trade.session_id == bot_state.current_session_id,
            Trade.status == "open",
        ).all()

        now = datetime.now(timezone.utc)
        for trade in open_trades:
            state = market_store.get(trade.symbol)
            exit_price = float(state.last_price) if state and state.last_price else float(trade.entry_price or 0)
            entry_qty = float(trade.entry_qty or 0)

            trade_service.close_trade(
                db, trade.id,
                exit_execution_id=None,
                exit_price=exit_price,
                exit_fee_usdt=0.0,
                exit_reason="session_end",
                closed_at=now,
            )

            # Return proceeds to paper balance and remove in-memory position
            if entry_qty > 0:
                execution_service._paper_usdt += entry_qty * exit_price
            risk_service.close_position(trade.symbol)

        # Commit all trade closures, then close the session with the final balance
        db.commit()
        session_service.close_session(db, bot_state.current_session_id, execution_service.paper_usdt)
        bot_state.current_session_id = None

    logger.info("Bot stopped by user")
    from app.services import db_logger
    db_logger.log_info("system", "bot_stop", "Bot stopped by user")
    return {"running": False, "message": "Bot stopped"}


@router.post("/reset")
def reset(db: Session = Depends(get_db)) -> dict:
    if bot_state.cycle_active:
        raise HTTPException(
            status_code=409,
            detail="A trading cycle is currently running. Wait for it to finish before resetting.",
        )

    if bot_state.running:
        try:
            scheduler.remove_job("hourly_trading_cycle")
        except Exception:
            pass
        bot_state.running = False

    # Clear all trading data in FK-safe cascade order
    from app.models.system_log import SystemLog
    from app.models.ohlcv_candle import OhlcvCandle
    from app.models.trade import Trade
    from app.models.trading_session import TradingSession
    db.query(Execution).delete()
    db.query(AIDecision).delete()
    db.query(Position).delete()
    db.query(HourlyMarketSnapshot).delete()
    db.query(Trade).delete()
    db.query(TradingSession).delete()
    db.query(Asset).delete()
    db.query(OhlcvCandle).delete()
    db.query(SystemLog).delete()
    db.commit()

    # Reset in-memory state
    risk_service._positions.clear()
    execution_service._paper_usdt = settings.paper_balance_usdt
    bot_state.current_session_id = None

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
    settings.ohlcv_interval = getattr(row, "ohlcv_interval", "1h") or "1h"
    settings.ohlcv_limit = int(getattr(row, "ohlcv_limit", 50) or 50)
    settings.taker_fee_rate = getattr(row, "taker_fee_rate", 0.001) or 0.001
    if row.tracked_symbols:
        settings.tracked_symbols = [s.strip().upper() for s in row.tracked_symbols.split(",") if s.strip()]


def _reschedule(interval_minutes: int) -> None:
    scheduler.reschedule_job(
        "hourly_trading_cycle",
        trigger=IntervalTrigger(minutes=interval_minutes),
    )
