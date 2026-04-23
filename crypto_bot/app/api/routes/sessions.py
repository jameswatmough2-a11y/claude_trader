from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.trading_session import TradingSession
from app.models.trade import Trade
from app.models.execution import Execution
from app.models.ai_decision import AIDecision
from app.models.hourly_market_snapshot import HourlyMarketSnapshot
from app.models.asset import Asset

router = APIRouter(prefix="/sessions", tags=["sessions"])

TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}


def _session_to_dict(sess: TradingSession, stats: Optional[dict] = None) -> dict:
    pnl_usdt = sess.pnl_usdt
    start = float(sess.starting_balance_usdt)
    pnl_pct = sess.pnl_pct
    return {
        "id": sess.id,
        "started_at": sess.started_at.isoformat(),
        "ended_at": sess.ended_at.isoformat() if sess.ended_at else None,
        "status": sess.status,
        "duration_seconds": sess.duration_seconds,
        "starting_balance_usdt": float(sess.starting_balance_usdt),
        "ending_balance_usdt": float(sess.ending_balance_usdt) if sess.ending_balance_usdt is not None else None,
        "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        **(stats or {}),
    }


def _trade_to_dict(trade: Trade) -> dict:
    return {
        "id": trade.id,
        "session_id": trade.session_id,
        "symbol": trade.symbol,
        "entry_price": float(trade.entry_price) if trade.entry_price else None,
        "exit_price": float(trade.exit_price) if trade.exit_price else None,
        "entry_qty": float(trade.entry_qty) if trade.entry_qty else None,
        "size_pct": float(trade.size_pct) if trade.size_pct else None,
        "stop_loss_price": float(trade.stop_loss_price) if trade.stop_loss_price else None,
        "take_profit_price": float(trade.take_profit_price) if trade.take_profit_price else None,
        "realized_pnl_pct": float(trade.realized_pnl_pct) if trade.realized_pnl_pct else None,
        "realized_pnl_usdt": float(trade.realized_pnl_usdt) if trade.realized_pnl_usdt else None,
        "entry_fee_usdt": float(trade.entry_fee_usdt) if trade.entry_fee_usdt else None,
        "exit_fee_usdt": float(trade.exit_fee_usdt) if trade.exit_fee_usdt else None,
        "exit_reason": trade.exit_reason,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "status": trade.status,
    }


@router.get("")
def list_sessions(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    from app.services.trade_service import TradeService
    ts = TradeService()

    sessions = (
        db.query(TradingSession)
        .order_by(TradingSession.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    result = []
    for sess in sessions:
        stats = ts.get_session_stats(db, sess.id)
        result.append(_session_to_dict(sess, stats))
    return result


@router.get("/current")
def get_current_session(db: Session = Depends(get_db)) -> Optional[dict]:
    from app.state import bot_state, execution_service, risk_service, market_store
    from app.services.trade_service import TradeService
    ts = TradeService()

    if bot_state.current_session_id is None:
        return None

    sess = db.get(TradingSession, bot_state.current_session_id)
    if sess is None:
        return None

    stats = ts.get_session_stats(db, sess.id)

    # Live balance = available USDT + current market value of all open positions.
    # Without this, every BUY looks like a loss because USDT decreases immediately
    # while the offsetting position value is ignored.
    live_usdt = execution_service.paper_usdt
    for symbol, pos in risk_service.get_open_positions().items():
        open_trade = ts.get_open_trade_for_symbol(db, sess.id, symbol)
        if open_trade and open_trade.entry_qty:
            state = market_store.get(symbol)
            cur_price = float(state.last_price) if state and state.last_price else pos.current_price
            live_usdt += float(open_trade.entry_qty) * cur_price

    start = float(sess.starting_balance_usdt)
    live_pnl = live_usdt - start
    live_pnl_pct = live_pnl / start * 100 if start > 0 else 0.0

    return {
        **_session_to_dict(sess, stats),
        "live_balance_usdt": round(live_usdt, 4),
        "live_pnl_usdt": round(live_pnl, 4),
        "live_pnl_pct": round(live_pnl_pct, 2),
    }


@router.get("/current/markers")
def get_current_session_markers(
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    timeframe: str = Query("1m"),
    include_holds: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    from app.state import bot_state
    session_id = bot_state.current_session_id
    if session_id is None:
        return {"markers": [], "trade_ranges": []}
    return get_session_markers(session_id, symbol, timeframe, include_holds, db)


@router.get("/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)) -> dict:
    from app.services.trade_service import TradeService
    ts = TradeService()

    sess = db.get(TradingSession, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")

    stats = ts.get_session_stats(db, sess.id)
    trades = db.query(Trade).filter(Trade.session_id == session_id).order_by(Trade.opened_at).all()
    return {
        **_session_to_dict(sess, stats),
        "trades": [_trade_to_dict(t) for t in trades],
    }


@router.get("/{session_id}/markers")
def get_session_markers(
    session_id: int,
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    timeframe: str = Query("1m"),
    include_holds: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    interval_sec = TIMEFRAME_SECONDS.get(timeframe, 60)
    symbol = symbol.upper()

    # Fetch all non-HOLD executions for this session + symbol
    rows = (
        db.query(Execution, AIDecision, HourlyMarketSnapshot)
        .join(AIDecision, Execution.ai_decision_id == AIDecision.id)
        .join(HourlyMarketSnapshot, AIDecision.snapshot_id == HourlyMarketSnapshot.id)
        .join(Asset, HourlyMarketSnapshot.asset_id == Asset.id)
        .filter(Execution.session_id == session_id)
        .filter(Asset.symbol == symbol)
        .filter(Execution.status.in_(["filled", "paper_filled"]) if not include_holds else True)
        .order_by(HourlyMarketSnapshot.snapshot_time)
        .all()
    )

    markers = []
    for execution, decision, snapshot in rows:
        action = execution.executed_action.upper()
        if action == "HOLD" and not include_holds:
            continue

        raw_ts = snapshot.snapshot_time.timestamp()
        candle_ts = int(raw_ts // interval_sec * interval_sec)
        price = float(execution.execution_price or snapshot.close_price or 0)

        if action == "BUY":
            color = "#089981"
            shape = "arrowUp"
            position = "belowBar"
            text = f"BUY {float(decision.recommended_size or 0):.0f}%"
        elif action == "SELL":
            color = "#f23645"
            shape = "arrowDown"
            position = "aboveBar"
            exit_reason = ""
            if execution.trade_id:
                trade = db.get(Trade, execution.trade_id)
                if trade:
                    exit_reason = f" {trade.exit_reason or ''}".strip()
            text = f"SELL{exit_reason}"
        else:
            color = "#eab308"
            shape = "circle"
            position = "aboveBar"
            text = "HOLD"

        markers.append({
            "time": candle_ts,
            "position": position,
            "color": color,
            "shape": shape,
            "text": text,
            "action": action,
            "price": price,
            "confidence": float(decision.confidence_score or 0),
            "trade_id": execution.trade_id,
        })

    # Trade ranges for background highlighting
    trades = (
        db.query(Trade)
        .filter(Trade.session_id == session_id, Trade.symbol == symbol)
        .order_by(Trade.opened_at)
        .all()
    )
    trade_ranges = []
    for trade in trades:
        entry_ts = int(trade.opened_at.timestamp() // interval_sec * interval_sec)
        exit_ts = int(trade.closed_at.timestamp() // interval_sec * interval_sec) if trade.closed_at else None
        trade_ranges.append({
            "trade_id": trade.id,
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "pnl_pct": float(trade.realized_pnl_pct) if trade.realized_pnl_pct is not None else None,
            "status": trade.status,
        })

    return {"markers": markers, "trade_ranges": trade_ranges}
