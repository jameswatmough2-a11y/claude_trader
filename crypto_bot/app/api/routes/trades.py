from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.trade import Trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
def list_trades(
    db: Session = Depends(get_db),
    session_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="open | closed"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    q = db.query(Trade)
    if session_id is not None:
        q = q.filter(Trade.session_id == session_id)
    if symbol:
        q = q.filter(Trade.symbol == symbol.upper())
    if status in ("open", "closed"):
        q = q.filter(Trade.status == status)

    trades = q.order_by(Trade.opened_at.desc()).offset(offset).limit(limit).all()

    return [
        {
            "id": t.id,
            "session_id": t.session_id,
            "symbol": t.symbol,
            "entry_price": float(t.entry_price) if t.entry_price else None,
            "exit_price": float(t.exit_price) if t.exit_price else None,
            "entry_qty": float(t.entry_qty) if t.entry_qty else None,
            "size_pct": float(t.size_pct) if t.size_pct else None,
            "stop_loss_price": float(t.stop_loss_price) if t.stop_loss_price else None,
            "take_profit_price": float(t.take_profit_price) if t.take_profit_price else None,
            "realized_pnl_pct": float(t.realized_pnl_pct) if t.realized_pnl_pct else None,
            "realized_pnl_usdt": float(t.realized_pnl_usdt) if t.realized_pnl_usdt else None,
            "entry_fee_usdt": float(t.entry_fee_usdt) if t.entry_fee_usdt else None,
            "exit_fee_usdt": float(t.exit_fee_usdt) if t.exit_fee_usdt else None,
            "exit_reason": t.exit_reason,
            "opened_at": t.opened_at.isoformat(),
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "status": t.status,
        }
        for t in trades
    ]
