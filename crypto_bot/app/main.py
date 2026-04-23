from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.assets import router as assets_router
from app.api.routes.positions import router as positions_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.market import router as market_router
from app.api.routes.chart import router as chart_router
from app.api.routes.bot_control import router as bot_control_router
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.state import (
    ws_service,
    trigger_executor,
    risk_service,
    execution_service,
    scheduler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import asyncio

    init_db()

    db = SessionLocal()
    try:
        risk_service.restore_from_db(db)
        execution_service.restore_paper_balance_from_db(db)

        # Apply persisted config to live settings so risk params survive restarts
        from app.models.bot_config import BotConfig
        from app.api.routes.bot_control import _apply_settings
        row = db.get(BotConfig, 1)
        if row:
            _apply_settings(row)
    finally:
        db.close()

    asyncio.create_task(ws_service.run_forever())
    asyncio.create_task(trigger_executor.run_forever())

    scheduler.start()
    logger.info("Scheduler started — waiting for user to press Start")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Bot stopped")


app = FastAPI(title="Claude Trader", lifespan=lifespan)

app.include_router(health_router)
app.include_router(assets_router)
app.include_router(positions_router)
app.include_router(decisions_router)
app.include_router(market_router)
app.include_router(chart_router)
app.include_router(bot_control_router)
