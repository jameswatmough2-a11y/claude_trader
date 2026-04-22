from __future__ import annotations

import logging
import os
from typing import Any

import ccxt
import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)

OHLCV_LIMIT = 24

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


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = OHLCV_LIMIT) -> pd.DataFrame:
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


def get_all_market_data(symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Full ccxt market data fetch (used as fallback; WebSocket is preferred for live prices)."""
    symbols = symbols or settings.tracked_symbols
    results: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        try:
            ticker = fetch_ticker(symbol)
            ohlcv = fetch_ohlcv(symbol)
            results[symbol.upper()] = {
                "symbol": symbol.upper(),
                "last_price": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "quote_volume_24h": ticker.get("quoteVolume"),
                "ohlcv": {
                    "last_close": float(ohlcv["close"].iloc[-1]),
                    "high_24h": float(ohlcv["high"].max()),
                    "low_24h": float(ohlcv["low"].min()),
                    "avg_volume_24h": float(ohlcv["volume"].mean()),
                    "price_change_pct_24h": round(
                        (ohlcv["close"].iloc[-1] - ohlcv["close"].iloc[0])
                        / ohlcv["close"].iloc[0] * 100,
                        2,
                    ),
                },
                "orderbook": {},
            }
        except Exception:
            logger.exception("get_all_market_data failed for %s", symbol)
            results[symbol.upper()] = {"symbol": symbol.upper(), "error": "fetch failed"}

    return results
