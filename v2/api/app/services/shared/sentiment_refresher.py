"""Background coroutine that keeps SentimentCache warm for the union of all
tenants' tracked_symbols.

Runs on its own cadence — independent of any tenant cycle. This is the *third*
long-running coroutine alongside binance_ws and trigger_executor.

Schedule:
  - Fear & Greed: every 30 min (global)
  - Reddit per-symbol: every 15 min
  - RSS per-symbol: every 15 min
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.shared.sentiment_cache import SentimentCache
    from app.services.orchestration.tenant_registry import TenantRegistry


class SentimentRefresher:
    def __init__(
        self,
        cache: "SentimentCache",
        tenant_registry: "TenantRegistry",
    ) -> None:
        self.cache = cache
        self.tenant_registry = tenant_registry

    async def run_forever(self) -> None:
        while True:
            try:
                symbols = self.tenant_registry.union_of_tracked_symbols()
                await self._refresh_global()
                for symbol in symbols:
                    await self._refresh_symbol(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SentimentRefresher: cycle failed")
            await asyncio.sleep(60 * 15)  # coarse loop; per-source TTLs gate actual fetches

    async def _refresh_global(self) -> None:
        """TODO: fetch Fear & Greed index, write SentimentEntry(source='fear_greed')."""
        pass

    async def _refresh_symbol(self, symbol: str) -> None:
        """TODO: fetch Reddit + RSS for `symbol`, score via TextBlob, cache."""
        pass
