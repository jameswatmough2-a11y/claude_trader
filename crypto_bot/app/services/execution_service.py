from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import ccxt

from app.config import settings
from app.services.data_feeds import get_exchange
from app.services.risk_service import RiskService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TRADE_LOG_PATH = Path("logs/trades.jsonl")


class ExecutionService:
    """Executes approved trading decisions in paper or live mode."""

    def __init__(self, risk_service: RiskService) -> None:
        self.risk_service = risk_service
        self._paper_usdt: float = settings.paper_balance_usdt
        TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    @property
    def paper_usdt(self) -> float:
        return self._paper_usdt

    def restore_paper_balance_from_db(self, db: "Session") -> None:
        from app.models.execution import Execution

        balance = settings.paper_balance_usdt
        executions = (
            db.query(Execution)
            .filter(Execution.status.in_(["filled", "paper_filled"]))
            .all()
        )
        for ex in executions:
            size = float(ex.executed_size or 0)
            price = float(ex.execution_price or 0)
            fees = float(ex.fees_paid or 0)
            cost = size * price
            if ex.executed_action == "BUY":
                balance -= cost + fees
            elif ex.executed_action == "SELL":
                balance += cost - fees

        self._paper_usdt = max(0.0, balance)
        logger.info("Paper balance restored from DB: %.2f USDT", self._paper_usdt)

    def execute_decision(
        self,
        decision: dict[str, Any],
        market_data: dict[str, Any],
        balance: dict[str, Any],
    ) -> dict[str, Any]:
        from app.services import db_logger

        asset = decision["asset"].upper()
        action = decision["action"].upper()

        md = market_data.get(asset, {})
        last_price = float(md.get("last_price", 0) or 0)
        bid = float(md.get("bid") or last_price)
        ask = float(md.get("ask") or last_price)
        portfolio_usdt = float(balance.get("USDT", {}).get("total", 0))
        timestamp = datetime.now(timezone.utc).isoformat()
        fee_rate = settings.taker_fee_rate

        record: dict[str, Any] = {
            "timestamp": timestamp,
            "asset": asset,
            "action": action,
            "confidence": decision["confidence"],
            "size_pct": decision["size_pct"],
            "current_price": last_price,
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
            self._execute_paper(record, asset, action, last_price, bid, ask, portfolio_usdt, timestamp, fee_rate)
        else:
            self._execute_live(record, asset, action, last_price, portfolio_usdt, fee_rate)

        self._log(record)

        if record.get("order") and not record.get("error"):
            db_logger.log_info(
                "execution", "order_placed",
                f"{action} {asset}: qty={record['order'].get('qty', 0):.6f} "
                f"@ {record['order'].get('price', 0):.4f} "
                f"fee={record['order'].get('fee_amount', 0):.4f} USDT",
                symbol=asset,
                details={"action": action, "order": record["order"]},
            )

        return record

    # ── Paper execution ────────────────────────────────────────────────────────

    def _execute_paper(
        self,
        record: dict[str, Any],
        asset: str,
        action: str,
        last_price: float,
        bid: float,
        ask: float,
        portfolio_usdt: float,
        timestamp: str,
        fee_rate: float,
    ) -> None:
        from app.services.market_validator import validate_and_normalize_qty
        from app.services import db_logger

        # Use ask for BUY (taker buys at ask), bid for SELL (taker sells at bid)
        if action == "BUY":
            fill_price = ask if ask > 0 else last_price
            fill_source = "ask" if ask > 0 else "last_price_fallback"
        else:
            fill_price = bid if bid > 0 else last_price
            fill_source = "bid" if bid > 0 else "last_price_fallback"

        if fill_source.endswith("fallback"):
            db_logger.log_warning(
                "execution", "fill_price_fallback",
                f"No bid/ask for {asset} — using last_price for paper fill",
                symbol=asset,
            )

        try:
            raw_qty = self._compute_qty(asset, record["size_pct"], fill_price, portfolio_usdt)
        except ValueError as exc:
            record["error"] = str(exc)
            logger.warning("Paper order skipped for %s: %s", asset, exc)
            return

        qty, validation_error = validate_and_normalize_qty(asset, raw_qty, fill_price)
        if validation_error:
            record["error"] = f"Order validation failed: {validation_error}"
            db_logger.log_warning(
                "execution", "order_validation_failed",
                f"Paper order rejected for {asset}: {validation_error}",
                symbol=asset,
                details={"raw_qty": raw_qty, "fill_price": fill_price},
            )
            logger.warning("Paper order rejected for %s: %s", asset, validation_error)
            return

        cost = qty * fill_price
        fee_amount = cost * fee_rate

        record["order"] = {
            "id": f"PAPER-{timestamp}",
            "symbol": asset,
            "side": action.lower(),
            "type": "market",
            "qty": qty,
            "price": fill_price,
            "fee_amount": fee_amount,
            "fee_rate": fee_rate,
            "fill_source": fill_source,
            "status": "paper_filled",
        }

        if action == "BUY":
            self._paper_usdt -= (cost + fee_amount)
        elif action == "SELL":
            self._paper_usdt += (cost - fee_amount)

        self._paper_usdt = max(0.0, self._paper_usdt)
        logger.info(
            "Paper %s %s: qty=%.6f @ %.4f fee=%.4f USDT | balance=%.2f",
            action, asset, qty, fill_price, fee_amount, self._paper_usdt,
        )
        self._update_positions(asset, action, fill_price, record["size_pct"])

    # ── Live execution ─────────────────────────────────────────────────────────

    def _execute_live(
        self,
        record: dict[str, Any],
        asset: str,
        action: str,
        last_price: float,
        portfolio_usdt: float,
        fee_rate: float,
    ) -> None:
        from app.services.market_validator import validate_and_normalize_qty
        from app.services import db_logger

        try:
            raw_qty = self._compute_qty(asset, record["size_pct"], last_price, portfolio_usdt)
        except ValueError as exc:
            record["error"] = str(exc)
            logger.exception("Live order qty error for %s", asset)
            return

        qty, validation_error = validate_and_normalize_qty(asset, raw_qty, last_price)
        if validation_error:
            record["error"] = f"Order validation failed: {validation_error}"
            db_logger.log_warning(
                "execution", "order_validation_failed",
                f"Live order rejected for {asset}: {validation_error}",
                symbol=asset,
                details={"raw_qty": raw_qty, "price": last_price},
            )
            return

        try:
            side = "buy" if action == "BUY" else "sell"
            ccxt_symbol = f"{asset[:-4]}/USDT" if asset.endswith("USDT") and "/" not in asset else asset
            order = get_exchange().create_market_order(ccxt_symbol, side, qty)

            # Verify fill after placement
            verification_status = "unverified"
            filled_price = last_price
            filled_qty = qty

            try:
                time.sleep(1)  # brief wait for exchange to process
                fetched = get_exchange().fetch_order(order["id"], ccxt_symbol)
                status = fetched.get("status", "unknown")
                if status == "closed":
                    verification_status = "filled"
                    filled_price = float(fetched.get("average") or fetched.get("price") or last_price)
                    filled_qty = float(fetched.get("filled") or qty)
                elif status == "open":
                    verification_status = "partial_or_open"
                    filled_price = float(fetched.get("average") or last_price)
                    filled_qty = float(fetched.get("filled") or 0)
                    logger.warning("Live order %s for %s is still open after placement", order["id"], asset)
                else:
                    verification_status = f"unknown_{status}"
            except Exception as ve:
                logger.warning("Fill verification failed for %s order %s: %s", asset, order.get("id"), ve)
                verification_status = "verification_failed"
                filled_price = float(order.get("average") or last_price)

            fee_amount = filled_qty * filled_price * fee_rate

            record["order"] = {
                **order,
                "qty": filled_qty,
                "price": filled_price,
                "fee_amount": fee_amount,
                "fee_rate": fee_rate,
                "fill_source": "live",
                "verification_status": verification_status,
            }

            db_logger.log_info(
                "execution", "live_order_verified",
                f"Live {action} {asset}: qty={filled_qty:.6f} @ {filled_price:.4f} status={verification_status}",
                symbol=asset,
                details={"verification_status": verification_status, "order_id": order.get("id")},
            )

            self._update_positions(asset, action, filled_price, record["size_pct"])

        except (ccxt.BaseError, ValueError, Exception) as exc:
            record["error"] = str(exc)
            logger.exception("Live order failed for %s", asset)

    # ── Helpers ────────────────────────────────────────────────────────────────

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
