from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import AIDecision

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("")
def list_decisions(db: Session = Depends(get_db)):
    rows = db.query(AIDecision).all()
    return [
        {
            "id": row.id,
            "snapshot_id": row.snapshot_id,
            "action": row.action,
            "confidence_score": float(row.confidence_score) if row.confidence_score is not None else None,
            "reasoning_summary": row.reasoning_summary,
        }
        for row in rows
    ]