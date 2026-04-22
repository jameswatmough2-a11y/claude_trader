# Data Flow

This document traces every piece of data through the system, from raw exchange packets to database rows.

---

## Overview

```
Binance Exchange
      │
      │  WebSocket frames (~1s per symbol)
      ▼
BinanceWebSocketService._handle_message()
      │
      ├──► MarketStateStore.update()         [in-memory, keyed by symbol]
      │
      └──► RiskService.check_exit_conditions()
                  │
                  └──► asyncio.Queue (if triggered)
                              │
                              ▼
                        TriggerExecutor._execute()
                              │
                              └──► ExecutionService.execute_decision()
                                          │
                                          ├──► logs/trades.jsonl  [file append]
                                          └──► RiskService._positions  [in-memory]


APScheduler (every 60 minutes from startup)
      │
      ▼
TradingCycleService.run()
      │
      ├── MarketStateStore.all()             [read in-memory cache]
      │         │
      │         └── market_data dict         [Python dict, passed around]
      │
      ├── SentimentService.get_all_sentiment()
      │         │
      │         ├── feedparser (RSS HTTP)
      │         ├── requests (Reddit JSON API)
      │         ├── requests (Fear & Greed API)
      │         └── AssetSentiment objects   [passed to AI]
      │
      ├── AIService.get_trading_decisions()
      │         │
      │         ├── Anthropic Claude API (HTTPS)
      │         └── list[dict] decisions     [validated JSON decisions]
      │
      ├── RiskService.filter_decisions()
      │         │
      │         └── list[dict] filtered      [approved decisions]
      │
      ├── data_feeds.fetch_balance()
      │         │
      │         └── balance dict             [USDT total/free]
      │
      └── _persist_cycle() per symbol
                │
                ├── ExecutionService.execute_decision()
                │         │
                │         └── logs/trades.jsonl  [file append]
                │
                └── SQLAlchemy session
                          │
                          ├── HourlyMarketSnapshot row
                          ├── Position row
                          ├── AIDecision row
                          └── Execution row


REST API (on-demand HTTP request)
      │
      └── SQLAlchemy query → JSON response
```

---

## Data Formats at Each Stage

### 1. Raw WebSocket Payload

Binance sends one JSON object per symbol per combined stream message:

```json
{
  "stream": "btcusdt@ticker",
  "data": {
    "e": "24hrTicker",
    "s": "BTCUSDT",
    "c": "93142.50",
    "b": "93140.00",
    "a": "93145.00",
    "h": "94200.00",
    "l": "92100.00",
    "q": "1482934200.50",
    "P": "-0.42"
  }
}
```

Key fields used: `s` (symbol), `c` (last price), `b` (bid), `a` (ask), `h` (24h high), `l` (24h low), `q` (24h quote volume), `P` (24h % change).

### 2. MarketStateStore Entry

After parsing, each symbol becomes a `SymbolMarketState`:

```python
SymbolMarketState(
    symbol="BTCUSDT",
    last_price=Decimal("93142.50"),
    bid=Decimal("93140.00"),
    ask=Decimal("93145.00"),
    volume_24h=Decimal("1482934200.50"),
    price_change_24h_pct=Decimal("-0.42"),
    high_24h=Decimal("94200.00"),
    low_24h=Decimal("92100.00"),
    updated_at=datetime(2026, 4, 22, 19, 0, 1, tzinfo=timezone.utc),
)
```

All price fields are stored as `Decimal` to avoid floating-point rounding errors.

### 3. Market Data Dict (Trading Cycle Input)

`_build_market_data()` converts the store into a plain dict that AI and risk services consume:

```python
{
    "BTCUSDT": {
        "symbol": "BTCUSDT",
        "last_price": 93142.50,       # float (converted from Decimal)
        "bid": 93140.0,
        "ask": 93145.0,
        "volume_24h": 1482934200.5,
        "price_change_24h_pct": -0.42,
        "high_24h": 94200.0,
        "low_24h": 92100.0,
        "quote_volume_24h": 1482934200.5,
        "ohlcv": {
            "last_close": 93142.50,
            "high_24h": 94200.0,
            "low_24h": 92100.0,
            "avg_volume_24h": 1482934200.5,
            "price_change_pct_24h": -0.42,
        },
        "orderbook": {},
    }
}
```

The `ohlcv` sub-dict exists for AI prompt compatibility — the AI prompt reads from `ohlcv.high_24h` etc. Since we only have WebSocket data (not OHLCV candles), these are derived from the 24h ticker fields.

### 4. Sentiment Data (AssetSentiment Objects)

```python
AssetSentiment(
    symbol="BTCUSDT",
    score=0.124,              # blended sentiment [-1, 1]
    headline_count=8,
    top_headlines=[
        "Bitcoin surges past $93k as institutional demand grows",
        "BTC dominance reaches 52% amid altcoin weakness",
    ],
    source_scores={
        "rss": 0.21,
        "reddit": 0.08,
        "fear_greed": 0.06,   # (Fear & Greed 53 → (53-50)/50 = 0.06)
    },
    fear_greed_index=53,
)
```

### 5. AI Prompt (sent to Claude)

```
=== HOURLY TRADING ANALYSIS — 2026-04-22 19:00 UTC ===

Analyse the following data and return one JSON decision per asset.

--- BTCUSDT ---
Price: $93,142.5000  Bid: $93,140.0000  Ask: $93,145.0000
24h High: $94,200.0000  24h Low: $92,100.0000  Change: -0.42%
Avg 24h Volume: 1,482,934,200  Quote Volume: $1,482,934,200
Sentiment: 0.124 | Fear & Greed: 53/100
  1. Bitcoin surges past $93k as institutional demand grows
  2. BTC dominance reaches 52% amid altcoin weakness

--- ETHUSDT ---
...

Return ONLY a JSON array.
```

### 6. AI Response (raw JSON from Claude)

```json
[
  {
    "asset": "BTCUSDT",
    "action": "HOLD",
    "confidence": 0.72,
    "size_pct": 0,
    "reasoning": "Price retreating from recent high with mild negative 24h change. Waiting for clearer momentum signal."
  },
  {
    "asset": "ETHUSDT",
    "action": "BUY",
    "confidence": 0.81,
    "size_pct": 10,
    "reasoning": "Positive sentiment and stable volume suggest accumulation opportunity."
  },
  {
    "asset": "SOLUSDT",
    "action": "HOLD",
    "confidence": 0.65,
    "size_pct": 0,
    "reasoning": "Confidence below threshold — insufficient data signal."
  }
]
```

### 7. Validated Decisions (after _validate_decisions)

```python
[
    {"asset": "BTCUSDT", "action": "HOLD", "confidence": 0.72, "size_pct": 0, "reasoning": "..."},
    {"asset": "ETHUSDT", "action": "BUY",  "confidence": 0.81, "size_pct": 10, "reasoning": "..."},
    {"asset": "SOLUSDT", "action": "HOLD", "confidence": 0.0,  "size_pct": 0,
     "reasoning": "Confidence 0.65 below minimum 0.7 — forcing HOLD."},
]
```

SOLUSDT's action was changed from the raw Claude response because confidence was below `MIN_CONFIDENCE`.

### 8. Filtered Decisions (after RiskService.filter_decisions)

Assuming no open positions and no stop-losses triggered:

```python
[
    {"asset": "BTCUSDT", "action": "HOLD", "size_pct": 0, ...},
    {"asset": "ETHUSDT", "action": "BUY",  "size_pct": 10, ...},  # passes all checks
    {"asset": "SOLUSDT", "action": "HOLD", "size_pct": 0, ...},
]
```

### 9. Execution Result (paper mode)

```python
{
    "timestamp": "2026-04-22T19:00:45Z",
    "asset": "ETHUSDT",
    "action": "BUY",
    "confidence": 0.81,
    "size_pct": 10,
    "current_price": 1742.30,
    "portfolio_usdt": 10000.0,
    "reasoning": "Positive sentiment...",
    "paper_trading": True,
    "order": {
        "id": "PAPER-2026-04-22T19:00:45Z",
        "symbol": "ETHUSDT",
        "side": "buy",
        "type": "market",
        "qty": 5.739,           # 10000 * 0.10 / 1742.30
        "price": 1742.30,
        "status": "paper_filled",
    },
    "error": None,
}
```

### 10. Database Rows (per symbol per cycle)

```
HourlyMarketSnapshot:
  asset_id=2, snapshot_time=2026-04-22T19:00:00Z,
  open=1742.30, high=1760.00, low=1730.50, close=1742.30,
  volume=982341200.00

Position:
  snapshot_id=<snapshot.id>, asset_id=2,
  side="flat", size=0, entry_price=NULL,
  wallet_balance=10000.00

AIDecision:
  snapshot_id=<snapshot.id>, prompt_version="v1",
  model_name="claude-sonnet-4-6", action="BUY",
  confidence_score=0.8100, reasoning_summary="Positive sentiment...",
  recommended_size=10.00

Execution:
  ai_decision_id=<decision.id>, executed_action="BUY",
  executed_size=5.73900000, execution_price=1742.30000000,
  fees_paid=0, slippage=0,
  execution_time=2026-04-22T19:00:45Z, status="paper_filled"
```

### 11. API Response (GET /decisions)

```json
[
  {
    "id": 7,
    "snapshot_id": 12,
    "action": "BUY",
    "confidence_score": 0.81,
    "reasoning_summary": "Positive sentiment and stable volume suggest accumulation opportunity."
  }
]
```
