from __future__ import annotations

import logging
import os
from typing import Any

import ccxt
import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)

_exchange: ccxt.binance | None = None


def _to_ccxt_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol


def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        _exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        mode = "authenticated" if api_key else "public-only"
        logger.info("Binance exchange initialised (%s)", mode)
    return _exchange


def fetch_ticker(symbol: str) -> dict[str, Any]:
    return get_exchange().fetch_ticker(_to_ccxt_symbol(symbol))


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 50) -> pd.DataFrame:
    raw = get_exchange().fetch_ohlcv(_to_ccxt_symbol(symbol), timeframe=timeframe, limit=limit)
    timestamps = pd.to_datetime([r[0] for r in raw], unit="ms", utc=True)
    return pd.DataFrame(
        [r[1:] for r in raw],
        index=timestamps,
        columns=["open", "high", "low", "close", "volume"],
    )


def fetch_balance() -> dict[str, Any]:
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        paper = settings.paper_balance_usdt
        logger.info("fetch_balance: paper mode — %.2f USDT", paper)
        return {"USDT": {"free": paper, "used": 0.0, "total": paper}}
    return get_exchange().fetch_balance()
