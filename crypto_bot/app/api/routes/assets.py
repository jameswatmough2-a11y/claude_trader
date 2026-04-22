from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Asset

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("")
def list_assets(db: Session = Depends(get_db)):
    rows = db.query(Asset).all()
    return [
        {
            "id": row.id,
            "symbol": row.symbol,
            "base_currency": row.base_currency,
            "quote_currency": row.quote_currency,
        }
        for row in rows
    ]