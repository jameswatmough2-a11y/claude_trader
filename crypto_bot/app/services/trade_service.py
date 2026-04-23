from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class TradeService:
    """Manages Trade lifecycle: open on BUY, close on SELL/SL/TP."""

    def open_trade(
        self,
        db: "Session",
        session_id: int,
        symbol: str,
        entry_execution_id: int,
        entry_price: float,
        entry_qty: float,
        entry_fee_usdt: float,
        size_pct: float,
        opened_at: datetime,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> "object":
        from app.models.trade import Trade

        trade = Trade(
            session_id=session_id,
            user_id=1,
            symbol=symbol.upper(),
            entry_execution_id=entry_execution_id,
            entry_price=Decimal(str(round(entry_price, 8))),
            entry_qty=Decimal(str(round(entry_qty, 8))),
            entry_fee_usdt=Decimal(str(round(entry_fee_usdt, 8))),
            size_pct=Decimal(str(round(size_pct, 4))),
            opened_at=opened_at,
            stop_loss_price=Decimal(str(round(stop_loss_price, 8))) if stop_loss_price else None,
            take_profit_price=Decimal(str(round(take_profit_price, 8))) if take_profit_price else None,
            status="open",
        )
        db.add(trade)
        db.flush()
        logger.info("Trade %d opened: %s @ %.4f", trade.id, symbol, entry_price)
        return trade

    def close_trade(
        self,
        db: "Session",
        trade_id: int,
        exit_execution_id: Optional[int],
        exit_price: float,
        exit_fee_usdt: float,
        exit_reason: str,
        closed_at: datetime,
    ) -> Optional["object"]:
        from app.models.trade import Trade

        trade = db.get(Trade, trade_id)
        if trade is None or trade.status == "closed":
            return trade

        trade.exit_execution_id = exit_execution_id
        trade.exit_price = Decimal(str(round(exit_price, 8)))
        trade.exit_fee_usdt = Decimal(str(round(exit_fee_usdt, 8)))
        trade.exit_reason = exit_reason
        trade.closed_at = closed_at
        trade.status = "closed"

        entry = float(trade.entry_price or 0)
        if entry > 0:
            pnl_pct = (exit_price - entry) / entry * 100
            trade.realized_pnl_pct = Decimal(str(round(pnl_pct, 4)))
            qty = float(trade.entry_qty or 0)
            if qty > 0:
                gross = qty * (exit_price - entry)
                fees = float(trade.entry_fee_usdt or 0) + exit_fee_usdt
                trade.realized_pnl_usdt = Decimal(str(round(gross - fees, 8)))

        db.flush()
        logger.info(
            "Trade %d closed: %s @ %.4f reason=%s pnl=%.2f%%",
            trade.id, trade.symbol, exit_price, exit_reason,
            float(trade.realized_pnl_pct or 0),
        )
        return trade

    def get_open_trade_for_symbol(
        self,
        db: "Session",
        session_id: Optional[int],
        symbol: str,
    ) -> Optional["object"]:
        from app.models.trade import Trade

        q = db.query(Trade).filter(
            Trade.symbol == symbol.upper(),
            Trade.status == "open",
        )
        if session_id is not None:
            q = q.filter(Trade.session_id == session_id)
        return q.order_by(Trade.opened_at.desc()).first()

    def get_session_stats(self, db: "Session", session_id: int) -> dict:
        from app.models.trade import Trade

        trades = db.query(Trade).filter(Trade.session_id == session_id).all()
        closed = [t for t in trades if t.status == "closed"]
        winners = [t for t in closed if float(t.realized_pnl_usdt or 0) > 0]
        total_pnl = sum(float(t.realized_pnl_usdt or 0) for t in closed)
        total_fees = sum(
            float(t.entry_fee_usdt or 0) + float(t.exit_fee_usdt or 0)
            for t in trades
        )
        return {
            "total_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "winning_trades": len(winners),
            "losing_trades": len(closed) - len(winners),
            "win_rate": round(len(winners) / len(closed) * 100, 1) if closed else 0.0,
            "total_pnl_usdt": round(total_pnl, 4),
            "total_fees_usdt": round(total_fees, 4),
        }
