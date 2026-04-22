from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional


@dataclass
class SymbolMarketState:
    symbol: str
    last_price: Optional[Decimal] = None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    price_change_24h_pct: Optional[Decimal] = None
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketStateStore:
    def __init__(self) -> None:
        self._state: dict[str, SymbolMarketState] = {}

    def update(
        self,
        symbol: str,
        *,
        last_price: Decimal | None = None,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        volume_24h: Decimal | None = None,
        price_change_24h_pct: Decimal | None = None,
        high_24h: Decimal | None = None,
        low_24h: Decimal | None = None,
    ) -> None:
        symbol = symbol.upper()
        current = self._state.get(symbol) or SymbolMarketState(symbol=symbol)

        if last_price is not None:
            current.last_price = last_price
        if bid is not None:
            current.bid = bid
        if ask is not None:
            current.ask = ask
        if volume_24h is not None:
            current.volume_24h = volume_24h
        if price_change_24h_pct is not None:
            current.price_change_24h_pct = price_change_24h_pct
        if high_24h is not None:
            current.high_24h = high_24h
        if low_24h is not None:
            current.low_24h = low_24h

        current.updated_at = datetime.now(timezone.utc)
        self._state[symbol] = current

    def get(self, symbol: str) -> SymbolMarketState | None:
        return self._state.get(symbol.upper())

    def all(self) -> dict[str, SymbolMarketState]:
        return dict(self._state)