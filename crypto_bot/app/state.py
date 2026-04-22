"""
Module-level service singletons.

Centralised here so that app/main.py and API routes can both import from a
single location without creating circular imports.
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.services.market_state import MarketStateStore
from app.services.risk_service import RiskService
from app.services.execution_service import ExecutionService
from app.services.trading_cycle import TradingCycleService
from app.services.binance_ws import BinanceWebSocketService
from app.services.trigger_executor import TriggerExecutor

trigger_queue: asyncio.Queue = asyncio.Queue()

market_store = MarketStateStore()
risk_service = RiskService()
execution_service = ExecutionService(risk_service=risk_service)
trading_cycle_service = TradingCycleService(
    market_store=market_store,
    risk_service=risk_service,
    execution_service=execution_service,
)
ws_service = BinanceWebSocketService(
    symbols=settings.tracked_symbols,
    market_store=market_store,
    risk_service=risk_service,
    trigger_queue=trigger_queue,
)
trigger_executor = TriggerExecutor(
    queue=trigger_queue,
    execution_service=execution_service,
)
