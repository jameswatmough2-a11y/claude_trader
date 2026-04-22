from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.config import settings
from app.state import market_store, risk_service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("")
def live_market() -> dict[str, Any]:
    """
    Returns the current live market state for all tracked symbols.

    Includes price data from the WebSocket feed and, when a position is open,
    the computed stop-loss / take-profit levels so the UI can draw reference lines.
    """
    positions = risk_service.get_open_positions()
    result: dict[str, Any] = {}

    for symbol, state in market_store.all().items():
        price = float(state.last_price) if state.last_price is not None else None
        pos = positions.get(symbol)

        position_info: dict[str, Any] | None = None
        if pos is not None:
            sl_price = pos.entry_price * (1 - settings.stop_loss_pct / 100)
            tp_price = (
                pos.entry_price * (1 + settings.take_profit_pct / 100)
                if settings.take_profit_pct > 0
                else None
            )
            position_info = {
                "entry_price": pos.entry_price,
                "size_pct": pos.size_pct,
                "stop_loss_price": round(sl_price, 8),
                "take_profit_price": round(tp_price, 8) if tp_price is not None else None,
            }

        result[symbol] = {
            "symbol": symbol,
            "price": price,
            "bid": float(state.bid) if state.bid is not None else None,
            "ask": float(state.ask) if state.ask is not None else None,
            "change_24h_pct": float(state.price_change_24h_pct) if state.price_change_24h_pct is not None else None,
            "high_24h": float(state.high_24h) if state.high_24h is not None else None,
            "low_24h": float(state.low_24h) if state.low_24h is not None else None,
            "updated_at": state.updated_at.isoformat(),
            "position": position_info,
        }

    return result
