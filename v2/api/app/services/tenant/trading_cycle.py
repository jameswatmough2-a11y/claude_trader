"""Per-tenant cycle. Called by tick_scheduler when this tenant is due.

One run =
  1. Load tenant's BotConfig
  2. Snapshot shared market_state for their tracked_symbols
  3. Reuse latest HourlyMarketSnapshot if fresh (shared), else write a new one
  4. Read shared SentimentCache (no fetch)
  5. Build DecisionContext (positions + previous decisions + config)
  6. decision_provider.decide(context)
  7. risk_service.evaluate_decision per item
  8. execution_service.execute_decision per approved BUY/SELL
  9. Persist AIDecision + Execution + Trade rows (scoped to tenant_id)
 10. Publish 'cycle_complete' event for the user's WebSocket
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.bot_config import BotConfig
    from app.services.shared.market_state import MarketStateStore
    from app.services.shared.sentiment_cache import SentimentCache
    from app.services.shared.decision_provider import DecisionProvider
    from app.services.tenant.risk_service import RiskService
    from app.services.tenant.execution_service import ExecutionService
    from app.event_bus import EventBus


class TradingCycleService:
    def __init__(
        self,
        tenant_id: int,
        market_store: "MarketStateStore",
        sentiment_cache: "SentimentCache",
        decision_provider: "DecisionProvider",
        risk_service: "RiskService",
        execution_service: "ExecutionService",
        event_bus: "EventBus",
    ) -> None:
        self.tenant_id = tenant_id
        self.market_store = market_store
        self.sentiment_cache = sentiment_cache
        self.decision_provider = decision_provider
        self.risk_service = risk_service
        self.execution_service = execution_service
        self.event_bus = event_bus

    async def run(self, db: "Session", config: "BotConfig") -> None:
        """TODO: orchestrate steps 1-10 above."""
        pass
