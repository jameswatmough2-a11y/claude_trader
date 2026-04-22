# Claude Trader — Documentation

Claude Trader is a prototype cryptocurrency trading bot. It connects to Binance via WebSocket for live prices, asks Claude AI for hourly trading decisions, applies a risk management layer, and executes paper (or live) trades. The system is built as a FastAPI application with an async event loop driving two concurrent loops: a continuous price stream and an hourly decision cycle.

---

## Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| AI decisions | Anthropic Claude (`claude-sonnet-4-6`) |
| Exchange | Binance via WebSocket + ccxt |
| Scheduler | APScheduler (`AsyncIOScheduler`) |
| Database | SQLite via SQLAlchemy 2.0 (ORM) |
| Sentiment | TextBlob + feedparser + Reddit JSON API |
| Config | `python-dotenv` + dataclass |

**Status:** prototype. Paper trading is on by default. No real money moves unless you supply Binance API keys and set `PAPER_TRADING=false`.

---

## Quick Start

```bash
# 1. Create and activate virtualenv
python -m venv crypto_bot/env
source crypto_bot/env/Scripts/activate   # Windows
# source crypto_bot/env/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp crypto_bot/.env.example crypto_bot/.env
# Set ANTHROPIC_API_KEY in crypto_bot/.env

# 4. Run
cd crypto_bot
uvicorn app.main:app --reload
```

The first trading cycle fires immediately on startup. The server runs at `http://localhost:8000`.

---

## Documentation Index

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | System layers, components, how they connect |
| [workflow.md](workflow.md) | Step-by-step runtime flow from startup to execution |
| [services.md](services.md) | Deep breakdown of every service class and module |
| [trading_logic.md](trading_logic.md) | AI decisions, sentiment, risk rules, execution mechanics |
| [data_flow.md](data_flow.md) | How data moves through the system end to end |
| [configuration.md](configuration.md) | Every environment variable explained |
| [api.md](api.md) | REST endpoints with example requests and responses |
| [persistence.md](persistence.md) | Database schema, in-memory state, restart behaviour |
| [concurrency.md](concurrency.md) | WebSocket loop, scheduler, async queue, race conditions |
| [limitations.md](limitations.md) | Honest prototype constraints and production gaps |
| [upgrade_guide.md](upgrade_guide.md) | How to extend or replace any part of the system |

---

## Project Structure

```
claude_trader/
├── requirements.txt
├── README.md
├── docs/                         ← you are here
└── crypto_bot/
    ├── .env.example
    ├── .env                      ← your local config (not committed)
    ├── logs/
    │   └── trades.jsonl          ← execution log (appended each cycle)
    └── app/
        ├── main.py               ← FastAPI app, service wiring, lifespan
        ├── config.py             ← Settings dataclass loaded from .env
        ├── db/
        │   ├── session.py        ← SQLAlchemy engine + SessionLocal
        │   └── init_db.py        ← create tables on startup
        ├── models/               ← ORM models
        │   ├── asset.py
        │   ├── hourly_market_snapshot.py
        │   ├── position.py
        │   ├── ai_decision.py
        │   └── execution.py
        ├── api/
        │   └── routes/
        │       ├── health.py
        │       ├── assets.py
        │       ├── positions.py
        │       └── decisions.py
        └── services/
            ├── market_state.py
            ├── binance_ws.py
            ├── trigger_executor.py
            ├── data_feeds.py
            ├── sentiment_service.py
            ├── ai_service.py
            ├── risk_service.py
            ├── execution_service.py
            └── trading_cycle.py
```
