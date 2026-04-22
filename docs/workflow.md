# Runtime Workflow

This document describes exactly what happens at each stage of the bot's lifetime, from process start to individual trade execution.

---

## 1. Startup

### 1.1 Module-level initialization (`main.py`)

Before `lifespan` runs, Python imports `main.py` and executes the module body. This creates all service singletons:

```python
trigger_queue     = asyncio.Queue()        # shared between WS and TriggerExecutor
market_store      = MarketStateStore()     # empty dict
risk_service      = RiskService()          # empty _positions dict
execution_service = ExecutionService(risk_service)
trading_cycle     = TradingCycleService(market_store, risk_service, execution_service)
ws_service        = BinanceWebSocketService(symbols, market_store, risk_service, trigger_queue)
trigger_executor  = TriggerExecutor(trigger_queue, execution_service)
scheduler         = AsyncIOScheduler(timezone="UTC")
```

At this point no I/O has occurred and no async loop is running.

### 1.2 FastAPI lifespan: `startup`

Uvicorn starts the asyncio event loop and calls the `lifespan` context manager's `__aenter__`:

**Step 1 — Database initialization**
```python
init_db()
```
Calls `Base.metadata.create_all(bind=engine)`. Creates all five SQLite tables if they do not exist. Existing data is preserved (not dropped). This is a synchronous call.

**Step 2 — WebSocket task**
```python
asyncio.create_task(ws_service.run_forever())
```
Schedules the WebSocket loop as a background asyncio task. It begins connecting to Binance immediately (before the hourly cycle fires).

**Step 3 — Trigger executor task**
```python
asyncio.create_task(trigger_executor.run_forever())
```
Schedules the queue drain loop as a background asyncio task. It immediately awaits `queue.get()` — suspended until the first exit order arrives.

**Step 4 — Hourly cycle scheduler**
```python
scheduler.add_job(
    run_hourly_cycle,
    trigger=IntervalTrigger(hours=1),
    next_run_time=datetime.now(timezone.utc),   # fire immediately
)
scheduler.start()
```
The `next_run_time=now` means the **first cycle fires at startup**, not one hour later. Subsequent cycles run every 60 minutes from that point.

**Step 5 — Yield**
FastAPI yields control back to Uvicorn. The server is now accepting HTTP requests and the three background tasks are running.

---

## 2. Continuous Operation: WebSocket Loop

This runs forever in the background (task 1).

### 2.1 Connection

```python
async with connect(url, ping_interval=20, ping_timeout=60) as ws:
    async for message in ws:
        self._handle_message(message)
```

`ping_interval=20` sends WebSocket pings every 20 seconds. `ping_timeout=60` disconnects if no pong is received within 60 seconds. On any exception (network error, timeout, disconnect), the outer `while True` catches it, logs it, sleeps 5 seconds, and reconnects.

### 2.2 Per-tick message handling (`_handle_message`)

Every Binance `@ticker` message delivers a full 24h statistics snapshot for one symbol. The combined-stream envelope looks like:

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

Processing steps:
1. `json.loads(message)` → parse envelope
2. `payload.get("data", payload)` → extract the `data` sub-object (handles both combined-stream and direct-stream formats)
3. Extract symbol from `data["s"]`
4. Parse all numeric fields as `Decimal`
5. Call `market_store.update(symbol, last_price=..., bid=..., ...)`
6. If `last_price` is available and `risk_service` is wired: call `risk_service.check_exit_conditions(symbol, float(last_price))`
7. If exit is triggered: `trigger_queue.put_nowait(order)`

`put_nowait()` is used (non-blocking) because `_handle_message` is a synchronous method called within the async loop. The queue is unbounded, so `put_nowait` never raises `QueueFull`.

### 2.3 Exit condition check

`risk_service.check_exit_conditions(symbol, price)` is called on **every tick** for every tracked symbol. This is inexpensive — it's a dict lookup plus two float comparisons:

```python
pos = self._positions.get(symbol.upper())
if pos is None:
    return None  # fast path: no position open

drawdown_pct = (pos.entry_price - price) / pos.entry_price * 100
gain_pct     = (price - pos.entry_price) / pos.entry_price * 100

if drawdown_pct >= settings.stop_loss_pct:
    # stop-loss triggered
elif settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
    # take-profit triggered
```

If triggered:
1. The position is **deleted immediately** from `_positions` (prevents re-triggering on next tick)
2. A SELL order dict is returned and placed on `trigger_queue`

### 2.4 Trigger executor processes the exit

The `TriggerExecutor` wakes on `await self.queue.get()`, then:
1. Dispatches `self._execute(order)` to a thread pool (`loop.run_in_executor`)
2. Calls `execution_service.execute_decision(order, market_data, balance)`
3. `market_data` is constructed as `{asset: {"last_price": trigger_price}}`
4. `balance` is fetched fresh for each execution (paper stub or ccxt)
5. The result is logged at `WARNING` level

This means exit executions happen **out-of-band** from the hourly cycle. They can fire at any price tick, independent of the cycle schedule.

---

## 3. Hourly Trading Cycle

This runs once per hour (and once immediately at startup) in a thread pool worker managed by APScheduler. The entry point is `run_hourly_cycle()` in `main.py`:

```python
def run_hourly_cycle() -> None:
    db = SessionLocal()
    try:
        trading_cycle_service.run(db)
    except Exception:
        logger.exception("Unhandled error in hourly trading cycle")
    finally:
        db.close()
```

A fresh `SessionLocal` is opened for each cycle and closed in the `finally` block.

### Step 1 — Build market data

`TradingCycleService._build_market_data()` converts the current `MarketStateStore` state into a flat dict:

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
      "price_change_pct_24h": -0.842
    },
    "orderbook": {}
  },
  ...
}
```

If `market_store.all()` is empty (WebSocket hasn't connected yet), the method returns `{}` and the cycle logs a warning and returns early — no AI call, no persistence.

### Step 2 — Fetch sentiment

`get_all_sentiment(symbols)` is called. This:
1. Fetches the Fear & Greed Index once (shared across all symbols)
2. For each symbol, calls `get_asset_sentiment(symbol, fear_greed)`
3. Each `get_asset_sentiment` fetches RSS and Reddit, scores with TextBlob, and blends the three source scores

This call is wrapped in a try/except — if sentiment fails, the cycle proceeds with `sentiment_data = {}` (Claude sees "Sentiment: unavailable").

### Step 3 — Fetch AI decisions

`get_trading_decisions(market_data, sentiment_data)` calls Claude:

1. `_build_prompt()` constructs the user message (see [trading_logic.md](trading_logic.md))
2. `client.messages.create(model=..., max_tokens=2048, system=SYSTEM_PROMPT, messages=[...])` is called synchronously
3. Response text is extracted, JSON stripped of markdown, parsed
4. `_validate_decisions()` validates, enforces confidence threshold, fills in HOLDs for missing symbols

If the API call fails, the cycle defaults all symbols to `HOLD` with `confidence=0.0` and a "AI service unavailable" reasoning.

### Step 4 — Filter through risk

`risk_service.filter_decisions(decisions, market_data)` runs two checks:

1. `check_stop_losses(market_data)` — for any open positions, checks if the current price breaches stop-loss or take-profit thresholds. Returns a list of SELL orders. Assets with stop-loss triggers skip step 2 and go straight to execution.

2. For each remaining decision, `evaluate_decision(decision, current_price)` checks:
   - Kill switch (if active → HOLD)
   - BUY while already holding → HOLD
   - SELL with no open position → HOLD
   - Confidence below threshold → HOLD
   - Size cap enforcement (cap at `MAX_POSITION_PCT`)
   - Total exposure cap (reduce size to available headroom or → HOLD if none)

The method returns a new list of decisions with actions and sizes adjusted by the risk rules.

### Step 5 — Fetch balance

`fetch_balance()` returns either the paper balance stub (`{"USDT": {"free": 10000, "used": 0, "total": 10000}}`) or the live ccxt balance. Used to compute trade quantities.

### Step 6 — Execute each decision

For each decision in the filtered list, `execution_service.execute_decision(decision, {symbol: md}, balance)` is called. This happens inside `_persist_cycle()`, not before it, so the execution result is available for persistence.

**HOLD**: The record is logged to `trades.jsonl` with `order=None`.

**BUY/SELL (paper)**: Computes `qty = portfolio * size_pct% / price`, builds synthetic order, updates position tracking in `RiskService`.

**BUY/SELL (live)**: Places market order via ccxt, uses fill price from `order["average"]`.

### Step 7 — Persist to database

For each decision, `_persist_cycle()` writes four records in sequence:

```
HourlyMarketSnapshot  →  flush (get snapshot.id)
Position              →  flush (get position.id)
[execute decision]    →  get exec_result
AIDecision            →  flush (get ai_rec.id)
Execution             →  commit
```

`db.flush()` is used after each insert to obtain the auto-generated primary key before the next insert needs it as a foreign key. The final `db.commit()` writes all four records atomically.

If a snapshot for the same `(asset_id, snapshot_time)` already exists (e.g. APScheduler fired twice), `IntegrityError` is caught, the session is rolled back, and the cycle continues with the next symbol.

---

## 4. Shutdown

When Uvicorn receives `SIGTERM` (or the process is killed), the `lifespan` context manager's `__aexit__` runs:

```python
scheduler.shutdown(wait=False)
logger.info("Bot stopped")
```

`wait=False` means APScheduler stops immediately without waiting for a running job to finish. The two asyncio tasks (`ws_service.run_forever`, `trigger_executor.run_forever`) are cancelled by the event loop shutdown. Positions in `RiskService._positions` are lost.

---

## Timeline: First 5 Minutes

```
t=0s    Process starts
        Module-level singletons created
        Lifespan enters

t=0s    init_db() runs — tables created/verified
t=0s    WebSocket task created — begins connecting to Binance
t=0s    TriggerExecutor task created — awaiting queue items
t=0s    APScheduler starts — first cycle job queued with next_run_time=now

t~1s    Binance WebSocket connected
        First @ticker messages arrive, MarketStateStore populated

t~1s    First hourly cycle fires (APScheduler job runs)
        Sentiment fetch begins (may take 10-30s for RSS + Reddit)
        Claude API called
        Risk filter applied
        Decisions executed (paper)
        DB records written

t~30s   First cycle complete

t=1h    Second cycle fires
        ...
```

If the WebSocket hasn't connected before the first cycle fires (very unlikely on a fast network), `_build_market_data()` returns empty and the cycle skips with a warning. The next hourly cycle will succeed once the WebSocket is connected.
