"""FastAPI entry point. Lifespan wires the shared plane and starts the
long-running coroutines + the tick scheduler.

Diagram of what starts here:

    ┌─── Shared plane (singletons, in state.py) ────┐
    │  market_store    sentiment_cache              │
    │  position_registry  event_bus                 │
    │  tenant_registry    trigger_queue             │
    └───────────────────────────────────────────────┘
              │
    ┌─── Wired here in lifespan ────────────────────┐
    │  ws_service (binance WS)                      │
    │  trigger_executor                             │
    │  sentiment_refresher                          │
    │  decision_provider (Claude for v1)            │
    │  tick_scheduler  ──added to APScheduler       │
    └───────────────────────────────────────────────┘
              │
    ┌─── Per-tenant (lazy, via tenant_registry) ────┐
    │  RiskService                                   │
    │  ExecutionService                              │
    │  TradingCycleService                           │
    └────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.api.routes.bot_control import router as bot_control_router
from app.api.routes.chart import router as chart_router
from app.api.routes.clerk_webhooks import router as clerk_webhooks_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.health import router as health_router
from app.api.routes.positions import router as positions_router
from app.api.routes.trades import router as trades_router
from app.api.routes.websocket import router as websocket_router
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.shared.binance_ws import BinanceWebSocketService
from app.services.shared.claude_provider import ClaudeDecisionProvider
from app.services.shared.sentiment_refresher import SentimentRefresher
from app.services.shared.trigger_executor import TriggerExecutor
from app.services.orchestration.tick_scheduler import TickScheduler
from app.state import (
    event_bus,
    market_store,
    position_registry,
    scheduler,
    sentiment_cache,
    tenant_registry,
    trigger_queue,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()

    # Build the shared services that need to reference singletons
    ws_service = BinanceWebSocketService(
        market_store=market_store,
        position_registry=position_registry,
        trigger_queue=trigger_queue,
    )
    trigger_executor = TriggerExecutor(
        queue=trigger_queue,
        tenant_registry=tenant_registry,
        event_bus=event_bus,
        session_factory=SessionLocal,
    )
    sentiment_refresher = SentimentRefresher(
        cache=sentiment_cache,
        tenant_registry=tenant_registry,
    )
    decision_provider = ClaudeDecisionProvider()
    tick_scheduler = TickScheduler(
        tenant_registry=tenant_registry,
        ws_service=ws_service,
    )

    # Stash on app.state so routes can reach them without another import cycle
    app.state.ws_service = ws_service
    app.state.decision_provider = decision_provider
    app.state.tick_scheduler = tick_scheduler

    # Hydrate shared caches from DB
    db = SessionLocal()
    try:
        sentiment_cache.hydrate_from_db(db)
        # TODO: for each tenant with running=true, rebuild their services in
        #       tenant_registry + restore positions + paper balance.
    finally:
        db.close()

    # Kick off long-running coroutines
    asyncio.create_task(ws_service.run_forever())
    asyncio.create_task(trigger_executor.run_forever())
    asyncio.create_task(sentiment_refresher.run_forever())

    # Schedule the tick job (once per minute, scans due tenants)
    scheduler.add_job(tick_scheduler.tick, "interval", seconds=60, id="tick", replace_existing=True)
    scheduler.start()

    logger.info("v2 API started")
    yield
    scheduler.shutdown(wait=False)
    logger.info("v2 API stopped")


app = FastAPI(title="Claude Trader v2", lifespan=lifespan)

app.include_router(health_router)
app.include_router(bot_control_router)
app.include_router(decisions_router)
app.include_router(trades_router)
app.include_router(positions_router)
app.include_router(chart_router)
app.include_router(websocket_router)
app.include_router(clerk_webhooks_router)
