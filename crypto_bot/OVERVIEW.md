# Crypto Trading Bot — Overview

## What it does

An automated trading bot that runs every hour. It connects to Binance via WebSocket for live prices, fetches real hourly candle data, reads market sentiment from news and Reddit, asks Claude (an AI) whether to buy, sell, or hold, applies safety checks, then either places a real trade or logs what it would have done (paper trading mode). All decisions and positions are persisted to a SQLite database and survive restarts.

## Assets traded

- BTC/USDT (Bitcoin)
- ETH/USDT (Ethereum)
- SOL/USDT (Solana)

---

## How one cycle works (runs every hour)

```
1. Read live prices from Binance WebSocket
2. Fetch 24 real hourly candles from Binance REST API
3. Measure market mood from news + Reddit
4. Ask Claude: buy, sell, or hold?
5. Risk manager checks Claude's answer
6. Execute trade (or log it in paper mode)
7. Persist snapshot, position, decision, and execution to DB
```

The cycle runs in a thread pool so it never blocks the async WebSocket price stream.

---

## Files

### FastAPI app (`app/`)

#### `app/main.py` — Startup and scheduler
Wires all services together, restores open positions and paper balance from the DB on startup, starts the Binance WebSocket stream and real-time exit processor, then runs the hourly cycle via APScheduler. The cycle is dispatched to a thread pool via `asyncio.to_thread` so it never blocks the event loop.

#### `app/config.py` — Settings
All configuration loaded from `.env`. See the configuration table below.

#### `app/db/` — Database
SQLite via SQLAlchemy. Tables: `assets`, `hourly_market_snapshots`, `positions`, `ai_decisions`, `executions`. Initialised on startup with `create_all`.

#### `app/models/` — ORM models
- `Asset` — tracked symbol (BTCUSDT, ETHUSDT, SOLUSDT)
- `HourlyMarketSnapshot` — price/volume at each cycle
- `Position` — actual position state after execution (side, size_pct, entry_price, unrealized_pnl, wallet_balance)
- `AIDecision` — Claude's raw output
- `Execution` — what was actually submitted (or paper-logged)

#### `app/services/binance_ws.py` — Live price stream
Persistent WebSocket to Binance's combined `@ticker` stream. Continuously updates `MarketStateStore`. On each tick it also checks stop-loss and take-profit conditions via `RiskService.check_exit_conditions`, and queues any triggered exits for immediate execution.

#### `app/services/market_state.py` — In-memory price store
Holds the latest ticker state per symbol (price, bid, ask, 24h high/low/volume/change).

#### `app/services/trading_cycle.py` — Hourly orchestration
1. Builds market data from the live WebSocket state
2. Enriches it with real 24-hour OHLCV candles from Binance REST
3. Fetches sentiment
4. Calls Claude for decisions
5. Runs decisions through the risk filter
6. Executes each decision and persists to DB

Position records in the DB reflect the true post-execution state (side, size, entry_price) read from `RiskService`.

#### `app/services/ai_service.py` — Claude integration
Builds a prompt containing live prices, the last 6 hourly close prices (trend indicator), full 24h OHLCV summary, and sentiment scores. Sends to Claude (`claude-sonnet-4-6`) and parses the JSON response. Missing or malformed assets default to HOLD.

#### `app/services/risk_service.py` — Safety checks
Tracks open positions in memory. On startup, positions are **restored from the DB** so the risk rules survive restarts.

| Check | What happens |
|---|---|
| Kill switch | If `KILL_SWITCH=true`, all trading stops |
| HOLD | Passes straight through |
| Confidence too low | Below `MIN_CONFIDENCE` → forced HOLD |
| Already holding | BUY into an existing position → HOLD |
| No position | SELL with no open position → HOLD |
| Position too large | Capped at `MAX_POSITION_PCT` |
| Total exposure too high | Blocked or reduced if total exceeds `MAX_TOTAL_EXPOSURE_PCT` |
| Stop-loss / take-profit | Checked on every WebSocket tick; forced SELL injected immediately |

#### `app/services/execution_service.py` — Order placement
Executes approved decisions in paper or live mode. Tracks the paper USDT balance in memory (`_paper_usdt`), updating it on every BUY or SELL. On startup, the balance is **restored from DB** by replaying all historical filled executions.

- **Paper mode** (`PAPER_TRADING=true`): logs the trade, updates the internal balance and position book
- **Live mode** (`PAPER_TRADING=false`): places a real market order via ccxt

#### `app/services/sentiment_service.py` — Market mood
Blends three free sources into a score from **-1.0** (very negative) to **+1.0** (very positive):

| Source | What it does |
|---|---|
| RSS feeds | CoinTelegraph, Decrypt, Bitcoin Magazine, CoinDesk — each fetched with explicit HTTP headers, scored with TextBlob |
| Reddit | r/cryptocurrency, r/bitcoin, r/ethtrader — upvote-weighted sentiment |
| Fear & Greed Index | Alternative.me free API — 0–100 converted to -1.0/+1.0 |

#### `app/services/trigger_executor.py` — Real-time exit processor
Consumes stop-loss/take-profit orders from an asyncio queue (posted by the WebSocket handler) and executes them in a thread pool.

#### `app/api/routes/` — REST endpoints
| Route | Description |
|---|---|
| `GET /health` | Liveness check |
| `GET /assets` | Tracked assets |
| `GET /positions` | All position records (including current open positions) |
| `GET /decisions` | All AI decision records |

---

## Configuration (`.env` file)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Needed to call Claude |
| `BINANCE_API_KEY` | empty | Only needed for live trading |
| `BINANCE_API_SECRET` | empty | Only needed for live trading |
| `PAPER_TRADING` | true | When true, logs decisions but places no real orders |
| `PAPER_BALANCE_USDT` | 10000 | Starting simulated portfolio (replayed from DB on restart) |
| `KILL_SWITCH` | false | Set to true to halt all new positions immediately (hot-reload) |
| `TRACKED_SYMBOLS` | BTCUSDT,ETHUSDT,SOLUSDT | Comma-separated list of symbols |
| `MIN_CONFIDENCE` | 0.7 | Claude must be at least this confident to act |
| `MAX_POSITION_PCT` | 20 | Maximum % of portfolio per asset |
| `MAX_TOTAL_EXPOSURE_PCT` | 60 | Maximum % of portfolio across all open positions |
| `STOP_LOSS_PCT` | 5 | Sell if price drops this % below entry (checked on every tick) |
| `TAKE_PROFIT_PCT` | 0 | Sell if price rises this % above entry (0 = disabled) |
| `MODEL_NAME` | claude-sonnet-4-6 | Claude model to use |

---

## How to run

```bash
# 1. Copy the example env file and fill in your Anthropic API key
cp .env.example .env

# 2. Activate the virtualenv
source env/bin/activate   # macOS/Linux
# source env/Scripts/activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. One-time TextBlob setup
python -m textblob.download_corpora

# 5. Start the FastAPI server
cd crypto_bot
uvicorn app.main:app --reload
```

The bot starts immediately, connects to Binance, and fires the first trading cycle. API docs at `http://localhost:8000/docs`.

To stop it: `Ctrl+C`

---

## Logs and data

**Terminal / `logs/bot.log`** — structured logs with timestamps and levels (INFO, WARNING, ERROR).

**`logs/trades.jsonl`** — one JSON record per decision, written by `ExecutionService`:
- Timestamp, asset, action, confidence, size_pct
- Current price, paper balance
- Claude's reasoning
- Order result (or paper stub)
- Any errors

**`crypto_bot.db`** — SQLite database with full historical record of snapshots, positions, decisions, and executions. Survives restarts.

---

## Switching to live trading

1. Register on Binance and create API keys
2. Add them to `.env`:
   ```
   BINANCE_API_KEY=your_key
   BINANCE_API_SECRET=your_secret
   ```
3. Set `PAPER_TRADING=false` in `.env`
4. Restart the bot

**Warning:** live mode places real orders with real money. Run in paper mode first and review `logs/trades.jsonl` to confirm the bot behaves sensibly before going live.

---

## Known limitations (prototype)

- Paper balance is restored from DB by replaying executions, which may drift slightly if prices were not recorded exactly at fill time.
- Position `size` is stored as `size_pct` (portfolio percentage), not absolute units.
- The `/positions` API returns all historical position snapshots, not just open ones — filter by `side != "flat"` for open positions.
- Sentiment fetching is synchronous within the thread pool worker — each cycle can take 30–60 seconds due to Reddit rate-limit sleeps between subreddits.
