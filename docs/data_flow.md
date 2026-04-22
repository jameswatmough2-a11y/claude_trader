# Data Flow

This document traces every piece of data through the system, from the raw Binance WebSocket packet to the database row and API response.

---

## High-Level Flow

```
Binance Exchange
      │
      │  WebSocket @ticker events (continuous)
      ▼
BinanceWebSocketService._handle_message()
      │
      ├─► MarketStateStore.update()
      │        ↑ read by TradingCycleService._build_market_data()
      │
      └─► RiskService.check_exit_conditions()
               │
               │ (if stop-loss / take-profit triggered)
               ▼
          trigger_queue.put_nowait(order)
               │
               ▼
          TriggerExecutor._execute()
               │
               ▼
          ExecutionService.execute_decision()
               │
               ├─► logs/trades.jsonl
               └─► RiskService._positions (update)


APScheduler (every hour)
      │
      ▼
TradingCycleService.run(db)
      │
      ├─► MarketStateStore.all()          → market_data dict
      │
      ├─► SentimentService
      │     ├─► RSS feeds (HTTP)           → per-asset headline scores
      │     ├─► Reddit JSON API (HTTP)     → per-asset post scores
      │     └─► Fear & Greed API (HTTP)    → global score
      │         → dict[str, AssetSentiment]
      │
      ├─► ai_service.get_trading_decisions()
      │     ├─► _build_prompt(market_data, sentiment_data) → string
      │     ├─► anthropic.messages.create()  → raw text
      │     ├─► _extract_json()              → JSON string
      │     └─► _validate_decisions()        → list[dict]
      │
      ├─► RiskService.filter_decisions()
      │     ├─► check_stop_losses()          → stop-loss SELL orders
      │     └─► evaluate_decision()          → approved/adjusted decisions
      │
      ├─► data_feeds.fetch_balance()         → balance dict
      │
      └─► For each decision:
            ExecutionService.execute_decision()
              ├─► logs/trades.jsonl
              └─► RiskService._positions (update)
            _persist_cycle(db, ...)
              └─► SQLite
                    ├─► assets
                    ├─► hourly_market_snapshots
                    ├─► positions
                    ├─► ai_decisions
                    └─► executions


HTTP Client
      │
      ▼
FastAPI routes
      │
      └─► SQLAlchemy Session → SQLite
            ├─► GET /assets      → assets table
            ├─► GET /positions   → positions table
            └─► GET /decisions   → ai_decisions table
```

---

## Step-by-Step: WebSocket Tick → Market State

**Raw Binance message** (combined stream envelope):
```json
{
  "stream": "btcusdt@ticker",
  "data": {
    "s": "BTCUSDT",
    "c": "67423.0100",
    "b": "67422.9900",
    "a": "67423.0100",
    "q": "1234567890.00",
    "P": "-0.842",
    "h": "68500.0000",
    "l": "66200.0000"
  }
}
```

**After `_handle_message` parsing:**
```python
market_store.update(
    symbol="BTCUSDT",
    last_price=Decimal("67423.0100"),
    bid=Decimal("67422.9900"),
    ask=Decimal("67423.0100"),
    volume_24h=Decimal("1234567890.00"),
    price_change_24h_pct=Decimal("-0.842"),
    high_24h=Decimal("68500.0000"),
    low_24h=Decimal("66200.0000"),
)
```

**State in `MarketStateStore._state["BTCUSDT"]`:**
```python
SymbolMarketState(
    symbol="BTCUSDT",
    last_price=Decimal("67423.0100"),
    bid=Decimal("67422.9900"),
    ask=Decimal("67423.0100"),
    volume_24h=Decimal("1234567890.00"),
    price_change_24h_pct=Decimal("-0.842"),
    high_24h=Decimal("68500.0000"),
    low_24h=Decimal("66200.0000"),
    updated_at=datetime(2026, 4, 22, 14, 0, 1, tzinfo=UTC),
)
```

---

## Step-by-Step: Market State → AI Prompt

**`TradingCycleService._build_market_data()` output:**
```python
{
  "BTCUSDT": {
    "symbol": "BTCUSDT",
    "last_price": 67423.01,
    "bid": 67422.99,
    "ask": 67423.01,
    "volume_24h": 1234567890.0,
    "price_change_24h_pct": -0.842,
    "high_24h": 68500.0,
    "low_24h": 66200.0,
    "quote_volume_24h": 1234567890.0,
    "ohlcv": {
      "last_close": 67423.01,
      "high_24h": 68500.0,
      "low_24h": 66200.0,
      "avg_volume_24h": 1234567890.0,
      "price_change_pct_24h": -0.842,
    },
    "orderbook": {},
  }
}
```

Note: `open_price`, `high_price`, `low_price`, `close_price` in the database snapshot are all set to `last_price` from this dict. The `high` and `low` in the `ohlcv` sub-dict come from `high_24h` / `low_24h`.

**Prompt fragment for BTCUSDT:**
```
--- BTCUSDT ---
Price: $67,423.0100  Bid: $67,422.9900  Ask: $67,423.0100
24h High: $68,500.0000  24h Low: $66,200.0000  Change: -0.842%
Avg 24h Volume: 1,234,568  Quote Volume: $1,234,567,890
Sentiment: 0.123 | Fear & Greed: 62/100
  1. Bitcoin ETF inflows reach record high this week
  2. BTC holds above key $67k support level
  3. Institutional interest continues to drive BTC demand
```

---

## Step-by-Step: AI Response → Validated Decisions

**Raw Claude response text:**
```
[{"asset": "BTCUSDT", "action": "BUY", "confidence": 0.82, "size_pct": 15, "reasoning": "Strong momentum and positive sentiment."}, {"asset": "ETHUSDT", "action": "HOLD", "confidence": 0.54, "size_pct": 0, "reasoning": "Confidence below threshold."}, {"asset": "SOLUSDT", "action": "SELL", "confidence": 0.91, "size_pct": 10, "reasoning": "Bearish 24h trend warrants position reduction."}]
```

**After `_validate_decisions()`:**
```python
[
    {"asset": "BTCUSDT", "action": "BUY",  "confidence": 0.82, "size_pct": 15, "reasoning": "Strong momentum and positive sentiment."},
    {"asset": "ETHUSDT", "action": "HOLD", "confidence": 0.54, "size_pct": 0,  "reasoning": "Confidence below threshold."},  # confidence < 0.7, forced HOLD
    {"asset": "SOLUSDT", "action": "SELL", "confidence": 0.91, "size_pct": 10, "reasoning": "Bearish 24h trend warrants position reduction."},
]
```

If ETHUSDT were missing from Claude's response entirely:
```python
{"asset": "ETHUSDT", "action": "HOLD", "confidence": 0.0, "size_pct": 0, "reasoning": "Missing from model response — defaulted to HOLD."}
```

---

## Step-by-Step: Decisions → Risk-Filtered Decisions

Input (from AI validator, no positions currently open):
```python
[
    {"asset": "BTCUSDT", "action": "BUY",  "confidence": 0.82, "size_pct": 15},
    {"asset": "ETHUSDT", "action": "HOLD", "confidence": 0.54, "size_pct": 0},
    {"asset": "SOLUSDT", "action": "SELL", "confidence": 0.91, "size_pct": 10},
]
```

Risk check results:
- BTCUSDT BUY: no existing position, confidence OK, size OK, exposure OK → **approved BUY size=15**
- ETHUSDT HOLD: pass-through → **HOLD**
- SOLUSDT SELL: no open position → phantom sell guard → **forced HOLD**, reasoning updated

Output:
```python
[
    {"asset": "BTCUSDT", "action": "BUY",  "confidence": 0.82, "size_pct": 15, "reasoning": "Strong momentum and positive sentiment."},
    {"asset": "ETHUSDT", "action": "HOLD", "confidence": 0.54, "size_pct": 0,  "reasoning": "Confidence below threshold."},
    {"asset": "SOLUSDT", "action": "HOLD", "confidence": 0.91, "size_pct": 0,  "reasoning": "No open position for SOLUSDT — ignoring SELL."},
]
```

---

## Step-by-Step: Execution → Trade Log

**Paper execution for BTCUSDT BUY:**

Inputs:
- `portfolio_usdt = 10000.0`
- `size_pct = 15`
- `price = 67423.01`

Calculation:
```
qty = 10000 × 0.15 / 67423.01 = 0.022247 BTC
```

**Trade log entry (`logs/trades.jsonl`):**
```json
{
  "timestamp": "2026-04-22T14:00:01.234567+00:00",
  "asset": "BTCUSDT",
  "action": "BUY",
  "confidence": 0.82,
  "size_pct": 15,
  "current_price": 67423.01,
  "portfolio_usdt": 10000.0,
  "reasoning": "Strong momentum and positive sentiment.",
  "paper_trading": true,
  "order": {
    "id": "PAPER-2026-04-22T14:00:01.234567+00:00",
    "symbol": "BTCUSDT",
    "side": "buy",
    "type": "market",
    "qty": 0.022247,
    "price": 67423.01,
    "status": "paper_filled"
  },
  "error": null
}
```

---

## Step-by-Step: Execution → Database

After execution, `_persist_cycle` writes to four tables. For BTCUSDT BUY (paper filled):

**`assets` table (upserted):**
```
id=1, symbol="BTCUSDT", base_currency="BTC", quote_currency="USDT"
```

**`hourly_market_snapshots` table:**
```
id=1, asset_id=1, snapshot_time="2026-04-22T14:00:00+00:00",
open_price=67423.01, high_price=68500.0, low_price=66200.0, close_price=67423.01,
volume=1234567890.0, price_change_1h_pct=NULL, price_change_since_entry_pct=NULL
```

**`positions` table:**
```
id=1, snapshot_id=1, asset_id=1,
side="flat", size=0.0, entry_price=NULL,
unrealized_pnl=NULL, wallet_balance=10000.0
```

Note: `side="flat"` always. The DB position record does not reflect the just-executed BUY. Position tracking is in-memory only (see [persistence.md](persistence.md)).

**`ai_decisions` table:**
```
id=1, snapshot_id=1, prompt_version="v1", model_name="claude-sonnet-4-6",
action="BUY", confidence_score=0.82,
reasoning_summary="Strong momentum and positive sentiment.",
recommended_size=15, recommended_stop_loss=NULL, recommended_take_profit=NULL,
created_at="2026-04-22T14:00:00+00:00"
```

**`executions` table:**
```
id=1, ai_decision_id=1,
executed_action="BUY", executed_size=0.022247, execution_price=67423.01,
fees_paid=0.0, slippage=0.0,
execution_time="2026-04-22T14:00:00+00:00",
status="paper_filled"
```

---

## Step-by-Step: Database → API Response

**`GET /decisions`:**
```json
[
  {
    "id": 1,
    "snapshot_id": 1,
    "action": "BUY",
    "confidence_score": 0.82,
    "reasoning_summary": "Strong momentum and positive sentiment."
  },
  {
    "id": 2,
    "snapshot_id": 2,
    "action": "HOLD",
    "confidence_score": 0.54,
    "reasoning_summary": "Confidence below threshold."
  }
]
```

---

## Data Precision Notes

- All prices are stored in SQLite as `Numeric(20, 8)` — 20 total digits, 8 decimal places.
- Values flow through the system as `Decimal` (WebSocket → market store), converted to `float` for the AI prompt and risk calculations, then back to `Decimal` for DB writes.
- The conversion `Decimal(str(float_value))` is used consistently to avoid floating-point precision surprises when wrapping `float` back to `Decimal`.

## Data That Is Never Persisted

| Data | Where it lives | Lost on restart |
|---|---|---|
| Open position state | `RiskService._positions` | Yes |
| Current prices | `MarketStateStore._state` | Yes (repopulated by WebSocket) |
| Pending exit orders | `trigger_queue` | Yes |
| Paper balance | Computed from `PAPER_BALANCE_USDT` each cycle | N/A (not stateful) |
| Actual filled quantity after live trade | Only in `trades.jsonl` and `executions` table | No |

See [persistence.md](persistence.md) for full details.
