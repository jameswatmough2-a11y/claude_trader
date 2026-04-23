from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.system_log import SystemLog

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
def get_logs(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    level: Optional[str] = Query(None, description="Filter by level: INFO, WARNING, ERROR"),
    component: Optional[str] = Query(None, description="Filter by component"),
    symbol: Optional[str] = Query(None, description="Filter by asset symbol"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    cycle_id: Optional[str] = Query(None, description="Filter by cycle ID"),
    since: Optional[str] = Query(None, description="ISO datetime lower bound"),
    until: Optional[str] = Query(None, description="ISO datetime upper bound"),
) -> dict:
    from datetime import datetime

    q = db.query(SystemLog)

    if level:
        q = q.filter(SystemLog.level == level.upper())
    if component:
        q = q.filter(SystemLog.component == component)
    if symbol:
        q = q.filter(SystemLog.symbol == symbol.upper())
    if event_type:
        q = q.filter(SystemLog.event_type == event_type)
    if cycle_id:
        q = q.filter(SystemLog.cycle_id == cycle_id)
    if since:
        try:
            q = q.filter(SystemLog.created_at >= datetime.fromisoformat(since))
        except ValueError:
            pass
    if until:
        try:
            q = q.filter(SystemLog.created_at <= datetime.fromisoformat(until))
        except ValueError:
            pass

    total = q.count()
    items = (
        q.order_by(SystemLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": [
            {
                "id": item.id,
                "created_at": item.created_at.isoformat(),
                "level": item.level,
                "component": item.component,
                "event_type": item.event_type,
                "symbol": item.symbol,
                "cycle_id": item.cycle_id,
                "message": item.message,
                "details_json": item.details_json,
            }
            for item in items
        ],
    }


@router.get("/components")
def list_components(db: Session = Depends(get_db)) -> list[str]:
    rows = db.query(SystemLog.component).distinct().all()
    return sorted(r[0] for r in rows)


@router.get("/event-types")
def list_event_types(db: Session = Depends(get_db)) -> list[str]:
    rows = db.query(SystemLog.event_type).distinct().all()
    return sorted(r[0] for r in rows)
