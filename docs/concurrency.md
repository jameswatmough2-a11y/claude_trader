# Concurrency

The bot runs three concurrent execution paths inside a single Python process. Understanding how they interact — and where race conditions exist — is essential for reasoning about system behavior.

---

## The Three Loops

```
Thread: asyncio event loop (main thread)
  ├── Task 1: BinanceWebSocketService.run_forever()    [continuous, async]
  └── Task 2: TriggerExecutor.run_forever()            [continuous, async]

Thread pool: APScheduler workers
  └── run_hourly_cycle()                               [every 60 min, sync]
```

All three share access to `RiskService._positions` and `MarketStateStore._state`.

---

## Loop 1: WebSocket Task (Async, Continuous)

**Entry point:** `asyncio.create_task(ws_service.run_forever())`

**Runs:** In the asyncio event loop, continuously. Each `@ticker` message is processed synchronously inside `_handle_message()`. The async `async for message in ws` loop yields control between messages, but `_handle_message` itself runs to completion without yielding.

**What it does per tick:**
1. Calls `market_store.update(...)` — dict write
2. Calls `risk_service.check_exit_conditions(symbol, price)` — dict read + possible delete
3. Calls `trigger_queue.put_nowait(order)` if exit triggered

**Frequency:** Binance sends `@ticker` updates approximately every second per symbol for active markets.

---

## Loop 2: Trigger Executor Task (Async, Queue-Driven)

**Entry point:** `asyncio.create_task(trigger_executor.run_forever())`

**Runs:** In the asyncio event loop. Suspended on `await self.queue.get()` until an item arrives.

**What it does per item:**
1. Dispatches `self._execute(order)` to thread pool via `await loop.run_in_executor(None, ...)`
2. Calls `execution_service.execute_decision(...)` in the thread — this may make blocking HTTP calls (ccxt in live mode)
3. Calls `risk_service.close_position(asset)` as a side effect of execution

**Concurrency characteristic:** The executor processes one order at a time (no concurrent dispatches). If multiple exit orders queue up, they are processed sequentially.

---

## Loop 3: Hourly Cycle (Sync, Thread Pool)

**Entry point:** APScheduler calls `run_hourly_cycle()` from its thread pool

**Runs:** In a thread pool worker, not in the asyncio event loop. This is a regular synchronous function.

**What it does:**
1. Reads `market_store.all()` — dict read
2. Calls `risk_service.filter_decisions(...)` — dict read, possible dict delete (stop-loss)
3. Calls `execution_service.execute_decision(...)` — may make HTTP calls
4. Writes to SQLite — uses a fresh `SessionLocal()`

**Frequency:** Every 60 minutes, first run at startup (with `next_run_time=datetime.now()`).

---

## Shared Mutable State

### `MarketStateStore._state`

| Loop | Access type | Frequency |
|---|---|---|
| WebSocket task | Write | ~1/second per symbol |
| Hourly cycle (thread) | Read | Once per hour |

**Race condition:** Yes — the hourly cycle reading `market_store.all()` while the WebSocket is writing to it is a data race under strict concurrent access rules. In practice:
- CPython's GIL means dict operations are atomic at the bytecode level
- A one-tick-stale read from the hourly cycle is harmless (a few milliseconds of staleness in an hourly cycle is irrelevant)
- No locking is implemented and none is needed for this use case

### `RiskService._positions`

| Loop | Access type | When |
|---|---|---|
| WebSocket task | Read + Delete | Every tick for every symbol with a position |
| TriggerExecutor (thread pool) | Delete (via `execution_service`) | When processing exit order |
| Hourly cycle (thread) | Read + Delete + Write | Each cycle |

**Race condition — stop-loss double-execution:**

The most important concurrency concern in the system. Consider this sequence:

```
t=0ms: WebSocket tick arrives for BTCUSDT at price $64,000 (below stop-loss)
t=0ms: check_exit_conditions("BTCUSDT", 64000) called in event loop
t=0ms: Position found in _positions, drawdown >= STOP_LOSS_PCT
t=0ms: del _positions["BTCUSDT"]  ← position removed immediately
t=0ms: SELL order returned and placed on trigger_queue
t=1ms: Next WebSocket tick arrives for BTCUSDT
t=1ms: check_exit_conditions("BTCUSDT", 63990) called
t=1ms: _positions.get("BTCUSDT") returns None  ← position already removed
t=1ms: returns None — no duplicate trigger
```

The position deletion in `check_exit_conditions` before returning the order is what prevents double-execution on rapid ticks. This is a deliberate design choice.

**Race condition — WebSocket vs hourly cycle:**

```
t=0ms:   Hourly cycle starts
t=0ms:   cycle reads _positions = {"BTCUSDT": pos}
t=50ms:  WebSocket tick triggers stop-loss for BTCUSDT
t=50ms:  del _positions["BTCUSDT"]
t=50ms:  SELL order on trigger_queue
t=51ms:  TriggerExecutor processes SELL — position closed in execution service
t=100ms: Hourly cycle calls filter_decisions()
t=100ms: check_stop_losses(market_data) — _positions now empty, nothing triggered
t=100ms: evaluate_decision(BTCUSDT, action=HOLD) — HOLD passes through
t=100ms: execute_decision(HOLD) — no action
```

This scenario is safe: the stop-loss was already handled by the real-time path. The hourly cycle simply sees no open position and acts accordingly.

**Race condition — TriggerExecutor vs hourly cycle (simultaneous execution):**

Both the TriggerExecutor thread and the hourly cycle thread can call `execution_service.execute_decision()` for the same asset at the same time. The shared state they modify is `RiskService._positions`.

```
TriggerExecutor thread:  risk_service.close_position("BTCUSDT")
Hourly cycle thread:     risk_service.record_open_position("BTCUSDT", price, size_pct)
```

Under CPython's GIL, these dict operations do not interleave mid-operation, but they can execute in either order if they run "simultaneously" on the thread pool. The worst case:

1. TriggerExecutor closes the position
2. Hourly cycle opens a new position (BUY) for the same asset

This would result in a new BUY on an asset that was just stop-loss-exited in the same tick window. The double-entry guard in `evaluate_decision` only blocks BUY if the position exists — since it was just closed, the guard would not fire.

In practice, this scenario is extremely unlikely because:
- The stop-loss fires in the WebSocket task (loop) before the order reaches the TriggerExecutor
- The hourly cycle and TriggerExecutor rarely overlap in time (cycles are infrequent)
- If both run simultaneously, the next cycle will have the correct position state

This is a known prototype limitation. A production system would use a lock around position state modifications.

---

## Asyncio Event Loop: What Blocks It

The asyncio event loop must never be blocked by slow synchronous operations. In this system:

**Safe (does not block the loop):**
- `_handle_message()` — pure Python dict operations and Decimal parsing, completes in microseconds
- `trigger_queue.put_nowait()` — non-blocking queue append
- `await self.queue.get()` — suspends the coroutine without blocking the loop
- `await loop.run_in_executor()` — dispatches to thread pool and suspends

**Dangerous if done wrongly:**
- HTTP calls in `_handle_message` would block the loop (never done)
- `time.sleep()` in `_handle_message` would block the loop (never done)
- Calling synchronous ccxt methods directly in a coroutine would block the loop

**How sentiment HTTP calls are handled:**
`sentiment_service.py` uses synchronous `requests.get()` and `feedparser.parse()`. These run in the hourly cycle, which APScheduler dispatches to a thread pool worker. The event loop is never touched by sentiment fetching.

Note: `sentiment_service.py` contains `time.sleep(1)` between Reddit subreddit requests. This sleep runs in the thread pool worker, not in the event loop — it is safe but does slow down the hourly cycle by approximately 3 seconds (3 subreddits × 1 second each).

---

## APScheduler: Sync vs Async

`AsyncIOScheduler` is used (not `BackgroundScheduler`). The `run_hourly_cycle` function is a **synchronous** function, not a coroutine.

When APScheduler runs a synchronous callable with `AsyncIOScheduler`, it dispatches it to the default executor (a thread pool), not directly in the event loop. This is correct behavior — it means the hourly cycle does not block the WebSocket task.

If `run_hourly_cycle` were accidentally made `async`, APScheduler would run it as a coroutine in the event loop, and any blocking calls (ccxt REST, sentiment HTTP) would block the WebSocket.

---

## Shutdown Behavior

When Uvicorn receives `SIGTERM`:
1. FastAPI lifespan `__aexit__` runs: `scheduler.shutdown(wait=False)`
2. `wait=False` means APScheduler stops immediately, without waiting for a running job
3. If a trading cycle is mid-execution: it may be interrupted mid-persist. The SQLite session is not committed.
4. The asyncio event loop cancels all pending tasks (including WebSocket and TriggerExecutor)
5. `asyncio.CancelledError` propagates through both `run_forever()` loops cleanly (they re-raise it)

**Risk of data corruption on shutdown:** If shutdown occurs between the `db.flush()` calls in `_persist_cycle()` and the final `db.commit()`, no data is written (SQLite transactions are atomic). The cycle is simply lost — no partial write. The next startup will create a new cycle record.
