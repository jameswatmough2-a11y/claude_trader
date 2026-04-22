# Services

Each file in `app/services/` has a single responsibility. This document explains each one in depth — what it does, how it works internally, what it depends on, and what can go wrong.

---

## `market_state.py`

### Purpose
In-memory cache of the most recent ticker data for all tracked symbols. Serves as the bridge between the WebSocket stream (writer) and the trading cycle (reader).

### Classes

**`SymbolMarketState`** (dataclass)
Stores the latest values for one symbol. All numeric fields are `Optional[Decimal]` — they start as `None` and are only set once the WebSocket delivers them.

```python
@dataclass
class SymbolMarketState:
    symbol: str
    last_price: Optional[Decimal] = None
    bid:        Optional[Decimal] = None
    ask:        Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    price_change_24h_pct: Optional[Decimal] = None
    high_24h:   Optional[Decimal] = None
    low_24h:    Optional[Decimal] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

**`MarketStateStore`**
Wraps `dict[str, SymbolMarketState]` with three methods:

- `update(symbol, *, last_price, bid, ask, ...)` — creates or merges state for `symbol`. Only non-`None` keyword arguments update the stored field; this allows partial updates (though in practice the WebSocket sends all fields together).
- `get(symbol)` → `SymbolMarketState | None` — case-insensitive lookup.
- `all()` → `dict[str, SymbolMarketState]` — returns a shallow copy.

### Thread safety
No explicit locking. The store is written by the WebSocket task and read by the trading cycle (a thread pool worker). CPython's GIL prevents torn reads/writes to individual dict values, but a strict race still exists between `_build_market_data` reading a snapshot and the next WebSocket tick updating it. In practice this is harmless — a one-tick-stale price in the hourly snapshot is inconsequential.

### Edge cases
- Symbol stored in uppercase (`symbol = symbol.upper()` in `update`). The WebSocket sends uppercase symbols.
- If `last_price is None` (no tick received yet), `_build_market_data` skips the symbol. A `HOLD` default is emitted for missing symbols by the AI validator.

---

## `binance_ws.py`

### Purpose
Maintains a persistent, auto-reconnecting WebSocket connection to Binance. Updates the market state cache on every tick and triggers real-time stop-loss / take-profit checks.

### Key implementation details

**Stream URL construction**
```python
streams = "/".join(f"{s}@ticker" for s in self.symbols)
self.url = f"wss://data-stream.binance.vision/stream?streams={streams}"
```
`symbols` are lowercased at construction (`self.symbols = [s.lower() for s in symbols]`). The combined stream URL allows subscribing to all symbols in a single WebSocket connection.

**Reconnect loop**
```python
async def run_forever(self) -> None:
    while True:
        try:
            async with connect(self.url, ping_interval=20, ping_timeout=60) as ws:
                async for message in ws:
                    self._handle_message(message)
        except asyncio.CancelledError:
            raise           # do not catch cancellation
        except Exception as exc:
            logger.exception("Binance WebSocket error — reconnecting in 5s: %s", exc)
            await asyncio.sleep(5)
```

`asyncio.CancelledError` is re-raised so that event loop shutdown propagates correctly. All other exceptions (network errors, JSON parse failures, Binance errors) are caught at the outer level and trigger a 5-second reconnect delay.

**Message parsing**
The `@ticker` event provides 24h rolling statistics, not a stream of individual trades. Fields used:

| Binance field | Meaning |
|---|---|
| `s` | Symbol (e.g. `BTCUSDT`) |
| `c` | Last price (most recent trade price) |
| `b` | Best bid price |
| `a` | Best ask price |
| `q` | Total quote asset volume (24h) |
| `P` | Price change percent (24h) |
| `h` | 24h high price |
| `l` | 24h low price |

**Exit check integration**
```python
if self._risk_service is not None and self._trigger_queue is not None and last_price_raw:
    order = self._risk_service.check_exit_conditions(symbol, float(last_price_raw))
    if order:
        self._trigger_queue.put_nowait(order)
```

Both `_risk_service` and `_trigger_queue` are optional at construction, making the WebSocket usable without exit checking (e.g. in unit tests).

### Failure modes
- Binance connection drops → auto-reconnects after 5s
- JSON parse error for one message → `except` in `_handle_message` logs a warning and continues
- `put_nowait` on queue → never raises (queue is unbounded)

---

## `trigger_executor.py`

### Purpose
Consumes stop-loss and take-profit exit orders from `trigger_queue` and executes them without blocking the WebSocket event loop.

### Why a separate component?
The WebSocket message handler (`_handle_message`) is synchronous and runs in the event loop. `execution_service.execute_decision()` may make blocking HTTP calls (ccxt in live mode). Dispatching to `run_in_executor` moves the blocking work to a thread pool, freeing the loop immediately.

### Implementation

```python
async def run_forever(self) -> None:
    loop = asyncio.get_event_loop()
    while True:
        order = await self.queue.get()              # suspends until an order arrives
        await loop.run_in_executor(None, self._execute, order)
        self.queue.task_done()
```

`run_in_executor(None, ...)` uses the default `ThreadPoolExecutor`. Each execution call runs in its own thread from the pool.

**`_execute(order)`** (sync, runs in thread):
1. Calls `_get_balance()` — paper stub or live ccxt
2. Builds `market_data = {asset: {"last_price": trigger_price}}`
3. Calls `execution_service.execute_decision(order, market_data, balance)`
4. Logs result with `WARNING` level (WARNING because exits are always notable events)

### Balance fetching
`_get_balance()` imports `fetch_balance` inside the function (not at module level) to avoid a circular import between `trigger_executor` and `data_feeds`. If balance fetch fails, falls back to the paper balance from settings.

### Edge cases
- If the queue has multiple pending exit orders (e.g. market moved fast), they are processed sequentially — one at a time per `run_in_executor` call. Orders are not batched.
- The position has already been removed from `RiskService._positions` before the order reaches the queue, so re-triggering is impossible even if the queue drains slowly.

---

## `data_feeds.py`

### Purpose
ccxt exchange factory and market data fetch helpers. Provides a thin wrapper around ccxt with paper-trading awareness for balance fetches.

### Exchange singleton

```python
_exchange: ccxt.binance | None = None

def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        _exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
    return _exchange
```

`enableRateLimit=True` makes ccxt enforce Binance's rate limits automatically. The exchange instance is created once and reused across all calls.

If no API key is set, the exchange is created in public-only mode. Public endpoints (tickers, OHLCV) work without authentication; authenticated endpoints (create order, fetch balance) will fail.

### Symbol conversion

```python
def _to_ccxt_symbol(symbol: str) -> str:
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol
```

Converts Binance format (`BTCUSDT`) to ccxt format (`BTC/USDT`). The reverse conversion in `execution_service.py` does the same:
```python
ccxt_symbol = f"{asset[:-4]}/USDT" if asset.endswith("USDT") and "/" not in asset else asset
```

### `fetch_ohlcv`

Returns a pandas DataFrame with columns `[open, high, low, close, volume]` indexed by UTC timestamps. Fetches 24 hourly candles by default (`OHLCV_LIMIT = 24`). Used by `get_all_market_data()` as a fallback.

### `fetch_balance`

```python
def fetch_balance() -> dict[str, Any]:
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        paper = settings.paper_balance_usdt
        return {"USDT": {"free": paper, "used": 0.0, "total": paper}}
    return get_exchange().fetch_balance()
```

In paper mode, always returns the configured `PAPER_BALANCE_USDT` as both `free` and `total`. This means the paper balance never decreases — every cycle computes trade sizes from the same total. This is a known prototype limitation (see [limitations.md](limitations.md)).

### `get_all_market_data`

Full ccxt market data fetch used as a fallback if the WebSocket hasn't populated the market store. In practice, since the WebSocket connects nearly instantly on startup and the first trading cycle is scheduled with a small delay, this fallback is rarely needed.

---

## `sentiment_service.py`

### Purpose
Fetches and scores market sentiment from three sources (RSS news, Reddit, Fear & Greed Index) and returns a blended score per asset.

### Data sources

**RSS feeds** (`_rss_sentiment`)

Parses four RSS feeds:
- `https://www.coindesk.com/arc/outboundfeeds/rss/`
- `https://cointelegraph.com/rss`
- `https://decrypt.co/feed`
- `https://bitcoinmagazine.com/feed`

For each feed, takes the first 40 entries. An entry is included if its `title + summary` contains any keyword for the asset (`bitcoin` or `btc` for `BTCUSDT`). TextBlob polarity is applied to the combined text, clamped to `[-1, 1]`. The final score is the mean of all matched entry scores.

Returns: `(score: float, headlines: list[str])` where headlines are the top 3 by absolute polarity.

**Reddit** (`_reddit_sentiment`)

Queries Reddit's JSON search API for three subreddits (`/r/cryptocurrency`, `/r/bitcoin`, `/r/ethtrader`) using the primary keyword (e.g. `"bitcoin"` for `BTCUSDT`). Uses `sort=hot, t=day, limit=25`.

Scoring is **upvote-weighted**: each post's TextBlob polarity is weighted by its upvote count. This downweights controversial/low-upvote posts.

A 1-second sleep between subreddit requests (`time.sleep(1)`) respects Reddit's rate limits. Note: this is a synchronous sleep that blocks the thread pool worker, not the asyncio loop.

**Fear & Greed Index** (`_fear_greed_index`)

Fetches `https://api.alternative.me/fng/?limit=1` (a global crypto sentiment index from 0–100). Converted to `[-1, 1]` via `(value - 50) / 50`. The index is fetched once per cycle in `get_all_sentiment` and shared across all symbols.

### Score blending

```python
blended = _clamp(_mean(list(source_scores.values())))
```

All source scores that are available are averaged equally. If a source fails, it is simply excluded. The result is clamped to `[-1, 1]`.

### Asset keyword mapping

```python
ASSET_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "SOLUSDT": ["solana", "sol"],
}
```

For symbols not in this map, the ticker with "USDT" stripped is used as the keyword (e.g. `LINKUSDT` → `"link"`).

### `AssetSentiment` dataclass

```python
@dataclass
class AssetSentiment:
    symbol:          str
    score:           float          # blended [-1, 1]
    headline_count:  int
    top_headlines:   list[str]      # up to 3 unique headlines
    source_scores:   dict[str, float]  # {"rss": 0.12, "reddit": -0.05, "fear_greed": 0.20}
    fear_greed_index: int | None    # raw 0-100 value
```

The `top_headlines` are deduplicated (first 60 chars used as the key) and truncated to 3.

### Edge cases
- If all RSS feeds fail → `rss_score = 0.0`, empty headlines
- If Reddit is rate-limited → warning logged, subreddit skipped, `time.sleep(1)` still called
- If Fear & Greed API fails → `fear_greed_index = None`, excluded from blend

---

## `ai_service.py`

### Purpose
Calls the Claude API with a structured market context and returns validated trading decisions for each tracked symbol.

### System prompt

```
You are a disciplined crypto trading analyst. Evaluate the market and
sentiment data provided, then return a single JSON array of trading decisions — one object
per asset. Never deviate from the schema below.

Decision schema:
{
  "asset":      "<SYMBOL>",
  "action":     "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0–1.0>,
  "size_pct":   <int 0–20>,
  "reasoning":  "<one-sentence rationale>"
}

Rules:
- Return ONLY a valid JSON array. No markdown, no extra text.
- size_pct must be 0 when action is HOLD.
- size_pct maximum is 20 for any single asset.
- If confidence < 0.7, set action to HOLD and size_pct to 0.
- Base decisions solely on the data provided.
```

The prompt is sent as the `system` parameter (Claude's system prompt), not as a user message. This is the preferred approach for behavioral constraints.

### User prompt construction (`_build_prompt`)

One section per symbol, containing:
```
--- BTCUSDT ---
Price: $67,423.0100  Bid: $67,422.9900  Ask: $67,423.0100
24h High: $68,500.0000  24h Low: $66,200.0000  Change: -0.842%
Avg 24h Volume: 1,234,568  Quote Volume: $1,234,567,890
Sentiment: 0.123 | Fear & Greed: 62/100
  1. Bitcoin surges as ETF inflows reach record levels
  2. BTC holds above $67k support despite market uncertainty
  3. Analysts predict further upside for Bitcoin this quarter
```

Key design decisions:
- All numeric values are formatted as floats with explicit formatting (`{:,.4f}`, `{:,.0f}`). This prevents scientific notation or excessive precision in Claude's context.
- Headlines are truncated to 180 characters.
- If sentiment data is unavailable for a symbol, "Sentiment: unavailable" is written. Claude is expected to factor this in.

### API call

```python
response = client.messages.create(
    model=settings.model_name,
    max_tokens=2048,
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": prompt}],
)
```

`max_tokens=2048` is sufficient for up to ~20 symbols. Each decision object is approximately 80 tokens.

### Response parsing (`_extract_json`)

Claude sometimes wraps JSON in markdown fences despite the instruction not to. The extractor:
1. Strips leading/trailing whitespace
2. Removes ``` ``` ``` lines if present
3. Uses `re.search(r"\[.*\]", text, re.DOTALL)` to extract the JSON array

### Decision validation (`_validate_decisions`)

For each item in the parsed JSON array:
1. Check it is a `dict` with all required keys: `{asset, action, confidence, size_pct, reasoning}`
2. Normalize `asset` and `action` to uppercase
3. Clamp `confidence` to `[0.0, 1.0]`, `size_pct` to `[0, 20]`
4. Skip if `asset` is not in the set of tracked symbols
5. Force unknown `action` values to `"HOLD"`
6. Force `HOLD` if `confidence < settings.min_confidence` (default 0.7)
7. Force `size_pct = 0` for `HOLD`

After processing all items, any tracked symbol not present in the validated list gets a HOLD default added:
```python
{"asset": symbol, "action": "HOLD", "confidence": 0.0, "size_pct": 0,
 "reasoning": "Missing from model response — defaulted to HOLD."}
```

### Client singleton

```python
_client: anthropic.Anthropic | None = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client
```

The client is created on first call and reused. The `anthropic.Anthropic` client is thread-safe.

---

## `risk_service.py`

### Purpose
Tracks open positions in memory and enforces all risk rules before any decision is executed. Also performs real-time exit checks on every WebSocket tick.

### `OpenPosition` dataclass

```python
@dataclass
class OpenPosition:
    asset:         str
    entry_price:   float
    size_pct:      float   # percentage of portfolio this position represents
    current_price: float = 0.0
```

`size_pct` is stored so the exposure cap can be computed quickly: `sum(p.size_pct for p in _positions.values())`.

### `evaluate_decision` (hourly cycle)

Called once per decision, in order:

1. **Kill switch** — `os.getenv("KILL_SWITCH")` read at call time. If `"true"` → force HOLD.
2. **HOLD passthrough** — HOLD decisions are approved as-is without further checks.
3. **Double-entry guard** — BUY blocked if `asset in _positions`. Returns HOLD, not rejection.
4. **Phantom sell guard** — SELL blocked if `asset not in _positions`. Returns HOLD.
5. **Confidence threshold** — if `confidence < min_confidence` → force HOLD (redundant with AI validator but acts as a second layer).
6. **Size cap** — `size_pct = min(size_pct, max_position_pct)`. Applied silently.
7. **Exposure cap** — for BUY only: checks `sum(all position size_pcts) + proposed_size_pct <= max_total_exposure_pct`. If at cap → HOLD. If partial headroom → reduce `size_pct`.

Note: all non-kill-switch rejections return `RiskCheck(approved=True, ...)` with a HOLD decision. This is intentional — the cycle still persists a HOLD record for auditing.

### `check_exit_conditions` (real-time)

```python
drawdown_pct = (pos.entry_price - price) / pos.entry_price * 100
gain_pct     = (price - pos.entry_price) / pos.entry_price * 100

if drawdown_pct >= settings.stop_loss_pct:
    ...trigger stop-loss...
elif settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
    ...trigger take-profit...
```

Take-profit is only active when `TAKE_PROFIT_PCT > 0` (default is 0, meaning disabled).

When triggered: position is deleted from `_positions` immediately (before the SELL order is dispatched). This is critical — without this, the next tick (arriving milliseconds later) would re-trigger the exit on the same position.

### `filter_decisions` (hourly cycle batch)

```python
def filter_decisions(decisions, market_data):
    stop_orders = self.check_stop_losses(market_data)      # check all open positions
    stop_assets = {o["asset"] for o in stop_orders}

    for decision in decisions:
        if asset in stop_assets:
            final.append(stop_order)                       # override AI decision with SELL
        else:
            check = self.evaluate_decision(decision, price)
            final.append(check.adjusted_decision)
```

If an asset has an hourly stop-loss trigger AND the AI also decided to SELL, the stop-loss SELL takes priority (it was the first SELL found in `stop_orders`).

---

## `execution_service.py`

### Purpose
Executes approved trading decisions in paper or live mode and logs all activity.

### Trade log

All activity (including HOLDs) is appended to `logs/trades.jsonl` as newline-delimited JSON. Example record:

```json
{
  "timestamp": "2026-04-22T14:00:00.123456+00:00",
  "asset": "BTCUSDT",
  "action": "BUY",
  "confidence": 0.87,
  "size_pct": 15,
  "current_price": 67423.01,
  "portfolio_usdt": 10000.0,
  "reasoning": "Strong upward momentum and positive sentiment indicate buying opportunity.",
  "paper_trading": true,
  "order": {
    "id": "PAPER-2026-04-22T14:00:00.123456+00:00",
    "symbol": "BTCUSDT",
    "side": "buy",
    "type": "market",
    "qty": 0.022247,
    "price": 67423.01,
    "status": "paper_filled"
  },
  "error": null
}
```

### Quantity calculation

```python
qty = portfolio_usdt * (size_pct / 100.0) / price
```

For a portfolio of $10,000 and `size_pct=15` at BTC price $67,423:
```
qty = 10000 * 0.15 / 67423 = 0.02225 BTC
```

No minimum order size validation is performed. If `price <= 0`, a `ValueError` is raised and recorded in `record["error"]`.

### Position update after execution

```python
def _update_positions(self, asset, action, price, size_pct):
    if action == "BUY":
        self.risk_service.record_open_position(asset, price, size_pct)
    elif action == "SELL":
        self.risk_service.close_position(asset)
```

This is called after a successful execution (paper or live). If execution fails (exception caught), position state is not updated.

### Error handling

In paper mode: `ValueError` from `_compute_qty` is caught, recorded in `record["error"]`, and the order is not simulated.

In live mode: `ccxt.BaseError` and all other exceptions are caught and logged at `EXCEPTION` level (stack trace included). The trade log records the error string.

---

## `trading_cycle.py`

### Purpose
Hourly orchestrator. Coordinates data collection, AI, risk, execution, and persistence for one trading cycle.

### `_persist_cycle` — database write sequence

This is the most complex method in the service. It writes to four tables in order, using `flush()` to get IDs before the next insert:

```
1. HourlyMarketSnapshot
   - asset_id (from _get_or_create_asset)
   - snapshot_time (cycle start time, same for all symbols in the cycle)
   - open/high/low/close = last_price (OHLCV is not stored separately; 24h H/L from WebSocket)
   - volume = quote_volume_24h from WebSocket
   - price_change_1h_pct = None (not computed)
   - price_change_since_entry_pct = None (not computed)
   → flush → gets snapshot.id

2. Position
   - snapshot_id = snapshot.id
   - asset_id = asset.id
   - side = "flat" (always; actual position tracking is in-memory, not DB-backed)
   - size = 0, entry_price = None
   - wallet_balance = USDT total from balance
   → flush → gets position.id

3. [execute_decision is called here]

4. AIDecision
   - snapshot_id = snapshot.id
   - action, confidence_score, reasoning_summary from decision dict
   - recommended_size = size_pct
   - recommended_stop_loss = None, recommended_take_profit = None (not AI-specified)
   - prompt_version = "v1", model_name from settings
   → flush → gets ai_rec.id

5. Execution
   - ai_decision_id = ai_rec.id
   - executed_action, executed_size, execution_price from exec_result["order"]
   - fees_paid = 0, slippage = 0 (not tracked)
   - status = "paper_filled" | "filled" | "rejected" | "none"
   → commit
```

### `_get_or_create_asset`

Queries the DB for an existing `Asset` with the given symbol. If not found, creates one:
```python
base = symbol[:-4] if symbol.endswith("USDT") else symbol
asset = Asset(symbol=symbol, base_currency=base, quote_currency="USDT")
```

This is called inside `_persist_cycle` which is inside `try/except IntegrityError`, so concurrent cycle runs won't double-create assets.

### Execution before AIDecision insert

The call to `execution_service.execute_decision()` happens **before** the `AIDecision` is written to the DB. This is because the execution result is needed to populate the `Execution` record. The execution modifies `RiskService._positions` as a side effect, so the position state is updated mid-cycle.

### Duplicate handling

If `scheduler` fires twice in quick succession (a bug or restart scenario), the `UniqueConstraint("asset_id", "snapshot_time")` on `hourly_market_snapshots` raises `IntegrityError`. This is caught, the session is rolled back, and a warning is logged. The cycle continues with remaining symbols.
