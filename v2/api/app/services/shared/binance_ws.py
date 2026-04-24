"""Shared Binance WebSocket — singleton.

URL is the UNION of every running tenant's tracked_symbols. When a tenant
starts/stops/edits their tracked_symbols, tick_scheduler tells us to
reconnect so the subscription set is updated.

On each tick:
  1. Update shared MarketStateStore.
  2. Ask PositionRegistry which tenants hold this symbol; for each, call
     their RiskService.check_exit_conditions. Any triggered order is
     enqueued on the shared trigger_queue (with tenant_id attached).

TODO: port v1 binance_ws.py, adapting the exit-check loop to iterate
PositionRegistry instead of a single risk_service.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.shared.market_state import MarketStateStore
    from app.services.shared.position_registry import PositionRegistry


class BinanceWebSocketService:
    def __init__(
        self,
        market_store: "MarketStateStore",
        position_registry: "PositionRegistry",
        trigger_queue: asyncio.Queue,
    ) -> None:
        self.market_store = market_store
        self.position_registry = position_registry
        self.trigger_queue = trigger_queue
        self._ws = None
        self._symbols: set[str] = set()

    def set_subscription(self, symbols: set[str]) -> None:
        """Update the desired subscription set. Call reconnect() to apply."""
        self._symbols = {s.upper() for s in symbols}

    async def reconnect(self) -> None:
        """Close active WS so run_forever rebuilds the URL with current symbols."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def run_forever(self) -> None:
        """Outer reconnect loop. TODO: port from v1."""
        while True:
            try:
                # TODO: connect to wss://data-stream.binance.vision/stream?streams=...
                # TODO: for each message -> _handle_message
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance WS error — reconnecting: %s", exc)
                await asyncio.sleep(5)

    def _handle_message(self, message: str) -> None:
        """Parse tick -> market_store.update -> check every holder's positions."""
        # TODO: parse payload
        # TODO: market_store.update(...)
        # TODO: for (tenant_id, risk_service) in position_registry.holders_of(symbol):
        #           order = risk_service.check_exit_conditions(symbol, price)
        #           if order: trigger_queue.put_nowait({**order, "tenant_id": tenant_id})
        pass
