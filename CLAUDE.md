# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

All commands run from the `crypto_bot/` directory with the `env` virtualenv activated:

```bash
# Activate virtualenv (Windows)
source env/Scripts/activate

# Start the FastAPI server (development)
cd crypto_bot
uvicorn app.main:app --reload

# Install dependencies
pip install -r requirements.txt
```

The server starts on `http://localhost:8000`. API docs at `/docs`.

## Environment

Copy `crypto_bot/.env.example` to `crypto_bot/.env`. The only required key for paper trading is `ANTHROPIC_API_KEY`. Binance keys are only needed for live order placement.

Key settings (all configurable via `.env`):
- `PAPER_TRADING=true` — simulates trades, no real orders sent
- `KILL_SWITCH=false` — set to `true` to halt all new positions immediately
- `TRACKED_SYMBOLS` — comma-separated list, e.g. `BTCUSDT,ETHUSDT,SOLUSDT`
- `MIN_CONFIDENCE`, `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `STOP_LOSS_PCT` — risk parameters

## Architecture

```
crypto_bot/
  app/
    main.py              # FastAPI app, APScheduler setup, startup/shutdown hooks
    config.py            # Settings dataclass loaded from .env
    db/
      session.py         # SQLAlchemy engine + SessionLocal
      init_db.py         # Creates tables on startup
    models/              # SQLAlchemy ORM models (Asset, HourlyMarketSnapshot, Position, AIDecision, Execution)
    api/routes/          # REST endpoints: /health, /assets, /positions, /decisions
    services/
      binance_ws.py      # Persistent WebSocket to Binance; populates MarketStateStore
      market_state.py    # In-memory store of live ticker data (SymbolMarketState)
      trading_cycle.py   # Hourly cron job: snapshot → AI decision → risk check → execution → DB persist
      ai_service.py      # Claude API call; builds prompt, parses/validates JSON response
      risk_service.py    # Position tracking, kill switch, stop-loss logic, exposure caps
      execution_service.py # Paper or live order placement via ccxt; logs to logs/trades.jsonl
      data_feeds.py      # ccxt exchange factory and market data fetch helpers
      sentiment_service.py # Sentiment scoring (NLTK/TextBlob + Fear & Greed index)
```

### Data flow

1. **BinanceWebSocketService** connects to Binance's combined stream (`@ticker`) and continuously updates `MarketStateStore` with live prices.
2. **APScheduler** fires `TradingCycleService.run()` at the top of every hour.
3. The cycle reads live state from `MarketStateStore`, calls `ai_service.get_trading_decisions()` (Claude API), passes results through `risk_service.filter_decisions()`, then calls `execution_service.execute_decision()`.
4. All results (snapshot, position, AI decision, execution) are written to SQLite via SQLAlchemy.

### Claude integration (`ai_service.py`)

- Uses `claude-sonnet-4-6` via the `anthropic` SDK.
- The system prompt constrains Claude to return **only** a JSON array. The response is parsed, validated, and any missing assets are defaulted to `HOLD`.
- Confidence below `MIN_CONFIDENCE` (default 0.7) forces a `HOLD` regardless of Claude's output.

### Risk layer (`risk_service.py`)

- In-memory `_positions` dict tracks open positions (module-level state, not DB-backed).
- Stop-loss is checked on each WebSocket tick (price-based, not order-book based).
- Kill switch reads `KILL_SWITCH` env var at evaluation time (hot-reload without restart).

### Paper trading

When `PAPER_TRADING=true`, `execution_service` skips ccxt and writes a synthetic order to `logs/trades.jsonl`. Real market data still flows through the WebSocket.

## Planned features (from README)

- Live stop-loss checking via Binance WebSocket (in progress on `feat/websocket_streaming`)
- Bayesian trade scoring
- Entry/exit condition logic
- Backend refactor/file-structure cleanup
