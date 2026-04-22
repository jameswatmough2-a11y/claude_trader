# Upgrade Guide

This document explains how to extend the system in common directions. Each section is self-contained and shows exactly which files to change and what to add.

---

## Add New Trading Symbols

**Where to change:** `.env` only (no code changes required)

```env
TRACKED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,ADAUSDT
```

On next startup:
1. The WebSocket URL is rebuilt to include all new symbols
2. `_get_or_create_asset()` creates new `Asset` records on first cycle
3. Claude is prompted to evaluate the new symbols

**Caveats:**
- Add keyword mappings in `sentiment_service.py` for accurate sentiment filtering:
  ```python
  ASSET_KEYWORDS = {
      ...
      "BNBUSDT": ["binance", "bnb"],
      "ADAUSDT": ["cardano", "ada"],
  }
  ```
- The AI prompt grows by ~5 lines per symbol. At ~15+ symbols, consider increasing `max_tokens`.
- Sentiment fetch time scales linearly with symbols (Reddit has 1-second sleeps per symbol).

---

## Add a New Exchange

The system is coupled to Binance in two places:
1. `binance_ws.py` — hardcodes the Binance WebSocket URL format
2. `data_feeds.py` — uses `ccxt.binance` explicitly

**Step 1 — Abstract the WebSocket:**

Create a base class and a new exchange implementation:

```python
# app/services/ws_base.py
class WebSocketService(ABC):
    @abstractmethod
    async def run_forever(self) -> None: ...
```

```python
# app/services/coinbase_ws.py
class CoinbaseWebSocketService(WebSocketService):
    URL = "wss://advanced-trade-ws.coinbase.com"
    
    def __init__(self, symbols, market_store, risk_service, trigger_queue):
        ...  # map symbols to Coinbase format (BTC-USD instead of BTCUSDT)
    
    async def run_forever(self):
        ...  # Coinbase uses a subscribe message instead of URL-encoded streams
```

**Step 2 — Abstract the exchange:**

```python
# app/services/data_feeds.py
def get_exchange(exchange_id: str = "binance") -> ccxt.Exchange:
    return getattr(ccxt, exchange_id)({
        "apiKey": os.getenv(f"{exchange_id.upper()}_API_KEY", ""),
        "secret": os.getenv(f"{exchange_id.upper()}_API_SECRET", ""),
        "enableRateLimit": True,
    })
```

**Step 3 — Add config:**

```env
EXCHANGE=binance   # or coinbase, kraken, etc.
```

**Caveats:**
- Symbol formats differ across exchanges (Binance: `BTCUSDT`, Coinbase: `BTC-USD`, Kraken: `XBTUSD`)
- WebSocket protocols differ significantly (Binance: topic-in-URL, Coinbase: JSON subscribe message)
- ccxt handles most REST differences, but WebSocket must be reimplemented per-exchange

---

## Swap the AI Provider

**Files to change:** `app/services/ai_service.py`

The AI service is isolated in a single module. To replace Claude with GPT-4o:

```python
# app/services/ai_service.py
from openai import OpenAI

_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _client

def get_trading_decisions(market_data, sentiment_data):
    symbols = list(market_data.keys())
    client = _get_client()
    prompt = _build_prompt(market_data, sentiment_data)
    
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    
    raw_text = _extract_json(response.choices[0].message.content)
    parsed = json.loads(raw_text)
    return _validate_decisions(parsed, symbols)
```

The `SYSTEM_PROMPT`, `_build_prompt`, `_extract_json`, and `_validate_decisions` functions can be reused unchanged.

**For a local LLM (Ollama, LM Studio):**

```python
# Using OpenAI-compatible API
_client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # placeholder
)
```

Update `MODEL_NAME` in `.env` to the local model name (e.g. `llama3.2`, `mistral`).

---

## Add Technical Indicators

**Goal:** Include RSI, MACD, Bollinger Bands, etc. in the AI prompt.

**Step 1 — Fetch OHLCV data:**

`data_feeds.fetch_ohlcv()` already returns a pandas DataFrame. Integrate it into the trading cycle:

```python
# In trading_cycle.py _build_market_data():
from app.services.data_feeds import fetch_ohlcv

for symbol in settings.tracked_symbols:
    df = fetch_ohlcv(symbol, "1h", 48)   # 48 hourly candles
    market_data[symbol]["ohlcv_df"] = df
```

**Step 2 — Compute indicators:**

```python
# app/services/indicators.py
import pandas as pd

def compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return float(100 - (100 / (1 + rs.iloc[-1])))

def compute_sma(df: pd.DataFrame, period: int) -> float:
    return float(df["close"].rolling(period).mean().iloc[-1])
```

**Step 3 — Include in prompt:**

In `ai_service._build_prompt()`:
```python
from app.services.indicators import compute_rsi, compute_sma
df = md.get("ohlcv_df")
if df is not None and len(df) >= 14:
    rsi = compute_rsi(df)
    sma_20 = compute_sma(df, 20)
    lines.append(f"RSI(14): {rsi:.1f}  SMA(20): ${sma_20:,.2f}")
```

**Caveats:**
- Each ccxt OHLCV fetch adds ~100-500ms to the cycle. For 3 symbols, this is ~300-1500ms additional latency.
- Consider caching OHLCV data between cycles (the candle for "last hour" is the same for the first 59 minutes of any cycle).

---

## Persist Open Positions in the Database

**Goal:** Survive restarts with position state intact. Addresses [limitations.md § 1](limitations.md#1-position-state-is-lost-on-restart).

**Step 1 — Add a `live_positions` table:**

```python
# app/models/live_position.py
class LivePosition(Base):
    __tablename__ = "live_positions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    size_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

**Step 2 — Write position opens/closes:**

In `RiskService`:
```python
def record_open_position(self, asset, entry_price, size_pct, db: Session | None = None):
    self._positions[asset.upper()] = OpenPosition(...)
    if db:
        db.merge(LivePosition(symbol=asset, entry_price=entry_price, size_pct=size_pct, opened_at=datetime.now()))
        db.commit()

def close_position(self, asset, db: Session | None = None):
    self._positions.pop(asset.upper(), None)
    if db:
        db.query(LivePosition).filter_by(symbol=asset.upper()).delete()
        db.commit()
```

**Step 3 — Recover on startup:**

In `main.py` lifespan, after `init_db()`:
```python
db = SessionLocal()
try:
    rows = db.query(LivePosition).all()
    for row in rows:
        risk_service.record_open_position(
            row.symbol, float(row.entry_price), float(row.size_pct)
        )
    logger.info("Recovered %d open positions from DB", len(rows))
finally:
    db.close()
```

---

## Improve Paper Trading Accuracy

**Goal:** Make the simulated balance decrease as trades are executed. Addresses [limitations.md § 3](limitations.md#3-paper-balance-is-not-stateful).

**Step 1 — Add a virtual balance tracker:**

```python
# app/services/paper_wallet.py
class PaperWallet:
    def __init__(self, initial_usdt: float):
        self._usdt = initial_usdt
        self._holdings: dict[str, float] = {}  # symbol → quantity held
    
    def buy(self, symbol: str, usdt_amount: float, price: float) -> float:
        qty = usdt_amount / price
        self._usdt -= usdt_amount
        self._holdings[symbol] = self._holdings.get(symbol, 0) + qty
        return qty
    
    def sell(self, symbol: str, price: float) -> float:
        qty = self._holdings.pop(symbol, 0)
        usdt = qty * price
        self._usdt += usdt
        return usdt
    
    def balance(self) -> dict:
        return {"USDT": {"free": self._usdt, "used": 0.0, "total": self._usdt}}
```

**Step 2 — Wire into execution service:**

Replace `fetch_balance()` in paper mode with `paper_wallet.balance()`. Call `paper_wallet.buy()` / `paper_wallet.sell()` in `_execute_paper()` instead of computing qty from the global paper balance.

---

## Add Backtesting

**Goal:** Test AI decision quality against historical Binance data without running live.

**Approach:** Create a `BacktestRunner` that replays historical OHLCV candles through the trading cycle in a loop.

**Step 1 — Fetch historical data:**

```python
# scripts/fetch_history.py
from crypto_bot.app.services.data_feeds import fetch_ohlcv

df = fetch_ohlcv("BTCUSDT", "1h", 8760)   # 1 year of hourly candles
df.to_parquet("data/BTCUSDT_1h.parquet")
```

**Step 2 — Replay engine:**

```python
# scripts/backtest.py
class BacktestMarketStore(MarketStateStore):
    def load_candle(self, symbol: str, candle: pd.Series) -> None:
        self.update(
            symbol=symbol,
            last_price=Decimal(str(candle["close"])),
            high_24h=Decimal(str(candle["high"])),
            low_24h=Decimal(str(candle["low"])),
            volume_24h=Decimal(str(candle["volume"])),
        )

def run_backtest(symbol: str, candles: pd.DataFrame):
    store = BacktestMarketStore()
    risk = RiskService()
    execution = ExecutionService(risk)
    cycle = TradingCycleService(store, risk, execution)
    
    for timestamp, row in candles.iterrows():
        store.load_candle(symbol, row)
        db = SessionLocal()
        try:
            cycle.run(db)
        finally:
            db.close()
```

**Caveats:**
- Each backtest candle makes a real Claude API call — backtesting 1 year at 1 hour = 8,760 API calls. This is expensive. Consider adding a mock AI layer for backtesting.
- Sentiment data is not available historically — you'll need to skip it or use a neutral value.
- Stop-loss checks in backtesting use end-of-candle prices, not intra-candle prices — this underestimates stop-loss frequency.

---

## Add Prometheus Metrics

**Step 1 — Add the package:**
```bash
pip install prometheus-fastapi-instrumentator
```

**Step 2 — Wire in `main.py`:**
```python
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="Claude Trader", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)
```

**Step 3 — Add custom metrics:**
```python
from prometheus_client import Counter, Gauge

trades_executed = Counter("trades_executed_total", "Total trades executed", ["action", "symbol"])
open_positions = Gauge("open_positions_total", "Number of open positions")
```

Emit in `execution_service.py`:
```python
trades_executed.labels(action=action, symbol=asset).inc()
```

---

## Add Alerting on Stop-Loss Triggers

**Goal:** Send a notification (Slack, email, etc.) when a stop-loss fires.

**Where to add it:** `risk_service.check_exit_conditions()`, after the exit is determined:

```python
if reason:
    size_pct = int(pos.size_pct)
    del self._positions[symbol.upper()]
    logger.warning("Exit triggered for %s: %s", symbol, reason)
    self._notify_exit(symbol, reason, price)   # ← add this
    return {...}

def _notify_exit(self, symbol: str, reason: str, price: float) -> None:
    try:
        import requests
        requests.post(os.getenv("SLACK_WEBHOOK_URL", ""), json={
            "text": f":stop_sign: *{symbol}* exit triggered at ${price:,.4f}\n{reason}"
        }, timeout=5)
    except Exception:
        logger.warning("Slack notification failed")
```

Note: this runs synchronously in the WebSocket message handler (in the event loop). The `requests.post()` is blocking. For production, use `aiohttp` and make `check_exit_conditions` async, or dispatch to the thread pool.

---

## Replace SQLite with PostgreSQL

**Step 1 — Install driver:**
```bash
pip install psycopg2-binary
```

**Step 2 — Update `.env`:**
```env
DATABASE_URL=postgresql://user:password@localhost:5432/claude_trader
```

**Step 3 — Remove SQLite-specific config in `session.py`:**
```python
# Remove:
connect_args={"check_same_thread": False}

# Result:
engine = create_engine(settings.database_url, echo=False)
```

**Step 4 — Handle migrations:**

The current codebase uses `create_all()` which does not handle schema migrations. Add Alembic:
```bash
pip install alembic
alembic init alembic
```

Configure `alembic.ini` with the `DATABASE_URL` and generate the initial migration from the existing models.

**No other code changes are required** — SQLAlchemy abstracts the database difference.
