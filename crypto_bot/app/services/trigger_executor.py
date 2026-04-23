from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.config import settings
from app.services.execution_service import ExecutionService

logger = logging.getLogger(__name__)


class TriggerExecutor:
    """Processes real-time exit orders (stop-loss / take-profit) fired by the WebSocket.

    Orders arrive on an asyncio.Queue and are executed in a thread pool so the
    WebSocket price stream is never blocked.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        execution_service: ExecutionService,
        session_factory: Optional[Callable] = None,
    ) -> None:
        self.queue = queue
        self.execution_service = execution_service
        self.session_factory = session_factory

    async def run_forever(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                order: dict[str, Any] = await self.queue.get()
                await loop.run_in_executor(None, self._execute, order)
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TriggerExecutor: error processing order")

    def _execute(self, order: dict[str, Any]) -> None:
        asset = order["asset"]
        trigger_price = float(order.get("trigger_price", 0))

        balance = self._get_balance()
        market_data = {asset: {"last_price": trigger_price, "symbol": asset}}

        result = self.execution_service.execute_decision(order, market_data, balance)

        status = "error" if result.get("error") else "ok"
        logger.warning(
            "TriggerExecutor [%s]: %s %s @ %.4f — %s",
            status, order["action"], asset, trigger_price, order.get("reasoning", ""),
        )

        if status == "ok" and self.session_factory:
            self._close_trade(order, result)

    def _close_trade(self, order: dict[str, Any], result: dict[str, Any]) -> None:
        from app.state import bot_state, trade_service

        session_id = bot_state.current_session_id
        if session_id is None:
            return

        exec_order = result.get("order") or {}
        exit_price = float(exec_order.get("price", 0) or order.get("trigger_price", 0))
        exit_fee = float(exec_order.get("fee_amount", 0) or 0)

        reasoning = order.get("reasoning", "").lower()
        if "stop" in reasoning:
            exit_reason = "stop_loss"
        elif "take" in reasoning or "profit" in reasoning:
            exit_reason = "take_profit"
        else:
            exit_reason = "ai_sell"

        db = self.session_factory()
        try:
            open_trade = trade_service.get_open_trade_for_symbol(db, session_id, order["asset"])
            if open_trade:
                trade_service.close_trade(
                    db, open_trade.id,
                    exit_execution_id=None,  # execution record created by execution_service
                    exit_price=exit_price,
                    exit_fee_usdt=exit_fee,
                    exit_reason=exit_reason,
                    closed_at=datetime.now(timezone.utc),
                )
                db.commit()
                logger.info(
                    "TriggerExecutor: closed trade %d for %s (%s)",
                    open_trade.id, order["asset"], exit_reason,
                )
        except Exception:
            logger.exception("TriggerExecutor: failed to close trade for %s", order["asset"])
            db.rollback()
        finally:
            db.close()

    def _get_balance(self) -> dict[str, Any]:
        if settings.paper_trading:
            bal = self.execution_service.paper_usdt
            return {"USDT": {"free": bal, "used": 0.0, "total": bal}}
        try:
            from app.services.data_feeds import fetch_balance
            return fetch_balance()
        except Exception:
            logger.warning("TriggerExecutor: balance fetch failed — using paper balance")
            bal = self.execution_service.paper_usdt
            return {"USDT": {"free": bal, "used": 0.0, "total": bal}}
