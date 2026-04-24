"""Shared in-memory store of live ticker data.

Ported from v1 (crypto_bot/app/services/market_state.py) — no tenant changes
needed; prices are universal. Read by dashboard chart, tenant cycles, and
risk checks.

TODO: port SymbolMarketState dataclass + MarketStateStore.update/get/snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Iterable


@dataclass
class SymbolMarketState:
    symbol: str
    last_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume_24h: Decimal | None = None
    price_change_24h_pct: Decimal | None = None
    high_24h: Decimal | None = None
    low_24h: Decimal | None = None


class MarketStateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, SymbolMarketState] = {}

    def update(self, symbol: str, **fields) -> None:
        with self._lock:
            state = self._state.setdefault(symbol, SymbolMarketState(symbol=symbol))
            for k, v in fields.items():
                if v is not None:
                    setattr(state, k, v)

    def get(self, symbol: str) -> SymbolMarketState | None:
        with self._lock:
            return self._state.get(symbol)

    def snapshot(self, symbols: Iterable[str] | None = None) -> dict[str, SymbolMarketState]:
        with self._lock:
            if symbols is None:
                return dict(self._state)
            return {s: self._state[s] for s in symbols if s in self._state}
