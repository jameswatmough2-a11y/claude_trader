from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

from websockets.asyncio.client import connect

from app.services.market_state import MarketStateStore

logger = logging.getLogger(__name__)


class BinanceWebSocketService:
    def __init__(self, symbols: list[str], market_store: MarketStateStore) -> None:
        self.symbols = [s.lower() for s in symbols]
        self.market_store = market_store

        streams = "/".join(f"{symbol}@ticker" for symbol in self.symbols)
        self.url = f"wss://data-stream.binance.vision/stream?streams={streams}"

    async def run_forever(self) -> None:
        while True:
            try:
                logger.info("Connecting Binance websocket: %s", self.url)
                async with connect(self.url, ping_interval=20, ping_timeout=60) as ws:
                    async for message in ws:
                        self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance websocket error: %s", exc)
                await asyncio.sleep(5)

    def _handle_message(self, message: str) -> None:
        payload = json.loads(message)
        data = payload.get("data", payload)

        symbol = data.get("s")
        if not symbol:
            return

        try:
            self.market_store.update(
                symbol=symbol,
                last_price=Decimal(data["c"]) if data.get("c") else None,
                bid=Decimal(data["b"]) if data.get("b") else None,
                ask=Decimal(data["a"]) if data.get("a") else None,
                volume_24h=Decimal(data["q"]) if data.get("q") else None,
                price_change_24h_pct=Decimal(data["P"]) if data.get("P") else None,
                high_24h=Decimal(data["h"]) if data.get("h") else None,
                low_24h=Decimal(data["l"]) if data.get("l") else None,
            )
        except Exception as exc:
            logger.warning("Failed to parse websocket payload: %s", exc)