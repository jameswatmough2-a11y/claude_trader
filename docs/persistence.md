# Persistence

This document explains what data is stored in the database, what is kept only in memory, and what happens to each type of state on process restart.

---

## Database: SQLite via SQLAlchemy ORM

The database is a single SQLite file at the path configured by `DATABASE_URL` (default: `crypto_bot/crypto_bot.db`).

Tables are created on startup by `init_db()` → `Base.metadata.create_all(bind=engine)`. Existing tables are not modified or dropped — schema changes require manual migration.

### Session management

`app/db/session.py`:
```python
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite with threads
)
SessionLocal = sessionmaker(autoflush=False, autocommit=False, bind=engine)
```

`check_same_thread=False` is needed because APScheduler runs the hourly cycle in a thread pool, while SQLAlchemy's default assumes the connection is used from a single thread.

`autoflush=False` means SQLAlchemy will not automatically flush the session before queries. The `_persist_cycle` method calls `db.flush()` explicitly after each insert to obtain generated primary keys.

A new `SessionLocal()` instance is created for each hourly cycle in `run_hourly_cycle()` and closed in the `finally` block. The FastAPI routes get a session per-request via the `get_db()` dependency.

---

## ORM Models

### `Asset`

```
assets
├── id            INTEGER   PRIMARY KEY
├── symbol        VARCHAR   UNIQUE, INDEXED (e.g. "BTCUSDT")
├── base_currency VARCHAR   (e.g. "BTC")
└── quote_currency VARCHAR  (always "USDT")
```

Created once per symbol on first appearance in a trading cycle. The `_get_or_create_asset` method queries by `symbol` and inserts if not found.

Relationships:
- `snapshots` → list of `HourlyMarketSnapshot` (cascade delete)
- `positions` → list of `Position` (cascade delete)

---

### `HourlyMarketSnapshot`

```
hourly_market_snapshots
├── id                         INTEGER   PRIMARY KEY
├── asset_id                   INTEGER   FK → assets.id
├── snapshot_time              DATETIME  (timezone-aware)
├── open_price                 NUMERIC(20,8)
├── high_price                 NUMERIC(20,8)
├── low_price                  NUMERIC(20,8)
├── close_price                NUMERIC(20,8)
├── volume                     NUMERIC(30,8)
├── price_change_1h_pct        NUMERIC(10,4)  NULLABLE
└── price_change_since_entry_pct NUMERIC(10,4) NULLABLE
```

Unique constraint: `(asset_id, snapshot_time)` — prevents duplicate snapshots for the same symbol in the same cycle.

**What the OHLC columns actually contain:**

Despite being named `open_price`, `high_price`, `low_price`, `close_price`, all four are set to `last_price` from the WebSocket at cycle time. The `high_price` and `low_price` use `high_24h` / `low_24h` from the WebSocket state (24-hour range, not the candle for the last hour).

`price_change_1h_pct` and `price_change_since_entry_pct` are always `None` — the computation for these fields was not implemented.

Relationships:
- `asset` → `Asset` (many-to-one)
- `position` → `Position` (one-to-one, cascade delete)
- `ai_decision` → `AIDecision` (one-to-one, cascade delete)

---

### `Position`

```
positions
├── id              INTEGER    PRIMARY KEY
├── snapshot_id     INTEGER    FK → hourly_market_snapshots.id, UNIQUE
├── asset_id        INTEGER    FK → assets.id
├── side            VARCHAR    ("flat", "long", "short")
├── size            NUMERIC(20,8)
├── entry_price     NUMERIC(20,8)  NULLABLE
├── unrealized_pnl  NUMERIC(20,8)  NULLABLE
└── wallet_balance  NUMERIC(20,8)  NULLABLE
```

One `Position` record is written per snapshot (per symbol per cycle). The `snapshot_id` is unique — there is at most one position record per snapshot.

**Position records reflect actual post-execution state.** After executing a BUY or SELL, `_persist_cycle` writes the real position state:

```python
position = Position(
    side="long" | "short" | "flat",   # reflects actual position after execution
    size=size_pct,                     # percentage of portfolio allocated
    entry_price=execution_price,       # fill price from the order
    unrealized_pnl=current_pnl,        # computed from current market price
    wallet_balance=wallet_usdt,        # from fetch_balance()
)
```

This means `GET /positions` returns meaningful position data, and the database can be used as a source of truth for startup recovery via `RiskService.restore_from_db(db)`.

---

### `AIDecision`

```
ai_decisions
├── id                     INTEGER   PRIMARY KEY
├── snapshot_id            INTEGER   FK → hourly_market_snapshots.id, UNIQUE
├── prompt_version         VARCHAR   (always "v1")
├── model_name             VARCHAR   (e.g. "claude-sonnet-4-6")
├── action                 VARCHAR   ("BUY", "SELL", "HOLD")
├── confidence_score       NUMERIC(5,4)   NULLABLE
├── reasoning_summary      TEXT      NULLABLE
├── recommended_size       NUMERIC(20,8)  NULLABLE
├── recommended_stop_loss  NUMERIC(20,8)  NULLABLE
└── recommended_take_profit NUMERIC(20,8) NULLABLE
```

One `AIDecision` per snapshot (unique on `snapshot_id`). The action and reasoning stored here reflect the **post-risk-filter** decision, not Claude's raw output.

For example:
- Claude returns: `{"action": "SELL", "confidence": 0.91}`
- Risk service blocks (no open position): overwrites with HOLD + override reasoning
- DB records: `action="HOLD"`, `reasoning_summary="No open position for SOLUSDT — ignoring SELL."`

`recommended_stop_loss` and `recommended_take_profit` are always `None` — Claude's schema does not include these fields; the system uses globally configured thresholds instead.

Relationships:
- `snapshot` → `HourlyMarketSnapshot`
- `execution` → `Execution` (one-to-one, cascade delete)

---

### `Execution`

```
executions
├── id               INTEGER    PRIMARY KEY
├── ai_decision_id   INTEGER    FK → ai_decisions.id, UNIQUE
├── executed_action  VARCHAR    ("BUY", "SELL", "HOLD")
├── executed_size    NUMERIC(20,8)   NULLABLE
├── execution_price  NUMERIC(20,8)   NULLABLE
├── fees_paid        NUMERIC(20,8)   NULLABLE  (always 0)
├── slippage         NUMERIC(20,8)   NULLABLE  (always 0)
├── execution_time   DATETIME         NULLABLE
└── status           VARCHAR    ("paper_filled", "filled", "rejected", "none")
```

Status values:
- `"paper_filled"` — paper trade successfully simulated
- `"filled"` — live order filled via ccxt (not verified with exchange — just the intent)
- `"rejected"` — execution failed (error in `exec_result["error"]`)
- `"none"` — HOLD decision, no execution attempted

`fees_paid` and `slippage` are always `Decimal("0")`. Fee tracking was not implemented.

`execution_time` is `None` for HOLD decisions.

---

## In-Memory State

### `MarketStateStore._state`

`dict[str, SymbolMarketState]` — the most recent ticker data for each symbol.

- Written: every WebSocket tick (multiple times per second)
- Read: each hourly cycle start
- Lost on restart: yes — the WebSocket repopulates it within seconds of reconnection

### `RiskService._positions`

`dict[str, OpenPosition]` — the set of currently open positions with their entry prices and sizes.

- Written: by `ExecutionService._update_positions()` after each BUY/SELL execution
- Read: by `check_exit_conditions()` on every WebSocket tick, and by `evaluate_decision()` on every cycle
- **Restored on startup** via `RiskService.restore_from_db(db)` — queries the latest filled BUY/SELL execution per asset and reconstructs in-memory positions

On startup `restore_from_db` replays the execution log to find the current state for each symbol:
- If the most recent filled execution for a symbol is a BUY, the position is reconstructed with the fill price and size
- If the most recent is a SELL (or no executions exist), no position is recorded

This means stop-loss and take-profit checks resume correctly after a restart for any position that was properly executed and persisted to the database.

### `trigger_queue`

`asyncio.Queue[dict]` — exit orders produced by the WebSocket task and consumed by `TriggerExecutor`.

- Lost on restart: yes — any queued orders that hadn't been executed are lost

---

## What Survives a Restart

| Data | Survives restart? | Notes |
|---|---|---|
| Asset records | Yes | DB is persistent |
| Market snapshots (historical) | Yes | DB is persistent |
| Position wallet-balance records | Yes | DB is persistent (but these aren't live position state) |
| AI decisions (historical) | Yes | DB is persistent |
| Execution records | Yes | DB is persistent |
| Trade log (`logs/trades.jsonl`) | Yes | File is appended, not overwritten |
| Open positions (which assets held) | **Yes** | Restored via `RiskService.restore_from_db(db)` on startup |
| Entry prices of open positions | **Yes** | Stored in `positions` table, restored on startup |
| Current market prices | **No** | `MarketStateStore` repopulates from WebSocket |
| Pending exit orders in queue | **No** | `asyncio.Queue` is in-memory |
| Paper USDT balance | **Yes** | `ExecutionService.restore_paper_balance_from_db(db)` replays all executions on startup |

---

## Trade Journal (`logs/trades.jsonl`)

Every call to `execution_service.execute_decision()` appends a JSON record to `logs/trades.jsonl`. This file is an append-only log that survives restarts and contains the authoritative record of all execution attempts (including HOLDs).

Format: one JSON object per line (newline-delimited JSON / NDJSON).

The directory `logs/` is created on startup if it doesn't exist (`TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)` in `ExecutionService.__init__`).

This file is **not** read by the application. It is a debug/audit log only.

---

## Database Schema Diagram

```
assets (1) ──────────────────────────── (N) hourly_market_snapshots
    │                                             │ (1)
    │                                             ├── (1) positions
    │                                             └── (1) ai_decisions
    │                                                        │ (1)
    │                                                        └── (1) executions
    │
    └── (N) positions  [also linked via asset_id]
```

Cascade delete is set on all child relationships: deleting an `Asset` deletes its snapshots, which deletes their positions, AI decisions, and executions.
