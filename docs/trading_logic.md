# Trading Logic

This document explains the decision-making chain in full detail — how the bot goes from raw market data to an executed trade, and what rules govern every step.

---

## Overview

The trading logic pipeline has four stages:

```
1. Data assembly (market + sentiment)
       ↓
2. AI decision (Claude API)
       ↓
3. Risk filtering (RiskService)
       ↓
4. Execution (paper or live)
```

Each stage is fault-isolated — a failure in any stage produces a safe default (HOLD) rather than propagating an error to the next stage.

---

## Stage 1: Data Assembly

### Market data

The trading cycle reads from `MarketStateStore`, which is continuously updated by the WebSocket. For each symbol, the following values are available:

- `last_price` — most recent trade price
- `bid` / `ask` — best bid and ask
- `volume_24h` — 24-hour quote asset volume in USDT
- `price_change_24h_pct` — percentage change over last 24 hours
- `high_24h` / `low_24h` — 24-hour high and low

The cycle constructs a `market_data` dict and also builds an `ohlcv` sub-dict by repurposing these fields (`last_price` as all OHLC values, `high_24h`/`low_24h` for range, `volume_24h` for average volume). There is no separate OHLCV fetch during the cycle — the WebSocket stats are used directly.

### Sentiment data

`get_all_sentiment(symbols)` is called once per cycle. It:
1. Fetches the Fear & Greed Index (global, shared across symbols)
2. For each symbol: fetches and scores RSS headlines + Reddit posts
3. Returns `dict[str, AssetSentiment]`

The entire sentiment block is wrapped in a try/except. If it fails, the cycle uses `sentiment_data = {}` and Claude sees "Sentiment: unavailable" for all symbols.

---

## Stage 2: AI Decision (Claude)

### Prompt structure

The user prompt has this exact structure:

```
=== HOURLY TRADING ANALYSIS — 2026-04-22 14:00 UTC ===

Analyse the following data and return one JSON decision per asset.

--- BTCUSDT ---
Price: $67,423.0100  Bid: $67,422.9900  Ask: $67,423.0100
24h High: $68,500.0000  24h Low: $66,200.0000  Change: -0.842%
Avg 24h Volume: 1,234,568  Quote Volume: $1,234,567,890
Sentiment: 0.123 | Fear & Greed: 62/100
  1. Bitcoin ETF inflows reach record high this week
  2. BTC holds above key support at $67k
  3. Institutional interest in crypto remains elevated

--- ETHUSDT ---
Price: $3,245.1200  Bid: $3,245.0800  Ask: $3,245.1400
24h High: $3,310.0000  24h Low: $3,190.0000  Change: -1.234%
Avg 24h Volume: 456,789  Quote Volume: $456,789,012
Sentiment: -0.045 | Fear & Greed: 62/100
  1. Ethereum network activity increases ahead of upgrade
  2. ETH staking rewards remain stable

Return ONLY a JSON array.
```

### Expected response schema

Claude is expected to return exactly:

```json
[
  {
    "asset": "BTCUSDT",
    "action": "BUY",
    "confidence": 0.82,
    "size_pct": 15,
    "reasoning": "Strong 24h volume and positive sentiment support bullish entry."
  },
  {
    "asset": "ETHUSDT",
    "action": "HOLD",
    "confidence": 0.55,
    "size_pct": 0,
    "reasoning": "Mixed signals; confidence below threshold, maintaining HOLD."
  }
]
```

### System prompt constraints

The system prompt encodes the following rules that Claude is expected to follow:
- Return only a valid JSON array — no explanation, no markdown
- `size_pct` must be 0 when `action` is HOLD
- `size_pct` maximum is 20 for any single asset
- If `confidence < 0.7`, set action to HOLD and size_pct to 0
- Base decisions solely on the data provided (no outside knowledge about future events)

### Confidence handling

Confidence reflects how certain Claude is in its recommendation. The system is designed with a hard floor:

| Confidence | Effect |
|---|---|
| `>= 0.7` (default `MIN_CONFIDENCE`) | Decision passes through to risk check |
| `< 0.7` | Action forced to HOLD in `_validate_decisions` |
| `< 0.7` | Action forced to HOLD again in `risk_service.evaluate_decision` (second layer) |

The double enforcement (AI validator + risk service) is intentional redundancy. Even if the JSON parsing yields an unexpected confidence value, the risk layer catches it.

### Fallback behavior

If the Claude API call fails for any reason (network error, rate limit, invalid response, JSON parse error):
- All symbols default to `{"action": "HOLD", "confidence": 0.0, "size_pct": 0, "reasoning": "AI service unavailable — defaulted to HOLD."}`
- The cycle continues to the risk filter and persistence stages
- The error is logged at `EXCEPTION` level

If Claude returns a valid JSON array but omits one or more tracked symbols, the validator inserts a HOLD default for each missing symbol:
- `{"action": "HOLD", "confidence": 0.0, "reasoning": "Missing from model response — defaulted to HOLD."}`

---

## Stage 3: Risk Filtering

### Processing order

`risk_service.filter_decisions(decisions, market_data)` processes decisions in this order:

**First pass — stop-loss / take-profit check for existing positions:**

For each symbol with an open position, `check_exit_conditions(symbol, price)` is called using the current price from `market_data`. If triggered, the AI's decision for that symbol is **replaced** with a SELL order. The AI never gets to override a stop-loss.

**Second pass — evaluate each decision:**

For each decision (that wasn't replaced by a stop-loss):

1. **Kill switch** — any action → HOLD. Hot-reload: reads env var at call time.
2. **HOLD** — passed through unchanged (no further checks needed).
3. **BUY while holding** → HOLD. Prevents stacking positions on the same asset.
4. **SELL without position** → HOLD. Prevents phantom sells (no position to close).
5. **Confidence check** — below `MIN_CONFIDENCE` → HOLD.
6. **Size cap** — `size_pct > MAX_POSITION_PCT` → silently capped to `MAX_POSITION_PCT`.
7. **Exposure cap** — for BUY only: compute `current_exposure = sum(all open size_pcts)`. If `current_exposure >= MAX_TOTAL_EXPOSURE_PCT` → HOLD. If `current_exposure + proposed_size_pct > MAX_TOTAL_EXPOSURE_PCT` → reduce `size_pct` to available headroom.

### Exposure cap example

Settings: `MAX_TOTAL_EXPOSURE_PCT=60`, `MAX_POSITION_PCT=20`. Current positions:
- BTCUSDT: 20%
- ETHUSDT: 20%

Total exposure: 40%. Headroom: 20%.

Claude proposes `SOLUSDT BUY size_pct=20`. Risk evaluation:
- `current_exposure = 40%`
- `headroom = 60 - 40 = 20%`
- `proposed_size_pct = min(20, 20) = 20%`
- Approved with `size_pct=20`

If Claude proposes `SOLUSDT BUY size_pct=20` but BTCUSDT is 30%:
- `headroom = 60 - 50 = 10%`
- `size_pct` is reduced from 20 → 10

If total exposure is already at 60%:
- `headroom = 0` → HOLD, cannot open.

---

## Stage 4: Execution

### Mode selection

Mode is determined by `settings.paper_trading` (from `PAPER_TRADING` env var). This is evaluated at process start, not per-decision. The mode cannot be changed without restarting.

### Paper trading

```python
qty = portfolio_usdt * (size_pct / 100.0) / price
order = {
    "id": f"PAPER-{timestamp}",
    "symbol": asset,
    "side": "buy" | "sell",
    "type": "market",
    "qty": qty,
    "price": price,      # uses last_price from MarketStateStore
    "status": "paper_filled",
}
```

The price used for quantity calculation and the simulated fill price is the `last_price` from the WebSocket at the time of the cycle. This is **not** a guaranteed fill price — in live trading, slippage would apply.

No slippage simulation is performed. `fees_paid` and `slippage` are both stored as `0` in the database.

### Live trading

```python
ccxt_symbol = f"{asset[:-4]}/USDT"   # BTCUSDT → BTC/USDT
qty = portfolio_usdt * (size_pct / 100.0) / price
order = get_exchange().create_market_order(ccxt_symbol, side, qty)
filled_price = float(order.get("average") or price)
```

`create_market_order` places a market order at Binance's current best price. `order["average"]` is the volume-weighted average fill price for partially-filled orders.

For live mode, the bot needs `BINANCE_API_KEY` and `BINANCE_API_SECRET` in `.env`, and `PAPER_TRADING=false`.

### Position tracking update

After execution (both modes), `_update_positions` is called:
- BUY → `risk_service.record_open_position(asset, price, size_pct)`
- SELL → `risk_service.close_position(asset)`

This keeps `RiskService._positions` in sync. Critically, this state is **only in memory** and is lost on process restart.

---

## Stop-Loss Logic

Stop-loss is checked in two contexts: real-time (every WebSocket tick) and hourly (in the trading cycle).

### Real-time stop-loss (per tick)

```python
drawdown_pct = (entry_price - current_price) / entry_price * 100
if drawdown_pct >= STOP_LOSS_PCT:
    # trigger exit
```

Example: Entry at $67,423. `STOP_LOSS_PCT=5`. Exit triggers when price falls to:
```
$67,423 × (1 - 0.05) = $64,051.85
```

The check fires on every WebSocket tick for every open position. Binance sends `@ticker` updates roughly every second, so the maximum delay between the threshold being breached and the exit firing is approximately 1 second.

### Hourly stop-loss (cycle check)

The same `check_exit_conditions` method is called at the start of each hourly cycle via `check_stop_losses(market_data)`. This catches cases where:
- The real-time check was missed (e.g. WebSocket disconnected during the breach)
- A position was opened in a previous cycle and the real-time check wasn't active at the time

In practice, if the real-time check fires, the position is removed from `_positions` immediately, so the hourly check will find nothing to trigger.

### Take-profit logic

Symmetric to stop-loss but for upside:

```python
gain_pct = (current_price - entry_price) / entry_price * 100
if TAKE_PROFIT_PCT > 0 and gain_pct >= TAKE_PROFIT_PCT:
    # trigger exit
```

`TAKE_PROFIT_PCT=0` (the default) **disables** take-profit entirely. Set to a positive value (e.g. `10`) to exit when the position gains 10%.

---

## HOLD semantics

HOLD is not a no-op in all contexts:
- The AI validator emits HOLD when confidence is below threshold
- The risk layer emits HOLD when a rule is violated (but marks it approved)
- HOLD decisions are still persisted to the database (AIDecision + Execution records are written)
- HOLD decisions are still logged to `trades.jsonl`
- The execution record for a HOLD gets `status="none"` and `execution_time=None`

This means every cycle always produces a full DB record for every tracked symbol, regardless of action.

---

## Summary: Decision Fate Matrix

| Scenario | AI action | After risk filter | After execution |
|---|---|---|---|
| High confidence, no position | BUY | BUY (possibly size-adjusted) | Paper/live order placed, position recorded |
| High confidence, already holding | BUY | HOLD (double-entry guard) | Logged as HOLD |
| Low confidence | BUY | HOLD (confidence floor) | Logged as HOLD |
| Has position, AI says SELL | SELL | SELL | Order placed, position closed |
| No position, AI says SELL | SELL | HOLD (phantom sell guard) | Logged as HOLD |
| Stop-loss breaches (real-time) | — | SELL (overrides AI) | Exit order via TriggerExecutor |
| Stop-loss breaches (hourly) | — | SELL (overrides AI decision) | Exit order in cycle |
| Kill switch active | any | HOLD | Logged as HOLD |
| AI API failure | — | HOLD (fallback) | Logged as HOLD |
| Portfolio at exposure cap | BUY | HOLD | Logged as HOLD |
