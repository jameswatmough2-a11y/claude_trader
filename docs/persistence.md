# Persistence

This document explains what data is stored in the database, what is kept only in memory, and what happens to each type of state on process restart.

---

## Database: SQLite via SQLAlchemy ORM

The database is a single SQLite file at the path configured by `DATABASE_URL` (default: `crypto_bot/crypto_bot.db`).

Tables are created on startup by `init_db()` → `Base.metadata.create_all(bind=engine)`. New columns on existing tables are added by the migration functions (`_migrate_bot_config`, `_migrate_ai_decision`, `_migrate_execution`, `_migrate_hourly_market_snapshots`, `_migrate_executions_session_trade`) using `ALTER TABLE ADD COLUMN` — safe for zero-downtime upgrades.

`_ensure_default_user()` runs on every startup and inserts `id=1` `name='default'` into `users` if it doesn't exist, guaranteeing multi-tenancy scaffolding is always ready.

### Session management

- `check_same_thread=False` — required for SQLite with APScheduler thread pool workers
- `autoflush=False` — `_persist_cycle` calls `db.flush()` explicitly after each insert to get generated PKs
- One `SessionLocal()` per trading cycle (created and closed in `run_hourly_cycle`)
- One `SessionLocal()` per FastAPI request (via `get_db()` dependency)
- One `SessionLocal()` per `db_logger` call — independent sessions ensure logs commit even when the caller's main session rolls back

---

## ORM Models

### `User`

```
users
├── id          INTEGER   PRIMARY KEY (always 1 for the default user)
├── name        VARCHAR   NOT NULL    (default "default")
└── created_at  DATETIME  NOT NULL
```

Multi-tenancy scaffold. A single default user (`id=1`) is auto-created by `_ensure_default_user()` on startup. All `TradingSession` and `Trade` rows carry `user_id=1`.

---

### `TradingSession`

```
trading_sessions
├── id                    INTEGER   PRIMARY KEY
├── user_id               INTEGER   FK → users.id
├── started_at            DATETIME  NOT NULL    (UTC)
├── ended_at              DATETIME  NULLABLE
├── status                VARCHAR   NOT NULL    ("active", "stopped", "crashed")
├── starting_balance_usdt NUMERIC(20,8)  NOT NULL
├── ending_balance_usdt   NUMERIC(20,8)  NULLABLE
└── notes                 TEXT      NULLABLE    (JSON snapshot of BotConfig at session start)
```

One row per bot run. Created by `POST /start`, closed by `POST /stop`.

**Computed properties** (Python, not DB columns):
- `duration_seconds` — `(ended_at - started_at).total_seconds()` or live elapsed time
- `pnl_usdt` — `ending_balance_usdt - starting_balance_usdt`
- `pnl_pct` — `pnl_usdt / starting_balance_usdt × 100`

On server restart, `session_service.get_active_session()` re-links `bot_state.current_session_id` to any session left in `active` status.

---

### `Trade`

```
trades
├── id                  INTEGER   PRIMARY KEY
├── session_id          INTEGER   FK → trading_sessions.id
├── user_id             INTEGER   FK → users.id
├── symbol              VARCHAR   NOT NULL     (e.g. "BTCUSDT")
├── entry_execution_id  INTEGER   FK → executions.id
├── entry_price         NUMERIC(20,8)
├── entry_qty           NUMERIC(20,8)
├── entry_fee_usdt      NUMERIC(20,8)
├── size_pct            NUMERIC(20,8)    (% of portfolio)
├── opened_at           DATETIME  NOT NULL
├── stop_loss_price     NUMERIC(20,8)   NULLABLE
├── take_profit_price   NUMERIC(20,8)   NULLABLE
├── exit_execution_id   INTEGER   FK → executions.id    NULLABLE
├── exit_price          NUMERIC(20,8)   NULLABLE
├── exit_fee_usdt       NUMERIC(20,8)   NULLABLE
├── exit_reason         VARCHAR   NULLABLE    ("ai_sell", "stop_loss", "take_profit", "session_end")
├── closed_at           DATETIME  NULLABLE
├── realized_pnl_pct    NUMERIC(20,8)   NULLABLE
├── realized_pnl_usdt   NUMERIC(20,8)   NULLABLE
└── status              VARCHAR   NOT NULL    ("open", "closed")
```

One row per BUY→SELL lifecycle. At most one `open` trade per `(session_id, symbol)` pair.

`realized_pnl_usdt` = `qty × (exit_price − entry_price) − total_fees`. Computed by `TradeService.close_trade()`.

---

### `BotConfig`

```
bot_config
├── id                      INTEGER   PRIMARY KEY (always 1)
├── interval_minutes        INTEGER   (default 60)
├── min_confidence          NUMERIC(5,4)
├── max_position_pct        NUMERIC(10,4)
├── max_total_exposure_pct  NUMERIC(10,4)
├── stop_loss_pct           NUMERIC(10,4)
├── take_profit_pct         NUMERIC(10,4)
├── tracked_symbols         VARCHAR
├── paper_balance_usdt      NUMERIC(20,8)
├── chart_interval          VARCHAR   (display only)
├── model_name              VARCHAR
├── ohlcv_interval          VARCHAR   (default "1h")
├── ohlcv_limit             INTEGER   (default 50 — candles fetched per cycle)
├── taker_fee_rate          NUMERIC(10,6)  (default 0.001)
├── timezone                VARCHAR   (display only)
├── display_currency        VARCHAR   (display only)
└── updated_at              DATETIME  NULLABLE
```

Single-row table (`id=1`). `PUT /api/bot/config` upserts via `INSERT OR REPLACE`.

`_apply_settings(row)` mutates the live `settings` singleton on save and on startup (if a row exists). Trading services pick up changes immediately without restart.

`ohlcv_interval` controls which candle timeframe is fetched each cycle and labelled in the AI prompt. `taker_fee_rate` controls paper trade fee deductions and is stored on each execution record.

---

### `Asset`

```
assets
├── id            INTEGER   PRIMARY KEY
├── symbol        VARCHAR   UNIQUE, INDEXED
├── base_currency VARCHAR
└── quote_currency VARCHAR
```

Created once per symbol on first appearance in a trading cycle.

---

### `HourlyMarketSnapshot`

```
hourly_market_snapshots
├── id                         INTEGER   PRIMARY KEY
├── asset_id                   INTEGER   FK → assets.id
├── snapshot_time              DATETIME
├── open_price                 NUMERIC(20,8)   (= last_price from WebSocket)
├── high_price                 NUMERIC(20,8)   (= high_24h from WebSocket)
├── low_price                  NUMERIC(20,8)   (= low_24h from WebSocket)
├── close_price                NUMERIC(20,8)   (= last_price from WebSocket)
├── volume                     NUMERIC(30,8)
├── price_change_1h_pct        NUMERIC(10,4)   NULLABLE
├── price_change_since_entry_pct NUMERIC(10,4) NULLABLE
└── session_id                 INTEGER         NULLABLE   FK → trading_sessions.id (migration-added)
```

Unique constraint: `(asset_id, snapshot_time)`.

**Note:** The OHLC columns store WebSocket ticker values, not actual candlestick data. Real OHLCV candles are in the `ohlcv_candles` table.

---

### `OhlcvCandle`

```
ohlcv_candles
├── id         INTEGER   PRIMARY KEY
├── symbol     VARCHAR   NOT NULL
├── timeframe  VARCHAR   NOT NULL  (e.g. "1h")
├── open_time  DATETIME  NOT NULL
├── close_time DATETIME
├── open       NUMERIC(20,8)
├── high       NUMERIC(20,8)
├── low        NUMERIC(20,8)
├── close      NUMERIC(20,8)
└── volume     NUMERIC(30,8)

UNIQUE (symbol, timeframe, open_time)  — upsert-safe
INDEX  (symbol, timeframe, open_time)
```

Populated by `_enrich_and_store_ohlcv()` in each trading cycle. Uses `settings.ohlcv_interval` as the timeframe. Candles are upserted: if a row with the same `(symbol, timeframe, open_time)` already exists it is skipped (not overwritten). This means the first fetch of a candle wins — subsequent cycles won't corrupt in-progress candles.

The candle list is passed to the AI prompt (last 6 closes shown as a trend line) and to the fallback strategy.

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

One record per snapshot. Reflects actual post-execution position state. Used by `RiskService.restore_from_db(db)` on startup to reconstruct in-memory positions.

---

### `AIDecision`

```
ai_decisions
├── id                     INTEGER   PRIMARY KEY
├── snapshot_id            INTEGER   FK → hourly_market_snapshots.id, UNIQUE
├── prompt_version         VARCHAR
├── model_name             VARCHAR
├── action                 VARCHAR   ("BUY", "SELL", "HOLD")
├── confidence_score       NUMERIC(5,4)   NULLABLE
├── reasoning_summary      TEXT      NULLABLE
├── decision_source        VARCHAR(20)   ("ai" or "fallback_rule")
├── recommended_size       NUMERIC(20,8)  NULLABLE
├── recommended_stop_loss  NUMERIC(20,8)  NULLABLE
└── recommended_take_profit NUMERIC(20,8) NULLABLE
```

`decision_source` distinguishes Claude API decisions from rule-based fallback decisions. Useful for auditing system behavior during API outages.

The action and reasoning stored here reflect the **post-risk-filter** decision, not the raw AI/fallback output.

---

### `Execution`

```
executions
├── id                  INTEGER    PRIMARY KEY
├── ai_decision_id      INTEGER    FK → ai_decisions.id, UNIQUE
├── executed_action     VARCHAR    ("BUY", "SELL", "HOLD")
├── executed_size       NUMERIC(20,8)   NULLABLE
├── execution_price     NUMERIC(20,8)   NULLABLE
├── fees_paid           NUMERIC(20,8)   NULLABLE
├── fee_rate            NUMERIC(10,6)   NULLABLE
├── fill_source         VARCHAR         NULLABLE  ("ask", "bid", "last_price_fallback")
├── verification_status VARCHAR         NULLABLE  ("filled", "partial_or_open", "verification_failed")
├── slippage            NUMERIC(20,8)   NULLABLE
├── execution_time      DATETIME        NULLABLE
├── status              VARCHAR    ("paper_filled", "filled", "rejected", "none")
├── session_id          INTEGER    NULLABLE    FK → trading_sessions.id (migration-added)
└── trade_id            INTEGER    NULLABLE    FK → trades.id (migration-added)
```

`fees_paid` = `qty × execution_price × fee_rate`. Accurate for paper trades.

`fill_source`: `"ask"` for paper BUY fills, `"bid"` for paper SELL fills, `"last_price_fallback"` if bid/ask was unavailable.

`verification_status`: only set for live trades — `"filled"` means the exchange confirmed the order closed; `"partial_or_open"` means the order exists but status is not "closed"; `"verification_failed"` means the fetch call itself failed.

---

### `SystemLog`

```
system_logs
├── id           INTEGER   PRIMARY KEY
├── created_at   DATETIME  NOT NULL  (UTC)  INDEXED
├── level        VARCHAR   NOT NULL  ("INFO", "WARNING", "ERROR")  INDEXED
├── component    VARCHAR   NOT NULL  INDEXED  (e.g. "trading_cycle")
├── event_type   VARCHAR   NOT NULL  INDEXED  (e.g. "cycle_end")
├── symbol       VARCHAR   NULLABLE  INDEXED
├── cycle_id     VARCHAR   NULLABLE
├── message      TEXT      NOT NULL
└── details_json TEXT      NULLABLE  (JSON string)
```

Structured event log for all significant system events. Indexed on `created_at`, `level`, `component`, `event_type`, and `symbol` to support the log-viewer queries (filtered, paginated).

Written exclusively via `db_logger.log_info/warning/error()` — each call uses a separate `SessionLocal()` session to ensure commits are independent of the caller's transaction.

`details_json` stores arbitrary extra context as a JSON string (e.g. order size, fill price, error details).

---

## In-Memory State

### `MarketStateStore._state`
Written every WebSocket tick. Lost on restart — repopulates within seconds.

### `RiskService._positions`
Restored on startup via `restore_from_db(db)`. Replays execution log to find the current state per symbol (last filled BUY = open; last filled SELL or no executions = flat).

### `SentimentService._sentiment_cache`
In-memory TTL cache. Lost on restart — will fetch fresh data on the next cycle.

### `MarketValidator._market_cache`
In-memory market info cache. Lost on restart — refetched from ccxt on first use.

### `trigger_queue`
`asyncio.Queue` — lost on restart. Any queued stop-loss/take-profit orders are lost. Stop-loss resumes checking on the next WebSocket tick after restart.

---

## What Survives a Restart

| Data | Survives restart? | Notes |
|---|---|---|
| Bot configuration | **Yes** | `bot_config` row; `_apply_settings()` reapplies on startup |
| Open positions | **Yes** | Restored via `RiskService.restore_from_db()` |
| Entry prices | **Yes** | Stored in `positions` table |
| Paper USDT balance | **Yes** | `ExecutionService.restore_paper_balance_from_db()` replays executions (subtracts fees correctly) |
| Active session | **Yes** | `session_service.get_active_session()` re-links `bot_state.current_session_id` |
| Trade rows | **Yes** | `trades` table is persistent |
| OHLCV candles | **Yes** | `ohlcv_candles` table is persistent |
| System log events | **Yes** | `system_logs` table is persistent |
| Asset records | **Yes** | DB is persistent |
| AI decision records | **Yes** | DB is persistent |
| Execution records | **Yes** | DB is persistent |
| Trade log (`trades.jsonl`) | **Yes** | File is appended, not overwritten |
| Live market prices | **No** | `MarketStateStore` repopulates from WebSocket |
| Sentiment cache | **No** | Pre-warmed async task fires 8 s after startup |
| Market info cache | **No** | Refetched from ccxt on next execution |
| Pending exit queue | **No** | `asyncio.Queue` is in-memory |

---

## Trade Journal (`logs/trades.jsonl`)

Every execution attempt appends a JSON record to `logs/trades.jsonl`. This file now includes `fee_amount`, `fee_rate`, and `fill_source` fields for paper trades. It is an append-only debug/audit log and is not read by the application.

---

## Database Schema

```
users (1) ───────── (N) trading_sessions (1) ─── (N) trades
                              │
                              │ session_id (nullable)
                              │
assets (1) ──── (N) hourly_market_snapshots ─────── session_id ─┘
                          │ (1)
                          ├── (1) positions
                          └── (1) ai_decisions
                                     │ (1)
                                     └── (1) executions ─── trade_id ──► trades

ohlcv_candles    (independent — linked by symbol string, not FK)

system_logs      (independent — component-scoped event log)
```

Cascade delete on all `assets` child relationships: deleting an `Asset` deletes its snapshots, positions, AI decisions, and executions. `ohlcv_candles`, `system_logs`, `users`, `trading_sessions`, and `trades` are not part of the cascade — they are cleared explicitly by `POST /reset-db` in FK-safe order.
