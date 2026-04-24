"""CCXT factory + REST fetches (ticker, ohlcv, balance).

Port of v1 data_feeds.py. Stays in the shared plane — exchange clients are
stateless from the tenant's perspective. For v2.x live-trading with per-tenant
API keys, wrap this with a per-tenant factory that injects the tenant's keys.

TODO: port get_exchange, fetch_ticker, fetch_ohlcv, fetch_balance from v1.
"""
from __future__ import annotations

import logging

import ccxt

from app.config import settings

logger = logging.getLogger(__name__)

_exchange: ccxt.Exchange | None = None


def get_exchange() -> ccxt.Exchange:
    """Singleton ccxt.binance client for paper-mode reads (OHLCV / ticker)."""
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
        })
    return _exchange


def fetch_ticker(symbol: str) -> dict:
    """TODO: return normalised ticker dict."""
    return {}


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 24) -> list[list[float]]:
    """TODO: return [[ts, o, h, l, c, v], ...]."""
    return []


def fetch_balance() -> dict:
    """TODO: live mode only; return ccxt balance dict."""
    return {}
