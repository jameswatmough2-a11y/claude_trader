"""Rule-based fallback trading strategy.

Used when the Claude API is unavailable or returns an invalid response.
Implements simple momentum + moving-average signals.
All decisions are tagged with decision_source="fallback_rule".
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_FALLBACK_SIZE_PCT = 10
_MOMENTUM_THRESHOLD_PCT = 1.5
_MIN_CANDLES = 10


def get_fallback_decisions(
    market_data: dict[str, Any],
    open_positions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    decisions = []
    positions = open_positions or {}
    for symbol, md in market_data.items():
        decisions.append(_evaluate(symbol, md, positions))
    logger.info("Fallback rule strategy produced %d decisions", len(decisions))
    return decisions


def _evaluate(
    symbol: str,
    md: dict[str, Any],
    open_positions: dict[str, Any],
) -> dict[str, Any]:
    ohlcv = md.get("ohlcv", {})
    candles = ohlcv.get("candles", [])
    price = float(md.get("last_price") or 0)
    has_position = symbol.upper() in open_positions

    if len(candles) < _MIN_CANDLES or price <= 0:
        return _hold(symbol, f"Insufficient data for rule-based strategy ({len(candles)} candles).")

    closes = [float(c["close"]) for c in candles]
    sma5 = sum(closes[-5:]) / 5
    sma10 = sum(closes[-10:]) / min(10, len(closes))
    last_candle = candles[-1]
    candle_bullish = float(last_candle["close"]) > float(last_candle["open"])

    recent_return_pct = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] != 0 else 0.0

    # Volatility guardrail: skip if recent range is too wide (> 5%)
    recent_high = max(float(c["high"]) for c in candles[-5:])
    recent_low = min(float(c["low"]) for c in candles[-5:])
    if recent_low > 0 and (recent_high - recent_low) / recent_low * 100 > 5.0:
        return _hold(symbol, f"High volatility guard: {(recent_high-recent_low)/recent_low*100:.1f}% range in last 5 candles.")

    if has_position:
        # Exit signal: price below SMA5, bearish candle, negative momentum
        if price < sma5 and not candle_bullish and recent_return_pct < -_MOMENTUM_THRESHOLD_PCT:
            return {
                "asset": symbol,
                "action": "SELL",
                "confidence": 0.72,
                "size_pct": 0,
                "reasoning": (
                    f"Fallback rule: price {price:.4f} < SMA5 {sma5:.4f}, "
                    f"bearish candle, momentum {recent_return_pct:.1f}%"
                ),
                "decision_source": "fallback_rule",
            }
        return _hold(symbol, f"Fallback rule: holding, no exit signal (SMA5={sma5:.4f}).")
    else:
        # Entry signal: price above SMA5 > SMA10, bullish candle, positive momentum
        if price > sma5 > sma10 and candle_bullish and recent_return_pct > _MOMENTUM_THRESHOLD_PCT:
            return {
                "asset": symbol,
                "action": "BUY",
                "confidence": 0.72,
                "size_pct": _FALLBACK_SIZE_PCT,
                "reasoning": (
                    f"Fallback rule: price {price:.4f} > SMA5 {sma5:.4f} > SMA10 {sma10:.4f}, "
                    f"bullish, momentum {recent_return_pct:.1f}%"
                ),
                "decision_source": "fallback_rule",
            }
        return _hold(symbol, f"Fallback rule: no entry signal (SMA5={sma5:.4f}, momentum={recent_return_pct:.1f}%).")


def _hold(symbol: str, reason: str) -> dict[str, Any]:
    return {
        "asset": symbol,
        "action": "HOLD",
        "confidence": 0.0,
        "size_pct": 0,
        "reasoning": reason,
        "decision_source": "fallback_rule",
    }
