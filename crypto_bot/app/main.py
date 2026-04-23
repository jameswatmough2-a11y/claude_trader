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
from app.api.routes.logs import router as logs_router
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
    from app.config import settings
    from app.services import db_logger

    init_db()

    db = SessionLocal()
    try:
        risk_service.restore_from_db(db)
        execution_service.restore_paper_balance_from_db(db)

        from app.models.bot_config import BotConfig
        from app.api.routes.bot_control import _apply_settings
        row = db.get(BotConfig, 1)
        if row:
            _apply_settings(row)
    finally:
        db.close()

    db_logger.log_info(
        "system", "server_start",
        f"Claude Trader starting — mode={'paper' if settings.paper_trading else 'live'} "
        f"symbols={settings.tracked_symbols}",
        details={
            "paper_trading": settings.paper_trading,
            "tracked_symbols": settings.tracked_symbols,
            "ohlcv_interval": settings.ohlcv_interval,
        },
    )

    # In live mode, reconcile positions against exchange on startup
    if not settings.paper_trading:
        _reconcile_live_positions()

    asyncio.create_task(ws_service.run_forever())
    asyncio.create_task(trigger_executor.run_forever())

    scheduler.start()
    logger.info("Scheduler started — waiting for user to press Start")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Bot stopped")


def _reconcile_live_positions() -> None:
    from app.services import db_logger
    from app.state import risk_service

    logger.info("Live mode: reconciling positions against exchange")
    try:
        from app.services.data_feeds import get_exchange, _to_ccxt_symbol
        from app.config import settings

        exchange = get_exchange()
        balance = exchange.fetch_balance()

        internal_positions = risk_service.get_open_positions()
        reconciliation_notes: list[str] = []

        for symbol, pos in internal_positions.items():
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            exchange_qty = float(balance.get(base, {}).get("total", 0))
            expected_qty = pos.size_pct / 100.0  # approximate — exact qty not tracked in-memory

            if exchange_qty <= 0:
                note = f"{symbol}: internal says LONG but exchange balance is 0 — position may be closed"
                logger.warning(note)
                reconciliation_notes.append(note)
            else:
                note = f"{symbol}: internal LONG confirmed (exchange {base} balance={exchange_qty:.6f})"
                logger.info(note)
                reconciliation_notes.append(note)

        db_logger.log_info(
            "system", "live_reconciliation",
            f"Live reconciliation complete: {len(internal_positions)} internal positions checked",
            details={"notes": reconciliation_notes},
        )
    except Exception as exc:
        logger.exception("Live reconciliation failed: %s", exc)
        db_logger.log_error(
            "system", "reconciliation_error",
            f"Live reconciliation failed: {exc!s:.200}",
        )


app = FastAPI(title="Claude Trader", lifespan=lifespan)

app.include_router(health_router)
app.include_router(assets_router)
app.include_router(positions_router)
app.include_router(decisions_router)
app.include_router(market_router)
app.include_router(chart_router)
app.include_router(bot_control_router)
app.include_router(logs_router)
