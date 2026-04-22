from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Position

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("")
def list_positions(
    db: Session = Depends(get_db),
    limit: int = Query(default=30, le=500),
):
    rows = (
        db.query(Position)
        .order_by(Position.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "symbol": row.asset.symbol,
            "snapshot_time": row.snapshot.snapshot_time.isoformat(),
            "side": row.side,
            "size": float(row.size),
            "entry_price": float(row.entry_price) if row.entry_price is not None else None,
            "wallet_balance": float(row.wallet_balance) if row.wallet_balance is not None else None,
        }
        for row in rows
    ]
