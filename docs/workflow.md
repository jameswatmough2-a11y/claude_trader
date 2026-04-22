# Runtime Workflow

This document describes exactly what happens at each stage of the bot's life, from process start to individual trade execution.

---

## 1. Startup Sequence

When you run `uvicorn app.main:app`, the following happens in order:

### 1.1 Module-level instantiation (before lifespan)

`main.py` runs its top-level code immediately when the module is imported:

```python
trigger_queue  = asyncio.Queue()
market_store   = MarketStateStore()
risk_service   = RiskService()
execution_service    = ExecutionService(risk_service=risk_service)
trading_cycle_service = TradingCycleService(...)
ws_service     = BinanceWebSocketService(..., risk_service=risk_service, trigger_queue=trigger_queue)
trigger_executor = TriggerExecutor(queue=trigger_queue, execution_service=execution_service)
scheduler      = AsyncIOScheduler(timezone="UTC")
```

At this point no I/O has happened. All objects exist in memory.

### 1.2 FastAPI lifespan starts

The `lifespan` async context manager runs:

**Step 1 — Database initialisation**
```python
init_db()
```
`init_db()` calls `Base.metadata.create_all(bind=engine)`. SQLAlchemy inspects all registered ORM models and creates their tables if they don't already exist. If the database file doesn't exist, SQLite creates it. This is idempotent — safe to run on every restart.

**Step 2 — WebSocket task**
```python
asyncio.create_task(ws_service.run_forever())
```
Spawns a background async task. The WebSocket does not block startup — it begins connecting concurrently while the rest of startup continues.

**Step 3 — Trigger executor task**
```python
asyncio.create_task(trigger_executor.run_forever())
```
Spawns another background async task. This immediately begins `await self.queue.get()` — it blocks asynchronously, waiting for the first exit order to appear.

**Step 4 — Scheduler**
```python
scheduler.add_job(
    run_hourly_cycle,
    trigger=IntervalTrigger(hours=1),
    next_run_time=datetime.now(timezone.utc),
)
scheduler.start()
```
`next_run_time=now` means the first cycle is scheduled to run in the next event loop iteration. Practically it fires within a second or two of startup completing.

**Step 5 — Startup complete**

FastAPI logs `Application startup complete`. The server is now accepting HTTP requests.

---

## 2. WebSocket Loop (Continuous)

The WebSocket loop runs for the entire lifetime of the process. Here is what happens on each price tick:

### 2.1 Receiving a message

Binance sends a JSON payload for each symbol roughly every second. The combined stream format wraps the data:

```json
{
  "stream": "btcusdt@ticker",
  "data": {
    "s": "BTCUSDT",
    "c": "93142.50",    ← last price
    "b": "93140.00",    ← best bid
    "a": "93145.00",    ← best ask
    "h": "94200.00",    ← 24h high
    "l": "92100.00",    ← 24h low
    "q": "1482934200",  ← 24h quote volume
    "P": "-0.42"        ← 24h price change %
  }
}
```

### 2.2 Updating MarketStateStore

`BinanceWebSocketService._handle_message()` extracts the `data` field and calls:

```python
self.market_store.update(
    symbol=symbol,
    last_price=Decimal(data["c"]),
    bid=Decimal(data["b"]),
    ...
)
```

`MarketStateStore.update()` upserts a `SymbolMarketState` entry. If the symbol already exists, it updates only the fields provided and refreshes `updated_at`. This is a simple dict write — no I/O.

### 2.3 Real-time exit check

After updating the store, the WebSocket handler checks for exit conditions:

```python
order = self._risk_service.check_exit_conditions(symbol, float(data["c"]))
if order:
    self._trigger_queue.put_nowait(order)
```

`check_exit_conditions()` looks up whether there is an open position for this symbol, computes the drawdown or gain since entry, and compares against `STOP_LOSS_PCT` and `TAKE_PROFIT_PCT`. If either threshold is breached:

1. The position is **immediately removed** from `_positions` — this prevents the same position from triggering again on the very next tick before the order has been filled.
2. A SELL order dict is returned containing `trigger_price`.
3. `put_nowait()` places it on the queue without blocking.

If no threshold is breached, `check_exit_conditions()` returns `None` and nothing else happens.

### 2.4 Error handling and reconnection

If the WebSocket connection drops or throws any exception, `run_forever()` catches it, logs the error, and reconnects after a 5-second delay:

```python
except Exception as exc:
    logger.exception("Binance WebSocket error — reconnecting in 5s: %s", exc)
    await asyncio.sleep(5)
```

`asyncio.CancelledError` is re-raised so the task can be cleanly shut down by the lifespan exit.

---

## 3. Real-Time Exit Execution

When an order lands on the queue, `TriggerExecutor.run_forever()` picks it up:

```python
order = await self.queue.get()
await loop.run_in_executor(None, self._execute, order)
self.queue.task_done()
```

`run_in_executor(None, ...)` runs `_execute` in the default thread pool. This is critical — `_execute` may call ccxt (HTTP) and write to files, both of which are blocking operations that would stall the async event loop if called directly.

Inside `_execute`:

1. Fetch the current balance (`fetch_balance()` — uses paper constant if no API key).
2. Build a minimal `market_data` dict: `{asset: {"last_price": trigger_price}}`.
3. Call `self.execution_service.execute_decision(order, market_data, balance)`.
4. The execution service handles paper/live logic, position state update, and file logging.

The trigger executor does **not** write to the database. The next hourly cycle will capture the updated position state (flat) when it persists its snapshot.

---

## 4. Hourly Trading Cycle

The cycle runs in the APScheduler thread (sync context). Each run goes through six phases:

### Phase 1 — Build market data

```python
market_data = self._build_market_data()
```

Iterates `MarketStateStore.all()` and converts each `SymbolMarketState` into a dict. Only symbols with a non-None `last_price` are included — if the WebSocket hasn't received a tick for a symbol yet, it's skipped. No HTTP calls are made here.

If `market_data` is empty (WebSocket not yet connected), the cycle logs a warning and returns early.

### Phase 2 — Sentiment

```python
sentiment_data = self._fetch_sentiment(symbols)
```

Calls `get_all_sentiment(symbols)` which fetches the Fear & Greed index once, then for each symbol fetches and scores RSS headlines and Reddit posts. This typically takes 20–60 seconds depending on network conditions.

Internally, each RSS feed is parsed with `feedparser`. Each entry's title and summary are combined and run through `TextBlob(text).sentiment.polarity`, which returns a float in `[-1, 1]`. Entries are matched to a symbol using keyword lists (e.g. `["bitcoin", "btc"]` for BTCUSDT). Matched scores are averaged per source. The Fear & Greed value (0–100) is normalised to `[-1, 1]` as `(value - 50) / 50`. All source scores are averaged into a final blended score.

If any source fails (network error, API down), it's skipped and the others continue. If all sources fail, the sentiment dict is empty and Claude is told "sentiment unavailable".

### Phase 3 — AI decisions

```python
decisions = get_trading_decisions(market_data, sentiment_data)
```

`_build_prompt()` formats the market and sentiment data into a plain-text prompt, one section per symbol. The prompt is sent to Claude with `SYSTEM_PROMPT` which instructs it to return only a JSON array.

Claude's response is parsed with `_extract_json()` which strips any markdown fences and uses a regex to locate the `[...]` array. The raw JSON is passed to `_validate_decisions()` which:

- Skips items missing required keys
- Uppercases asset and action
- Clamps confidence to `[0, 1]` and `size_pct` to `[0, 20]`
- Forces HOLD if `confidence < 0.7` (regardless of what Claude said)
- Adds a default HOLD for any tracked symbol absent from Claude's response

If the Claude API call fails entirely, all symbols default to HOLD with a note in the reasoning field.

### Phase 4 — Risk filtering

```python
filtered = self.risk_service.filter_decisions(decisions, market_data)
```

First runs `check_stop_losses(market_data)` — a batch version of the real-time exit check, in case a stop-loss was triggered between WebSocket ticks and wasn't caught by the real-time path. Stop-loss decisions take priority over whatever Claude returned.

Then for each non-stop-loss decision, calls `evaluate_decision()`:

| Check | Result |
|---|---|
| Kill switch active | HOLD |
| action == HOLD | pass through as HOLD |
| BUY and already holding | HOLD (entry guard) |
| SELL and not holding | HOLD (exit guard) |
| confidence < MIN_CONFIDENCE | HOLD |
| size_pct > MAX_POSITION_PCT | cap size_pct |
| BUY and total exposure at cap | HOLD |
| otherwise | approve with adjusted size |

### Phase 5 — Execution

```python
exec_result = self.execution_service.execute_decision(decision, {symbol: md}, balance)
```

Called per decision inside `_persist_cycle`. The execution service:

1. Computes order quantity: `portfolio_usdt * (size_pct / 100) / current_price`
2. In paper mode: constructs a synthetic order, calls `risk_service.record_open_position()` or `close_position()`, appends to `logs/trades.jsonl`
3. In live mode: calls `ccxt.binance.create_market_order()`, uses the filled price for position tracking

HOLD decisions skip quantity computation and only log the decision.

### Phase 6 — Persistence

For each symbol, the cycle writes four rows to SQLite:

1. **`HourlyMarketSnapshot`** — price, volume, high/low at the time of the cycle
2. **`Position`** — current wallet balance snapshot (the position side is always "flat" at snapshot time since actual open positions are tracked in memory)
3. **`AIDecision`** — the approved decision: action, confidence, reasoning, model name
4. **`Execution`** — the order result: action taken, quantity, price, status

Each symbol is committed independently. If one symbol fails (duplicate snapshot due to a re-run, DB error, etc.), the session is rolled back for that symbol only and the others continue.

---

## 5. Shutdown

When the process receives SIGTERM or CTRL+C:

1. FastAPI triggers the lifespan exit (the `yield` returns).
2. `scheduler.shutdown(wait=False)` stops the scheduler immediately without waiting for a running job.
3. The WebSocket task and trigger executor task are cancelled by the asyncio event loop.
4. Uvicorn exits.

Any in-flight cycle or trigger execution that was running in a thread pool will complete naturally — thread pool tasks are not forcibly killed.
