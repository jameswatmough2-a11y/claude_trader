"""Shared queue consumer for real-time SL/TP exits.

Orders arrive on the shared trigger_queue with a tenant_id attached:
    {asset, action: "SELL", trigger_price, size_pct, reasoning, tenant_id}

For each order:
  1. Look up the tenant's ExecutionService via TenantRegistry.
  2. Dispatch execute_decision to the default thread pool (blocking I/O).
  3. On success, close the open Trade row with the right exit_reason.
  4. Publish a 'trade_closed' event on EventBus so the user's WebSocket
     can push it to the browser instantly.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.orchestration.tenant_registry import TenantRegistry
    from app.event_bus import EventBus


class TriggerExecutor:
    def __init__(
        self,
        queue: asyncio.Queue,
        tenant_registry: "TenantRegistry",
        event_bus: "EventBus",
        session_factory: Callable | None = None,
    ) -> None:
        self.queue = queue
        self.tenant_registry = tenant_registry
        self.event_bus = event_bus
        self.session_factory = session_factory

    async def run_forever(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                order = await self.queue.get()
                await loop.run_in_executor(None, self._execute, order)
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TriggerExecutor: error processing order")

    def _execute(self, order: dict[str, Any]) -> None:
        tenant_id = order["tenant_id"]
        tenant = self.tenant_registry.get(tenant_id)
        if tenant is None:
            logger.warning("TriggerExecutor: no registered tenant %s", tenant_id)
            return

        # TODO: call tenant.execution_service.execute_decision(order, market_data, balance)
        # TODO: on success, close Trade row (port _close_trade from v1)
        # TODO: self.event_bus.publish(tenant_id, {"type": "trade_closed", ...})
        pass
