"""Read-only: closed-trade history + open trades for the calling tenant.
Powers the Trades screen and the chart's entry/exit markers."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(None, pattern="^(open|closed)$"),
    symbol: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict]:
    # TODO: query Trade WHERE tenant_id = tenant.id [AND status = ...] [AND symbol = ...]
    return []
