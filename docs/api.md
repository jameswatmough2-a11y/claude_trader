# API

The bot exposes a minimal read-only REST API via FastAPI. Interactive docs are available at `http://localhost:8000/docs` when the server is running.

All endpoints are read-only GET requests. There is no authentication — the API is intended for local inspection and monitoring only.

---

## Endpoints

### `GET /health`

Returns the current operational status and configuration summary.

**Route:** `app/api/routes/health.py`

**Example request:**
```
GET http://localhost:8000/health
```

**Example response:**
```json
{
  "status": "ok",
  "paper_trading": true,
  "tracked_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "model": "claude-sonnet-4-6"
}
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` (the endpoint exists at all means the server is up) |
| `paper_trading` | boolean | Whether paper trading mode is active |
| `tracked_symbols` | array of strings | Symbols being tracked (from `TRACKED_SYMBOLS` env var) |
| `model` | string | Claude model being used for decisions |

**Notes:**
- This endpoint does not check whether the WebSocket is connected, the AI service is reachable, or the database is writable. It only reflects startup-time configuration.
- Use this as a liveness check for process-level health only.

---

### `GET /assets`

Returns all assets registered in the database. An asset record is created the first time a symbol appears in a trading cycle.

**Route:** `app/api/routes/assets.py`

**Example request:**
```
GET http://localhost:8000/assets
```

**Example response:**
```json
[
  {
    "id": 1,
    "symbol": "BTCUSDT",
    "base_currency": "BTC",
    "quote_currency": "USDT"
  },
  {
    "id": 2,
    "symbol": "ETHUSDT",
    "base_currency": "ETH",
    "quote_currency": "USDT"
  },
  {
    "id": 3,
    "symbol": "SOLUSDT",
    "base_currency": "SOL",
    "quote_currency": "USDT"
  }
]
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-incremented primary key |
| `symbol` | string | Binance symbol (uppercase, e.g. `BTCUSDT`) |
| `base_currency` | string | Base asset (derived by stripping `USDT`, e.g. `BTC`) |
| `quote_currency` | string | Always `USDT` |

**Notes:**
- Returns an empty array `[]` before the first trading cycle has completed.
- Assets are created with `_get_or_create_asset()` in `trading_cycle.py`. The `base_currency` is derived as `symbol[:-4]` for USDT pairs (e.g. `BTCUSDT[:-4] = "BTC"`).
- The `positions` and `snapshots` relationships on each Asset are not included in this response (no lazy loading in the route).

---

### `GET /positions`

Returns all position snapshot records from the database. Each row represents the position state at the time of an hourly cycle for a given symbol.

**Route:** `app/api/routes/positions.py`

**Example request:**
```
GET http://localhost:8000/positions
```

**Example response:**
```json
[
  {
    "id": 1,
    "asset_id": 1,
    "side": "flat",
    "size": 0.0,
    "entry_price": null,
    "wallet_balance": 10000.0
  },
  {
    "id": 2,
    "asset_id": 2,
    "side": "flat",
    "size": 0.0,
    "entry_price": null,
    "wallet_balance": 10000.0
  }
]
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-incremented primary key |
| `asset_id` | integer | Foreign key → `assets.id` |
| `side` | string | Always `"flat"` (see note below) |
| `size` | float | Always `0.0` (see note below) |
| `entry_price` | float or null | Always `null` (see note below) |
| `wallet_balance` | float or null | USDT wallet balance at time of cycle (from `fetch_balance()`) |

**Important note on position data accuracy:**

The `Position` DB records do not reflect actual open positions. The `_persist_cycle` method always writes `side="flat", size=0, entry_price=None` regardless of what was just executed. Actual position state is tracked exclusively in `RiskService._positions` (in-memory). See [persistence.md](persistence.md) for full explanation.

To see the current live position state, you would need to expose a new endpoint that reads from `risk_service.get_open_positions()` — this is not currently implemented.

**When this returns data:**
- Returns `[]` before the first trading cycle
- One row per symbol per hourly cycle (grows over time)

---

### `GET /decisions`

Returns all AI decision records from the database. Each row represents Claude's recommendation for one symbol in one hourly cycle.

**Route:** `app/api/routes/decisions.py`

**Example request:**
```
GET http://localhost:8000/decisions
```

**Example response:**
```json
[
  {
    "id": 1,
    "snapshot_id": 1,
    "action": "BUY",
    "confidence_score": 0.82,
    "reasoning_summary": "Strong 24h momentum and positive sentiment support a long entry."
  },
  {
    "id": 2,
    "snapshot_id": 2,
    "action": "HOLD",
    "confidence_score": 0.54,
    "reasoning_summary": "Confidence below threshold — maintaining HOLD."
  },
  {
    "id": 3,
    "snapshot_id": 3,
    "action": "HOLD",
    "confidence_score": 0.91,
    "reasoning_summary": "No open position for SOLUSDT — ignoring SELL."
  }
]
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-incremented primary key |
| `snapshot_id` | integer | Foreign key → `hourly_market_snapshots.id` |
| `action` | string | `"BUY"`, `"SELL"`, or `"HOLD"` |
| `confidence_score` | float or null | Claude's stated confidence `[0.0, 1.0]` |
| `reasoning_summary` | string or null | Claude's one-sentence rationale (or risk override reason) |

**Notes:**
- The `model_name`, `prompt_version`, `recommended_size`, `recommended_stop_loss`, and `recommended_take_profit` columns exist in the DB but are not included in this API response.
- When the risk layer forces a decision to HOLD, the `reasoning_summary` is replaced with the risk rule's reason (e.g. `"No open position for SOLUSDT — ignoring SELL."`). Claude's original reasoning is overwritten.
- Returns `[]` before the first trading cycle.
- Records are returned in insertion order (most recent last).

---

## Interactive Documentation

FastAPI automatically generates:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

---

## Adding New Endpoints

To add a new endpoint:

1. Create a file in `app/api/routes/`, e.g. `snapshots.py`
2. Define an `APIRouter` with appropriate prefix and tags
3. Implement the route function using `get_db` dependency for DB access
4. Register the router in `main.py`:
   ```python
   from app.api.routes.snapshots import router as snapshots_router
   app.include_router(snapshots_router)
   ```

To expose live position state (in-memory, from `RiskService`):
```python
# In a new route file:
from app.main import risk_service  # import the singleton

@router.get("/live-positions")
def live_positions():
    positions = risk_service.get_open_positions()
    return [
        {
            "asset": p.asset,
            "entry_price": p.entry_price,
            "size_pct": p.size_pct,
            "current_price": p.current_price,
        }
        for p in positions.values()
    ]
```

Note: importing `risk_service` from `main.py` creates a circular dependency risk if the route module itself is imported by `main.py`. The cleanest solution is to store service singletons in a separate `app/state.py` module.
