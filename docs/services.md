# Services

Each file in `app/services/` has a single responsibility. This document explains each one in depth.

---

## `market_state.py`

### Purpose
In-memory cache of the most recent ticker data for all tracked symbols. Bridge between the WebSocket stream (writer) and the trading cycle (reader).

### Classes

**`SymbolMarketState`** (dataclass) — all numeric fields are `Optional[Decimal]`, starting as `None` until the WebSocket delivers them.

**`MarketStateStore`** — wraps `dict[str, SymbolMarketState]`:
- `update(symbol, *, last_price, bid, ask, ...)` — creates or merges state; only non-`None` kwargs update stored fields
- `get(symbol)` → `SymbolMarketState | None` — case-insensitive lookup
- `all()` → shallow copy of the full dict

### Thread safety
No explicit locking. CPython's GIL serializes dict reads/writes. A strict race between `_build_market_data` reading a snapshot and the next WebSocket tick updating it exists but is harmless — one-tick-stale price in an hourly snapshot is inconsequential.

---

## `binance_ws.py`

### Purpose
Persistent, auto-reconnecting WebSocket to Binance. Updates market state on every tick and triggers real-time stop-loss/take-profit checks.

### Key details

**Stream URL** — built from `settings.tracked_symbols` at connection time (not on init). `run_forever` rebuilds the URL on each reconnect iteration, so symbol changes take effect on the next reconnect. When `PUT /api/bot/config` changes `tracked_symbols`, it calls `asyncio.create_task(ws_service.reconnect())` to force an immediate reconnect.

**Message handling** — `@ticker` stream provides 24h rolling stats. Fields used: `s` (symbol), `c` (last price), `b` (bid), `a` (ask), `q` (quote volume), `P` (24h change %), `h` (24h high), `l` (24h low).

**Exit check** — after updating market state, calls `risk_service.check_exit_conditions(symbol, price)`. If an exit is triggered, pushes the SELL order onto `trigger_queue`.

**Logging** — `db_logger.log_info` on connect; `log_warning` on disconnect or error.

**Reconnect loop:**
```python
while True:
    try:
        async with connect(url, ...) as ws:
            async for message in ws:
                self._handle_message(message)
    except asyncio.CancelledError:
        raise
    except Exception:
        await asyncio.sleep(5)
```

---

## `trigger_executor.py`

### Purpose
Consumes stop-loss and take-profit exit orders from `trigger_queue` without blocking the WebSocket event loop.

### Implementation
```python
async def run_forever(self):
    loop = asyncio.get_event_loop()
    while True:
        order = await self.queue.get()
        await loop.run_in_executor(None, self._execute, order)
        self.queue.task_done()
```

`_execute(order)` runs in a thread pool worker:
1. Fetches balance (paper stub or ccxt)
2. Builds `market_data = {asset: {"last_price": trigger_price}}`
3. Calls `execution_service.execute_decision(order, market_data, balance)`
4. On success, calls `_close_trade(order, result)` if `session_factory` is set
5. Logs result at `WARNING` level

`_close_trade(order, result)` opens a short-lived `SessionLocal` session:
1. Reads `bot_state.current_session_id`
2. Calls `trade_service.get_open_trade_for_symbol(db, session_id, symbol)`
3. Infers `exit_reason` from `order["reasoning"]` (contains "stop" → `stop_loss`, "take/profit" → `take_profit`)
4. Calls `trade_service.close_trade(...)`, commits, closes session

`session_factory` is injected in `main.py`'s lifespan as `SessionLocal` after the DB is initialized.

The position is already removed from `RiskService._positions` before the order reaches the queue — re-triggering on the next tick is impossible.

---

## `data_feeds.py`

### Purpose
ccxt exchange factory and market data fetch helpers.

### Exchange singleton
`ccxt.binance` instance created on first call to `get_exchange()`, reused across all calls. `enableRateLimit=True` enforces Binance rate limits automatically. Without API credentials, only public endpoints work.

### Key functions
- `fetch_ohlcv(symbol, interval, limit)` — fetches `limit` candles at `interval`; returns list of dicts with `open/high/low/close/volume/open_time/close_time`
- `fetch_balance()` — live USDT balance via ccxt; paper stub (from `settings.paper_balance_usdt`) when no API key is set
- `get_all_market_data()` — full ccxt market data; fallback if WebSocket hasn't populated the store

---

## `sentiment_service.py`

### Purpose
Fetches and scores market sentiment from RSS news, Reddit, and the Fear & Greed Index. TTL-cached to avoid re-fetching on every cycle.

### Caches
- `_sentiment_cache: dict[str, tuple[float, AssetSentiment]]` — per-asset `(timestamp, result)`. Stale after 30 minutes.
- `_fear_greed_cache: tuple[float, int | None]` — global `(timestamp, value)`. Stale after 30 minutes.
- `clear_sentiment_cache()` — clears both caches (for testing).

### Data sources

**RSS** (`_fetch_rss`) — 4 feeds (CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine). Filters entries whose `title + summary` contains any asset keyword. TextBlob polarity on matched entries. Returns `(score, headlines)`.

**Reddit** (`_fetch_reddit`) — 3 subreddits (`r/cryptocurrency`, `r/bitcoin`, `r/ethtrader`), `sort=hot, t=day, limit=25`. Upvote-weighted TextBlob polarity. No `time.sleep` — fetches all subreddits concurrently in a `ThreadPoolExecutor(2)` alongside the RSS fetch.

**Fear & Greed** (`_fetch_fear_greed`) — `api.alternative.me/fng/?limit=1`. Converted to `[-1, 1]` via `(value - 50) / 50`. Cached globally.

### Concurrency
`get_asset_sentiment()` uses `ThreadPoolExecutor(2)` to fetch RSS and Reddit in parallel (both are synchronous `requests` calls). `get_all_sentiment()` fetches all symbols concurrently using `ThreadPoolExecutor(4)`.

### Text sanitization
`_sanitize(text, max_len=200)` — strips HTML tags (regex), removes markdown (`**`, `*`, `_`, `` ` ``), removes URLs, normalizes whitespace, caps at `max_len` characters. Applied to all RSS/Reddit text before TextBlob scoring and before inclusion in the AI prompt.

### Score blending
Equal-weight mean of available source scores, clamped to `[-1, 1]`. If a source fails, it is excluded from the mean.

### `AssetSentiment` dataclass
```python
@dataclass
class AssetSentiment:
    symbol:          str
    score:           float          # blended [-1, 1]
    headline_count:  int
    top_headlines:   list[str]      # up to 3, sanitized
    source_scores:   dict[str, float]
    fear_greed_index: int | None    # raw 0-100
```

---

## `ai_service.py`

### Purpose
Calls the Claude API with structured market context and returns validated trading decisions.

### System prompt
`_build_system_prompt()` reads `settings.min_confidence` and `settings.max_position_pct` at call time — no hardcoded thresholds. The prompt instructs Claude to return only a JSON array with a fixed schema.

### User prompt (`_build_prompt`)
Header: `=== TRADING ANALYSIS ({interval} candles) ===` where `interval` is `settings.ohlcv_interval`.

Per symbol:
- Live price / bid / ask
- Period high/low/avg volume/price change (labeled from OHLCV stats, e.g. "24h High" for `1h` interval × 24 candles)
- Last 6 hourly closes as trend line
- Sentiment score + Fear & Greed index
- Up to 3 sanitized headlines
- Current position state: "FLAT — may BUY or HOLD" vs "LONG (entry: $X, P&L: Y%)"
- Previous cycle decision: action / confidence / reasoning

### API call
```python
client.messages.create(
    model=settings.model_name,
    max_tokens=2048,
    system=system_prompt,
    messages=[{"role": "user", "content": prompt}],
)
```

### Response parsing
1. Strip markdown fences if present
2. `re.search(r"\[.*\]", text, re.DOTALL)` to extract JSON array
3. `json.loads()`
4. `_validate_decisions()` — normalizes fields, enforces confidence threshold, fills HOLD defaults for missing symbols
5. Adds `"decision_source": "ai"` to all validated items

---

## `fallback_strategy.py`

### Purpose
Rule-based trading decisions used when the Claude API is unavailable.

### `get_fallback_decisions(market_data, open_positions)`

Returns a complete decision list for all symbols in `market_data`.

Per symbol:
1. Extract candles from `market_data[symbol]["ohlcv"]["candles"]`
2. Require `>= 10` candles (HOLD if fewer — `_MIN_CANDLES = 10`)
3. Compute `SMA5` and `SMA10` from recent closes
4. Volatility guard: `range = (max_high - min_low) / min_low` over last 5 candles; HOLD if range > 5%
5. Entry signal: `price > SMA5 > SMA10`, bullish last candle (`close > open`), `recent_return_pct > 1.5%`
6. Exit signal (existing position only): `price < SMA5`, bearish last candle, `recent_return_pct < -1.5%`
7. Conservative size: `_FALLBACK_SIZE_PCT = 10`
8. All decisions tagged `decision_source: "fallback_rule"`

`recent_return_pct` = `(candles[-1]["close"] / candles[-4]["close"] - 1) * 100` (3-candle return).

---

## `market_validator.py`

### Purpose
Validates and normalizes trade quantities against exchange rules before execution.

### `validate_and_normalize_qty(symbol, qty, price) → (normalized_qty, error_msg or None)`

1. Load market info via `_load_market_info(symbol)` — calls `get_exchange().load_markets()` once (lazy), caches in `_market_cache`
2. If no market info: return `(qty, None)` — permissive
3. Validate `qty >= limits["amount"]["min"]` — return error if below minimum
4. Validate `qty <= limits["amount"]["max"]` if max is set
5. Validate `qty × price >= limits["cost"]["min"]` if min cost is set
6. Normalize qty to `precision["amount"]` decimal places

---

## `execution_service.py`

### Purpose
Executes approved trading decisions in paper or live mode. Validates quantities, models fees, and records all activity.

### Paper mode (`_execute_paper`)
1. Call `validate_and_normalize_qty` — reject if validation fails
2. Determine fill price: `ask` for BUY, `bid` for SELL; fall back to `last_price` with a `db_logger.log_warning`
3. Compute `fee_amount = qty × fill_price × taker_fee_rate`
4. Update paper balance: BUY deducts `(qty × fill_price) + fee_amount`; SELL adds `(qty × fill_price) - fee_amount`
5. Build order dict with `fill_source`, `fee_amount`, `fee_rate`, `status: "paper_filled"`
6. Call `_update_positions`
7. Append to `logs/trades.jsonl`

### Live mode (`_execute_live`)
1. Validate qty
2. Place market order via `get_exchange().create_market_order()`
3. Wait 1 second, fetch order back via `get_exchange().fetch_order()`
4. Set `verification_status` based on fetched order status
5. Call `_update_positions` if order was placed (even if verification failed)

### Position update
```python
def _update_positions(self, asset, action, price, size_pct):
    if action == "BUY":
        self.risk_service.record_open_position(asset, price, size_pct)
    elif action == "SELL":
        self.risk_service.close_position(asset)
```

---

## `risk_service.py`

### Purpose
Tracks open positions in memory and enforces all risk rules before any decision is executed.

### `evaluate_decision` (hourly cycle)

Applied in order:
1. Kill switch (`KILL_SWITCH` env var read at call time) → force HOLD
2. HOLD passthrough
3. Double-entry guard (BUY with existing position) → force HOLD
4. Phantom sell guard (SELL with no position) → force HOLD
5. Confidence threshold → force HOLD
6. Size cap: `size_pct = min(size_pct, max_position_pct)`
7. Exposure cap: for BUY, checks `sum(all position size_pcts) + proposed <= max_total_exposure_pct`

### `check_exit_conditions` (real-time)
```python
drawdown_pct = (entry_price - price) / entry_price * 100
gain_pct     = (price - entry_price) / entry_price * 100

if drawdown_pct >= settings.stop_loss_pct:
    # trigger stop-loss
elif settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
    # trigger take-profit
```

Position is deleted from `_positions` immediately before returning the SELL order — prevents re-triggering on the next tick.

### `filter_decisions` (hourly batch)
Checks hourly stop-losses first, then runs `evaluate_decision` for each AI/fallback decision. Stop-loss SELL takes priority over AI SELL for the same asset.

### `restore_from_db(db)`
Replays the execution log to reconstruct in-memory positions on startup. Most recent filled BUY = open position; most recent filled SELL (or no executions) = flat.

---

## `trading_cycle.py`

### Purpose
Hourly (or configurable-interval) orchestrator. Coordinates OHLCV fetch, sentiment, AI/fallback decisions, risk, execution, and persistence.

### Cycle flow

```
1. Build market_data from MarketStateStore
2. _enrich_and_store_ohlcv():
   - fetch_ohlcv(symbol, settings.ohlcv_interval) per symbol via ccxt
   - upsert candles to ohlcv_candles table
   - attach candle list to market_data[symbol]["ohlcv"]
3. _fetch_sentiment(): TTL cache hit or concurrent fetch
4. _fetch_decisions():
   - Try ai_service.get_trading_decisions()
   - On exception: log ai_request_failed, call fallback_strategy.get_fallback_decisions()
5. risk_service.filter_decisions()
6. _fetch_balance()
7. For each decision: execution_service.execute_decision(), _persist_cycle()
8. db_logger.log_info("cycle_end", ...)
```

### `_persist_cycle` — database write sequence

```
1. HourlyMarketSnapshot (session_id stamped)  →  flush  →  snapshot.id
2. Position              →  flush  →  position.id
3. execute_decision() called here (side effect: updates RiskService._positions)
4. AIDecision (with decision_source)  →  flush  →  ai_rec.id
5. Execution (session_id stamped)  →  flush  →  execution.id
6. Trade lifecycle:
   - BUY filled → trade_service.open_trade() → Trade row created
     execution.trade_id = trade.id
   - SELL filled → trade_service.get_open_trade_for_symbol() + close_trade()
     exit_reason inferred from decision reasoning text
7. db.commit()
```

### `_get_or_create_asset`
Queries DB for existing `Asset` by symbol; inserts if not found. Called inside `try/except IntegrityError` to handle concurrent cycle runs.

---

## `session_service.py`

### Purpose
Manages `TradingSession` lifecycle — create on bot start, close on bot stop, re-link on server restart.

### API

```python
create_session(db, starting_balance_usdt) → TradingSession
```
Creates a new `TradingSession` with `status="active"`, stamps `starting_balance_usdt`, and serializes the current `BotConfig` row into `notes` as a JSON snapshot. Commits and returns the refreshed row.

```python
close_session(db, session_id, ending_balance_usdt, status="stopped") → TradingSession | None
```
Sets `ended_at`, `status`, and `ending_balance_usdt`. Commits.

```python
get_active_session(db) → TradingSession | None
```
Returns the most recent session with `status="active"`. Used on server restart to re-link `bot_state.current_session_id`.

---

## `trade_service.py`

### Purpose
Manages `Trade` lifecycle — open on BUY execution, close on SELL/SL/TP.

### API

```python
open_trade(db, session_id, symbol, entry_execution_id, entry_price, entry_qty,
           entry_fee_usdt, size_pct, opened_at, stop_loss_price, take_profit_price)
    → Trade
```
Creates a `Trade` with `status="open"`, `user_id=1`. Flushes (does not commit — caller handles commit).

```python
close_trade(db, trade_id, exit_execution_id, exit_price, exit_fee_usdt,
            exit_reason, closed_at) → Trade | None
```
Fills exit fields. Computes:
- `realized_pnl_pct` = `(exit_price − entry_price) / entry_price × 100`
- `realized_pnl_usdt` = `qty × (exit_price − entry_price) − total_fees`

Flushes (caller commits). No-op if already closed.

```python
get_open_trade_for_symbol(db, session_id, symbol) → Trade | None
```
Finds the most recent `open` trade for the given symbol/session. Filters by `session_id` if provided.

```python
get_session_stats(db, session_id) → dict
```
Returns `{ total_trades, open_trades, winning_trades, losing_trades, win_rate, total_pnl_usdt, total_fees_usdt }` aggregated from the session's trades.

---

## `db_logger.py`

### Purpose
Structured event logging to the `system_logs` database table.

### API
```python
log_info(component, event_type, message, *, symbol=None, cycle_id=None, details=None)
log_warning(component, event_type, message, *, symbol=None, cycle_id=None, details=None)
log_error(component, event_type, message, *, symbol=None, cycle_id=None, details=None)
log_event(level, component, event_type, message, ...)   # underlying function
```

### Session isolation
Each call opens its own `SessionLocal()`, commits, and closes. This ensures log entries persist even when the caller's main transaction rolls back (e.g. duplicate snapshot `IntegrityError`).

`details` (dict) is serialized to `details_json` via `json.dumps`. If serialization fails, `details_json` is left null.
