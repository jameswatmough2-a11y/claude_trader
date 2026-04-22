# Claude Trader

An automated crypto trading bot powered by Claude AI. Runs on an hourly schedule, fetches live prices and real candle data from Binance, reads market sentiment, and asks Claude whether to buy, sell, or hold. All decisions are logged to SQLite and paper-traded by default.

See [OVERVIEW.md](OVERVIEW.md) for full documentation.

## Quick start

```bash
# 1. Copy env file and add your Anthropic API key
cp .env.example .env

# 2. Activate virtualenv
source env/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. One-time TextBlob setup
python -m textblob.download_corpora

# 5. Run
cd crypto_bot
uvicorn app.main:app --reload
```

API docs: `http://localhost:8000/docs`

Trade log: `logs/trades.jsonl`

## Configuration

The only required setting is `ANTHROPIC_API_KEY` in `.env`. Everything else has safe defaults (paper trading on, $10,000 simulated balance).

Set `PAPER_TRADING=false` and add Binance API keys only when you're ready for live trading.
