"""
main.py — Hourly orchestrator for the crypto trading bot.

Wires together: data_feeds → sentiment → claude_brain → risk_manager → executor.
Uses APScheduler to run the trading cycle every hour.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

# ── Logging setup (must come before module imports that log) ─────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_fmt)
_console_handler.setLevel(logging.INFO)

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)
_file_handler.setLevel(logging.DEBUG)

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(_console_handler)
root_logger.addHandler(_file_handler)

logger = logging.getLogger(__name__)

# ── Bot imports (after logging is configured) ─────────────────────────────────

from data_feeds import ASSETS, fetch_balance, get_all_market_data  # noqa: E402
from sentiment import get_all_sentiment  # noqa: E402
from claude_brain import get_trading_decisions  # noqa: E402
from risk_manager import filter_decisions  # noqa: E402
from executor import execute_all_decisions  # noqa: E402

PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"


def trading_cycle() -> None:
    """
    One complete hourly trading cycle:
      1. Fetch market data
      2. Aggregate sentiment
      3. Ask Claude for decisions
      4. Run risk checks
      5. Execute (or paper-log) approved decisions
    """
    logger.info("=" * 60)
    logger.info("TRADING CYCLE STARTED  [paper=%s]", PAPER_TRADING)
    logger.info("=" * 60)

    # ── 1. Market data ────────────────────────────────────────────────────────
    logger.info("step 1/5 — fetching market data")
    try:
        market_data = get_all_market_data()
    except Exception as exc:  # noqa: BLE001
        logger.error("market data fetch failed — aborting cycle: %s", exc)
        return

    # ── 2. Sentiment ──────────────────────────────────────────────────────────
    logger.info("step 2/5 — aggregating sentiment")
    try:
        sentiment_map = get_all_sentiment(ASSETS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentiment fetch failed — continuing with empty sentiment: %s", exc)
        sentiment_map = {}

    # ── 3. Claude decisions ───────────────────────────────────────────────────
    logger.info("step 3/5 — requesting Claude trading decisions")
    try:
        raw_decisions = get_trading_decisions(market_data, sentiment_map)
    except Exception as exc:  # noqa: BLE001
        logger.error("Claude decision failed — aborting cycle: %s", exc)
        return

    # ── 4. Risk filtering ─────────────────────────────────────────────────────
    logger.info("step 4/5 — applying risk manager")
    try:
        approved_decisions = filter_decisions(raw_decisions, market_data)
    except Exception as exc:  # noqa: BLE001
        logger.error("risk_manager failed — aborting cycle: %s", exc)
        return

    # ── 5. Execution ──────────────────────────────────────────────────────────
    logger.info("step 5/5 — executing decisions")
    try:
        balance = fetch_balance()
    except Exception as exc:  # noqa: BLE001
        logger.warning("balance fetch failed — using zero portfolio: %s", exc)
        balance = {"USDT": {"free": 0.0, "used": 0.0, "total": 0.0}}

    try:
        trade_records = execute_all_decisions(approved_decisions, market_data, balance)
    except Exception as exc:  # noqa: BLE001
        logger.error("execution failed: %s", exc)
        return

    # ── Cycle summary ─────────────────────────────────────────────────────────
    actions = {r["asset"]: r["action"] for r in trade_records}
    logger.info("TRADING CYCLE COMPLETE — %s", actions)
    logger.info("=" * 60)


def main() -> None:
    mode = "PAPER TRADING" if PAPER_TRADING else "*** LIVE TRADING ***"
    logger.info("Crypto Trading Bot starting — mode: %s", mode)

    if not PAPER_TRADING:
        logger.warning(
            "LIVE mode is active — real funds will be used. "
            "Set PAPER_TRADING=true in .env to disable."
        )

    # Run once immediately so we don't wait a full hour on first start
    logger.info("Running initial trading cycle on startup...")
    trading_cycle()

    # Schedule subsequent runs at the top of every hour
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        trading_cycle,
        trigger=CronTrigger(minute=0),   # top of every hour
        id="hourly_trading_cycle",
        name="Hourly Trading Cycle",
        max_instances=1,
        misfire_grace_time=300,           # allow up to 5-minute late start
    )

    logger.info("Scheduler started — next run at the top of the hour (UTC)")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
