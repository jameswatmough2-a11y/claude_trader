# Configuration

All configuration is loaded from a `.env` file at `crypto_bot/.env` via `python-dotenv`. The `Settings` dataclass in `app/config.py` reads each variable at import time and exposes a single `settings` singleton.

---

## Full `.env` Reference

```env
# ── Required ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-api03-...

# ── Binance credentials (only needed for live order placement) ──────────────────
BINANCE_API_KEY=
BINANCE_API_SECRET=

# ── Bot behaviour ───────────────────────────────────────────────────────────────
PAPER_TRADING=true
PAPER_BALANCE_USDT=10000
KILL_SWITCH=false

# ── Symbols ─────────────────────────────────────────────────────────────────────
TRACKED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT

# ── AI model ────────────────────────────────────────────────────────────────────
MODEL_NAME=claude-sonnet-4-6

# ── Risk parameters ─────────────────────────────────────────────────────────────
MIN_CONFIDENCE=0.7
MAX_POSITION_PCT=20
MAX_TOTAL_EXPOSURE_PCT=60
STOP_LOSS_PCT=5
TAKE_PROFIT_PCT=0

# ── Database ────────────────────────────────────────────────────────────────────
DATABASE_URL=sqlite:///./crypto_bot.db
```

---

## Variable Reference

### `ANTHROPIC_API_KEY`

| | |
|---|---|
| **Required** | Yes (in all modes) |
| **Default** | `""` (empty string — raises `ValueError` on first AI call) |
| **Type** | String |

The Anthropic API key used to call Claude. Obtained from `https://console.anthropic.com`.

If absent, the bot will start successfully but the first hourly cycle will fail with `ValueError: ANTHROPIC_API_KEY is not configured`. All decisions will fall back to HOLD.

---

### `BINANCE_API_KEY` / `BINANCE_API_SECRET`

| | |
|---|---|
| **Required** | No (only for live trading) |
| **Default** | `""` (empty string) |
| **Type** | String |

Binance API credentials for placing live orders and fetching live account balances.

If absent:
- The WebSocket still connects (uses the public stream endpoint — no authentication)
- `fetch_balance()` returns the paper balance stub (see `PAPER_BALANCE_USDT`)
- Live order placement will fail (ccxt returns an authentication error)

Setting these without `PAPER_TRADING=false` has no effect — paper mode is checked before any ccxt order is placed.

---

### `PAPER_TRADING`

| | |
|---|---|
| **Required** | No |
| **Default** | `true` |
| **Type** | Boolean (`"true"` / `"false"`) |

Controls whether trades are real or simulated.

- `true` — all trades are logged as paper orders, no real orders sent to Binance
- `false` — requires `BINANCE_API_KEY` and `BINANCE_API_SECRET`; real market orders are placed

This is evaluated at startup and stored in `settings.paper_trading`. Changing it requires a restart.

---

### `PAPER_BALANCE_USDT`

| | |
|---|---|
| **Required** | No |
| **Default** | `10000` |
| **Type** | Float |

Simulated portfolio size in USDT for paper trading mode.

Used by `fetch_balance()` to return a paper balance stub:
```python
{"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0}}
```

This balance is **not stateful** — it does not decrease as paper trades are executed. Every cycle computes quantities using the same full `PAPER_BALANCE_USDT`. This means the position sizes will be overstated in the later stages of a paper trading session if multiple positions are open simultaneously.

---

### `KILL_SWITCH`

| | |
|---|---|
| **Required** | No |
| **Default** | `false` |
| **Type** | Boolean (`"true"` / `"false"`) |

Halts all new position activity immediately.

Unlike most settings, this is **read from the environment at every risk evaluation**, not just at startup:
```python
def _kill_switch_active() -> bool:
    return os.getenv("KILL_SWITCH", "false").lower() == "true"
```

This means you can set `KILL_SWITCH=true` in the process environment without restarting. On the next tick or cycle, all BUY/SELL decisions will be forced to HOLD.

Existing positions are not automatically closed when the kill switch is activated — only new actions are blocked. Stop-loss / take-profit triggers still fire (they use `check_exit_conditions`, which does not check the kill switch).

To halt trading and close all positions: set `KILL_SWITCH=true`, then manually liquidate open positions.

---

### `TRACKED_SYMBOLS`

| | |
|---|---|
| **Required** | No |
| **Default** | `BTCUSDT,ETHUSDT,SOLUSDT` |
| **Type** | Comma-separated string |

The list of Binance USDT-margined symbols the bot will trade.

Symbols are uppercased and stripped of whitespace at startup:
```python
raw = os.getenv("TRACKED_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
self.tracked_symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
```

This list determines:
- Which streams to subscribe to in the WebSocket URL
- Which symbols the AI is asked to evaluate
- Which symbols to upsert in the `assets` table

Changing this requires a restart. Adding a new symbol is safe — the `_get_or_create_asset` method handles creating new `Asset` records automatically.

---

### `MODEL_NAME`

| | |
|---|---|
| **Required** | No |
| **Default** | `claude-sonnet-4-6` |
| **Type** | String |

The Anthropic model ID passed to `client.messages.create()`. Also stored in the `ai_decisions.model_name` column.

Valid values: any model supported by the Anthropic Messages API (e.g. `claude-opus-4-7`, `claude-haiku-4-5-20251001`). Using a less capable model reduces API costs but may produce lower-quality decisions.

---

### `MIN_CONFIDENCE`

| | |
|---|---|
| **Required** | No |
| **Default** | `0.7` |
| **Type** | Float `[0.0, 1.0]` |

Minimum confidence level Claude must return for a BUY or SELL to be acted on.

Enforced in two places:
1. `ai_service._validate_decisions()` — any decision with `confidence < MIN_CONFIDENCE` has its action forced to HOLD before reaching the risk layer
2. `risk_service.evaluate_decision()` — a second check as a safety net

Setting this too low (e.g. `0.4`) will allow more trades but based on uncertain signals. Setting it to `1.0` will effectively disable all trading (Claude almost never returns confidence=1.0).

---

### `MAX_POSITION_PCT`

| | |
|---|---|
| **Required** | No |
| **Default** | `20` |
| **Type** | Float (percentage) |

Maximum percentage of the portfolio that can be allocated to any single position.

Example: with `MAX_POSITION_PCT=20` and `PAPER_BALANCE_USDT=10000`, the maximum trade size per asset is `$10,000 × 0.20 = $2,000`.

If Claude returns `size_pct=15` and `MAX_POSITION_PCT=20`, the size is passed through unchanged. If Claude returns `size_pct=25`, it is silently capped to `20` in both the AI validator (`min(20, int(item["size_pct"]))`) and the risk evaluator.

---

### `MAX_TOTAL_EXPOSURE_PCT`

| | |
|---|---|
| **Required** | No |
| **Default** | `60` |
| **Type** | Float (percentage) |

Maximum total portfolio exposure across all open positions simultaneously.

Example: with `MAX_TOTAL_EXPOSURE_PCT=60`, the bot can hold at most 3 positions of 20% each, or 6 positions of 10% each, etc.

If a new BUY would push total exposure above this cap, the `size_pct` is reduced to the available headroom. If there is zero headroom, the decision is forced to HOLD.

This cap only applies to BUY decisions. SELL decisions and HOLD decisions are never blocked by this cap.

---

### `STOP_LOSS_PCT`

| | |
|---|---|
| **Required** | No |
| **Default** | `5` |
| **Type** | Float (percentage) |

Percentage below entry price at which a position is automatically closed.

```
stop_loss_price = entry_price × (1 - STOP_LOSS_PCT / 100)
```

Example: BTC entered at $67,423. `STOP_LOSS_PCT=5`. Stop-loss fires at `$67,423 × 0.95 = $64,051.85`.

Checked on every WebSocket tick and at the start of each hourly cycle. Cannot be set to `0` to disable — a value of `0` would trigger the stop-loss on any price drop at all.

---

### `TAKE_PROFIT_PCT`

| | |
|---|---|
| **Required** | No |
| **Default** | `0` |
| **Type** | Float (percentage) |

Percentage above entry price at which a position is automatically closed for profit.

`0` means **disabled**. The `check_exit_conditions` method explicitly checks:
```python
elif settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
```

Set to a positive value to enable. Example: `TAKE_PROFIT_PCT=10` will exit any position that gains 10% from entry.

---

### `DATABASE_URL`

| | |
|---|---|
| **Required** | No |
| **Default** | `sqlite:///./crypto_bot.db` |
| **Type** | SQLAlchemy database URL |

The SQLAlchemy connection URL for the persistence layer. The default creates a file `crypto_bot.db` in the `crypto_bot/` working directory.

For SQLite, the `check_same_thread=False` option is set automatically in `db/session.py` to allow the SQLAlchemy session to be used from the APScheduler thread pool.

Changing to PostgreSQL or MySQL requires also removing the `connect_args` in `session.py` and ensuring the target database exists.

---

---

## Dashboard Settings UI

Most settings can also be changed through the dashboard's **Settings page** (`/settings`) without editing `.env` directly. The page splits settings into two sections:

### Trading Settings
All risk and trading parameters (everything except `DATABASE_URL`). Saved via `PATCH /api/config`. **Disabled while the bot is running** — you must stop the bot first to edit these.

### Display Settings
Chart interval, timezone, and currency. Saved via the same `PATCH /api/config` endpoint. These can be changed at any time, even while the bot is running.

The dashboard settings page reads the current config on load (`GET /api/config`) and shows a save bar when you have unsaved changes.

---

## How Settings Are Loaded

`app/config.py`:
```python
@dataclass
class Settings:
    paper_trading: bool = field(
        default_factory=lambda: os.getenv("PAPER_TRADING", "true").lower() == "true"
    )
    # ... all other fields follow the same pattern
    
settings = Settings()
```

`load_dotenv()` is called at the top of `config.py`, which reads `.env` from the working directory. All `os.getenv()` calls in the `default_factory` lambdas then pick up the loaded values.

The `settings` singleton is created once at import time. The only exception is `KILL_SWITCH`, which is read via `os.getenv()` directly at call time in `risk_service.py` — not from `settings` — enabling hot-reload.

---

## Environment Precedence

Standard `python-dotenv` precedence:
1. Actual environment variables (set in the shell before starting uvicorn) take priority
2. `.env` file values are loaded only if the variable is not already set

This means you can override individual settings at launch:
```bash
KILL_SWITCH=true uvicorn app.main:app
```

without modifying `.env`.
