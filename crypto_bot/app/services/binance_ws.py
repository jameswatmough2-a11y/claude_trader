from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from websockets.asyncio.client import connect

from app.services.market_state import MarketStateStore

if TYPE_CHECKING:
    from app.services.risk_service import RiskService

logger = logging.getLogger(__name__)


class BinanceWebSocketService:
    def __init__(
        self,
        symbols: list[str],
        market_store: MarketStateStore,
        risk_service: RiskService | None = None,
        trigger_queue: asyncio.Queue | None = None,
    ) -> None:
        self.symbols = [s.lower() for s in symbols]
        self.market_store = market_store
        self._risk_service = risk_service
        self._trigger_queue = trigger_queue

        streams = "/".join(f"{s}@ticker" for s in self.symbols)
        self.url = f"wss://data-stream.binance.vision/stream?streams={streams}"

    async def run_forever(self) -> None:
        while True:
            try:
                logger.info("Connecting Binance WebSocket: %s", self.url)
                async with connect(self.url, ping_interval=20, ping_timeout=60) as ws:
                    async for message in ws:
                        self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance WebSocket error — reconnecting in 5s: %s", exc)
                await asyncio.sleep(5)

    def _handle_message(self, message: str) -> None:
        payload = json.loads(message)
        data = payload.get("data", payload)

        symbol = data.get("s")
        if not symbol:
            return

        try:
            last_price_raw = data.get("c")
            self.market_store.update(
                symbol=symbol,
                last_price=Decimal(last_price_raw) if last_price_raw else None,
                bid=Decimal(data["b"]) if data.get("b") else None,
                ask=Decimal(data["a"]) if data.get("a") else None,
                volume_24h=Decimal(data["q"]) if data.get("q") else None,
                price_change_24h_pct=Decimal(data["P"]) if data.get("P") else None,
                high_24h=Decimal(data["h"]) if data.get("h") else None,
                low_24h=Decimal(data["l"]) if data.get("l") else None,
            )
        except Exception as exc:
            logger.warning("Failed to parse WebSocket payload for %s: %s", symbol, exc)
            return

        # Real-time exit check — fires stop-loss / take-profit immediately on each tick
        if self._risk_service is not None and self._trigger_queue is not None and last_price_raw:
            try:
                order = self._risk_service.check_exit_conditions(symbol, float(last_price_raw))
                if order:
                    self._trigger_queue.put_nowait(order)
            except Exception as exc:
                logger.warning("Exit condition check failed for %s: %s", symbol, exc)
