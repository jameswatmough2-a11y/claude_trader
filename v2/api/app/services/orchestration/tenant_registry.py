"""In-memory registry of active per-tenant services.

A tenant becomes "active" when they flip `bot_configs.running = true` via
POST /api/bot/start. tick_scheduler calls ensure() on the next tick, which
lazily constructs their RiskService + ExecutionService + TradingCycleService
if they're not already registered.

Holds references; does NOT own lifecycle beyond eviction when running=false.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.services.tenant.risk_service import RiskService
    from app.services.tenant.execution_service import ExecutionService
    from app.services.tenant.trading_cycle import TradingCycleService


@dataclass
class TenantServices:
    tenant_id: int
    risk_service: "RiskService"
    execution_service: "ExecutionService"
    cycle_service: "TradingCycleService"
    tracked_symbols: set[str]


class TenantRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._tenants: dict[int, TenantServices] = {}

    def register(self, services: TenantServices) -> None:
        with self._lock:
            self._tenants[services.tenant_id] = services

    def unregister(self, tenant_id: int) -> None:
        with self._lock:
            self._tenants.pop(tenant_id, None)

    def get(self, tenant_id: int) -> TenantServices | None:
        with self._lock:
            return self._tenants.get(tenant_id)

    def all(self) -> list[TenantServices]:
        with self._lock:
            return list(self._tenants.values())

    def union_of_tracked_symbols(self) -> set[str]:
        """Drives binance_ws subscription. Empty set = disconnect."""
        with self._lock:
            result: set[str] = set()
            for t in self._tenants.values():
                result |= t.tracked_symbols
            return result
