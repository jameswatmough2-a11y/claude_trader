from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.services.data_feeds import fetch_ohlcv
from app.state import market_store, risk_service

router = APIRouter(tags=["chart"])

# ── REST: historical OHLCV ────────────────────────────────────────────────────

_VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"}
_CANDLE_LIMITS: dict[str, int] = {
    "1m": 200, "3m": 200, "5m": 200, "15m": 200, "30m": 150,
    "1h": 200, "2h": 200, "4h": 200, "6h": 200, "12h": 200,
    "1d": 200, "1w": 100,
}


@router.get("/chart/history")
async def chart_history(
    symbol: str = Query("BTCUSDT"),
    interval: str = Query("1m"),
) -> JSONResponse:
    if interval not in _VALID_INTERVALS:
        return JSONResponse({"error": f"Unknown interval '{interval}'"}, status_code=400)
    limit = _CANDLE_LIMITS.get(interval, 200)

    try:
        df = await asyncio.to_thread(fetch_ohlcv, symbol, interval, limit)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    candles: list[dict[str, Any]] = []
    for ts, row in df.iterrows():
        o, h, lo, c, v = row["open"], row["high"], row["low"], row["close"], row["volume"]
        if any(math.isnan(float(x)) for x in [o, h, lo, c]):
            continue
        candles.append({
            "time": int(ts.timestamp()),
            "open": float(o),
            "high": float(h),
            "low": float(lo),
            "close": float(c),
            "volume": float(v),
        })
    return JSONResponse({"symbol": symbol.upper(), "interval": interval, "candles": candles})


# ── WebSocket: real-time candle stream ────────────────────────────────────────

def _interval_seconds(interval: str) -> int:
    mult, unit = int(interval[:-1]), interval[-1]
    return mult * {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit, 60)


class _CandleBuilder:
    """Accumulates live price ticks into OHLCV candles for a fixed time interval."""

    __slots__ = ("_interval", "_open_time", "_open", "_high", "_low", "_close", "_volume")

    def __init__(self, interval_sec: int) -> None:
        self._interval = interval_sec
        self._open_time: int = 0
        self._open = self._high = self._low = self._close = self._volume = 0.0

    def push(self, price: float, now: float) -> dict[str, Any]:
        open_time = int(now) - int(now) % self._interval
        if open_time != self._open_time:
            # New period — start a fresh candle
            self._open_time = open_time
            self._open = self._high = self._low = price
            self._volume = 0.0
        else:
            if price > self._high:
                self._high = price
            if price < self._low:
                self._low = price
        self._close = price
        return {
            "time": self._open_time,
            "open": self._open,
            "high": self._high,
            "low": self._low,
            "close": self._close,
            "volume": self._volume,
        }


@router.websocket("/ws/chart")
async def chart_ws(ws: WebSocket) -> None:
    await ws.accept()

    symbol: str | None = None
    builder: _CandleBuilder | None = None
    last_price: float | None = None
    # Track sent position so we only push when it actually changes
    last_pos_key: tuple | None = None

    async def _send(payload: dict[str, Any]) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    async def _sync_position(sym: str) -> None:
        nonlocal last_pos_key
        pos = risk_service.get_open_positions().get(sym)
        pos_key = (pos.entry_price, pos.stop_loss_price, pos.take_profit_price) if pos else None
        if pos_key == last_pos_key:
            return
        last_pos_key = pos_key
        if pos is not None:
            await _send({
                "type": "position",
                "entry_price": pos.entry_price,
                "stop_loss_price": pos.stop_loss_price,
                "take_profit_price": pos.take_profit_price,
                "size_pct": pos.size_pct,
            })
        else:
            await _send({"type": "position_cleared"})

    try:
        while True:
            # Non-blocking receive so we can push ticks without waiting for the client
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=0.05)
                if msg.get("type") == "subscribe":
                    symbol = str(msg.get("symbol", "BTCUSDT")).upper()
                    interval = str(msg.get("interval", "1m"))
                    builder = _CandleBuilder(_interval_seconds(interval))
                    last_price = None
                    last_pos_key = None  # force resend of position state on resubscribe
                    await _sync_position(symbol)
            except asyncio.TimeoutError:
                pass
            except Exception:
                break

            if symbol is None or builder is None:
                await asyncio.sleep(0.1)
                continue

            # Sync position state on every iteration (detects opens/closes live)
            await _sync_position(symbol)

            state = market_store.get(symbol)
            if state and state.last_price is not None:
                price = float(state.last_price)
                if price != last_price:
                    last_price = price
                    now = datetime.now(timezone.utc).timestamp()
                    candle = builder.push(price, now)
                    await _send({"type": "candle", "data": candle})
                    await _send({
                        "type": "price",
                        "price": price,
                        "change_24h_pct": (
                            float(state.price_change_24h_pct)
                            if state.price_change_24h_pct is not None else None
                        ),
                    })

            await asyncio.sleep(0.2)  # 5 Hz

    except WebSocketDisconnect:
        pass
