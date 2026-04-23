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

---

### `GET /market`

Returns the current live market state for all tracked symbols, read directly from the in-memory `MarketStateStore`. Data is updated on every Binance WebSocket tick.

**Route:** `app/api/routes/market.py`

**Example response:**
```json
{
  "BTCUSDT": {
    "symbol": "BTCUSDT",
    "price": 83421.50,
    "bid": 83420.00,
    "ask": 83423.00,
    "change_24h_pct": 1.42,
    "high_24h": 84100.00,
    "low_24h": 81500.00,
    "updated_at": "2026-04-23T14:00:01.123456+00:00",
    "position": {
      "entry_price": 82000.00,
      "size_pct": 20,
      "stop_loss_price": 80360.00,
      "take_profit_price": null
    }
  },
  "ETHUSDT": {
    "symbol": "ETHUSDT",
    "price": 2379.86,
    "position": null,
    ...
  }
}
```

**The `position` field** is non-null when an open position exists for that symbol. It is computed from `RiskService._positions` and the configured `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT`. The dashboard uses this to draw reference lines on the price chart.

**Notes:**
- Returns an empty object `{}` before the WebSocket has received its first tick.
- Used by the old Recharts line chart. The TradingView chart uses `GET /chart/history` + `WS /ws/chart` instead.

---

### `GET /chart/history`

Returns historical OHLCV candlestick data for a symbol and interval. Used to seed the TradingView chart with historical bars before switching to the live WebSocket feed.

**Route:** `app/api/routes/chart.py`

**Query parameters:**

| Parameter | Default | Allowed values |
|---|---|---|
| `symbol` | `BTCUSDT` | Any Binance symbol |
| `interval` | `1m` | `1m` `3m` `5m` `15m` `30m` `1h` `2h` `4h` `6h` `12h` `1d` `1w` |

**Example request:**
```
GET http://localhost:8000/chart/history?symbol=ETHUSDT&interval=5m
```

**Example response:**
```json
{
  "symbol": "ETHUSDT",
  "interval": "5m",
  "candles": [
    { "time": 1745366400, "open": 2371.20, "high": 2385.00, "low": 2368.50, "close": 2379.86, "volume": 1243.7 },
    ...
  ]
}
```

`time` is a Unix timestamp in **seconds** (UTC), as required by TradingView Lightweight Charts. Up to 200 candles are returned (100 for `1w`).

**Implementation:** fetches via `ccxt.binance.fetch_ohlcv()` run in a thread (`asyncio.to_thread`) so the event loop is not blocked.

---

### `WS /ws/chart`

A WebSocket endpoint that streams real-time candlestick updates for a subscribed symbol. Unlike the REST endpoint, this pushes the current incomplete candle as it builds tick-by-tick.

**Route:** `app/api/routes/chart.py`

#### Subscribe

After connecting, send a subscribe message to begin receiving data:

```json
{ "type": "subscribe", "symbol": "BTCUSDT", "interval": "1m" }
```

The server immediately responds with the current open position (if any) and then begins streaming candle and price updates.

#### Server → Client message types

**`candle`** — the current live OHLCV bar, sent whenever the price changes (max 5 Hz):
```json
{
  "type": "candle",
  "data": {
    "time": 1745366400,
    "open": 83400.00,
    "high": 83450.00,
    "low": 83390.00,
    "close": 83421.50,
    "volume": 0.0
  }
}
```

Pass `data` directly to `series.update()` in TradingView Lightweight Charts. TradingView updates the current bar in place if `time` matches the last bar, or appends a new bar when the interval rolls over.

**`price`** — the latest tick price and 24h change, for the header display:
```json
{ "type": "price", "price": 83421.50, "change_24h_pct": 1.42 }
```

**`position`** — sent once on subscribe if an open position exists for the symbol:
```json
{
  "type": "position",
  "entry_price": 82000.00,
  "stop_loss_price": 80360.00,
  "take_profit_price": null,
  "size_pct": 20
}
```

The dashboard uses this to draw `IPriceLine` annotations on the chart.

#### Implementation notes

- The server polls `MarketStateStore.get(symbol)` every 200 ms and only sends a `candle` message when the price has changed since the last poll.
- Candle boundaries are computed as `floor(now / interval_seconds) * interval_seconds`. When the current time crosses a boundary, a new candle starts.
- Volume is always `0.0` — the Binance ticker stream does not provide per-tick volume.
- The connection loop uses `asyncio.wait_for(ws.receive_json(), timeout=0.05)` so it can push updates without being blocked waiting for client messages.

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

To expose live in-memory state, import from `app/state.py` (never from `main.py` — circular import):

```python
# app/api/routes/live_positions.py
from fastapi import APIRouter
from app.state import risk_service

router = APIRouter(tags=["positions"])

@router.get("/live-positions")
def live_positions():
    return [
        {
            "symbol": sym,
            "entry_price": pos.entry_price,
            "size_pct": pos.size_pct,
        }
        for sym, pos in risk_service.get_open_positions().items()
    ]
```

`app/state.py` holds all service singletons (`market_store`, `risk_service`, `execution_service`, etc.) and is safe to import from any route module.
