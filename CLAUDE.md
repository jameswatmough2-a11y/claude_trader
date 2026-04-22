# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

All commands run from the `crypto_bot/` directory with the `env` virtualenv activated:

```bash
# Activate virtualenv (macOS/Linux)
source env/bin/activate

# Activate virtualenv (Windows)
source env/Scripts/activate

# Start the FastAPI server (development)
cd crypto_bot
uvicorn app.main:app --reload

# Install dependencies
pip install -r requirements.txt

# One-time TextBlob setup
python -m textblob.download_corpora
```

The server starts on `http://localhost:8000`. API docs at `/docs`.

## Environment

Copy `crypto_bot/.env.example` to `crypto_bot/.env`. The only required key for paper trading is `ANTHROPIC_API_KEY`. Binance keys are only needed for live order placement.

Key settings (all configurable via `.env`):
- `PAPER_TRADING=true` — simulates trades, no real orders sent
- `KILL_SWITCH=false` — set to `true` to halt all new positions immediately (hot-reload, no restart needed)
- `TRACKED_SYMBOLS` — comma-separated list, e.g. `BTCUSDT,ETHUSDT,SOLUSDT`
- `MIN_CONFIDENCE`, `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT` — risk parameters

## Architecture

```
crypto_bot/
  app/
    main.py              # FastAPI app, APScheduler, startup/shutdown hooks, state restore
    config.py            # Settings dataclass loaded from .env
    db/
      session.py         # SQLAlchemy engine + SessionLocal
      init_db.py         # Creates tables on startup
    models/              # SQLAlchemy ORM models (Asset, HourlyMarketSnapshot, Position, AIDecision, Execution)
    api/routes/          # REST endpoints: /health, /assets, /positions, /decisions
    services/
      binance_ws.py      # Persistent WebSocket to Binance; populates MarketStateStore; checks stop-loss/take-profit on each tick
      market_state.py    # In-memory store of live ticker data (SymbolMarketState)
      trading_cycle.py   # Hourly cron: snapshot → OHLCV enrich → AI decision → risk check → execution → DB persist
      ai_service.py      # Claude API call; builds prompt (with last-6-candle trend), parses/validates JSON response
      risk_service.py    # Position tracking (restored from DB on startup), kill switch, stop-loss logic, exposure caps
      execution_service.py # Paper or live order placement; tracks paper USDT balance (restored from DB on startup)
      data_feeds.py      # ccxt exchange factory, fetch_ticker, fetch_ohlcv (24 1h candles), fetch_balance
      sentiment_service.py # Sentiment scoring via RSS (with HTTP headers), Reddit, Fear & Greed index
      trigger_executor.py  # Async queue consumer for real-time stop-loss/take-profit orders
  logs/
    bot.log              # Rotating log file (INFO/WARNING/ERROR)
    trades.jsonl         # One JSON record per decision
  crypto_bot.db          # SQLite database (auto-created on first run)
```

### Data flow

1. **BinanceWebSocketService** connects to Binance's combined `@ticker` stream and continuously updates `MarketStateStore`. On each tick it also calls `RiskService.check_exit_conditions` — if stop-loss or take-profit is breached, a SELL order is pushed to `trigger_queue`.
2. **TriggerExecutor** consumes `trigger_queue` in a thread pool, executing exit orders immediately without waiting for the hourly cycle.
3. **APScheduler** fires `run_hourly_cycle()` at the top of every hour. The async wrapper dispatches the blocking work to a thread via `asyncio.to_thread`, keeping the event loop free.
4. The cycle reads live state from `MarketStateStore`, fetches **real 24-hour OHLCV candles** from Binance REST, fetches sentiment, calls Claude, filters through `RiskService`, then executes via `ExecutionService`.
5. All results (snapshot, position, AI decision, execution) are written to SQLite via SQLAlchemy. The **Position record reflects the actual post-execution state** (side, size_pct, entry_price, unrealized_pnl).

### State persistence across restarts (`app/main.py` lifespan)

On startup, before the first cycle:
- `RiskService.restore_from_db(db)` — queries the latest filled BUY/SELL per asset and reconstructs in-memory positions
- `ExecutionService.restore_paper_balance_from_db(db)` — replays all historical filled executions to compute the current paper USDT balance

### Claude integration (`ai_service.py`)

- Uses `claude-sonnet-4-6` via the `anthropic` SDK.
- Prompt includes: live price/bid/ask, 24h high/low/change/volume, last 6 hourly close prices (trend), sentiment score, Fear & Greed index, top headlines.
- System prompt constrains Claude to return **only** a JSON array. Parsed, validated; missing assets default to HOLD.
- Confidence below `MIN_CONFIDENCE` (default 0.7) forces HOLD.

### Risk layer (`risk_service.py`)

- In-memory `_positions` dict, restored from DB on startup.
- Stop-loss and take-profit checked on every WebSocket tick via `check_exit_conditions`.
- Kill switch reads `KILL_SWITCH` env var at evaluation time (hot-reload without restart).

### Paper trading (`execution_service.py`)

- `_paper_usdt` tracks available USDT in memory, updated on every BUY/SELL.
- On startup, replays DB executions to restore the balance.
- `paper_usdt` property is read by `TradingCycleService` and `TriggerExecutor` for balance lookups.
- `PAPER_TRADING=true` skips ccxt and writes a synthetic order to `logs/trades.jsonl`.

### OHLCV candles (`trading_cycle.py` + `data_feeds.py`)

- After building market data from the WebSocket, `_enrich_with_ohlcv` fetches 24 hourly candles per symbol via `fetch_ohlcv`.
- Replaces the WebSocket-derived OHLCV approximation with real candle data.
- Candle list is passed through to the AI prompt (last 6 closes shown as a trend).
- Falls back gracefully to WebSocket ticker data if the REST fetch fails.

## Planned features

- Bayesian trade scoring
- Entry/exit condition logic beyond simple stop-loss/take-profit
- Live `/positions` endpoint filtered to open positions only
