"""
data_feeds.py — Phemex OHLCV and ticker data via ccxt.

Provides current price, 24h stats, and hourly OHLCV candles for
BTC/USDT, ETH/USDT, and SOL/USDT on Phemex (testnet or live).
"""

import logging
import os
from typing import Any

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
OHLCV_LIMIT = 24  # 24 hourly candles


def _build_exchange() -> ccxt.phemex:
    """Instantiate Phemex exchange object (testnet or live)."""
    live = os.getenv("PHEMEX_LIVE", "false").lower() == "true"
    exchange = ccxt.phemex(
        {
            "apiKey": os.getenv("PHEMEX_API_KEY", ""),
            "secret": os.getenv("PHEMEX_API_SECRET", ""),
            "enableRateLimit": True,
        }
    )
    if not live:
        exchange.set_sandbox_mode(True)
        logger.info("data_feeds: using Phemex TESTNET")
    else:
        logger.info("data_feeds: using Phemex LIVE — real funds at risk")
    return exchange


# Module-level singleton so callers share one connection.
_exchange: ccxt.phemex | None = None


def get_exchange() -> ccxt.phemex:
    global _exchange
    if _exchange is None:
        _exchange = _build_exchange()
    return _exchange


def fetch_ticker(symbol: str) -> dict[str, Any]:
    """Return raw ccxt ticker for *symbol*."""
    exchange = get_exchange()
    try:
        ticker = exchange.fetch_ticker(symbol)
        logger.debug("ticker %s: last=%.4f, 24h vol=%.2f", symbol, ticker["last"], ticker["quoteVolume"])
        return ticker
    except ccxt.BaseError as exc:
        logger.error("fetch_ticker failed for %s: %s", symbol, exc)
        raise


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = OHLCV_LIMIT) -> pd.DataFrame:
    """Return a DataFrame of OHLCV candles.

    Columns: timestamp, open, high, low, close, volume
    """
    exchange = get_exchange()
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        logger.debug("OHLCV %s: %d candles, last_close=%.4f", symbol, len(df), df["close"].iloc[-1])
        return df
    except ccxt.BaseError as exc:
        logger.error("fetch_ohlcv failed for %s: %s", symbol, exc)
        raise


def fetch_orderbook_summary(symbol: str, depth: int = 5) -> dict[str, Any]:
    """Return best bid/ask and spread for *symbol*."""
    exchange = get_exchange()
    try:
        book = exchange.fetch_order_book(symbol, limit=depth)
        best_bid = book["bids"][0][0] if book["bids"] else None
        best_ask = book["asks"][0][0] if book["asks"] else None
        spread = round(best_ask - best_bid, 6) if (best_bid and best_ask) else None
        summary = {"best_bid": best_bid, "best_ask": best_ask, "spread": spread}
        logger.debug("orderbook %s: bid=%.4f ask=%.4f spread=%.6f", symbol, best_bid, best_ask, spread)
        return summary
    except ccxt.BaseError as exc:
        logger.error("fetch_orderbook_summary failed for %s: %s", symbol, exc)
        raise


def fetch_balance() -> dict[str, Any]:
    """Return the current account balance (USDT free / used / total)."""
    exchange = get_exchange()
    try:
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        logger.info(
            "balance: free=%.2f used=%.2f total=%.2f",
            usdt.get("free", 0),
            usdt.get("used", 0),
            usdt.get("total", 0),
        )
        return balance
    except ccxt.BaseError as exc:
        logger.error("fetch_balance failed: %s", exc)
        raise


def get_all_market_data() -> dict[str, dict[str, Any]]:
    """Fetch price, OHLCV, and orderbook for every tracked asset.

    Returns a dict keyed by symbol, e.g. {"BTC/USDT": {...}}.
    """
    logger.info("data_feeds: fetching market data for %s", ASSETS)
    results: dict[str, dict[str, Any]] = {}
    for symbol in ASSETS:
        try:
            ticker = fetch_ticker(symbol)
            ohlcv = fetch_ohlcv(symbol)
            ob = fetch_orderbook_summary(symbol)

            # Summarise OHLCV for downstream consumption
            ohlcv_summary = {
                "last_close": float(ohlcv["close"].iloc[-1]),
                "high_24h": float(ohlcv["high"].max()),
                "low_24h": float(ohlcv["low"].min()),
                "avg_volume_24h": float(ohlcv["volume"].mean()),
                "price_change_pct_24h": round(
                    (ohlcv["close"].iloc[-1] - ohlcv["close"].iloc[0]) / ohlcv["close"].iloc[0] * 100, 2
                ),
                "candles": ohlcv.reset_index().to_dict(orient="records"),
            }

            results[symbol] = {
                "symbol": symbol,
                "last_price": ticker["last"],
                "bid": ticker["bid"],
                "ask": ticker["ask"],
                "quote_volume_24h": ticker["quoteVolume"],
                "ohlcv": ohlcv_summary,
                "orderbook": ob,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("get_all_market_data: skipping %s due to error: %s", symbol, exc)
            results[symbol] = {"symbol": symbol, "error": str(exc)}

    logger.info("data_feeds: market data fetch complete")
    return results
