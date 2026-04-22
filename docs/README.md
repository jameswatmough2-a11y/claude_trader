# Claude Trader — Developer Documentation

Claude Trader is a prototype cryptocurrency trading bot. It connects to Binance via a persistent WebSocket for real-time price data, asks Claude AI for hourly trading decisions, applies a layered risk management system, and executes paper or live trades.

**Status:** prototype / paper trading by default. No real orders are placed unless you supply Binance API keys and explicitly set `PAPER_TRADING=false`.

---

## Documentation Index

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | System layers, component map, dependency graph |
| [workflow.md](workflow.md) | Step-by-step runtime: startup, continuous operation, hourly cycle |
| [services.md](services.md) | Deep dive into every service: inputs, outputs, logic, edge cases |
| [trading_logic.md](trading_logic.md) | AI prompt design, risk rules, execution sizing |
| [data_flow.md](data_flow.md) | How data moves from WebSocket tick to database record |
| [configuration.md](configuration.md) | Every environment variable, defaults, and behavioral impact |
| [api.md](api.md) | REST endpoints with example requests and responses |
| [persistence.md](persistence.md) | ORM models, what lives in the DB vs. in memory, restart behavior |
| [concurrency.md](concurrency.md) | Async architecture, two concurrent loops, race condition handling |
| [limitations.md](limitations.md) | Prototype constraints, things that would break in production |
| [upgrade_guide.md](upgrade_guide.md) | How to extend the system: new assets, exchanges, AI providers |

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.136 + Uvicorn |
| AI decisions | Anthropic Claude (`claude-sonnet-4-6`) via `anthropic` SDK 0.49 |
| Exchange connectivity | Binance WebSocket (`wss://data-stream.binance.vision`) + ccxt 4.3 |
| Task scheduler | APScheduler 3.10 (`AsyncIOScheduler`) |
| Database | SQLite via SQLAlchemy 2.0 ORM |
| Sentiment analysis | TextBlob + feedparser (RSS) + Reddit JSON API + Fear & Greed Index |
| Configuration | `python-dotenv` + Python dataclass |
| Async runtime | Python `asyncio` (single event loop, no threads except trigger executor) |

---

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd claude_trader

# 2. Create and activate a virtual environment
python -m venv crypto_bot/env
source crypto_bot/env/Scripts/activate    # Windows
# source crypto_bot/env/bin/activate      # macOS / Linux

# 3. Install dependencies
pip install -r crypto_bot/requirements.txt

# 4. Configure environment
cp crypto_bot/.env.example crypto_bot/.env
# Edit .env — at minimum, set ANTHROPIC_API_KEY

# 5. Start the server
cd crypto_bot
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. API docs at `/docs`.

The bot will:
1. Initialize the SQLite database
2. Connect to the Binance WebSocket price stream
3. Run the first trading cycle immediately
4. Run a trading cycle every subsequent hour

---

## Minimum Configuration

```env
ANTHROPIC_API_KEY=sk-ant-...
PAPER_TRADING=true
TRACKED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
```

With these three values set and Binance keys absent, the bot will:
- Receive real Binance market prices via WebSocket
- Ask Claude for trading decisions every hour
- Log all decisions to `logs/trades.jsonl`
- Store all decisions in `crypto_bot.db` (SQLite)
- Never place a real order

---

## Project Layout

```
claude_trader/
├── crypto_bot/
│   ├── app/
│   │   ├── main.py                 # FastAPI app + service wiring + startup/shutdown
│   │   ├── config.py               # Settings dataclass loaded from .env
│   │   ├── db/
│   │   │   ├── session.py          # SQLAlchemy engine + SessionLocal factory
│   │   │   └── init_db.py          # Creates tables on startup
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── asset.py
│   │   │   ├── hourly_market_snapshot.py
│   │   │   ├── position.py
│   │   │   ├── ai_decision.py
│   │   │   └── execution.py
│   │   ├── api/
│   │   │   ├── deps.py             # get_db dependency
│   │   │   └── routes/             # REST endpoints
│   │   │       ├── health.py
│   │   │       ├── assets.py
│   │   │       ├── positions.py
│   │   │       └── decisions.py
│   │   └── services/
│   │       ├── market_state.py     # In-memory price cache
│   │       ├── binance_ws.py       # Persistent WebSocket + real-time exit checks
│   │       ├── trigger_executor.py # Async queue consumer for stop-loss / take-profit
│   │       ├── data_feeds.py       # ccxt exchange factory + market data helpers
│   │       ├── sentiment_service.py# RSS + Reddit + Fear & Greed sentiment scoring
│   │       ├── ai_service.py       # Claude API call + prompt + response validation
│   │       ├── risk_service.py     # Position tracking + risk rule enforcement
│   │       ├── execution_service.py# Paper / live order placement
│   │       └── trading_cycle.py   # Hourly orchestrator (data → AI → risk → execute → persist)
│   ├── .env.example
│   └── requirements.txt
├── docs/                           # This documentation
└── CLAUDE.md
```
