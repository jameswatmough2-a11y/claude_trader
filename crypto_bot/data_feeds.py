"""
data_feeds.py — Binance OHLCV and ticker data via ccxt.

Public market data (prices, candles, orderbook) requires NO API keys.
API keys are only needed for live order placement and balance queries.

Spot symbols: BTC/USDT, ETH/USDT, SOL/USDT
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

# Default paper balance used when no API keys are configured
PAPER_BALANCE_USDT = float(os.getenv("PAPER_BALANCE_USDT", "10000"))


def _build_exchange() -> ccxt.binance:
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")

    exchange = ccxt.binance(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
    )

    if api_key:
        logger.info("data_feeds: Binance authenticated (live trading capable)")
    else:
        logger.info(
            "data_feeds: Binance — no API keys, public market data only "
            "(paper trading mode)"
        )
    return exchange


_exchange: ccxt.binance | None = None


def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        _exchange = _build_exchange()
    return _exchange


def fetch_ticker(symbol: str) -> dict[str, Any]:
    exchange = get_exchange()
    try:
        ticker = exchange.fetch_ticker(symbol)
        logger.debug(
            "ticker %s: last=%.4f 24h_vol=%.2f", symbol, ticker["last"], ticker["quoteVolume"]
        )
        return ticker
    except ccxt.BaseError as exc:
        logger.error("fetch_ticker failed for %s: %s", symbol, exc)
        raise


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = OHLCV_LIMIT) -> pd.DataFrame:
    """Return a DataFrame of OHLCV candles (columns: open, high, low, close, volume)."""
    exchange = get_exchange()
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        timestamps = pd.to_datetime([r[0] for r in raw], unit="ms", utc=True)
        df = pd.DataFrame(
            [r[1:] for r in raw],
            index=timestamps,
            columns=["open", "high", "low", "close", "volume"],
        )
        logger.debug(
            "OHLCV %s: %d candles, last_close=%.4f", symbol, len(df), df["close"].iloc[-1]
        )
        return df
    except ccxt.BaseError as exc:
        logger.error("fetch_ohlcv failed for %s: %s", symbol, exc)
        raise


def fetch_orderbook_summary(symbol: str, depth: int = 5) -> dict[str, Any]:
    exchange = get_exchange()
    try:
        book = exchange.fetch_order_book(symbol, limit=depth)
        best_bid = book["bids"][0][0] if book["bids"] else None
        best_ask = book["asks"][0][0] if book["asks"] else None
        spread = round(best_ask - best_bid, 6) if (best_bid and best_ask) else None
        summary = {"best_bid": best_bid, "best_ask": best_ask, "spread": spread}
        logger.debug(
            "orderbook %s: bid=%.4f ask=%.4f spread=%.6f", symbol, best_bid, best_ask, spread
        )
        return summary
    except ccxt.BaseError as exc:
        logger.error("fetch_orderbook_summary failed for %s: %s", symbol, exc)
        raise


def fetch_balance() -> dict[str, Any]:
    """
    Return account balance. Falls back to a simulated paper balance when no
    API keys are configured so the rest of the bot can function without auth.
    """
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        paper = PAPER_BALANCE_USDT
        logger.info(
            "fetch_balance: no API key — using paper balance of %.2f USDT", paper
        )
        return {"USDT": {"free": paper, "used": 0.0, "total": paper}}

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
    """Fetch price, OHLCV, and orderbook for every tracked asset."""
    logger.info("data_feeds: fetching market data for %s", ASSETS)
    results: dict[str, dict[str, Any]] = {}

    for symbol in ASSETS:
        try:
            ticker = fetch_ticker(symbol)
            ohlcv = fetch_ohlcv(symbol)
            ob = fetch_orderbook_summary(symbol)

            ohlcv_summary = {
                "last_close": float(ohlcv["close"].iloc[-1]),
                "high_24h": float(ohlcv["high"].max()),
                "low_24h": float(ohlcv["low"].min()),
                "avg_volume_24h": float(ohlcv["volume"].mean()),
                "price_change_pct_24h": round(
                    (ohlcv["close"].iloc[-1] - ohlcv["close"].iloc[0])
                    / ohlcv["close"].iloc[0]
                    * 100,
                    2,
                ),
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
            logger.error("get_all_market_data: skipping %s — %s", symbol, exc)
            results[symbol] = {"symbol": symbol, "error": str(exc)}

    logger.info("data_feeds: market data fetch complete")
    return results
