"""Process-wide singletons. Imported by main.py for lifespan wiring and by
routes that need a reference (e.g. websocket -> event_bus).

Separate file from main.py to keep the service graph explicit and to avoid
import cycles between lifespan + routes.
"""
from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.event_bus import EventBus
from app.services.orchestration.tenant_registry import TenantRegistry
from app.services.shared.market_state import MarketStateStore
from app.services.shared.position_registry import PositionRegistry
from app.services.shared.sentiment_cache import SentimentCache

# ── Shared-plane singletons ────────────────────────────────────────────
market_store = MarketStateStore()
position_registry = PositionRegistry()
sentiment_cache = SentimentCache()
tenant_registry = TenantRegistry()
event_bus = EventBus()

# Inter-service queues
trigger_queue: asyncio.Queue = asyncio.Queue()

# Scheduler (holds the TickScheduler.tick job, added in lifespan)
scheduler = AsyncIOScheduler(timezone="UTC")

# The following are wired in main.py lifespan so they can reference the
# singletons above without circular imports:
#   ws_service, sentiment_refresher, trigger_executor, tick_scheduler,
#   decision_provider
