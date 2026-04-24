"""Per-tenant order execution. Owns this tenant's paper USDT balance.

Shared path for both the hourly cycle and the real-time trigger executor —
there's only one place orders get placed + logged to the DB.

On success, writes an Execution row scoped to this tenant_id.
Calls risk_service.record_open_position / close_position to keep
in-memory positions and the PositionRegistry in sync.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.services.tenant.risk_service import RiskService


class ExecutionService:
    def __init__(self, tenant_id: int, risk_service: "RiskService",
                 initial_paper_balance: float) -> None:
        self.tenant_id = tenant_id
        self.risk_service = risk_service
        self._paper_usdt = initial_paper_balance

    @property
    def paper_usdt(self) -> float:
        return self._paper_usdt

    def restore_paper_balance_from_db(self, db: "Session") -> None:
        """TODO: replay Execution rows WHERE tenant_id = self.tenant_id."""
        pass

    def execute_decision(self, decision: dict[str, Any], market_data: dict[str, Any],
                         balance: dict[str, Any]) -> dict[str, Any]:
        """TODO: port v1 execute_decision (paper + live branches)."""
        return {"order": None, "error": None}
