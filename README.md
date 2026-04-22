# Claude Trader

A prototype cryptocurrency trading bot powered by Claude AI. It ingests live Binance prices via WebSocket, scores market sentiment from RSS and Reddit, asks Claude for trading decisions, enforces risk rules, and executes (paper) trades — all on an hourly cycle.

> **Status: prototype / paper-trading only by default.** Real order placement requires Binance API keys and `PAPER_TRADING=false`.

---

## Features

- **Live prices** via Binance WebSocket (`@ticker` combined stream)
- **AI decisions** via Claude (`claude-sonnet-4-6`) — returns structured JSON decisions
- **Sentiment scoring** from crypto RSS feeds, Reddit, and the Fear & Greed index
- **Risk management** — confidence threshold, max position size, max total exposure, kill switch, stop-loss
- **Paper trading** by default — no real money moved
- **Persistent storage** — SQLite via SQLAlchemy (snapshots, positions, AI decisions, executions)
- **REST API** — health, assets, positions, decisions endpoints
- **Hourly trading cycle** — APScheduler fires at the top of every hour

---

## Architecture

```
crypto_bot/app/
├── main.py                  # FastAPI app, lifespan (WebSocket + scheduler)
├── config.py                # Settings loaded from .env
├── db/
│   ├── session.py           # SQLAlchemy engine + SessionLocal
│   └── init_db.py           # Creates tables on startup
├── models/                  # SQLAlchemy ORM (Asset, Snapshot, Position, AIDecision, Execution)
├── api/routes/              # GET /health  /assets  /positions  /decisions
└── services/
    ├── binance_ws.py        # WebSocket → MarketStateStore (reconnects on error)
    ├── market_state.py      # In-memory live price cache
    ├── data_feeds.py        # ccxt helpers (balance, OHLCV fallback)
    ├── sentiment_service.py # RSS + Reddit + Fear & Greed scoring
    ├── ai_service.py        # Claude API call, JSON parse + validate
    ├── risk_service.py      # RiskService class — position tracking, filters
    ├── execution_service.py # ExecutionService class — paper / live orders
    └── trading_cycle.py     # TradingCycleService — hourly orchestration
```

---

## Data Flow

```
Binance WebSocket ──► MarketStateStore (in-memory)
                                │
           APScheduler @ :00   │
                    ▼           ▼
            TradingCycleService.run()
                    │
                    ├─ build_market_data() from MarketStateStore
                    ├─ get_all_sentiment()  ← RSS / Reddit / Fear & Greed
                    ├─ get_trading_decisions() ← Claude API
                    ├─ RiskService.filter_decisions()
                    ├─ ExecutionService.execute_decision()  ← paper or live
                    └─ persist to SQLite (Snapshot → Position → AIDecision → Execution)
```

---

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd claude_trader
```

### 2. Create a virtual environment

```bash
python -m venv crypto_bot/env

# Windows
source crypto_bot/env/Scripts/activate

# macOS / Linux
source crypto_bot/env/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp crypto_bot/.env.example crypto_bot/.env
# Edit crypto_bot/.env and set ANTHROPIC_API_KEY at minimum
```

### 5. Run the server

```bash
cd crypto_bot
uvicorn app.main:app --reload
```

Server starts at `http://localhost:8000`. Interactive API docs at `/docs`.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Claude API key |
| `BINANCE_API_KEY` | *(empty)* | Binance key — only for live trading |
| `BINANCE_API_SECRET` | *(empty)* | Binance secret — only for live trading |
| `PAPER_TRADING` | `true` | Simulate trades, no real orders |
| `PAPER_BALANCE_USDT` | `10000` | Simulated portfolio size |
| `KILL_SWITCH` | `false` | Set `true` to halt all new positions |
| `TRACKED_SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Comma-separated Binance symbols |
| `MODEL_NAME` | `claude-sonnet-4-6` | Claude model ID |
| `MIN_CONFIDENCE` | `0.7` | Minimum confidence to act on a decision |
| `MAX_POSITION_PCT` | `20` | Max % of portfolio per position |
| `MAX_TOTAL_EXPOSURE_PCT` | `60` | Max % of portfolio in open positions |
| `STOP_LOSS_PCT` | `5` | Stop-loss threshold (% below entry) |
| `DATABASE_URL` | `sqlite:///./crypto_bot.db` | SQLAlchemy database URL |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Bot status + config summary |
| `GET` | `/assets` | Tracked assets in DB |
| `GET` | `/positions` | Position snapshots |
| `GET` | `/decisions` | AI decision history |

---

## Trading Logic

Each hour:

1. **Market data** is read directly from the in-memory WebSocket cache (no extra HTTP call). Data includes price, bid, ask, 24h high/low, and volume.
2. **Sentiment** is gathered from crypto RSS feeds (CoinDesk, CoinTelegraph, Decrypt), Reddit (r/cryptocurrency, r/bitcoin, r/ethtrader), and the Fear & Greed index. Scores are TextBlob polarity-weighted.
3. **Claude** receives a formatted prompt with market + sentiment data and returns a JSON array of `{asset, action, confidence, size_pct, reasoning}` decisions.
4. **Risk filters** are applied:
   - Kill switch → HOLD all
   - Confidence < `MIN_CONFIDENCE` → HOLD
   - Size > `MAX_POSITION_PCT` → capped
   - Total exposure > `MAX_TOTAL_EXPOSURE_PCT` → HOLD new BUYs
   - Stop-loss triggered → force SELL
5. **Execution** (paper mode): logs a synthetic `paper_filled` order to `logs/trades.jsonl`. In live mode, uses ccxt to place a Binance market order.
6. **Persistence**: each symbol gets a `HourlyMarketSnapshot → Position → AIDecision → Execution` chain written to SQLite.

---

## Modularity & Upgrade Path

The service layer is loosely coupled by design:

| Want to change | Where to look |
|---|---|
| Switch AI provider | `ai_service.py` — replace `_get_client()` + `get_trading_decisions()` |
| Add more sentiment sources | `sentiment_service.py` — new `_source_sentiment()` function |
| Switch exchange | `data_feeds.py` + `binance_ws.py` |
| Add live execution | Set `PAPER_TRADING=false`, supply Binance keys |
| Use PostgreSQL | Set `DATABASE_URL=postgresql://...` |
| Add Bayesian scoring | Inject a scorer into `TradingCycleService` |
| Tune cycle frequency | Change `CronTrigger(minute=0)` in `main.py` |

---

## Known Limitations (Prototype)

- **In-memory positions**: `RiskService._positions` is not persisted; position state resets on restart.
- **Blocking cycle**: The hourly cycle runs sync in the async event loop. For high-frequency use, move to a thread pool (`run_in_executor`).
- **No backtesting**: No historical simulation mode.
- **Sentiment latency**: RSS + Reddit fetches can take 20–60 seconds per cycle.
- **No partial fills**: Paper trading assumes full fills at the last price.
- **Single DB**: SQLite works for one instance; switch to PostgreSQL for production.

---

## Future Improvements

- Persist positions to DB (survive restarts)
- Bayesian trade scoring
- Entry/exit condition logic (not just BUY/SELL)
- WebSocket-triggered stop-loss (instant, not just on next cycle)
- Frontend dashboard
- Backtesting mode
- Multi-exchange support
