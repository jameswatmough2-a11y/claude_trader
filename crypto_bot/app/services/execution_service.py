from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt

from app.config import settings
from app.services.data_feeds import get_exchange
from app.services.risk_service import RiskService

logger = logging.getLogger(__name__)

TRADE_LOG_PATH = Path("logs/trades.jsonl")


class ExecutionService:
    """Executes approved trading decisions in paper or live mode."""

    def __init__(self, risk_service: RiskService) -> None:
        self.risk_service = risk_service
        TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def execute_decision(
        self,
        decision: dict[str, Any],
        market_data: dict[str, Any],
        balance: dict[str, Any],
    ) -> dict[str, Any]:
        asset = decision["asset"].upper()
        action = decision["action"].upper()

        md = market_data.get(asset, {})
        current_price = float(md.get("last_price", 0) or 0)
        portfolio_usdt = float(balance.get("USDT", {}).get("total", 0))
        timestamp = datetime.now(timezone.utc).isoformat()

        record: dict[str, Any] = {
            "timestamp": timestamp,
            "asset": asset,
            "action": action,
            "confidence": decision["confidence"],
            "size_pct": decision["size_pct"],
            "current_price": current_price,
            "portfolio_usdt": portfolio_usdt,
            "reasoning": decision["reasoning"],
            "paper_trading": settings.paper_trading,
            "order": None,
            "error": None,
        }

        if action == "HOLD":
            self._log(record)
            return record

        if settings.paper_trading:
            self._execute_paper(record, asset, action, current_price, portfolio_usdt, timestamp)
        else:
            self._execute_live(record, asset, action, current_price, portfolio_usdt)

        self._log(record)
        return record

    def execute_all_decisions(
        self,
        decisions: list[dict[str, Any]],
        market_data: dict[str, Any],
        balance: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [self.execute_decision(d, market_data, balance) for d in decisions]

    def _execute_paper(
        self,
        record: dict[str, Any],
        asset: str,
        action: str,
        price: float,
        portfolio_usdt: float,
        timestamp: str,
    ) -> None:
        try:
            qty = self._compute_qty(asset, record["size_pct"], price, portfolio_usdt)
            record["order"] = {
                "id": f"PAPER-{timestamp}",
                "symbol": asset,
                "side": action.lower(),
                "type": "market",
                "qty": qty,
                "price": price,
                "status": "paper_filled",
            }
            self._update_positions(asset, action, price, record["size_pct"])
        except ValueError as exc:
            record["error"] = str(exc)
            logger.warning("Paper order skipped for %s: %s", asset, exc)

    def _execute_live(
        self,
        record: dict[str, Any],
        asset: str,
        action: str,
        price: float,
        portfolio_usdt: float,
    ) -> None:
        try:
            qty = self._compute_qty(asset, record["size_pct"], price, portfolio_usdt)
            side = "buy" if action == "BUY" else "sell"
            ccxt_symbol = f"{asset[:-4]}/USDT" if asset.endswith("USDT") and "/" not in asset else asset
            order = get_exchange().create_market_order(ccxt_symbol, side, qty)
            record["order"] = order
            filled_price = float(order.get("average") or price)
            self._update_positions(asset, action, filled_price, record["size_pct"])
        except (ccxt.BaseError, ValueError, Exception) as exc:
            record["error"] = str(exc)
            logger.exception("Live order failed for %s", asset)

    def _compute_qty(self, symbol: str, size_pct: float, price: float, portfolio_usdt: float) -> float:
        if price <= 0:
            raise ValueError(f"Price for {symbol} is zero — cannot compute quantity")
        return portfolio_usdt * (size_pct / 100.0) / price

    def _update_positions(self, asset: str, action: str, price: float, size_pct: float) -> None:
        if action == "BUY":
            self.risk_service.record_open_position(asset, price, size_pct)
        elif action == "SELL":
            self.risk_service.close_position(asset)

    def _log(self, record: dict[str, Any]) -> None:
        with open(TRADE_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
