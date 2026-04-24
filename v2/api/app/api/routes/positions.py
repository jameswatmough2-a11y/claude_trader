"""Open positions for the calling tenant. Reads from RiskService's in-memory
dict (via TenantRegistry) — not the DB — so live prices and unrealised P&L
are up-to-date without a cycle having run."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("")
def list_open_positions(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    # TODO: tenant_registry.get(tenant.id).risk_service.get_open_positions()
    # TODO: enrich each with live last_price from market_store -> unrealized_pnl_pct
    return []
