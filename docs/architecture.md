# Architecture

## System Overview

Claude Trader is a **single-process FastAPI application** running inside Python's `asyncio` event loop. Two concurrent async tasks run for the lifetime of the process:

1. **BinanceWebSocketService** — maintains a persistent WebSocket connection to Binance and processes every price tick
2. **TriggerExecutor** — drains an `asyncio.Queue` of real-time exit orders (stop-loss / take-profit) produced by the WebSocket task

A third loop is driven by **APScheduler** at a configurable interval:

3. **TradingCycleService** — reads the current price cache, fetches real OHLCV candles, calls Claude (or falls back to rule-based logic), applies risk rules, executes decisions, and writes results to the database

These are orchestrated from `app/main.py` via a FastAPI lifespan context manager.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI HTTP layer                        │
│   /health  /assets  /positions  /decisions  /logs           │
│   /sessions  /sessions/{id}  /sessions/{id}/markers         │
│   /trades                                                    │
└────────────────────────┬────────────────────────────────────┘
                         │ reads DB via SQLAlchemy
┌────────────────────────▼────────────────────────────────────┐
│                    Persistence layer                         │
│             SQLite + SQLAlchemy ORM (10 tables)              │
│   users  /  trading_sessions  /  trades                      │
│   assets  /  hourly_market_snapshots  /  positions           │
│   ai_decisions  /  executions                                │
│   ohlcv_candles  /  system_logs                              │
└───────────────────┬──────────────────────────────────────────┘
          ▲ writes  │
          │         │
┌─────────┴────────────────┐       ┌────────────────────────┐
│   Trading Cycle           │       │   Trigger Executor      │
│   (scheduled, sync thread)│       │   (async queue drain)   │
└─────────┬────────────────┘       └──────────┬─────────────┘
          │ reads                              │ executes SELL
          │                                   │ consumes trigger_queue
          │                                   │ closes Trade on SL/TP
┌─────────▼───────────────────────────────────────────────────┐
│                  Market State Store                          │
│          in-memory dict[str, SymbolMarketState]              │
└─────────▲───────────────────────────────────────────────────┘
          │ writes on every tick
┌─────────┴───────────────────────────────────────────────────┐
│              BinanceWebSocketService                         │
│   wss://data-stream.binance.vision/stream?streams=...@ticker │
└─────────────────────────────────────────────────────────────┘
          │ calls check_exit_conditions() on every tick
┌─────────▼───────────────────────────────────────────────────┐
│                    Risk Service                              │
│       in-memory position tracking + rule enforcement         │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `app/main.py` — Application bootstrap

All service instances are created at **module level** (singletons for the process lifetime). The FastAPI `lifespan` context manager handles startup and shutdown:

```python
# Startup sequence (inside lifespan):
init_db()                                    # 1. Create/migrate SQLite tables + ensure default user
trigger_executor.session_factory = SessionLocal  # 2. Inject DB factory for trade close on SL/TP
restore_state()                              # 3. Restore positions + paper balance from DB
_apply_settings(row)                         # 4. Apply persistent bot config
active = session_service.get_active_session()  # 5. Re-link to session from before server restart
asyncio.create_task(ws_service.run_forever()) # 6. Start WebSocket stream
asyncio.create_task(trigger_executor.run_forever()) # 7. Start exit processor
asyncio.create_task(_prewarm_sentiment())    # 8. Warm sentiment cache in background
scheduler.start()                            # 9. Start scheduler (waits for /start)
```

Service wiring is explicit constructor injection — each service receives its dependencies at creation time:

```python
market_store      = MarketStateStore()
risk_service      = RiskService()
execution_service = ExecutionService(risk_service=risk_service)
trading_cycle     = TradingCycleService(market_store, risk_service, execution_service)
ws_service        = BinanceWebSocketService(symbols, market_store, risk_service, trigger_queue)
trigger_executor  = TriggerExecutor(trigger_queue, execution_service, session_factory=None)
session_service   = SessionService()
trade_service     = TradeService()
```

---

### `MarketStateStore` — In-memory price cache

A `dict[str, SymbolMarketState]` wrapped in a class. Holds the most recent 24h ticker fields for every tracked symbol:

| Field | Type | Source |
|---|---|---|
| `last_price` | `Decimal` | `c` (close/last) field in `@ticker` |
| `bid` | `Decimal` | `b` field |
| `ask` | `Decimal` | `a` field |
| `volume_24h` | `Decimal` | `q` (quote asset volume) field |
| `price_change_24h_pct` | `Decimal` | `P` field |
| `high_24h` | `Decimal` | `h` field |
| `low_24h` | `Decimal` | `l` field |
| `updated_at` | `datetime` | Set at update time |

---

### `BinanceWebSocketService` — Real-time price ingestion

Connects to the Binance combined stream endpoint. The URL is built from `settings.tracked_symbols` at connection time (not on init), so reconnects after a symbol change use the new symbol list.

When `PUT /api/bot/config` receives a new `tracked_symbols` value, it calls `asyncio.create_task(ws_service.reconnect())` which closes the active WebSocket and triggers immediate reconnect.

On connect/disconnect, `db_logger.log_info` / `log_warning` events are written to `system_logs`.

---

### `TriggerExecutor` — Real-time exit processor

Drains `trigger_queue` in an async loop. Each dequeued order is dispatched to the default thread pool executor via `loop.run_in_executor()`, keeping the WebSocket message loop unblocked.

On a successful execution, `_close_trade()` opens a short-lived `SessionLocal` session to look up the open `Trade` row for the symbol and close it with the appropriate `exit_reason` (`stop_loss` or `take_profit`). The `session_factory` is injected in `main.py`'s lifespan after `SessionLocal` is available.

---

### `TradingCycleService` — Scheduled orchestrator

The synchronous `run(db)` method is called by APScheduler from a thread pool worker. Steps:

1. Read `bot_state.current_session_id` — passed to `_persist_cycle` to stamp snapshots and executions
2. Build `market_data` from `MarketStateStore`
3. Call `_enrich_and_store_ohlcv()` — fetches real OHLCV candles via ccxt using `settings.ohlcv_interval`, upserts to `ohlcv_candles` table, attaches candle list to market data dict
4. Call `_fetch_sentiment(symbols)` — reads from TTL cache or fetches concurrently
5. Call `_fetch_decisions(market_data, sentiment_data, symbols)`:
   - Attempts `ai_service.get_trading_decisions()` (Claude API)
   - On failure, falls back to `fallback_strategy.get_fallback_decisions()` — SMA-based rule engine
   - Logs AI failure and fallback activation to `system_logs`
6. Call `risk_service.filter_decisions(decisions, market_data)`
7. For each decision, execute and persist — `_persist_cycle` handles Trade lifecycle:
   - BUY filled → `trade_service.open_trade()` creates a `Trade` row; `execution.trade_id` set
   - SELL filled → `trade_service.get_open_trade_for_symbol()` + `close_trade()` with inferred `exit_reason`
8. Log `cycle_start` / `cycle_end` events to `system_logs` with `cycle_id` timestamp

---

### `AIService` — Claude decision engine

Stateless module-level functions. Uses a lazy-initialized `anthropic.Anthropic` singleton.

**Dynamic system prompt:** `_build_system_prompt()` reads `settings.min_confidence` and `settings.max_position_pct` at call time — no hardcoded thresholds.

**Prompt inputs per symbol:**
- Live price, bid, ask
- `high_period` / `low_period` / `avg_volume_period` / `price_change_pct_period` — stats computed from the OHLCV candle window (labeled by interval, e.g. "24h High")
- Last 6 hourly closes as a trend line
- Sentiment score, Fear & Greed index, up to 3 sanitized headlines
- Current position state (flat vs open with P&L) — constrains valid actions
- Previous cycle's decision (action + confidence + reasoning) — for continuity

**`_validate_decisions`** adds `"decision_source": "ai"` to all validated items.

---

### `FallbackStrategy` — Rule-based decisions

`get_fallback_decisions(market_data, open_positions)` is called when the Claude API fails. It produces a complete decision list for all symbols using the OHLCV candles attached to `market_data`.

Logic per symbol:
- Requires ≥ 10 candles (HOLD if fewer)
- Entry signal: `price > SMA5 > SMA10`, bullish last candle, 3-candle return > 1.5%
- Exit signal: `price < SMA5`, bearish last candle, 3-candle return < -1.5%
- Volatility guard: HOLD if 5-candle range > 5%
- Default position size: 10%

All fallback decisions carry `decision_source: "fallback_rule"`.

---

### `RiskService` — Risk enforcement

Maintains `_positions: dict[str, OpenPosition]` in memory. Restored from DB on startup.

Risk checks in two contexts:
- **Cycle**: `filter_decisions()` — kill switch, double-entry guard, phantom-sell guard, confidence threshold, size cap, exposure cap
- **Real-time tick**: `check_exit_conditions()` — stop-loss and take-profit, position deleted immediately on trigger

---

### `ExecutionService` — Order placement

**Paper mode:**
- BUY fills at `ask` price; SELL fills at `bid` price (fallback to `last_price`)
- `validate_and_normalize_qty()` called before execution; rejects if below minimum
- `fee_amount = qty × fill_price × taker_fee_rate` recorded and deducted from paper balance
- `fill_source` and `fee_rate` stored in order dict and `executions` table

**Live mode:**
- Validates qty via `market_validator`
- Places market order via ccxt
- Waits 1s, fetches order back to verify fill
- `verification_status` recorded: "filled", "partial_or_open", or "verification_failed"

---

### `SentimentService` — Market sentiment

Three data sources (RSS, Reddit, Fear & Greed). TTL-cached per asset (30-minute default).

- `_sentiment_cache[symbol]` — `(timestamp, AssetSentiment)` tuple; stale if age > TTL
- `_fear_greed_cache` — global, 30-minute TTL
- `_sanitize(text)` — strips HTML, markdown, URLs; caps at 200 characters
- Concurrent fetch via `ThreadPoolExecutor(2)` per asset, `ThreadPoolExecutor(4)` across assets
- `clear_sentiment_cache()` for testing

---

### `MarketValidator` — Order size validation

`validate_and_normalize_qty(symbol, qty, price)` → `(normalized_qty, error_msg or None)`:
- Fetches market info from ccxt (cached in `_market_cache`)
- Validates: `qty >= min_amount`, `qty <= max_amount`, `qty × price >= min_cost`
- Normalizes qty to `amount_precision` decimal places
- Returns `(qty, None)` if no market info available (permissive)

---

### `DbLogger` — Structured event logging

Module-level functions: `log_event()`, `log_info()`, `log_warning()`, `log_error()`.

Each call opens its own `SessionLocal()` session and commits independently. This ensures log entries persist even when the caller's main transaction rolls back (e.g. on `IntegrityError` for duplicate snapshots).

Events written to `system_logs`:
- `server_start` — on application startup
- `ws_connected` / `ws_disconnected` / `ws_error` — WebSocket lifecycle
- `bot_start` / `bot_stop` — scheduler control
- `cycle_start` / `cycle_end` — per trading cycle
- `ai_request` / `ai_request_failed` / `fallback_activated` — AI layer
- `order_placed` / `fill_price_fallback` — execution layer
- `reconciliation_*` — startup live-mode reconciliation

---

### `DataFeeds` — ccxt market data

Module-level lazy singleton `ccxt.binance` exchange instance. Used for:
- `fetch_ohlcv(symbol, interval, limit)` — real candle data for the trading cycle
- `fetch_balance()` — live USDT balance; paper stub when no API key is set
- `GET /chart/history` — historical chart data

---

## Dependency Graph

```
main.py
  ├── MarketStateStore          (no deps)
  ├── RiskService               (reads settings directly)
  ├── ExecutionService          ← RiskService, MarketValidator, DbLogger
  ├── TradingCycleService       ← MarketStateStore, RiskService, ExecutionService,
  │                                AIService, FallbackStrategy, SentimentService,
  │                                DataFeeds, DbLogger
  ├── BinanceWebSocketService   ← MarketStateStore, RiskService, trigger_queue, DbLogger
  ├── TriggerExecutor           ← trigger_queue, ExecutionService, SessionLocal (injected)
  ├── SessionService            (no deps — thin DB wrapper)
  └── TradeService              (no deps — thin DB wrapper)

TradingCycleService.run()
  ├── data_feeds.fetch_ohlcv()              (← ccxt, settings.ohlcv_interval)
  ├── sentiment_service.get_all_sentiment() (← feedparser, requests, TextBlob, TTL cache)
  ├── ai_service.get_trading_decisions()    (← anthropic SDK, settings)
  ├── fallback_strategy.get_fallback_decisions()  (← pure Python, OHLCV candles)
  ├── data_feeds.fetch_balance()            (← ccxt or paper stub)
  ├── market_validator.validate_and_normalize_qty() (← ccxt market info, cache)
  ├── db_logger.log_info/warning/error()    (← separate SQLAlchemy session per call)
  └── SQLAlchemy Session                    (← SQLite file)
```

---

## Dashboard (Next.js Frontend)

The dashboard is a **Next.js App Router** application in `dashboard/`. It communicates with the FastAPI backend via REST (`/api/...` proxied by Next.js) and streams real-time chart data via WebSocket.

### Pages

- **Overview** (`/`) — bot controls, stat cards (including live session P&L), candlestick chart with trade markers, decisions table
- **Settings** (`/settings`) — config editor; fields locked while bot is running
- **Logs** (`/logs`) — structured event log viewer; filterable, paginated, 15s auto-refresh
- **Sessions** (`/sessions`) — paginated list of all bot sessions with P&L, win rate, trade count
- **Session Detail** (`/sessions/[id]`) — per-session stats and full trades table

### Key components

**`app-sidebar.tsx`** — nav links (Overview, Settings, Logs, Sessions), bot status badge, live clock, theme toggle.

**`mobile-nav.tsx`** — mobile slide-in drawer with the same four nav links.

**`candlestick-chart.tsx`** — TradingView Lightweight Charts. Live price, entry, SL, and TP price lines with toggles. Trade markers (BUY arrow up / SELL arrow down) and background shading for each trade range (green = win, red = loss) fetched from `GET /api/sessions/current/markers`.

**`providers/display-prefs-provider.tsx`** — global timezone/currency context; `fmtTime(iso)` corrects SQLite naive timestamps for display.

### Display preferences system

All monetary values from the backend are in USDT. Currency conversion is browser-side via `cvtPrice(usd)`. Exchange rates are hardcoded constants. Timezone is applied by `fmtTime` using `Intl`. Both preferences are persisted to `localStorage`.

---

## Design Rationale

**Single process, single event loop** — deployment is one `uvicorn` command. Blocking calls in the trading cycle run in APScheduler's thread pool and do not block the WebSocket loop.

**Isolated DB logger sessions** — `db_logger` opens a new `SessionLocal()` per call so log entries always commit, even when the caller's main session rolls back.

**TTL sentiment cache** — avoids re-fetching external APIs every cycle. 30-minute TTL is a reasonable balance between freshness and rate-limit safety.

**Rule-based fallback** — SMA crossover is simple, explainable, and conservative. It cannot make the same catastrophic error as a hallucinating LLM.

**Permissive market validator** — if ccxt can't load market info (new symbol, API issue), the order is allowed through. This prefers execution availability over strict validation for the MVP.

**Kill switch as env var read at call time** — lets an operator halt trading without restarting the process.

**Constructor injection** — makes the service graph explicit and unit-testable without mocking module globals.
