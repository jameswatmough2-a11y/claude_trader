# v2 API

FastAPI backend. Multi-tenant. Clerk for auth.

## Architecture

Two planes:

- **Shared plane** (singletons): `binance_ws`, `market_state`, `sentiment_cache`,
  `sentiment_refresher`, `decision_provider`, `position_registry`,
  `trigger_executor`. One instance serves all tenants.

- **Per-tenant plane**: `risk_service`, `execution_service`, `trading_cycle`.
  Constructed on demand, held in `tenant_registry`, keyed by `tenant_id`.

Orchestration (`tick_scheduler`) runs one job every 60s that scans `bot_configs`
for tenants whose `next_run_at <= now()` and fans out cycles bounded by a
semaphore.

## Run

```bash
cd v2/api
python -m venv env
source env/Scripts/activate   # Windows: source env/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # fill in ANTHROPIC_API_KEY, CLERK_* vars
uvicorn app.main:app --reload --port 8001
```

## Database

SQLite at `v2/api/crypto_bot_v2.db` (separate from v1's DB — schema diverges).
