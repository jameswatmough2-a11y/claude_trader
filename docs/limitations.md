# Limitations

This document is an honest accounting of what would break, degrade, or behave incorrectly in production use. Resolved items are struck through. Each section explains the impact and what would be required to address remaining issues.

---

## 1. ~~Position State Is Lost on Restart~~ — Resolved

Position state is persisted to the database and restored on startup via `RiskService.restore_from_db(db)`. Stop-loss and take-profit checks resume immediately after restart.

---

## 2. ~~DB Position Records Do Not Reflect Actual Positions~~ — Resolved

Position records now store actual post-execution state. `GET /positions` returns meaningful data and the database is used as the recovery source on startup.

---

## 3. ~~Paper Balance Is Not Stateful~~ — Resolved

The paper USDT balance is tracked in memory and restored on startup. `ExecutionService.restore_paper_balance_from_db(db)` replays all historical filled executions to compute the correct current balance.

---

## 4. ~~No Actual OHLCV Per Timeframe~~ — Resolved

**Fix applied:** `trading_cycle.py` now calls `fetch_ohlcv(symbol, settings.ohlcv_interval)` via ccxt REST each cycle. Real candles are upserted to the `ohlcv_candles` table (unique on `symbol + timeframe + open_time`). The AI prompt includes the last 6 hourly closes as a trend line. The OHLCV interval is configurable via `PUT /api/bot/config` (`ohlcv_interval` field) and takes effect on the next cycle without restart.

**What remains:** The `HourlyMarketSnapshot` table still stores `last_price` for all OHLC columns (unchanged) — it is a snapshot of the WebSocket state, not a candle. The `ohlcv_candles` table is the authoritative source for actual candle data.

---

## 5. ~~Sentiment Analysis Is Slow and Unreliable~~ — Partially Resolved

**Fix applied:**
- TTL cache (30-minute default) via `_sentiment_cache` dict. A cache hit costs nothing — no HTTP requests, no TextBlob scoring.
- Concurrent RSS + Reddit fetch per asset via `concurrent.futures.ThreadPoolExecutor(2)`, then all assets in parallel via `ThreadPoolExecutor(4)`. The 1-second-per-subreddit sleep is gone.
- Text sanitization (`_sanitize`): strips HTML tags, markdown formatting, and URLs before feeding to Claude and TextBlob. Limits text to 200 characters.
- Fear & Greed Index cached separately for 30 minutes, fetched once globally.

**What remains:**
- TextBlob is still a general-purpose polarity scorer. It misinterprets many crypto-specific phrases.
- The Fear & Greed Index is global, not asset-specific.
- No async HTTP — the ThreadPoolExecutor runs synchronous `requests` calls in threads.

---

## 6. ~~No Slippage or Fee Modeling~~ — Resolved

**Fix applied:**
- Paper BUY orders fill at `ask` price; SELL orders fill at `bid` price. Falls back to `last_price` with a logged warning if bid/ask is unavailable.
- Taker fee recorded: `fee_amount = qty × fill_price × taker_fee_rate` (default 0.1%). Deducted from paper balance on BUY, deducted from proceeds on SELL.
- `fee_amount`, `fee_rate`, and `fill_source` written to the `executions` table and `logs/trades.jsonl`.
- Fee rate configurable via `PUT /api/bot/config` (`taker_fee_rate` field).

**What remains:** Slippage (market impact beyond the spread) is not modeled. For MVP-scale order sizes this is acceptable.

---

## 7. ~~No Minimum Order Size Validation~~ — Resolved

**Fix applied:** `market_validator.py` fetches market info from ccxt (cached in `_market_cache`), validates minimum quantity, maximum quantity, and minimum notional (cost), and normalizes quantity to the exchange step size. If validation fails, the execution is rejected with an error message before any order is placed or paper balance is updated. If market info is unavailable, the order is allowed through (permissive fallback).

---

## 8. ~~Live Mode Is Unverified~~ — Partially Resolved

**Fix applied:**
- After placing a live order, `_execute_live` waits 1 second and fetches the order back from the exchange via `get_exchange().fetch_order()`.
- `verification_status` is recorded: `"filled"`, `"partial_or_open"`, or `"verification_failed"`.
- Stored in the `executions` table and logged via `db_logger`.

**What remains:**
- Partial fills are detected but not handled — the position is still recorded at the full intended size.
- No retry or cancellation logic for `"partial_or_open"` orders.

---

## 9. ~~Single Point of Failure: Claude API~~ — Resolved

**Fix applied:** `trading_cycle.py` now catches Claude API failures and falls back to `get_fallback_decisions()` from `fallback_strategy.py`. The fallback uses SMA5/SMA10 crossover + momentum + volatility guardrails to generate rule-based BUY/SELL/HOLD decisions. All fallback decisions are tagged `decision_source: "fallback_rule"`. AI decisions are tagged `decision_source: "ai"`. The source is stored in the `ai_decisions` table.

**Fallback logic:**
- Requires at least 10 candles (HOLD if fewer)
- Entry: price > SMA5 > SMA10, bullish last candle, recent 3-candle return > 1.5%
- Exit: price < SMA5, bearish last candle, recent return < -1.5%
- Volatility guard: skip if 5-candle range > 5% (no signal in choppy markets)
- Conservative default size: 10%

---

## 10. ~~No Order Cancellation or Position Reconciliation~~ — Partially Resolved

**Fix applied:** On startup in live mode, `main.py` calls `_reconcile_live_positions()`, which fetches balances from the exchange and logs any discrepancies between the exchange's actual holdings and the bot's internal position state.

**What remains:** Reconciliation is **log-only** — it does not auto-adjust `RiskService._positions`. An operator must review the log and manually decide whether to trust the exchange state or the internal state. This is intentionally conservative for MVP — silent auto-adjustment could mask bugs.

---

## 11. SQLite Is a Single-Writer Bottleneck

**What happens:** SQLite allows only one writer at a time.

**Mitigations added:** Indexes on `created_at`, `symbol`, `level`, and `component` in `system_logs`; index on `(symbol, timeframe, open_time)` in `ohlcv_candles`. These speed up the log-viewer queries that filter/sort by these columns.

**At current scale:** Not a problem. SQLite handles ~10,000 inserts/second; the cycle writes ~10 records per symbol per hour.

**If scaling to dozens of symbols or sub-hourly cycles:** Migrate to PostgreSQL.

---

## 12. No Backtesting

No way to test decision quality against historical data. Every evaluation requires a live WebSocket connection and live API calls.

**Fix:** See [upgrade_guide.md](upgrade_guide.md).

---

## 13. ~~Claude's Output Is Not Audited for Prompt Injection~~ — Partially Resolved

**Fix applied:** `_sanitize()` in `sentiment_service.py` strips HTML, markdown, and URLs from all RSS headlines and Reddit post titles before they are included in the prompt. Text is capped at 200 characters.

**What remains:**
- The sanitizer reduces attack surface but does not make prompt injection impossible.
- A sufficiently crafted plaintext injection (no markup) could still manipulate Claude's reasoning.
- The AI validator and risk layer remain the primary defenses.

---

## 14. No Alerting or Monitoring

**What's added:** Structured logs are now written to the `system_logs` database table and exposed via `GET /logs` (paginated, filterable). The frontend Logs page provides real-time visibility without SSH access.

**What remains:** No external alerts (Slack, email, PagerDuty), no Prometheus metrics endpoint, no log shipping to an aggregator. The logs page requires browser access to the dashboard — there is no push notification for stop-loss triggers or failed cycles.
