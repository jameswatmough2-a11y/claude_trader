"""Market order validation and quantity normalization.

Fetches market metadata from ccxt, caches it, and validates order parameters
before execution. Prevents invalid orders from reaching the exchange.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_market_cache: dict[str, dict] = {}


def _to_ccxt_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol


def _load_market_info(symbol: str) -> Optional[dict]:
    ccxt_symbol = _to_ccxt_symbol(symbol)
    if ccxt_symbol in _market_cache:
        return _market_cache[ccxt_symbol]
    try:
        from app.services.data_feeds import get_exchange
        exchange = get_exchange()
        exchange.load_markets()
        info = exchange.markets.get(ccxt_symbol)
        if info:
            _market_cache[ccxt_symbol] = info
            logger.info("Cached market info for %s", ccxt_symbol)
        return info
    except Exception as exc:
        logger.warning("Could not load market info for %s: %s", symbol, exc)
        return None


def validate_and_normalize_qty(
    symbol: str,
    qty: float,
    price: float,
) -> tuple[float, Optional[str]]:
    """Validate qty against exchange constraints and normalize to step size.

    Returns (normalized_qty, error_message).
    error_message is None when valid.
    """
    info = _load_market_info(symbol)
    if info is None:
        return qty, None  # allow when we can't fetch market info

    limits = info.get("limits") or {}
    precision = info.get("precision") or {}

    amount_limits = limits.get("amount") or {}
    cost_limits = limits.get("cost") or {}

    # Step-size normalization using amount precision
    amount_precision = precision.get("amount")
    if amount_precision is not None:
        try:
            ap = int(amount_precision)
            factor = 10 ** ap
            qty = math.floor(qty * factor) / factor
        except (TypeError, ValueError):
            pass

    min_qty = amount_limits.get("min")
    if min_qty is not None and qty < float(min_qty):
        return qty, (
            f"Quantity {qty:.8f} below exchange minimum {float(min_qty):.8f} for {symbol}"
        )

    max_qty = amount_limits.get("max")
    if max_qty is not None and qty > float(max_qty):
        return qty, (
            f"Quantity {qty:.8f} above exchange maximum {float(max_qty):.8f} for {symbol}"
        )

    min_cost = cost_limits.get("min")
    if min_cost is not None and price > 0 and qty * price < float(min_cost):
        return qty, (
            f"Notional {qty * price:.4f} below exchange minimum {float(min_cost):.4f} for {symbol}"
        )

    return qty, None


def clear_cache() -> None:
    _market_cache.clear()
