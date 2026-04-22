# Architecture

## System Overview

Claude Trader is structured as a single FastAPI process containing three concurrent execution paths:

1. **WebSocket loop** — a persistent async connection to Binance, running every second
2. **Hourly cycle** — APScheduler fires a sync trading cycle at startup then every 60 minutes
3. **Trigger executor** — an async queue consumer that processes real-time exit orders

These three paths share a small set of stateful objects injected at startup. Everything else is stateless.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Process                          │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐                   │
│  │  Binance WebSocket│   │  APScheduler     │                   │
│  │  (async loop)    │   │  (hourly trigger) │                   │
│  └────────┬─────────┘   └────────┬─────────┘                   │
│           │                      │                              │
│           ▼                      ▼                              │
│  ┌────────────────┐    ┌──────────────────────────────────┐     │
│  │ MarketStateStore│◄───│       TradingCycleService        │     │
│  │ (in-memory)    │    │  sentiment → AI → risk → execute │     │
│  └────────┬───────┘    └──────────────────────────────────┘     │
│           │                      │                              │
│           │ price tick           │ decisions                    │
│           ▼                      ▼                              │
│  ┌─────────────────┐   ┌──────────────────┐                    │
│  │  RiskService    │   │ ExecutionService  │                    │
│  │  (positions)    │   │ (paper / live)   │                    │
│  └────────┬────────┘   └────────┬─────────┘                    │
│           │                     │                              │
│           │ exit trigger        │                              │
│           ▼                     │                              │
│  ┌─────────────────┐            │                              │
│  │ asyncio.Queue   │            │                              │
│  └────────┬────────┘            │                              │
│           ▼                     │                              │
│  ┌─────────────────┐            │                              │
│  │ TriggerExecutor │            │                              │
│  │ (async)         │            │                              │
│  └─────────────────┘            │                              │
│                                 ▼                              │
│                        ┌────────────────┐                      │
│                        │   SQLite DB    │                      │
│                        │  (SQLAlchemy)  │                      │
│                        └────────────────┘                      │
│                                 │                              │
│                        ┌────────────────┐                      │
│                        │   REST API     │                      │
│                        │  /health etc.  │                      │
│                        └────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### MarketStateStore

An in-memory dictionary keyed by symbol (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`). Each entry is a `SymbolMarketState` dataclass holding the latest price, bid, ask, 24h high/low, volume, and timestamp. Updated on every WebSocket tick.

This is the single source of truth for live prices. It is read by the trading cycle to build market snapshots without making any HTTP calls.

### BinanceWebSocketService

Connects to Binance's combined `@ticker` stream, which pushes a full 24h statistics payload for each tracked symbol roughly every second. On each message it updates `MarketStateStore` and then checks whether any open position has hit its stop-loss or take-profit threshold.

### RiskService

Holds all open positions in a `dict[str, OpenPosition]` in memory. Every decision — whether from the hourly AI cycle or from a real-time WebSocket trigger — passes through this service before execution. It enforces all risk rules: kill switch, confidence threshold, position sizing, exposure cap, entry guards, and exit guards.

### TriggerExecutor

A background `asyncio` task that waits on a queue. When `RiskService.check_exit_conditions()` detects a breach during a WebSocket tick, it puts a SELL order on the queue. `TriggerExecutor` picks it up and runs `ExecutionService.execute_decision()` in a thread pool, keeping the WebSocket loop unblocked.

### TradingCycleService

The hourly orchestrator. Reads from `MarketStateStore`, fetches sentiment, calls Claude, applies risk filters, executes decisions, and writes every step to SQLite. It does not run on a fixed clock — it runs immediately on startup and then every 60 minutes from that point.

### ExecutionService

Handles the mechanics of placing an order. In paper mode it constructs a synthetic order dict and logs it. In live mode it calls `ccxt.binance.create_market_order()`. In both cases it updates `RiskService`'s position state and appends a record to `logs/trades.jsonl`.

### AI Service

A stateless module that constructs a prompt from market and sentiment data, calls the Claude API, extracts the JSON array from the response, validates each decision against the schema, and returns a list of `{asset, action, confidence, size_pct, reasoning}` dicts. It maintains a module-level singleton `anthropic.Anthropic` client.

### Sentiment Service

Fetches text from crypto RSS feeds and Reddit, runs TextBlob polarity analysis on headlines that match each symbol's keywords, and blends the scores with the Fear & Greed index into a single float in `[-1, 1]`.

### Data Feeds

A thin ccxt wrapper that provides balance fetching and OHLCV data. In paper mode, balance is a configured constant. The exchange object is a lazy singleton — created on first use and reused.

---

## Dependency Graph

```
main.py
  ├── MarketStateStore
  ├── RiskService
  ├── ExecutionService ──────────── depends on: RiskService
  ├── TradingCycleService ──────── depends on: MarketStateStore, RiskService, ExecutionService
  ├── BinanceWebSocketService ──── depends on: MarketStateStore, RiskService, trigger_queue
  └── TriggerExecutor ──────────── depends on: trigger_queue, ExecutionService
```

All dependencies are injected at startup in `main.py`. No service creates its own dependencies. This makes each service independently testable.

---

## Design Decisions

### Why class-based services?

`RiskService` and `ExecutionService` hold state (`_positions`, the trade log path). Classes make that state explicit and scoped to an instance rather than leaked as module globals. `TradingCycleService` holds no state itself — it's a class because it composes three injected dependencies.

### Why asyncio.Queue for exits?

The WebSocket handler (`_handle_message`) is called synchronously inside an async loop. `ExecutionService.execute_decision()` is blocking — it may call ccxt (HTTP), write files, and talk to SQLite. If we called it directly in `_handle_message`, we would block the entire WebSocket loop, potentially missing subsequent price ticks. The queue decouples detection from execution.

### Why IntervalTrigger instead of CronTrigger?

`CronTrigger(minute=0)` would wait until the next clock hour. `IntervalTrigger(hours=1, next_run_time=now)` fires immediately and then every 60 minutes from that point. This means if you restart the bot at 14:35, the first cycle runs at 14:35, not 15:00.

### Why SQLite?

This is a prototype. SQLite requires zero infrastructure. The SQLAlchemy ORM is written against the standard interface so switching to PostgreSQL is a one-line change to `DATABASE_URL`.
