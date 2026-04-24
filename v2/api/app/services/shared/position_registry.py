"""Reverse index: symbol -> list of (tenant_id, risk_service) holders.

Lets binance_ws answer "who holds BTCUSDT?" in O(holders) instead of iterating
every tenant. Kept in sync by RiskService.record_open_position / close_position
calling register() / unregister() on this singleton.
"""
from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tenant.risk_service import RiskService


class PositionRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        # symbol -> {tenant_id: risk_service}
        self._index: dict[str, dict[int, "RiskService"]] = {}

    def register(self, symbol: str, tenant_id: int, risk_service: "RiskService") -> None:
        with self._lock:
            self._index.setdefault(symbol.upper(), {})[tenant_id] = risk_service

    def unregister(self, symbol: str, tenant_id: int) -> None:
        with self._lock:
            holders = self._index.get(symbol.upper())
            if holders:
                holders.pop(tenant_id, None)
                if not holders:
                    del self._index[symbol.upper()]

    def holders_of(self, symbol: str) -> list[tuple[int, "RiskService"]]:
        with self._lock:
            return list(self._index.get(symbol.upper(), {}).items())

    def subscribed_symbols(self) -> set[str]:
        """Union of every holder's symbol — NOT what binance_ws subscribes to
        (that's driven by tenants' tracked_symbols config, which is broader)."""
        with self._lock:
            return set(self._index.keys())
