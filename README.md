# Claude Trader

A prototype cryptocurrency trading bot powered by Claude AI. It ingests live Binance prices via WebSocket, scores market sentiment from RSS and Reddit, asks Claude for trading decisions, enforces risk rules, and executes (paper) trades — running immediately on startup then every hour.

> **Status: prototype / paper-trading only by default.** Real order placement requires Binance API keys and `PAPER_TRADING=false`.

---

## Features

- **Live prices** via Binance WebSocket (`@ticker` combined stream) with auto-reconnect
- **Real-time stop-loss & take-profit** — triggered on each WebSocket price tick, not just hourly
- **AI decisions** via Claude (`claude-sonnet-4-6`) — structured JSON with confidence scoring
- **Sentiment scoring** from crypto RSS feeds, Reddit, and the Fear & Greed index
- **Entry / exit guards** — prevents stacking positions, prevents selling when flat
- **Risk management** — confidence threshold, max position size, max exposure cap, kill switch
- **Immediate start** — first cycle runs on startup, then every hour from that point
- **Paper trading** by default — no real money moved
- **Persistent storage** — SQLite via SQLAlchemy (snapshots, positions, AI decisions, executions)
- **REST API** — `/health`, `/assets`, `/positions`, `/decisions`

---

## Architecture

```
crypto_bot/app/
├── main.py                   # FastAPI app + service wiring + scheduler
├── config.py                 # All settings, loaded from .env
├── db/
│   ├── session.py            # SQLAlchemy engine + SessionLocal
│   └── init_db.py            # Creates tables on startup
├── models/                   # ORM: Asset, HourlyMarketSnapshot, Position, AIDecision, Execution
├── api/routes/               # GET /health  /assets  /positions  /decisions
└── services/
    ├── binance_ws.py         # WebSocket → MarketStateStore + exit trigger checks
    ├── market_state.py       # In-memory live price cache (thread-safe reads)
    ├── trigger_executor.py   # Async queue processor for real-time stop-loss / take-profit
    ├── data_feeds.py         # ccxt helpers: balance fetch, OHLCV fallback
    ├── sentiment_service.py  # RSS + Reddit + Fear & Greed sentiment scoring
    ├── ai_service.py         # Claude API call, JSON parse + validate + fallback
    ├── risk_service.py       # Position tracking, entry/exit guards, all risk filters
    ├── execution_service.py  # Paper or live order execution via ccxt
    └── trading_cycle.py      # Hourly orchestration: data → AI → risk → execute → persist
```

---

## Full Runtime Workflow

The bot runs two independent loops simultaneously from startup.

### Loop 1 — WebSocket price stream (continuous)

```
Binance WebSocket (@ticker)
        │
        ▼ every price tick (~1s per symbol)
BinanceWebSocketService._handle_message()
        │
        ├─ MarketStateStore.update()        ← live price cache updated
        │
        └─ RiskService.check_exit_conditions(symbol, price)
                │
                ├─ price still OK? → nothing
                │
                └─ stop-loss OR take-profit breached?
                        │
                        ├─ position removed immediately (prevents double-trigger)
                        └─ SELL order → asyncio.Queue
                                │
                                ▼
                        TriggerExecutor (background task)
                                │
                                └─ ExecutionService.execute_decision()
                                        ├─ paper: logs to logs/trades.jsonl
                                        └─ live:  places market order via ccxt
```

### Loop 2 — Hourly trading cycle (APScheduler)

```
Startup → fires immediately, then every 60 minutes
        │
        ▼
TradingCycleService.run()
        │
        ├─ 1. build_market_data()
        │       └─ reads live prices from MarketStateStore (no extra HTTP call)
        │
        ├─ 2. get_all_sentiment()
        │       ├─ RSS feeds: CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine
        │       ├─ Reddit: r/cryptocurrency, r/bitcoin, r/ethtrader
        │       └─ Fear & Greed index (alternative.me API)
        │
        ├─ 3. get_trading_decisions()  ← Claude API
        │       ├─ sends formatted prompt: price + sentiment per symbol
        │       ├─ receives JSON array: [{asset, action, confidence, size_pct, reasoning}]
        │       └─ fallback: all HOLD if API fails
        │
        ├─ 4. RiskService.filter_decisions()
        │       ├─ kill switch active?          → HOLD all
        │       ├─ already holding + BUY?       → HOLD  (entry guard)
        │       ├─ no position + SELL?          → HOLD  (exit guard)
        │       ├─ confidence < MIN_CONFIDENCE? → HOLD
        │       ├─ size > MAX_POSITION_PCT?     → cap size
        │       ├─ exposure > MAX_EXPOSURE?     → HOLD new BUYs
        │       └─ stop-loss / take-profit hit? → force SELL
        │
        ├─ 5. ExecutionService.execute_decision()
        │       ├─ HOLD: log only
        │       ├─ paper: synthetic paper_filled order → logs/trades.jsonl
        │       └─ live:  ccxt market order → Binance
        │
        └─ 6. Persist to SQLite per symbol:
                HourlyMarketSnapshot → Position → AIDecision → Execution
```

---

## Entry & Exit Conditions

### Entry (BUY)
| Condition | Result |
|---|---|
| No existing position | BUY allowed (subject to risk checks) |
| Already holding the asset | Forced to HOLD — no stacking |
| Confidence below `MIN_CONFIDENCE` | Forced to HOLD |
| Would breach `MAX_TOTAL_EXPOSURE_PCT` | Forced to HOLD |
| Kill switch active | Forced to HOLD |

### Exit (SELL)
| Condition | Result |
|---|---|
| Price drops ≥ `STOP_LOSS_PCT` below entry | Instant SELL via WebSocket tick |
| Price rises ≥ `TAKE_PROFIT_PCT` above entry | Instant SELL via WebSocket tick |
| AI returns SELL with open position | SELL executed on next hourly cycle |
| AI returns SELL with no position | Forced to HOLD — nothing to sell |

---

## Setup

### 1. Create a virtual environment and install dependencies

```bash
cd claude_trader

python -m venv crypto_bot/env

# Windows
source crypto_bot/env/Scripts/activate

# macOS / Linux
source crypto_bot/env/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp crypto_bot/.env.example crypto_bot/.env
# Edit crypto_bot/.env — set ANTHROPIC_API_KEY at minimum
```

### 3. Run

```bash
cd crypto_bot
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. API docs at `/docs`.

The first trading cycle fires immediately on startup. Subsequent cycles run every 60 minutes from that point.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Claude API key |
| `BINANCE_API_KEY` | *(empty)* | Binance key — only needed for live trading |
| `BINANCE_API_SECRET` | *(empty)* | Binance secret — only needed for live trading |
| `PAPER_TRADING` | `true` | Simulate trades, no real orders sent |
| `PAPER_BALANCE_USDT` | `10000` | Simulated portfolio size |
| `KILL_SWITCH` | `false` | Set `true` to halt all new positions immediately |
| `TRACKED_SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Comma-separated Binance symbols |
| `MODEL_NAME` | `claude-sonnet-4-6` | Claude model ID |
| `MIN_CONFIDENCE` | `0.7` | Minimum Claude confidence score to act (0–1) |
| `MAX_POSITION_PCT` | `20` | Max % of portfolio per single position |
| `MAX_TOTAL_EXPOSURE_PCT` | `60` | Max % of portfolio across all open positions |
| `STOP_LOSS_PCT` | `5` | Stop-loss threshold — % below entry price |
| `TAKE_PROFIT_PCT` | `0` | Take-profit threshold — % above entry (0 = disabled) |
| `DATABASE_URL` | `sqlite:///./crypto_bot.db` | SQLAlchemy database URL |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Bot status, config summary |
| `GET` | `/assets` | Tracked assets recorded in DB |
| `GET` | `/positions` | Position snapshots per cycle |
| `GET` | `/decisions` | AI decision history |

---

## Trade Log

Every execution (including HOLDs) is appended to `logs/trades.jsonl`. Each line is a JSON object:

```json
{
  "timestamp": "2026-04-22T19:00:01Z",
  "asset": "BTCUSDT",
  "action": "BUY",
  "confidence": 0.85,
  "size_pct": 10,
  "current_price": 93000.0,
  "portfolio_usdt": 10000.0,
  "reasoning": "Strong momentum with positive sentiment...",
  "paper_trading": true,
  "order": { "id": "PAPER-...", "qty": 0.01075, "price": 93000.0, "status": "paper_filled" },
  "error": null
}
```

---

## Modularity & Upgrade Path

| Want to change | Where to look |
|---|---|
| Switch AI provider | `ai_service.py` — replace `_get_client()` and `get_trading_decisions()` |
| Add more sentiment sources | `sentiment_service.py` — add a new `_source_sentiment()` function |
| Switch exchange | `data_feeds.py` + `binance_ws.py` |
| Enable live trading | Set `PAPER_TRADING=false`, supply Binance API keys |
| Use PostgreSQL | Set `DATABASE_URL=postgresql://user:pass@host/db` |
| Change cycle frequency | Edit `IntervalTrigger(hours=1)` in `main.py` |
| Add Bayesian scoring | Inject a scorer into `TradingCycleService` |
| Persist positions across restarts | Back `RiskService._positions` with the DB `Position` table |

---

## Known Limitations (Prototype)

- **In-memory positions** — `RiskService._positions` resets on restart; open positions are forgotten
- **Blocking cycle** — the hourly cycle runs sync in the async event loop (fine for one instance, not for high-frequency)
- **Sentiment latency** — RSS + Reddit fetches can take 20–60 seconds per cycle
- **No partial fills** — paper trading assumes full fill at the last WebSocket price
- **Single instance** — SQLite + in-memory state; not suitable for horizontal scaling
- **No backtesting** — no historical simulation mode

---

## Future Improvements

- Persist positions to DB so they survive restarts
- Bayesian trade scoring on top of AI decisions
- Frontend dashboard for live monitoring
- Backtesting mode against historical data
- Multi-exchange support via ccxt abstraction
- Async sentiment fetching to reduce cycle latency
