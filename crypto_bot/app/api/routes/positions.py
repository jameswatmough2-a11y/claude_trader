from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Position

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("")
def list_positions(db: Session = Depends(get_db)):
    rows = db.query(Position).all()
    return [
        {
            "id": row.id,
            "asset_id": row.asset_id,
            "side": row.side,
            "size": float(row.size),
            "entry_price": float(row.entry_price) if row.entry_price is not None else None,
            "wallet_balance": float(row.wallet_balance) if row.wallet_balance is not None else None,
        }
        for row in rows
    ]