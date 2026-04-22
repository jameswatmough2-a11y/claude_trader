from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.api.routes.health import router as health_router
from app.api.routes.assets import router as assets_router
from app.api.routes.positions import router as positions_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.market import router as market_router
from app.config import settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.state import (
    ws_service,
    trigger_executor,
    trading_cycle_service,
    risk_service,
    execution_service,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def run_hourly_cycle() -> None:
    db = SessionLocal()
    try:
        await asyncio.to_thread(trading_cycle_service.run, db)
    except Exception:
        logger.exception("Unhandled error in hourly trading cycle")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()

    # Restore positions and paper balance from DB so state survives restarts
    db = SessionLocal()
    try:
        risk_service.restore_from_db(db)
        execution_service.restore_paper_balance_from_db(db)
    finally:
        db.close()

    asyncio.create_task(ws_service.run_forever())
    asyncio.create_task(trigger_executor.run_forever())

    scheduler.add_job(
        run_hourly_cycle,
        trigger=IntervalTrigger(hours=1),
        id="hourly_trading_cycle",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    scheduler.start()

    logger.info(
        "Bot started — paper_trading=%s  symbols=%s  stop_loss=%.1f%%  take_profit=%.1f%%",
        settings.paper_trading,
        settings.tracked_symbols,
        settings.stop_loss_pct,
        settings.take_profit_pct,
    )
    yield
    scheduler.shutdown(wait=False)
    logger.info("Bot stopped")


app = FastAPI(title="Claude Trader", lifespan=lifespan)

app.include_router(health_router)
app.include_router(assets_router)
app.include_router(positions_router)
app.include_router(decisions_router)
app.include_router(market_router)
