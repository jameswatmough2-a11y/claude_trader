# Trading Logic

This document explains the decision-making chain in full detail — how the bot goes from raw market data to an executed trade.

---

## 1. AI Decision Engine

### Role

Claude is asked once per hour to produce a trading decision for each tracked symbol. It receives structured market and sentiment data and returns a JSON array.

### System prompt

```
You are a disciplined crypto trading analyst. Evaluate the market and
sentiment data provided, then return a single JSON array of trading decisions —
one object per asset. Never deviate from the schema below.

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

The prompt is structured as constraints, not suggestions. Claude is not asked for analysis — it is asked to populate a schema. This keeps outputs consistent and parseable.

### Confidence score

Confidence is a number Claude assigns to its own certainty. It is used in two places:

1. **Claude's self-filtering**: the system prompt instructs Claude to set action=HOLD when confidence < 0.7
2. **Bot's validation layer**: `_validate_decisions()` also enforces this, overriding Claude if it didn't

In practice, Claude's self-filtered decisions and the bot's override produce the same result — but the bot's layer is a safety net for cases where Claude ignores the instruction.

### size_pct

Claude recommends a position size as a percentage of the total portfolio. The maximum is 20% per position. The bot further caps this against `MAX_POSITION_PCT` and `MAX_TOTAL_EXPOSURE_PCT` in the risk layer.

### Fallback behaviour

| Failure | What happens |
|---|---|
| API key missing | `ValueError` raised → caught by `_fetch_decisions()` → all HOLD |
| Network error | Exception caught → all HOLD with "AI service unavailable" |
| Response is not JSON | `json.loads` fails → caught → all HOLD |
| Response is not an array | `ValueError` raised → caught → all HOLD |
| Symbol missing from response | Added as HOLD with "Missing from model response" |
| Invalid action value | Coerced to HOLD |
| Malformed confidence | Clamped to `[0, 1]` |

---

## 2. Sentiment Analysis

### Purpose

Sentiment provides context the price data alone cannot. A rising price with overwhelmingly negative news (e.g., regulatory action) is a different signal to a rising price with positive fundamentals.

### Sources and weights

All sources are averaged equally — there is no per-source weighting:

```python
blended = mean([rss_score, reddit_score, fear_greed_score])
```

Each source produces a score in `[-1, 1]`:

- **RSS**: TextBlob polarity of crypto news article titles/summaries matching the symbol's keywords
- **Reddit**: TextBlob polarity of Reddit post titles, weighted by upvotes
- **Fear & Greed**: `(index_value - 50) / 50` where index_value is 0–100

A score of `0.0` means neutral. Positive means bullish sentiment. Negative means bearish.

### How Claude uses it

Sentiment appears in the prompt as a single number and a list of headlines. Claude synthesises this with the price data to form its reasoning. The bot does not interpret sentiment directly — it passes it to Claude and Claude decides how to weight it.

### Limitations

TextBlob is a general-purpose NLP library. It was not trained on financial text. A headline like "Bitcoin crashes below $90k" scores negatively (correct), but "Bitcoin hodlers stay strong despite crash" may score positively due to words like "strong" — which is misleading. Sentiment is a noisy signal that Claude should treat as one data point, not a primary driver.

---

## 3. Risk Management

### Kill switch

`KILL_SWITCH` is read fresh from the environment on every decision evaluation:

```python
def _kill_switch_active() -> bool:
    return os.getenv("KILL_SWITCH", "false").lower() == "true"
```

This means you can set `KILL_SWITCH=true` in your `.env` file and send a SIGHUP (or just save the file if using `--reload`) to halt all new positions immediately, without restarting the process. All BUY and SELL decisions are converted to HOLD while the switch is active.

### Entry guard

```python
if action == "BUY" and asset in self._positions:
    return HOLD("Already holding — skipping BUY to avoid stacking.")
```

This prevents buying more of an asset you already hold. The bot uses fixed-fraction sizing — a second BUY would effectively double exposure without the AI having intended that. If Claude sees a continuing opportunity and returns BUY for an already-held asset, this guard converts it to HOLD until the position is closed.

### Exit guard

```python
if action == "SELL" and asset not in self._positions:
    return HOLD("No open position — ignoring SELL.")
```

Prevents issuing a sell order when there is nothing to sell. Without this, the bot could attempt to place a sell order for an asset it doesn't hold, which would either be rejected by the exchange or sell borrowed assets (margin trading, which is not intended here).

### Confidence threshold

`MIN_CONFIDENCE` (default 0.7) is enforced by the bot independently of Claude's own self-filtering. This is a double safety net. A decision only acts when Claude's confidence is at least 70%.

### Position sizing

The approved `size_pct` is the minimum of:
- Claude's recommended `size_pct`
- `MAX_POSITION_PCT` (default 20%)
- Available headroom under `MAX_TOTAL_EXPOSURE_PCT` (default 60%)

Example: if you already hold 50% exposure and `MAX_TOTAL_EXPOSURE_PCT=60`, a new BUY can use at most 10% even if Claude recommended 15%.

### Stop-loss (real-time)

On every WebSocket tick for a symbol with an open position:

```
drawdown_pct = (entry_price - current_price) / entry_price * 100

if drawdown_pct >= STOP_LOSS_PCT:
    → remove position from memory immediately
    → dispatch SELL order to TriggerExecutor queue
```

The position is removed **before** the order reaches the executor. This prevents a second WebSocket tick (arriving milliseconds later) from detecting the same drawdown and dispatching a second SELL order.

### Take-profit (real-time)

```
gain_pct = (current_price - entry_price) / entry_price * 100

if TAKE_PROFIT_PCT > 0 and gain_pct >= TAKE_PROFIT_PCT:
    → same mechanism as stop-loss
```

`TAKE_PROFIT_PCT=0` disables this entirely. The check `settings.take_profit_pct > 0` ensures zero does not accidentally trigger on any gain.

---

## 4. Execution Mechanics

### Order quantity

```python
qty = portfolio_usdt * (size_pct / 100.0) / current_price
```

This is **fixed-fraction position sizing**. The position is sized as a fixed percentage of the total USDT balance, regardless of the asset's price or volatility.

Example:
- Portfolio: $10,000 USDT
- BUY ETHUSDT at size_pct=10
- Price: $1,742.30
- USDT to spend: $10,000 × 10% = $1,000
- Quantity: $1,000 / $1,742.30 = **0.5739 ETH**

The bot does not adjust position sizes for volatility or risk-per-trade. Adding ATR-based sizing or Kelly criterion would be a meaningful upgrade.

### Paper trading

In paper mode, no HTTP call is made to Binance. A synthetic order is constructed:

```python
{
    "id": "PAPER-<ISO timestamp>",
    "symbol": "ETHUSDT",
    "side": "buy",
    "type": "market",
    "qty": 0.5739,
    "price": 1742.30,     ← WebSocket last price at time of execution
    "status": "paper_filled",
}
```

Slippage and fees are recorded as zero. In reality, a market order will fill at a slightly worse price than the quoted price due to:
- **Spread**: you buy at the ask, not the mid-price
- **Market impact**: large orders move the order book
- **Exchange fees**: Binance charges 0.1% per trade (0.075% with BNB)

The paper USDT balance (`PAPER_BALANCE_USDT`) is a static configured constant. It does not decrease when you buy or increase when you sell. Paper trading tracks position state in memory only — it does not simulate a changing balance.

### Live trading

```python
order = get_exchange().create_market_order(
    symbol="ETH/USDT",
    side="buy",
    amount=0.5739,         ← quantity in base currency
)
filled_price = float(order.get("average") or current_price)
```

`create_market_order()` sends a market order via Binance REST API. The response includes the actual fill details. If the order is partially filled (rare for liquid markets), `order["average"]` is the weighted average fill price.

The actual filled price is used for position tracking (not the price at decision time). This keeps stop-loss calculations accurate.

### Status values

| Status | Meaning |
|---|---|
| `paper_filled` | Paper trade — synthetic fill at last price |
| `filled` | Live trade — actually filled on exchange |
| `rejected` | Live trade — ccxt raised an error |
| `none` | HOLD decision — no order placed |
