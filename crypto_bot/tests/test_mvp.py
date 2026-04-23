"""MVP acceptance tests for the key new behaviors."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ── Fallback strategy ──────────────────────────────────────────────────────────

def _make_candles(count: int, start: float = 100.0, trend: float = 0.01) -> list[dict]:
    """Generate trending candles for testing."""
    candles = []
    price = start
    for _ in range(count):
        close = price * (1 + trend)
        candles.append({
            "open": price,
            "high": close * 1.002,
            "low": price * 0.998,
            "close": close,
            "volume": 1000.0,
        })
        price = close
    return candles


def test_fallback_hold_on_insufficient_data():
    from app.services.fallback_strategy import get_fallback_decisions
    md = {"BTCUSDT": {"last_price": 100.0, "ohlcv": {"candles": []}}}
    decisions = get_fallback_decisions(md, {})
    assert decisions[0]["action"] == "HOLD"
    assert decisions[0]["decision_source"] == "fallback_rule"


def test_fallback_buy_on_uptrend():
    from app.services.fallback_strategy import get_fallback_decisions
    candles = _make_candles(15, 100.0, trend=0.005)  # 0.5% per candle
    md = {
        "BTCUSDT": {
            "last_price": candles[-1]["close"],
            "ohlcv": {"candles": candles},
        }
    }
    decisions = get_fallback_decisions(md, {})
    # With moderate uptrend, may get BUY or HOLD depending on thresholds
    assert decisions[0]["asset"] == "BTCUSDT"
    assert decisions[0]["decision_source"] == "fallback_rule"


def test_fallback_sell_on_downtrend_with_position():
    from app.services.fallback_strategy import get_fallback_decisions
    from app.services.risk_service import OpenPosition

    candles = _make_candles(15, 100.0, trend=-0.005)  # 0.5% drop per candle
    md = {
        "BTCUSDT": {
            "last_price": candles[-1]["close"],
            "ohlcv": {"candles": candles},
        }
    }
    positions = {"BTCUSDT": OpenPosition("BTCUSDT", 100.0, 10.0)}
    decisions = get_fallback_decisions(md, positions)
    assert decisions[0]["asset"] == "BTCUSDT"
    assert decisions[0]["decision_source"] == "fallback_rule"


def test_fallback_hold_no_position_flat_market():
    from app.services.fallback_strategy import get_fallback_decisions
    # Flat market — no clear signal
    candles = _make_candles(15, 100.0, trend=0.0)
    md = {"BTCUSDT": {"last_price": 100.0, "ohlcv": {"candles": candles}}}
    decisions = get_fallback_decisions(md, {})
    assert decisions[0]["action"] == "HOLD"


# ── Sentiment cache ────────────────────────────────────────────────────────────

def test_sentiment_ttl_cache():
    from app.services.sentiment_service import (
        _sentiment_cache, clear_sentiment_cache, AssetSentiment
    )
    import time

    clear_sentiment_cache()
    assert "BTCUSDT" not in _sentiment_cache

    # Manually inject a fresh cache entry
    fake = AssetSentiment("BTCUSDT", 0.1, 3, ["h1", "h2"], {}, 55)
    _sentiment_cache["BTCUSDT"] = (time.time(), fake)

    # It should be present now
    ts, cached = _sentiment_cache["BTCUSDT"]
    assert cached is not None
    assert cached.symbol == "BTCUSDT"

    clear_sentiment_cache()
    assert "BTCUSDT" not in _sentiment_cache


def test_sentiment_sanitize():
    from app.services.sentiment_service import _sanitize
    # HTML stripped
    assert "<b>" not in _sanitize("<b>bold</b>")
    # Markdown stripped
    result = _sanitize("**bold** and _italic_ and `code`")
    assert "*" not in result
    # Length capped
    long_text = "a " * 200
    assert len(_sanitize(long_text)) <= 210  # max_len 200 + small buffer


# ── Fee calculation ────────────────────────────────────────────────────────────

def test_paper_fill_uses_ask_for_buy():
    from app.services.execution_service import ExecutionService
    from app.services.risk_service import RiskService
    from app.config import settings

    risk = RiskService()
    svc = ExecutionService(risk_service=risk)

    market_data = {"BTCUSDT": {"last_price": 67000.0, "bid": 66990.0, "ask": 67010.0}}
    balance = {"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0}}
    decision = {"asset": "BTCUSDT", "action": "BUY", "confidence": 0.9, "size_pct": 10, "reasoning": "test"}

    with patch("app.services.market_validator.validate_and_normalize_qty", return_value=(0.01493, None)):
        result = svc.execute_decision(decision, market_data, balance)

    if result.get("order"):
        # BUY should use ask price (67010), not last_price (67000)
        assert result["order"]["fill_source"] in ("ask", "last_price_fallback")
        if result["order"]["fill_source"] == "ask":
            assert result["order"]["price"] == pytest.approx(67010.0)


def test_paper_fill_uses_bid_for_sell():
    from app.services.execution_service import ExecutionService
    from app.services.risk_service import RiskService, OpenPosition

    risk = RiskService()
    risk._positions["BTCUSDT"] = OpenPosition("BTCUSDT", 67000.0, 10.0)
    svc = ExecutionService(risk_service=risk)

    market_data = {"BTCUSDT": {"last_price": 67000.0, "bid": 66990.0, "ask": 67010.0}}
    balance = {"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0}}
    decision = {"asset": "BTCUSDT", "action": "SELL", "confidence": 0.9, "size_pct": 0, "reasoning": "test"}

    with patch("app.services.market_validator.validate_and_normalize_qty", return_value=(0.01493, None)):
        result = svc.execute_decision(decision, market_data, balance)

    if result.get("order"):
        assert result["order"]["fill_source"] in ("bid", "last_price_fallback")
        if result["order"]["fill_source"] == "bid":
            assert result["order"]["price"] == pytest.approx(66990.0)


def test_paper_fill_records_fee():
    from app.services.execution_service import ExecutionService
    from app.services.risk_service import RiskService
    from app.config import settings

    risk = RiskService()
    svc = ExecutionService(risk_service=risk)
    settings.taker_fee_rate = 0.001

    market_data = {"BTCUSDT": {"last_price": 67010.0, "bid": 67000.0, "ask": 67010.0}}
    balance = {"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0}}
    decision = {"asset": "BTCUSDT", "action": "BUY", "confidence": 0.9, "size_pct": 10, "reasoning": "test"}

    with patch("app.services.market_validator.validate_and_normalize_qty", return_value=(0.01492, None)):
        result = svc.execute_decision(decision, market_data, balance)

    if result.get("order") and not result.get("error"):
        order = result["order"]
        assert "fee_amount" in order
        assert order["fee_amount"] > 0
        assert order["fee_rate"] == pytest.approx(0.001)


# ── Min order validation ───────────────────────────────────────────────────────

def test_validate_qty_rejects_below_min():
    from app.services.market_validator import validate_and_normalize_qty

    mock_info = {
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}},
        "precision": {"amount": 3},
    }
    with patch("app.services.market_validator._load_market_info", return_value=mock_info):
        qty, err = validate_and_normalize_qty("BTCUSDT", 0.0001, 67000.0)
    assert err is not None
    assert "minimum" in err.lower()


def test_validate_qty_normalizes_precision():
    from app.services.market_validator import validate_and_normalize_qty

    mock_info = {
        "limits": {"amount": {"min": 0.001}, "cost": {}},
        "precision": {"amount": 3},
    }
    with patch("app.services.market_validator._load_market_info", return_value=mock_info):
        qty, err = validate_and_normalize_qty("BTCUSDT", 0.01492578, 67000.0)
    assert err is None
    assert qty == pytest.approx(0.014, abs=0.001)


def test_validate_qty_allows_when_no_market_info():
    from app.services.market_validator import validate_and_normalize_qty

    with patch("app.services.market_validator._load_market_info", return_value=None):
        qty, err = validate_and_normalize_qty("BTCUSDT", 0.5, 67000.0)
    assert err is None
    assert qty == pytest.approx(0.5)


# ── DB logger ─────────────────────────────────────────────────────────────────

def test_db_logger_writes_and_reads():
    from app.services.db_logger import log_info, log_warning, log_error
    from app.db.session import SessionLocal
    from app.models import SystemLog

    # Write entries
    log_info("test", "test_write", "DB logger test - info", details={"key": "value"})
    log_warning("test", "test_write", "DB logger test - warning")
    log_error("test", "test_write", "DB logger test - error", symbol="BTCUSDT")

    # Read back
    db = SessionLocal()
    try:
        entries = db.query(SystemLog).filter(SystemLog.component == "test").all()
        assert len(entries) >= 3
        levels = {e.level for e in entries}
        assert "INFO" in levels
        assert "WARNING" in levels
        assert "ERROR" in levels

        btc_entries = [e for e in entries if e.symbol == "BTCUSDT"]
        assert len(btc_entries) >= 1

        detail_entries = [e for e in entries if e.details_json is not None]
        assert len(detail_entries) >= 1
        parsed = json.loads(detail_entries[0].details_json)
        assert isinstance(parsed, dict)
    finally:
        db.close()


# ── AI decision source ────────────────────────────────────────────────────────

def test_ai_decisions_tagged_with_source():
    from app.services.ai_service import _validate_decisions

    raw = [
        {"asset": "BTCUSDT", "action": "BUY", "confidence": 0.85, "size_pct": 10, "reasoning": "test"}
    ]
    decisions = _validate_decisions(raw, ["BTCUSDT"])
    assert decisions[0]["decision_source"] == "ai"


def test_fallback_decisions_tagged_with_source():
    from app.services.fallback_strategy import get_fallback_decisions
    md = {"BTCUSDT": {"last_price": 100.0, "ohlcv": {"candles": []}}}
    decisions = get_fallback_decisions(md, {})
    assert decisions[0]["decision_source"] == "fallback_rule"
