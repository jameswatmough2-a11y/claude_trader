from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.execution_service import ExecutionService

logger = logging.getLogger(__name__)


class TriggerExecutor:
    """Processes real-time exit orders (stop-loss / take-profit) fired by the WebSocket.

    Orders arrive on an asyncio.Queue and are executed in a thread pool so the
    WebSocket price stream is never blocked.
    """

    def __init__(self, queue: asyncio.Queue, execution_service: ExecutionService) -> None:
        self.queue = queue
        self.execution_service = execution_service

    async def run_forever(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                order: dict[str, Any] = await self.queue.get()
                await loop.run_in_executor(None, self._execute, order)
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TriggerExecutor: error processing order")

    def _execute(self, order: dict[str, Any]) -> None:
        asset = order["asset"]
        trigger_price = float(order.get("trigger_price", 0))

        balance = self._get_balance()
        market_data = {asset: {"last_price": trigger_price, "symbol": asset}}

        result = self.execution_service.execute_decision(order, market_data, balance)

        status = "error" if result.get("error") else "ok"
        logger.warning(
            "TriggerExecutor [%s]: %s %s @ %.4f — %s",
            status, order["action"], asset, trigger_price, order.get("reasoning", ""),
        )

    def _get_balance(self) -> dict[str, Any]:
        try:
            from app.services.data_feeds import fetch_balance
            return fetch_balance()
        except Exception:
            logger.warning("TriggerExecutor: balance fetch failed — using paper balance")
            bal = settings.paper_balance_usdt
            return {"USDT": {"free": bal, "used": 0.0, "total": bal}}
