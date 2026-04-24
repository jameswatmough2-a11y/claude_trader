"""Read-only: AI decisions for the calling tenant. Powers the decisions feed
on the overview page — the centerpiece of the v1 UX."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
def list_decisions(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, le=200),
    symbol: str | None = None,
) -> list[dict]:
    # TODO: query AIDecision WHERE tenant_id = tenant.id [AND symbol = symbol]
    # TODO: order by decision_time desc, limit
    return []
