# MVP Summary

What works, what is intentionally simplified, and what should be improved next.

---

## What now works

### Real OHLCV candles
The trading cycle fetches actual candlestick data from Binance via ccxt on every run, using the configured `ohlcv_interval` (default `1h`). Candles are upserted to the `ohlcv_candles` table and passed to the AI prompt as a trend line. The interval is configurable from the Settings page without restart.

### Realistic paper trading
Paper BUY orders fill at the ask price; SELL orders fill at the bid price. A configurable taker fee (default 0.1%) is deducted from every paper trade. `fee_amount`, `fee_rate`, and `fill_source` are recorded in the `executions` table and `logs/trades.jsonl`. Paper P&L now reflects the real cost of trading.

### Minimum order validation
Before any paper or live execution, `market_validator.py` checks the quantity against Binance's minimum order size and minimum notional, and normalizes it to the exchange step size. Executions below the minimum are rejected cleanly before touching the balance or position state.

### AI fallback to rule-based logic
When the Claude API is unavailable (outage, rate limit, misconfigured key), the system falls back to a simple SMA5/SMA10 crossover + momentum strategy rather than holding all positions inactively. The fallback covers entry signals, exit signals, and a volatility guardrail. Every decision is tagged with `decision_source: "ai"` or `"fallback_rule"` so the source of each trade is always auditable.

### Fast sentiment with caching
Sentiment is fetched concurrently (ThreadPoolExecutor) and cached with a 30-minute TTL. A warm cache means zero HTTP calls for sentiment during most cycles. Text from RSS and Reddit is sanitized (HTML/markdown/URL stripped, length capped) before being scored by TextBlob or included in the AI prompt.

### Structured event logging
All significant system events — cycle starts/ends, AI requests, fallbacks, order placements, WebSocket lifecycle — are written to the `system_logs` table with level, component, event type, symbol, cycle ID, and optional JSON details. The `GET /logs` API exposes these with pagination and filtering. The **Logs page** in the dashboard provides a real-time filterable view without SSH access.

### Live order verification
In live mode, after placing a market order the system waits 1 second and fetches the order back from the exchange to check fill status. `verification_status` (`"filled"`, `"partial_or_open"`, or `"verification_failed"`) is stored on each execution record.

### Startup reconciliation (live mode)
On startup in live mode, `_reconcile_live_positions()` compares the bot's internal position state against actual exchange balances and logs any discrepancies. This gives the operator visibility into drift without auto-adjusting state.

### Enhanced health endpoint
`GET /health` now reports: bot running, WebSocket connected, last cycle time, last AI call time, last AI failure time, sentiment cache status per symbol, OHLCV interval, and fee rate. Useful for at-a-glance operational status.

---

## What is intentionally simplified

### SQLite for everything
All tables — including `ohlcv_candles` and `system_logs` — are in the same SQLite file. The log viewer queries `system_logs` directly via SQLAlchemy. This is simple and works at MVP scale (3 symbols, hourly cycles). No external log aggregator or time-series database.

### Synchronous sentiment fetching
Sentiment uses `concurrent.futures.ThreadPoolExecutor` with synchronous `requests` calls. This works because the trading cycle runs in a thread (via `asyncio.to_thread`), not in the event loop directly. An async approach (aiohttp + asyncio) would be more efficient but adds complexity.

### TextBlob sentiment scoring
TextBlob is a general-purpose polarity scorer trained on movie reviews. It misinterprets many crypto-specific phrases. It was kept because it is simple, has no external API dependency, and has no rate limits. The TTL cache means it rarely runs anyway.

### Exchange rates are hardcoded
Display currency conversion (USD → GBP, EUR, etc.) uses hardcoded rates in `lib/display-prefs.ts`. There is no live FX feed. Rates drift over time but this is display-only — no trading logic uses them.

### Partial fill handling
When `verification_status` is `"partial_or_open"`, the position is still recorded at the full intended size. Partial fills are logged but not reconciled.

### Startup reconciliation is log-only
Discrepancies between internal positions and exchange balances are logged to `system_logs` with `event_type: "reconciliation_discrepancy"` but the internal state is not auto-adjusted. An operator must review and decide.

---

## Recommended next improvements

Ordered roughly by value:

### 1. Live position endpoint
`GET /positions` returns DB snapshot records, not live position state. Add `GET /live-positions` that reads from `risk_service.get_open_positions()` directly. The dashboard Overview page could then show a live position panel without waiting for the next cycle.

### 2. Async sentiment
Replace `requests` + `ThreadPoolExecutor` with `aiohttp` + `asyncio.gather`. This would eliminate the thread overhead and integrate more naturally with the async application.

### 3. Better NLP for sentiment
Replace TextBlob with a crypto-fine-tuned model (e.g. FinBERT via HuggingFace) or a dedicated crypto sentiment API. Current TextBlob accuracy on financial text is poor.

### 4. Partial fill handling
When `verification_status == "partial_or_open"`, fetch the actual filled quantity from the exchange order and record the real position size. Consider cancelling the remainder of a limit order.

### 5. Startup reconciliation → auto-adjust
Make `_reconcile_live_positions()` optionally correct `RiskService._positions` from the exchange balance. Add an `AUTO_RECONCILE` config flag (default `false`). This removes the need for manual operator intervention after unexpected restarts.

### 6. Backtesting
Add a `run_backtest(symbol, start_date, end_date)` function that replays historical candles through the fallback strategy (and optionally the AI) and reports P&L, max drawdown, win rate. This would let you tune the fallback thresholds without running live cycles.

### 7. External alerting
Add a webhook or Slack notification for: stop-loss triggers, consecutive AI failures, consecutive HOLD cycles, paper balance below a threshold. Currently all of this is only visible in the Logs page.

### 8. PostgreSQL migration
If the cycle interval is reduced below 15 minutes or the symbol list grows beyond ~10 symbols, SQLite's single-writer limit will cause queued write contention. The migration is straightforward — change `DATABASE_URL` and remove `connect_args={"check_same_thread": False}`.

### 9. Prometheus metrics endpoint
Expose `GET /metrics` with cycle latency, AI call latency, sentiment cache hit rate, open positions count, paper P&L. Lets you hook the bot into Grafana/Prometheus without parsing the logs.

### 10. Multi-timeframe signal confirmation
The current AI prompt and fallback strategy use a single OHLCV interval. Adding a second timeframe (e.g. daily candles for trend direction + hourly for entry timing) would reduce false signals from short-term noise.
