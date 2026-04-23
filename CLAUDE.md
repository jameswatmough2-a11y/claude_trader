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

## Running the dashboard

```bash
cd dashboard
npm run dev   # starts on http://localhost:3000
```

The dashboard proxies all `/api/*` requests to `http://localhost:8000`.

## Environment

Copy `crypto_bot/.env.example` to `crypto_bot/.env`. The only required key for paper trading is `ANTHROPIC_API_KEY`. Binance keys are only needed for live order placement.

Key `.env` settings (these are initial defaults — all are overridden at runtime by the `BotConfig` DB row):
- `PAPER_TRADING=true` — simulates trades, no real orders sent
- `KILL_SWITCH=false` — set to `true` to halt all new positions immediately (hot-reload, no restart needed)
- `TRACKED_SYMBOLS` — comma-separated list, e.g. `BTCUSDT,ETHUSDT,SOLUSDT`
- `MIN_CONFIDENCE`, `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT` — risk parameters

## Architecture

```
crypto_bot/
  app/
    main.py              # FastAPI app, startup/shutdown hooks, state restore; does NOT auto-start trading
    config.py            # Settings dataclass loaded from .env; mutated at runtime by _apply_settings()
    state.py             # BotState singleton (running bool), APScheduler instance, run_hourly_cycle()
    db/
      session.py         # SQLAlchemy engine + SessionLocal
      init_db.py         # Creates tables on startup; _migrate_bot_config() adds new columns safely
    models/
      bot_config.py      # BotConfig — single-row (id=1) persistent config table, upserted via PUT /api/bot/config
      (other models)     # Asset, HourlyMarketSnapshot, Position, AIDecision, Execution
    api/routes/
      bot_control.py     # POST /start, /stop, /reset-db; GET+PUT /config; GET /status; GET /health
      (other routes)     # /assets, /positions, /decisions
    services/
      binance_ws.py      # Persistent WebSocket to Binance; dynamic URL from settings.tracked_symbols;
                         #   reconnect() closes current ws so run_forever re-subscribes to new symbols
      market_state.py    # In-memory store of live ticker data (SymbolMarketState)
      trading_cycle.py   # Cron: snapshot → OHLCV enrich → AI decision → risk check → execution → DB persist
                         #   also fetches previous_decisions per symbol from AIDecision table
      ai_service.py      # Claude API call; dynamic system prompt (reads live settings); prompt includes
                         #   current positions + previous decisions; returns validated JSON array
      risk_service.py    # Position tracking (restored from DB on startup), kill switch, SL/TP logic
      execution_service.py # Paper or live order placement; paper_usdt restored from DB on startup
      data_feeds.py      # ccxt factory, fetch_ticker, fetch_ohlcv (24 1h candles), fetch_balance
      sentiment_service.py # Sentiment scoring via RSS, Reddit, Fear & Greed index
      trigger_executor.py  # Async queue consumer for real-time stop-loss/take-profit orders
  logs/
    bot.log              # Rotating log file (INFO/WARNING/ERROR)
    trades.jsonl         # One JSON record per decision
  crypto_bot.db          # SQLite database (auto-created on first run)

dashboard/
  app/
    page.tsx             # Root redirect → /overview
    overview/page.tsx    # Main dashboard: bot controls, stat cards, decisions table, chart
    settings/page.tsx    # Config editor; fetches /api/bot/status to lock trading fields while running
    components/
      candlestick-chart.tsx  # Lightweight-charts candlestick; live price/SL/TP/entry lines with toggles
      dashboard.tsx          # Overview page layout, stat cards, decisions table, ActionBadge
      app-sidebar.tsx        # Nav sidebar
  hooks/
    use-chart-ws.ts      # WebSocket hook for chart: candle updates, price ticks, position data
  lib/
    chart-utils.ts       # fmtPrice, autoDecimals, fmtChange, changeIsPositive
```

---

## Key subsystems

### Bot lifecycle (`state.py` + `bot_control.py`)

The bot does **not** auto-start on server restart. It must be explicitly started via the dashboard or `POST /api/bot/start`.

- `BotState.running` — in-memory flag; `True` while the scheduler is active
- `scheduler` — `AsyncIOScheduler` (UTC timezone); started/stopped by `/start` and `/stop`
- `run_hourly_cycle()` — async wrapper that dispatches blocking `TradingCycleService.run()` via `asyncio.to_thread`
- On `POST /start`: scheduler starts and immediately fires one cycle, then runs on `interval_minutes` cadence
- On `POST /stop`: scheduler is paused, `BotState.running = False`
- On `POST /reset-db`: drops and recreates all tables, resets in-memory positions and paper balance

### Persistent config (`models/bot_config.py` + `bot_control.py`)

All user-configurable settings are stored in a single-row `bot_config` table (always `id=1`).

Fields: `interval_minutes`, `min_confidence`, `max_position_pct`, `max_total_exposure_pct`,
`stop_loss_pct`, `take_profit_pct`, `tracked_symbols`, `paper_balance_usdt`,
`chart_interval`, `model_name`.

`_apply_settings(row)` in `bot_control.py` mutates the live `settings` dataclass so all services pick up changes without restart. Called on server startup (if a row exists) and after every successful `PUT /api/bot/config`.

`_migrate_bot_config()` in `init_db.py` uses `inspect(engine)` + `ALTER TABLE` to add any missing columns to existing databases — safe for hot upgrades.

### Dynamic symbol changes (`binance_ws.py`)

`BinanceWebSocketService._build_url()` reads `settings.tracked_symbols` at connection time — not on init. `run_forever` rebuilds the URL on each reconnect iteration. When `PUT /api/bot/config` receives a new `tracked_symbols` value, it calls `asyncio.create_task(ws_service.reconnect())` which closes the active WebSocket and triggers an immediate reconnect with the new symbol list.

### Claude integration (`ai_service.py`)

- Model is configurable via `settings.model_name` (default `claude-sonnet-4-6`).
- **Dynamic system prompt**: `_build_system_prompt()` reads `settings.min_confidence` and `settings.max_position_pct` at call time — no hardcoded values.
- **Position context**: `=== CURRENT POSITIONS ===` block tells Claude which assets are flat vs open with current P&L. Constrains: open position → SELL or HOLD only; flat → BUY or HOLD only.
- **Previous decision context**: `=== PREVIOUS DECISIONS (last cycle) ===` block passes the last action/confidence/reasoning per symbol so Claude reasons about continuity.
- Prompt also includes: live price/bid/ask, 24h high/low/change/volume, last 6 hourly closes (trend line), sentiment score, Fear & Greed index, top headlines.
- Response parsed from JSON array, validated; confidence below threshold forces HOLD; missing assets default to HOLD.

### Risk layer (`risk_service.py`)

- In-memory `_positions` dict, restored from DB on startup.
- Stop-loss and take-profit checked on every WebSocket tick via `check_exit_conditions`.
- Kill switch reads `KILL_SWITCH` env var at evaluation time (hot-reload without restart).

### Paper trading (`execution_service.py`)

- `_paper_usdt` tracks available USDT in memory, updated on every BUY/SELL.
- On startup, replays DB executions to restore the balance.
- `PAPER_TRADING=true` skips ccxt and writes a synthetic order to `logs/trades.jsonl`.

### OHLCV candles (`trading_cycle.py` + `data_feeds.py`)

- After building market data from the WebSocket, `_enrich_with_ohlcv` fetches 24 hourly candles per symbol via `fetch_ohlcv`.
- Replaces the WebSocket-derived OHLCV approximation with real candle data.
- Candle list is passed through to the AI prompt (last 6 closes shown as a trend).
- Falls back gracefully to WebSocket ticker data if the REST fetch fails.

---

## Dashboard

### Settings page (`dashboard/app/settings/page.tsx`)

- Fetches `GET /api/bot/status` on mount and polls every 5 s.
- When `running: true`, shows an amber lock banner and disables all trading-critical fields (`tracked_symbols`, `interval_minutes`, `model_name`, `min_confidence`, `max_position_pct`, `max_total_exposure_pct`, `stop_loss_pct`, `take_profit_pct`, `paper_balance_usdt`).
- `chart_interval` is display-only and remains editable while the bot runs.
- Save/Revert buttons also disabled when trading is locked.

### Candlestick chart (`dashboard/app/components/candlestick-chart.tsx`)

Built on [Lightweight Charts](https://tradingview.github.io/lightweight-charts/).

**Price lines** (all optional via toggle buttons):
| Line | Colour | Style | Notes |
|------|--------|-------|-------|
| Live price | Yellow `#eab308` | Solid | Updated on every WS tick via `IPriceLine.applyOptions()` — no re-create cost |
| Entry | Slate `#94a3b8` | Dotted | Shown when a position is open |
| Stop loss | Red `#ef4444` | Dashed | Shown when a position is open |
| Take profit | Green `#22c55e` | Dashed | Shown when a position is open with TP set |

**Toggle pattern**: `showRef` (ref) is updated synchronously in `toggleLine()` so stable `useCallback` handlers read the current value immediately; `show` (state) triggers a re-render + `useEffect` that redraws position lines.

**Candle colours**: TradingView-style teal `#089981` up / red `#f23645` down.

**Position row** shows: Entry price, SL price + `-X.XX%` from entry, TP price + `+X.XX%` from entry, position size %.

### Action badge colours (`dashboard/app/components/dashboard.tsx`)

- BUY → green outline badge
- SELL → red outline badge
- HOLD → amber outline badge

---

## Planned features

- Bayesian trade scoring
- Multi-timeframe signal confirmation
- Live `/positions` endpoint filtered to open positions only
