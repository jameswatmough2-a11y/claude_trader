# Architecture

## System Overview

Claude Trader is structured as a **single-process FastAPI application** running inside Python's `asyncio` event loop. Two concurrent async tasks run for the lifetime of the process:

1. **BinanceWebSocketService** — maintains a persistent WebSocket connection to Binance and processes every price tick
2. **TriggerExecutor** — drains an `asyncio.Queue` of real-time exit orders (stop-loss / take-profit) produced by the WebSocket task

A third loop is driven by **APScheduler** once per hour:

3. **TradingCycleService** — reads the current price cache, calls Claude, applies risk rules, executes decisions, and writes results to the database

These are orchestrated from `app/main.py` via a FastAPI lifespan context manager.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI HTTP layer                        │
│          /health  /assets  /positions  /decisions            │
└────────────────────────┬────────────────────────────────────┘
                         │ reads DB via SQLAlchemy
┌────────────────────────▼────────────────────────────────────┐
│                    Persistence layer                         │
│             SQLite + SQLAlchemy ORM (5 tables)               │
│   assets  /  hourly_market_snapshots  /  positions           │
│   ai_decisions  /  executions                                │
└───────────────────┬──────────────────────────────────────────┘
          ▲ writes  │
          │         │
┌─────────┴────────────────┐       ┌────────────────────────┐
│   Trading Cycle           │       │   Trigger Executor      │
│   (hourly, sync thread)   │       │   (async queue drain)   │
└─────────┬────────────────┘       └──────────┬─────────────┘
          │ reads                              │ executes SELL
          │                                   │ consumes trigger_queue
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
init_db()                                    # 1. Create SQLite tables
asyncio.create_task(ws_service.run_forever()) # 2. Start WebSocket stream
asyncio.create_task(trigger_executor.run_forever()) # 3. Start exit processor
scheduler.add_job(run_hourly_cycle, ...)     # 4. Schedule hourly cycle
scheduler.start()                            # 5. Fire first cycle immediately
```

Service wiring is explicit constructor injection — each service receives its dependencies at creation time:

```python
market_store      = MarketStateStore()
risk_service      = RiskService()
execution_service = ExecutionService(risk_service=risk_service)
trading_cycle     = TradingCycleService(market_store, risk_service, execution_service)
ws_service        = BinanceWebSocketService(symbols, market_store, risk_service, trigger_queue)
trigger_executor  = TriggerExecutor(trigger_queue, execution_service)
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

The store has no locking. CPython's GIL serializes dict reads/writes, which is sufficient here because the WebSocket callback is synchronous (`_handle_message` is called within the async loop but is not itself `async`).

---

### `BinanceWebSocketService` — Real-time price ingestion

Connects to the Binance combined stream endpoint:
```
wss://data-stream.binance.vision/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker
```

The URL is built from `settings.tracked_symbols` at construction time. Each message is a combined-stream envelope:
```json
{ "stream": "btcusdt@ticker", "data": { "s": "BTCUSDT", "c": "67423.01", ... } }
```

For each message:
1. Parse JSON, extract `data` sub-object
2. Update `MarketStateStore` with parsed `Decimal` values
3. Call `risk_service.check_exit_conditions(symbol, last_price)`
4. If an exit is triggered (stop-loss or take-profit), push the SELL order onto `trigger_queue`

The `run_forever()` method wraps the WebSocket connection in `while True` with a 5-second reconnect delay on any exception.

---

### `TriggerExecutor` — Real-time exit processor

Drains `trigger_queue` in an async loop. Each dequeued order is dispatched to the default thread pool executor via `loop.run_in_executor(None, self._execute, order)`. This keeps the WebSocket message loop unblocked even if the execution involves a synchronous HTTP call (ccxt in live mode).

The `_execute` method:
1. Retrieves the current balance (paper stub or ccxt)
2. Builds a single-asset `market_data` dict using `trigger_price` as `last_price`
3. Calls `execution_service.execute_decision(order, market_data, balance)`
4. Logs the result at `WARNING` level

---

### `TradingCycleService` — Hourly orchestrator

The synchronous `run(db: Session)` method is called by APScheduler from a thread pool worker. It is **not** an async function — APScheduler's `AsyncIOScheduler` runs jobs via `asyncio.get_event_loop().run_in_executor()` by default for sync callables.

Steps in `run()`:
1. Call `_build_market_data()` to convert `MarketStateStore` state to the market-data dict
2. If no data (WebSocket not ready), log and return early
3. Call `_fetch_sentiment(symbols)` — wraps `get_all_sentiment()` with exception isolation
4. Call `_fetch_decisions(market_data, sentiment_data, symbols)` — wraps `get_trading_decisions()` with HOLD fallback
5. Call `risk_service.filter_decisions(decisions, market_data)`
6. Call `_fetch_balance()` — paper stub or ccxt
7. For each filtered decision, call `_persist_cycle(db, ...)` inside a try/except that rolls back on integrity errors

---

### `AIService` — Claude decision engine

Stateless module-level functions. Uses a lazy-initialized `anthropic.Anthropic` singleton client (created on first call to `_get_client()`).

The system prompt constrains Claude to return **only** a JSON array with a fixed schema. The user prompt is a plaintext block with one section per symbol containing price, 24h range, volume, sentiment score, Fear & Greed Index, and up to 3 top headlines.

Response processing:
1. Strip markdown fences (```` ``` ````) if present
2. Extract the JSON array using a regex (`\[.*\]` with `DOTALL`)
3. Parse with `json.loads()`
4. Validate each item against the required field set
5. Enforce confidence threshold and size caps
6. Fill in `HOLD` defaults for any symbols missing from the response

---

### `RiskService` — Risk enforcement

Maintains `_positions: dict[str, OpenPosition]` in memory. Each `OpenPosition` holds `asset`, `entry_price`, `size_pct`, and `current_price`.

Risk checks are performed in two contexts:
- **Hourly cycle**: `filter_decisions()` calls `check_stop_losses()` for existing positions, then `evaluate_decision()` for each AI decision
- **Real-time (every tick)**: `check_exit_conditions()` is called directly by the WebSocket handler

When a stop-loss or take-profit triggers in `check_exit_conditions()`, the position is **deleted immediately** from `_positions` before returning the SELL order. This prevents re-triggering on the next tick while the order is still in the queue.

The kill switch is implemented as `os.getenv("KILL_SWITCH", "false").lower() == "true"` evaluated at call time. No restart needed to activate it.

---

### `ExecutionService` — Order placement

Handles both paper and live execution paths.

**Paper mode** (`PAPER_TRADING=true`):
- Computes `qty = portfolio_usdt * (size_pct / 100) / price`
- Builds a synthetic order dict with `id = "PAPER-{timestamp}"`
- Updates `RiskService` position tracking
- Logs to `logs/trades.jsonl`

**Live mode** (`PAPER_TRADING=false`):
- Converts symbol: `BTCUSDT` → `BTC/USDT`
- Calls `get_exchange().create_market_order(symbol, side, qty)`
- Uses `order["average"]` as the filled price for position tracking
- Logs the raw ccxt order response to `logs/trades.jsonl`

In both modes, all records (including HOLDs) are appended to `logs/trades.jsonl` as newline-delimited JSON.

---

### `SentimentService` — Market sentiment

Three data sources, all producing scores in `[-1.0, 1.0]`:

| Source | Method | Weighting |
|---|---|---|
| RSS headlines | TextBlob polarity on `title + summary` for matching entries | Simple mean |
| Reddit posts | Upvote-weighted TextBlob polarity on post titles | Upvote-weighted mean |
| Fear & Greed Index | `(value - 50) / 50.0` | Equal weight with others |

Final score = simple mean of the three source scores, clamped to `[-1, 1]`.

The Fear & Greed Index is fetched once per cycle and shared across all symbols (it is a global market indicator, not asset-specific). RSS and Reddit are fetched per-asset using keyword matching (`BTCUSDT` → `["bitcoin", "btc"]`).

---

### `DataFeeds` — ccxt market data

Module-level lazy singleton `ccxt.binance` exchange instance. Created on first call to `get_exchange()`.

In paper trading mode (no `BINANCE_API_KEY`), `fetch_balance()` returns a static dict using `PAPER_BALANCE_USDT` from settings. This means `get_exchange()` is still constructed (for public market data access) but without API credentials.

`get_all_market_data()` is marked as a fallback in comments — the primary price source is the WebSocket, not ccxt REST. ccxt REST is only used for `fetch_balance()` in live mode.

---

## Dependency Graph

```
main.py
  ├── MarketStateStore          (no deps)
  ├── RiskService               (reads settings directly)
  ├── ExecutionService          ← RiskService
  ├── TradingCycleService       ← MarketStateStore, RiskService, ExecutionService
  ├── BinanceWebSocketService   ← MarketStateStore, RiskService, trigger_queue
  └── TriggerExecutor           ← trigger_queue, ExecutionService

TradingCycleService.run()
  ├── ai_service.get_trading_decisions()     (← anthropic SDK, settings)
  ├── sentiment_service.get_all_sentiment()  (← feedparser, requests, TextBlob)
  ├── data_feeds.fetch_balance()             (← ccxt or paper stub)
  └── SQLAlchemy Session                     (← SQLite file)
```

---

---

## Dashboard (Next.js Frontend)

The dashboard is a **Next.js App Router** application in the `dashboard/` directory. It communicates with the FastAPI backend via REST (`/api/...` proxied by Next.js) and displays real-time data via a 30-second auto-refresh and WebSocket streaming for the candlestick chart.

### Key components

**`app/layout.tsx`** — Root layout. Wraps the entire app in `ThemeProvider` (dark/light mode) and `DisplayPrefsProvider` (global display preferences).

**`app/providers/display-prefs-provider.tsx`** — React Context that exposes:
- `prefs` — current display preferences (timezone, currency)
- `cvtPrice(usd)` — converts a USD amount to the display currency using exchange rates
- `currencySymbol` — the symbol for the active currency (e.g. `£`)
- `fmtTime(iso)` — formats a UTC ISO timestamp in the user's selected timezone via `Intl`

Preferences are persisted to `localStorage` and loaded on mount to avoid SSR hydration mismatches.

**`app/components/dashboard.tsx`** — Main overview page. Fetches from `/api/status`, `/api/config`, `/api/decisions`, `/api/positions`, and `/api/assets`. Uses `fmtTime` and `cvtPrice` from the display prefs context for all visible timestamps and monetary values.

**`app/components/candlestick-chart.tsx`** — Lightweight-charts candlestick chart with:
- `autoSize: true` — chart fills its container width automatically; no manual `ResizeObserver` required
- Real-time data from Binance WebSocket (via backend streaming); `timeScale().fitContent()` called after each history load
- Three price lines: Entry (yellow `#eab308`, dotted), Stop Loss (red, dashed), Take Profit (green, dashed)
- Toggle buttons for each price line
- `autoscaleInfoProvider` that expands the chart's visible range to always include SL/TP levels
- Position info row: entry price, SL with % distance, TP with % distance, R/R ratio
- Timezone-aware time axis via `chart.applyOptions({ localization: { timeFormatter } })`

**`app/settings/page.tsx`** — Flat two-column layout with a single SaveBar:
- **AI Model** column — `model_name`, `interval_minutes`, `min_confidence`
- **Trading** column — `tracked_symbols`, position sizing, risk parameters, paper balance
- **Display** row (full width) — `chart_interval`, `display_currency`, `timezone`
- Bot running: AI Model and Trading fields locked (disabled + lock icon on heading); only Display fields editable. Save sends display values merged onto last saved config.
- Bot stopped: all fields editable; Save sends full config.

**`lib/display-prefs.ts`** — Display preference types, currency/timezone lists, exchange rates, localStorage helpers.

### Display preferences system

**Currency.** The backend always operates in USDT and raw crypto quantities. Every monetary value returned by the API — `wallet_balance`, `entry_price`, `execution_price`, `unrealized_pnl` — is in USD (USDT). No currency conversion occurs server-side. The display currency and its symbol are applied in the browser by `cvtPrice(usd)` from `DisplayPrefsProvider`:

```
Backend:   wallet_balance = 10000.0  (USDT)
Browser:   cvtPrice(10000.0) → 7900.0   currencySymbol → '£'
UI shows:  £7,900.00
```

Exchange rates are hardcoded constants in `lib/display-prefs.ts` — there is no live FX feed.

**Timezone.** All timestamps stored in the database are produced by Python's `datetime.utcnow()`. SQLite persists these as ISO 8601 strings **without a timezone suffix** (e.g. `"2026-04-23T01:34:00"`). JavaScript's `Date` constructor interprets bare ISO strings as *local time* per spec, not UTC — this produces a wrong result on any machine not in the UTC timezone.

`fmtTime` in `DisplayPrefsProvider` corrects this by appending `Z` when no timezone designator is present:

```tsx
// SQLite returns naive ISO strings — JS parses them as local time without this fix.
const utc = /[Z+]/.test(iso) ? iso : iso + 'Z'
return new Date(utc).toLocaleString('en', { timeZone: prefs.timezone, ... })
```

`Z` forces UTC interpretation; `toLocaleString` with the IANA string then converts to the user's chosen timezone. Every component that renders a timestamp calls `fmtTime` — the fix is applied once and propagates to all views.

Changing the display timezone in the dashboard does **not** affect the backend. Python always logs UTC, SQLite always stores naive strings, and the API always returns them unchanged. The `timezone` field in `bot_config` is read by `DisplayPrefsProvider` on load to synchronise preferences across browser sessions; it is never read by any trading service.

---

## Design Rationale

**Single process, single event loop** — deployment is one `uvicorn` command, no message queue, no worker processes. Blocking calls in the hourly cycle run in APScheduler's thread pool and do not block the WebSocket loop.

**In-memory position tracking** — eliminates DB round-trips on every WebSocket tick. The tradeoff is position state is lost on restart. See [persistence.md](persistence.md).

**Kill switch as env var read at call time** — lets an operator halt trading without restarting the process. Set `KILL_SWITCH=true` in the environment and it takes effect on the next risk check.

**Constructor injection** — makes the service graph explicit and unit-testable without mocking module globals.
