"""Per-tenant start / stop / status / config.

Every route depends on `current_tenant` — tenant isolation is enforced at
the dependency layer. Routes never accept a tenant_id from the client.

Changes to `tracked_symbols` trigger a ws_service.reconnect() so the shared
WebSocket resubscribes to the new union of symbols.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/bot", tags=["bot"])


class BotConfigIn(BaseModel):
    interval_minutes: int | None = None
    tracked_symbols: str | None = None
    min_confidence: float | None = None
    max_position_pct: float | None = None
    max_total_exposure_pct: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    paper_balance_usdt: float | None = None
    chart_interval: str | None = None
    model_name: str | None = None


@router.get("/status")
def status(tenant: Annotated[Tenant, Depends(current_tenant)], db: Annotated[Session, Depends(get_db)]) -> dict:
    # TODO: load BotConfig WHERE tenant_id = tenant.id, return {running, next_run_at, ...}
    return {"tenant_id": tenant.id, "running": False}


@router.post("/start")
def start(tenant: Annotated[Tenant, Depends(current_tenant)], db: Annotated[Session, Depends(get_db)]) -> dict:
    # TODO: flip BotConfig.running = true, set next_run_at = now
    # TODO: open a new TradingSession
    # TODO: ws_service.reconnect() if this tenant added new symbols
    return {"ok": True}


@router.post("/stop")
def stop(tenant: Annotated[Tenant, Depends(current_tenant)], db: Annotated[Session, Depends(get_db)]) -> dict:
    # TODO: flip BotConfig.running = false, clear next_run_at
    # TODO: close active TradingSession
    # TODO: tenant_registry.unregister(tenant.id)
    return {"ok": True}


@router.get("/config")
def get_config(tenant: Annotated[Tenant, Depends(current_tenant)], db: Annotated[Session, Depends(get_db)]) -> dict:
    # TODO: load + return BotConfig for this tenant
    return {}


@router.put("/config")
def put_config(
    body: BotConfigIn,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    # TODO: upsert BotConfig for tenant, applying non-null fields from body
    # TODO: if tracked_symbols changed, ws_service.reconnect()
    return {"ok": True}
