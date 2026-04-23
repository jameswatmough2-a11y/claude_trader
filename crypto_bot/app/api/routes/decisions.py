from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import AIDecision

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("")
def list_decisions(
    db: Session = Depends(get_db),
    limit: int = Query(default=60, le=500),
):
    rows = (
        db.query(AIDecision)
        .order_by(AIDecision.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "snapshot_id": row.snapshot_id,
            "symbol": row.snapshot.asset.symbol if row.snapshot and row.snapshot.asset else None,
            "snapshot_time": row.snapshot.snapshot_time.isoformat() if row.snapshot else None,
            "action": row.action,
            "confidence_score": float(row.confidence_score) if row.confidence_score is not None else None,
            "reasoning_summary": row.reasoning_summary,
            "execution_price": float(row.execution.execution_price) if row.execution and row.execution.execution_price else None,
            "trade_id": row.execution.trade_id if row.execution else None,
        }
        for row in rows
    ]
