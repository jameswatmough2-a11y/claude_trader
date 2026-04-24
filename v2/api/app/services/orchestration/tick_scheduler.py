"""Scans bot_configs every 60s, fires cycles for tenants whose next_run_at <= now.

Replaces v1's hourly-per-bot APScheduler job model. Key advantages:
  - One job, no churn when tenants start/stop
  - next_run_at lives in the DB -> server restart doesn't desync schedules
  - Semaphore gates concurrency so 500 tenants due at the same hour don't
    spawn 500 simultaneous Claude calls

Called by main.py lifespan via APScheduler AsyncIOScheduler.add_job(
    tick, 'interval', seconds=60).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.orchestration.tenant_registry import TenantRegistry
    from app.services.shared.binance_ws import BinanceWebSocketService


MAX_CONCURRENT_CYCLES = 10


class TickScheduler:
    def __init__(
        self,
        tenant_registry: "TenantRegistry",
        ws_service: "BinanceWebSocketService",
    ) -> None:
        self.tenant_registry = tenant_registry
        self.ws_service = ws_service
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CYCLES)

    async def tick(self) -> None:
        """Fire once per minute. TODO:
          1. Open a DB session.
          2. SELECT bot_configs WHERE running = true AND next_run_at <= now()
          3. For each: ensure tenant is registered (build services if needed),
             asyncio.create_task(self._run_one(tenant_id, config))
          4. Update next_run_at = now + interval_minutes.
          5. If the union of tracked_symbols changed, ws_service.set_subscription + reconnect().
        """
        pass

    async def _run_one(self, tenant_id: int, config) -> None:
        async with self._semaphore:
            services = self.tenant_registry.get(tenant_id)
            if services is None:
                return
            # TODO: new DB session, await services.cycle_service.run(db, config)
            pass
