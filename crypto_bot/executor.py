"""
executor.py — Place orders on Phemex via ccxt and log every trade with
full context (decision, market price, order response, reasoning).

Honours the PAPER_TRADING flag: in paper mode all decisions are logged
but no real orders are submitted.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
from dotenv import load_dotenv

from data_feeds import get_exchange
from risk_manager import close_position, record_open_position

load_dotenv()

logger = logging.getLogger(__name__)

TRADE_LOG_PATH = Path("logs/trades.jsonl")
PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"


def _ensure_log_dir() -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _append_trade_log(record: dict[str, Any]) -> None:
    """Append a single trade record as a JSON line."""
    _ensure_log_dir()
    with open(TRADE_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    logger.debug("executor: appended trade log entry for %s", record.get("asset"))


def _portfolio_value_usdt(balance: dict[str, Any]) -> float:
    """Estimate total portfolio value in USDT from the ccxt balance object."""
    return float(balance.get("USDT", {}).get("total", 0))


def _compute_order_quantity(
    symbol: str,
    size_pct: float,
    current_price: float,
    portfolio_usdt: float,
) -> float:
    """
    Convert a size percentage into a base-asset quantity.

    size_pct is the percentage of total portfolio USDT to deploy.
    """
    usdt_to_spend = portfolio_usdt * (size_pct / 100.0)
    if current_price <= 0:
        raise ValueError(f"executor: current price for {symbol} is zero — cannot compute qty")
    qty = usdt_to_spend / current_price
    logger.debug(
        "executor: %s  size_pct=%.1f  portfolio=%.2f USDT  spend=%.2f USDT  qty=%.6f",
        symbol, size_pct, portfolio_usdt, usdt_to_spend, qty,
    )
    return qty


def _place_market_order(
    symbol: str,
    side: str,
    qty: float,
) -> dict[str, Any]:
    """Submit a market order to Phemex and return the raw ccxt response."""
    exchange = get_exchange()
    logger.info("executor: placing %s %s %s (qty=%.6f)", side.upper(), symbol, "MARKET", qty)
    order = exchange.create_market_order(symbol, side, qty)
    logger.info(
        "executor: order placed — id=%s status=%s",
        order.get("id"), order.get("status"),
    )
    return order


def execute_decision(
    decision: dict[str, Any],
    market_data: dict[str, Any],
    balance: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute (or paper-log) a single trading decision.

    Returns a trade record dict that is also appended to the JSONL log.
    """
    asset = decision["asset"]
    action = decision["action"]
    confidence = decision["confidence"]
    size_pct = decision["size_pct"]
    reasoning = decision["reasoning"]

    md = market_data.get(asset, {})
    current_price = float(md.get("last_price", 0))
    portfolio_usdt = _portfolio_value_usdt(balance)

    timestamp = datetime.now(timezone.utc).isoformat()

    trade_record: dict[str, Any] = {
        "timestamp": timestamp,
        "asset": asset,
        "action": action,
        "confidence": confidence,
        "size_pct": size_pct,
        "current_price": current_price,
        "portfolio_usdt": portfolio_usdt,
        "reasoning": reasoning,
        "paper_trading": PAPER_TRADING,
        "order": None,
        "error": None,
    }

    if action == "HOLD":
        logger.info("executor: %s HOLD — no order submitted", asset)
        _append_trade_log(trade_record)
        return trade_record

    if PAPER_TRADING:
        qty = _compute_order_quantity(asset, size_pct, current_price, portfolio_usdt)
        paper_order = {
            "id": f"PAPER-{timestamp}",
            "symbol": asset,
            "side": action.lower(),
            "type": "market",
            "qty": qty,
            "price": current_price,
            "status": "paper_filled",
        }
        trade_record["order"] = paper_order
        logger.info(
            "PAPER TRADE — %s %s  qty=%.6f @ %.4f  (%.1f%% of portfolio)",
            action, asset, qty, current_price, size_pct,
        )
        # Update position book even in paper mode so risk_manager stays accurate
        if action == "BUY":
            record_open_position(asset, current_price, size_pct)
        elif action == "SELL":
            close_position(asset)
        _append_trade_log(trade_record)
        return trade_record

    # ── Live order path ───────────────────────────────────────────────────────
    try:
        qty = _compute_order_quantity(asset, size_pct, current_price, portfolio_usdt)
        side = "buy" if action == "BUY" else "sell"
        order = _place_market_order(asset, side, qty)
        trade_record["order"] = order

        filled_price = float(order.get("average", current_price) or current_price)
        if action == "BUY":
            record_open_position(asset, filled_price, size_pct)
        elif action == "SELL":
            close_position(asset)

        logger.info(
            "LIVE TRADE executed — %s %s  qty=%.6f  filled@%.4f",
            action, asset, qty, filled_price,
        )
    except (ccxt.BaseError, ValueError, Exception) as exc:  # noqa: BLE001
        trade_record["error"] = str(exc)
        logger.error("executor: order failed for %s %s — %s", action, asset, exc)

    _append_trade_log(trade_record)
    return trade_record


def execute_all_decisions(
    decisions: list[dict[str, Any]],
    market_data: dict[str, Any],
    balance: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Execute all approved decisions in order.
    Returns a list of trade records (one per decision).
    """
    mode = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info("executor: processing %d decision(s) in %s mode", len(decisions), mode)
    records: list[dict[str, Any]] = []
    for decision in decisions:
        record = execute_decision(decision, market_data, balance)
        records.append(record)
    logger.info("executor: %d trade record(s) written to %s", len(records), TRADE_LOG_PATH)
    return records
