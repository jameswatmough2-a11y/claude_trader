# Services

Each file in `app/services/` has a single responsibility. This document explains each one in depth — what it does, how it works internally, what it depends on, and what can go wrong.

---

## market_state.py

### Purpose

An in-memory cache for live price data. It is the shared state that the WebSocket writes to and the trading cycle reads from.

### Key types

```python
@dataclass
class SymbolMarketState:
    symbol: str
    last_price: Optional[Decimal] = None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    price_change_24h_pct: Optional[Decimal] = None
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None
    updated_at: datetime = ...
```

All price fields are `Decimal` — the WebSocket handler parses raw string values from Binance directly into `Decimal` before storing them. This prevents floating-point drift accumulating across ticks.

```python
class MarketStateStore:
    _state: dict[str, SymbolMarketState]

    def update(self, symbol, *, last_price=None, bid=None, ...) -> None
    def get(self, symbol) -> SymbolMarketState | None
    def all(self) -> dict[str, SymbolMarketState]
```

### Update logic

`update()` is a partial update — it only overwrites fields that are explicitly provided. This matters because the Binance ticker stream provides all fields on every tick, but other data sources (e.g., ccxt) might only provide a subset.

### Thread safety

The store is accessed from two contexts: the async WebSocket loop (writes) and the sync APScheduler thread (reads via `trading_cycle.run()`). In CPython, simple dict reads and writes are GIL-protected and effectively atomic for single operations. The store does not use `threading.Lock` because:

- Writes are single-dict-key assignments (`self._state[symbol] = current`)
- Reads copy the dict (`dict(self._state)`) — a snapshot is taken at the start of the cycle

A more concurrent architecture could use `asyncio.Lock`, but for this prototype the GIL provides sufficient protection.

---

## binance_ws.py

### Purpose

Maintains a persistent WebSocket connection to Binance's public data stream, continuously feeding `MarketStateStore` with live price data and dispatching real-time exit orders.

### Connection

Connects to Binance's combined stream endpoint:

```
wss://data-stream.binance.vision/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker
```

Each `@ticker` sub-stream provides 24-hour rolling statistics for a symbol. Binance sends updates roughly every second per symbol.

### Message handling

```python
def _handle_message(self, message: str) -> None:
    payload = json.loads(message)
    data = payload.get("data", payload)   # combined stream wraps in {"data": ...}

    symbol = data.get("s")                # e.g. "BTCUSDT"
    ...
    self.market_store.update(symbol=symbol, last_price=Decimal(data["c"]), ...)

    # real-time exit check
    order = self._risk_service.check_exit_conditions(symbol, float(data["c"]))
    if order:
        self._trigger_queue.put_nowait(order)
```

`put_nowait()` is used (not `await queue.put()`) because `_handle_message` is a sync method. `put_nowait()` raises `asyncio.QueueFull` if the queue is full — this will not happen in practice because the queue is unbounded (default `asyncio.Queue()`).

### Reconnection

Any exception other than `asyncio.CancelledError` causes a 5-second wait and reconnection attempt. This handles Binance server restarts, network drops, and ping timeouts. `CancelledError` is re-raised to allow the task to be cleanly cancelled on shutdown.

### Optional wiring

`risk_service` and `trigger_queue` are optional constructor parameters. If they are `None`, the exit check is silently skipped. This makes the service usable in contexts where you only want price data.

---

## trigger_executor.py

### Purpose

Processes real-time exit orders (stop-loss and take-profit) that the WebSocket loop places on the `asyncio.Queue`. It decouples the price stream from blocking I/O (order execution).

### Why a separate task?

`ExecutionService.execute_decision()` is blocking: it may call ccxt (HTTP request to Binance), write to a file, and call `fetch_balance()`. If called directly inside `_handle_message`, it would block the entire async event loop, causing the WebSocket to miss subsequent ticks. By dispatching to a queue and consuming in a thread pool, the price stream stays fast.

### Execution flow

```python
async def run_forever(self) -> None:
    loop = asyncio.get_event_loop()
    while True:
        order = await self.queue.get()                        # blocks async until order arrives
        await loop.run_in_executor(None, self._execute, order) # runs blocking code in thread pool
        self.queue.task_done()

def _execute(self, order: dict) -> None:
    balance = self._get_balance()
    market_data = {asset: {"last_price": trigger_price}}
    self.execution_service.execute_decision(order, market_data, balance)
```

`run_in_executor(None, ...)` uses the default `ThreadPoolExecutor`. Multiple exit orders can execute concurrently across threads.

### Balance fetch

The executor fetches balance at execution time, not at trigger detection time. This gives the most accurate picture of available funds, at the cost of a small latency between trigger detection and balance fetch.

### What it does NOT do

The trigger executor does not write to the database. The SQLite session is owned by the trading cycle's thread. Writing to it from the trigger executor's thread would cause concurrency issues. The trade is captured in `logs/trades.jsonl` immediately, and the database snapshot reflects the updated (flat) position on the next hourly cycle.

---

## data_feeds.py

### Purpose

Provides exchange connectivity via ccxt — balance fetching and OHLCV data. The WebSocket is the preferred source for live prices; this module is a fallback and a utility.

### Exchange singleton

```python
_exchange: ccxt.binance | None = None

def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({"apiKey": ..., "enableRateLimit": True})
    return _exchange
```

The singleton is lazy — created only on first use. `enableRateLimit=True` tells ccxt to automatically sleep between requests to avoid Binance rate limit errors.

### Symbol conversion

Binance WebSocket and REST use `BTCUSDT` format. ccxt uses `BTC/USDT` format. The conversion is centralised here:

```python
def _to_ccxt_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"   # "BTCUSDT" → "BTC/USDT"
    return symbol
```

### fetch_balance()

In paper mode (no `BINANCE_API_KEY`), returns a synthetic balance dict using `PAPER_BALANCE_USDT` from config. In live mode, calls `exchange.fetch_balance()` which hits Binance's authenticated REST API.

```python
def fetch_balance() -> dict[str, Any]:
    if not os.getenv("BINANCE_API_KEY", ""):
        return {"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0}}
    return get_exchange().fetch_balance()
```

### get_all_market_data()

This function fetches tickers, OHLCV, and order book summaries via ccxt REST. It is **not used** in the main trading cycle — the cycle uses `MarketStateStore` directly. This function exists as a fallback or for manual inspection. It is significantly slower than the WebSocket path.

---

## sentiment_service.py

### Purpose

Generates a sentiment score for each tracked symbol by aggregating text signals from multiple sources and running NLP analysis.

### Sources

**RSS feeds** — four crypto news sites are polled:
- `coindesk.com/arc/outboundfeeds/rss/`
- `cointelegraph.com/rss`
- `decrypt.co/feed`
- `bitcoinmagazine.com/feed`

Up to 40 entries per feed are inspected. Each entry's title and summary are concatenated and checked against the symbol's keyword list. Matching entries are scored with TextBlob polarity.

**Reddit** — three subreddits are searched:
- `r/cryptocurrency`, `r/bitcoin`, `r/ethtrader`

A hot search is performed for the symbol's primary keyword, limited to the past day. Post titles are scored with TextBlob. Scores are weighted by upvote count to give more popular posts more influence.

**Fear & Greed index** — fetched once per cycle from `api.alternative.me/fng/?limit=1`. Returns an integer 0–100. Normalised to `[-1, 1]` as `(value - 50) / 50`. A value of 50 (neutral) maps to 0.0.

### Keyword matching

```python
ASSET_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "SOLUSDT": ["solana", "sol"],
}
```

For symbols not in this dict, the fallback is `[symbol.replace("USDT", "").lower()]` — e.g. `LINKUSDT` → `["link"]`. This fallback is imprecise (many non-crypto uses of the word "link") but avoids crashes.

### Blending

Scores from all sources that succeeded are averaged. A failed source (exception, network error) is simply omitted — it does not drag the score to zero or propagate an error.

```python
blended = mean(list(source_scores.values()))  # simple average
blended = clamp(blended, -1.0, 1.0)
```

### Performance note

This function is the slowest part of the hourly cycle. Each Reddit subreddit call includes a `time.sleep(1)` to avoid Reddit rate limiting. With three subreddits, this is at least 3 seconds of sleeping, on top of network latency. Total sentiment fetch time is typically 20–60 seconds.

---

## ai_service.py

### Purpose

Formats market and sentiment data into a prompt, calls the Claude API, and parses + validates the response into a structured list of trading decisions.

### Client singleton

```python
_client: anthropic.Anthropic | None = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client
```

The client is created once and reused. Creating a new client per call would re-initialise HTTP connection pools unnecessarily.

### System prompt

The system prompt instructs Claude to return only a JSON array — no prose, no markdown, no explanation outside the array. This is enforced in two ways: the prompt says "Return ONLY a JSON array", and the response parser explicitly strips any surrounding text.

### Prompt structure

One section per symbol, containing:
- Last price, bid, ask
- 24h high, low, percentage change
- Volume
- Sentiment score, Fear & Greed value
- Up to 3 top headlines

The prompt is plain text, not structured JSON. Plain text is easier to read in Claude's context and produces more natural reasoning.

### JSON extraction

Claude occasionally wraps its response in markdown fences despite being told not to. `_extract_json()` handles this:

```python
def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # strip fence lines
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    # last-resort: regex to find the array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text
```

### Validation

`_validate_decisions()` runs after parsing:

1. Each item must have `asset`, `action`, `confidence`, `size_pct`, `reasoning`
2. Asset must be in the tracked symbols set
3. Action must be `BUY`, `SELL`, or `HOLD` — unknown values default to `HOLD`
4. Confidence is clamped to `[0, 1]`
5. `size_pct` is clamped to `[0, 20]`
6. If `confidence < 0.7`, action is forced to `HOLD` and `size_pct` to `0`
7. If any tracked symbol is missing from the response, a default HOLD is added

This validation is intentionally strict. Claude is generally reliable but can occasionally return slightly malformed JSON or unexpected values. The validation ensures the risk layer always receives well-formed input.

---

## risk_service.py

### Purpose

The single enforcer of all risk rules. Every decision — whether from AI or from a WebSocket trigger — passes through this service before execution. It also maintains the in-memory position ledger.

### State

```python
_positions: dict[str, OpenPosition]
```

Keyed by symbol (uppercase). Each `OpenPosition` stores entry price, size as a percentage of portfolio, and current price. This dict is the sole source of truth for what the bot currently holds.

### evaluate_decision()

Processes a single decision dict through the full rule chain:

```
kill switch?                    → HOLD all
action == HOLD?                 → pass through unchanged
BUY and already in _positions?  → HOLD (entry guard)
SELL and not in _positions?     → HOLD (exit guard)
confidence < MIN_CONFIDENCE?    → HOLD
size_pct > MAX_POSITION_PCT?    → cap size_pct
BUY and exposure at cap?        → HOLD
otherwise                       → approve with final size_pct
```

Each check short-circuits — as soon as a rule triggers, a `RiskCheck` with an explanation is returned without evaluating further rules.

### check_exit_conditions()

Designed for WebSocket tick frequency — must be extremely fast.

```python
def check_exit_conditions(self, symbol: str, price: float) -> dict | None:
    pos = self._positions.get(symbol.upper())
    if pos is None:
        return None               # fast path: no position, nothing to check

    drawdown_pct = (pos.entry_price - price) / pos.entry_price * 100
    gain_pct     = (price - pos.entry_price) / pos.entry_price * 100

    if drawdown_pct >= settings.stop_loss_pct:
        del self._positions[symbol.upper()]   # prevent double-trigger
        return { "action": "SELL", "reasoning": f"Stop-loss: {drawdown_pct:.2f}%", ... }

    if settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
        del self._positions[symbol.upper()]
        return { "action": "SELL", "reasoning": f"Take-profit: {gain_pct:.2f}%", ... }

    return None
```

The position is removed immediately on detection, before the order is even placed. This prevents a scenario where the next tick (arriving milliseconds later) would also trigger the same position.

### filter_decisions() (hourly cycle batch)

First calls `check_stop_losses()` — which calls `check_exit_conditions()` for each symbol in `market_data` — to handle any exit conditions. Stop-loss decisions replace whatever Claude said. Then each non-stop-loss decision goes through `evaluate_decision()`.

---

## execution_service.py

### Purpose

Takes an approved decision and either places a real order on Binance or constructs a synthetic paper order. Updates `RiskService` position state in both cases. Logs every execution to `logs/trades.jsonl`.

### Order quantity calculation

```python
qty = portfolio_usdt * (size_pct / 100.0) / current_price
```

Example: 10% of $10,000 portfolio at $1,742.30/ETH = 1,000 / 1,742.30 = **0.574 ETH**

This is a fixed-fraction position sizing approach — each position is a fixed percentage of the total portfolio value at the time of entry. It does not compound position size based on profits.

### Paper trading

A synthetic order is constructed with status `paper_filled`. The quantity is computed, position state is updated in `RiskService`, and the record is logged. No network call is made.

### Live trading

```python
ccxt_symbol = f"{asset[:-4]}/USDT"   # ETHUSDT → ETH/USDT
order = get_exchange().create_market_order(ccxt_symbol, side, qty)
filled_price = float(order.get("average") or current_price)
```

`create_market_order()` sends a market order to Binance. The filled price comes from `order["average"]` (the volume-weighted average fill price). If Binance doesn't return it, the last WebSocket price is used as a fallback.

### Position state update

After any non-HOLD execution:

```python
if action == "BUY":
    self.risk_service.record_open_position(asset, price, size_pct)
elif action == "SELL":
    self.risk_service.close_position(asset)
```

This keeps `RiskService._positions` in sync with what was actually executed.

### Trade log format

Every execution appends one JSON line to `logs/trades.jsonl`:

```json
{
  "timestamp": "2026-04-22T19:00:45Z",
  "asset": "ETHUSDT",
  "action": "BUY",
  "confidence": 0.81,
  "size_pct": 10,
  "current_price": 1742.30,
  "portfolio_usdt": 10000.0,
  "reasoning": "...",
  "paper_trading": true,
  "order": { "id": "PAPER-...", "qty": 0.574, "price": 1742.30, "status": "paper_filled" },
  "error": null
}
```

---

## trading_cycle.py

### Purpose

The hourly orchestrator. Reads market state, fetches sentiment, calls AI, applies risk, executes, and persists. It is a thin coordinator — all real logic lives in the services it calls.

### run()

```python
def run(self, db: Session) -> None:
    market_data = self._build_market_data()
    if not market_data:
        return                              # WebSocket not ready yet

    sentiment_data = self._fetch_sentiment(symbols)
    decisions      = self._fetch_decisions(market_data, sentiment_data, symbols)
    filtered       = self.risk_service.filter_decisions(decisions, market_data)
    balance        = self._fetch_balance()

    for decision in filtered:
        try:
            self._persist_cycle(db, symbol, md, decision, balance, now)
        except IntegrityError:
            db.rollback()                   # duplicate snapshot — skip
        except Exception:
            db.rollback()                   # any other error — skip this symbol
```

Each symbol's database write is wrapped in its own try/except so a failure on one symbol doesn't prevent others from being persisted.

### _persist_cycle()

Writes four rows per symbol in a single transaction:

1. `HourlyMarketSnapshot` — current price/volume data
2. `Position` — wallet balance at snapshot time
3. `AIDecision` — the approved decision
4. `Execution` — the result of executing that decision

Each `db.flush()` after each `db.add()` forces SQLAlchemy to assign auto-increment IDs (needed for foreign key references to subsequent rows). `db.commit()` at the end atomically commits all four rows.

### _get_or_create_asset()

Queries the `assets` table for the symbol. If not found, inserts a new row with `base_currency` derived by stripping `USDT` from the symbol. This means the first time the bot processes `ETHUSDT`, it creates the Asset record automatically.
