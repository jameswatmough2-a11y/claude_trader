"""Per-tenant risk layer. Owns this tenant's in-memory positions.

Two responsibilities:
  1. Evaluate AI decisions at cycle time (evaluate_decision).
  2. Check exit conditions on every WS tick (check_exit_conditions) — called
     by the shared binance_ws loop for every tenant holding that symbol.

Registration side-effect: record_open_position() / close_position() also
call PositionRegistry so the WS can find this tenant via reverse lookup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.services.shared.position_registry import PositionRegistry
    from app.models.bot_config import BotConfig


@dataclass
class OpenPosition:
    symbol: str
    entry_price: float
    size_pct: float
    current_price: float = 0.0
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


class RiskService:
    def __init__(self, tenant_id: int, position_registry: "PositionRegistry") -> None:
        self.tenant_id = tenant_id
        self._registry = position_registry
        self._positions: dict[str, OpenPosition] = {}

    # ── Position state ─────────────────────────────────────────────────

    def record_open_position(self, symbol: str, entry_price: float, size_pct: float,
                             stop_loss_price: float | None = None,
                             take_profit_price: float | None = None) -> None:
        symbol = symbol.upper()
        self._positions[symbol] = OpenPosition(
            symbol=symbol, entry_price=entry_price, size_pct=size_pct,
            current_price=entry_price,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
        )
        self._registry.register(symbol, self.tenant_id, self)

    def close_position(self, symbol: str) -> None:
        if self._positions.pop(symbol.upper(), None):
            self._registry.unregister(symbol, self.tenant_id)

    def get_open_positions(self) -> dict[str, OpenPosition]:
        return dict(self._positions)

    def restore_from_db(self, db: "Session") -> None:
        """TODO: port v1 restore logic, filtered by tenant_id."""
        pass

    # ── Decision evaluation ────────────────────────────────────────────

    def evaluate_decision(self, decision: dict[str, Any], current_price: float,
                          config: "BotConfig") -> dict[str, Any]:
        """TODO: port v1 evaluate_decision, using `config` instead of globals."""
        return decision

    def check_exit_conditions(self, symbol: str, price: float,
                              stop_loss_pct: float, take_profit_pct: float) -> dict | None:
        """TODO: port v1 check_exit_conditions.

        Still deletes the position immediately on trigger to prevent
        double-firing on subsequent ticks.
        """
        return None
