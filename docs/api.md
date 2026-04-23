# API

The bot exposes a REST API via FastAPI. Interactive docs are available at `http://localhost:8000/docs` when the server is running.

All endpoints are unauthenticated — the API is intended for local use only.

---

## Bot control

### `POST /start`

Starts the trading cycle scheduler. Fires one cycle immediately, then runs on `interval_minutes` cadence. No-op if already running.

### `POST /stop`

Pauses the scheduler. Open positions are not closed; stop-loss/take-profit resume on the next start.

### `POST /reset-db`

Drops and recreates all database tables. Resets in-memory positions and paper balance. **Disabled while the bot is running.**

---

## Config

### `GET /api/bot/config`

Returns the current persistent configuration.

### `PUT /api/bot/config`

Updates the persistent configuration. Mutates the live `settings` singleton immediately — no restart needed.

**Body fields:**

| Field | Type | Notes |
|---|---|---|
| `interval_minutes` | integer | Cycle cadence |
| `min_confidence` | float | Minimum confidence to act on a decision |
| `max_position_pct` | float | Max portfolio % per position |
| `max_total_exposure_pct` | float | Max total portfolio % in open positions |
| `stop_loss_pct` | float | Stop-loss drawdown threshold |
| `take_profit_pct` | float | Take-profit gain threshold (0 = disabled) |
| `tracked_symbols` | string | Comma-separated, e.g. `"BTCUSDT,ETHUSDT"` |
| `paper_balance_usdt` | float | Starting paper balance |
| `chart_interval` | string | Default chart timeframe (display only) |
| `model_name` | string | Claude model, e.g. `"claude-sonnet-4-6"` |
| `ohlcv_interval` | string | OHLCV fetch interval: `1m` `5m` `15m` `1h` `4h` `1d` |
| `taker_fee_rate` | float | Paper trade fee rate (default `0.001` = 0.1%) |
| `timezone` | string | IANA timezone for display (display only) |
| `display_currency` | string | Display currency (display only) |

---

## Health & status

### `GET /health`

Returns operational status and live diagnostics.

**Example response:**
```json
{
  "status": "ok",
  "paper_trading": true,
  "tracked_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "model": "claude-sonnet-4-6",
  "bot_running": true,
  "ws_connected": true,
  "ws_symbols": ["btcusdt", "ethusdt", "solusdt"],
  "last_cycle_time": "2026-04-23T14:00:01.234567",
  "last_ai_time": "2026-04-23T14:00:00.987654",
  "last_ai_failure": null,
  "sentiment_cache": {
    "BTCUSDT": {"cached": true, "age_seconds": 412.3},
    "ETHUSDT": {"cached": false, "age_seconds": null}
  },
  "ohlcv_interval": "1h",
  "taker_fee_rate": 0.001
}
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` (server is up) |
| `paper_trading` | boolean | Paper trading mode active |
| `tracked_symbols` | array | Symbols from live settings |
| `model` | string | Current Claude model |
| `bot_running` | boolean | Scheduler active |
| `ws_connected` | boolean | WebSocket received a tick in the last 60 s |
| `ws_symbols` | array | Symbols the WebSocket is subscribed to |
| `last_cycle_time` | string or null | Timestamp of last `cycle_end` event from `system_logs` |
| `last_ai_time` | string or null | Timestamp of last `ai_request` event |
| `last_ai_failure` | string or null | Timestamp of last `ai_request_failed` event; null if none |
| `sentiment_cache` | object | Per-symbol cache status (cached, age in seconds) |
| `ohlcv_interval` | string | Active OHLCV interval |
| `taker_fee_rate` | float | Active fee rate |

### `GET /api/bot/status`

Returns `{ "running": bool, ... }`. Used by the settings page to lock/unlock fields while the bot is running.

---

## Market data

### `GET /market`

Returns live WebSocket state for all tracked symbols — price, bid, ask, 24h stats, and current open position.

### `GET /chart/history`

Historical OHLCV candles for chart seeding.

**Query params:** `symbol` (default `BTCUSDT`), `interval` (default `1m`).

**Response:** `{ "symbol", "interval", "candles": [{ "time", "open", "high", "low", "close", "volume" }] }`

`time` is Unix timestamp in **seconds** (UTC). Up to 200 candles.

### `WS /ws/chart`

Real-time chart stream. After connecting, send:
```json
{ "type": "subscribe", "symbol": "BTCUSDT", "interval": "1m" }
```

Server pushes messages of type `candle`, `price`, and `position`.

---

## Decisions & positions

### `GET /decisions`

All AI decision records (post-risk-filter). Most recent last.

**Additional field vs. previous version:** `decision_source` — `"ai"` or `"fallback_rule"`.

### `GET /positions`

All position snapshot records.

### `GET /assets`

All asset records (created on first cycle per symbol).

---

## Logs

### `GET /logs`

Paginated, filterable structured event log from the `system_logs` table.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `page_size` | integer | `50` | Items per page (max 200) |
| `level` | string | — | Filter: `INFO`, `WARNING`, `ERROR` |
| `component` | string | — | Filter by service component (e.g. `trading_cycle`) |
| `symbol` | string | — | Filter by asset symbol (e.g. `BTCUSDT`) |
| `event_type` | string | — | Filter by event type (e.g. `cycle_end`) |
| `cycle_id` | string | — | Filter by cycle ID (timestamp string) |
| `since` | ISO datetime | — | Return only entries at or after this time |
| `until` | ISO datetime | — | Return only entries before this time |

**Example request:**
```
GET /logs?level=ERROR&component=trading_cycle&page=1&page_size=25
```

**Example response:**
```json
{
  "total": 3,
  "page": 1,
  "page_size": 25,
  "pages": 1,
  "items": [
    {
      "id": 142,
      "created_at": "2026-04-23T14:00:02.345678",
      "level": "ERROR",
      "component": "trading_cycle",
      "event_type": "ai_request_failed",
      "symbol": null,
      "cycle_id": "2026-04-23T14:00:01.123456",
      "message": "Claude API error: 529 overloaded",
      "details_json": null
    }
  ]
}
```

**Item fields:**

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-incremented primary key |
| `created_at` | string | UTC timestamp (naive ISO 8601) |
| `level` | string | `INFO`, `WARNING`, or `ERROR` |
| `component` | string | Service that produced the event |
| `event_type` | string | Machine-readable event type |
| `symbol` | string or null | Asset symbol if event is symbol-specific |
| `cycle_id` | string or null | Trading cycle identifier |
| `message` | string | Human-readable description |
| `details_json` | string or null | JSON-encoded extra context |

### `GET /logs/components`

Returns a list of distinct `component` values present in `system_logs`. Used to populate the component filter dropdown in the logs UI.

**Example response:** `["trading_cycle", "ai_service", "execution_service", "binance_ws", "trigger_executor", "startup"]`

### `GET /logs/event-types`

Returns a list of distinct `event_type` values present in `system_logs`.

**Example response:** `["cycle_start", "cycle_end", "ai_request", "ai_request_failed", "fallback_activated", "order_placed", "ws_connected", "server_start"]`

---

## Interactive Documentation

FastAPI automatically generates:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

---

## Adding New Endpoints

1. Create a file in `app/api/routes/`, e.g. `snapshots.py`
2. Define an `APIRouter` with appropriate prefix and tags
3. Implement route functions using the `get_db` dependency for DB access
4. Register in `main.py`: `app.include_router(router)`

To expose live in-memory state, import from `app/state.py` (not `main.py` — circular import):

```python
from app.state import risk_service

@router.get("/live-positions")
def live_positions():
    return [
        {"symbol": sym, "entry_price": pos.entry_price, "size_pct": pos.size_pct}
        for sym, pos in risk_service.get_open_positions().items()
    ]
```
