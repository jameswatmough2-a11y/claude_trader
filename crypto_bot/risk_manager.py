"""
risk_manager.py — Enforce position limits, confidence thresholds,
total exposure caps, stop-loss logic, and a kill switch.

All limits are read from environment variables (with sensible defaults).
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.7"))
MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "20"))
MAX_TOTAL_EXPOSURE_PCT: float = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "60"))
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "5"))


def _kill_switch_active() -> bool:
    """Re-read the env var on every call so it can be changed at runtime."""
    return os.getenv("KILL_SWITCH", "false").lower() == "true"


# ── Position tracking (in-memory for the lifetime of the process) ─────────────


@dataclass
class OpenPosition:
    asset: str
    entry_price: float
    size_pct: float          # % of portfolio allocated
    current_price: float = 0.0


# Module-level position book
_positions: dict[str, OpenPosition] = {}


def record_open_position(asset: str, entry_price: float, size_pct: float) -> None:
    """Track that we've opened a position for *asset*."""
    _positions[asset] = OpenPosition(
        asset=asset, entry_price=entry_price, size_pct=size_pct, current_price=entry_price
    )
    logger.info("risk_manager: opened position %s @ %.4f (%.1f%%)", asset, entry_price, size_pct)


def close_position(asset: str) -> None:
    """Remove a position from the book."""
    if asset in _positions:
        del _positions[asset]
        logger.info("risk_manager: closed position %s", asset)


def update_current_prices(market_data: dict[str, Any]) -> None:
    """Refresh current prices from the latest market snapshot."""
    for asset, pos in _positions.items():
        md = market_data.get(asset, {})
        if "last_price" in md:
            pos.current_price = float(md["last_price"])


def get_total_exposure_pct() -> float:
    """Return sum of all open position size_pct values."""
    return sum(p.size_pct for p in _positions.values())


# ── Core validation ───────────────────────────────────────────────────────────


@dataclass
class RiskCheck:
    approved: bool
    reason: str
    adjusted_decision: dict[str, Any] = field(default_factory=dict)


def _hold_decision(decision: dict[str, Any], reason: str) -> dict[str, Any]:
    """Return a copy of *decision* forced to HOLD."""
    return {**decision, "action": "HOLD", "size_pct": 0, "reasoning": reason}


def evaluate_decision(
    decision: dict[str, Any],
    current_price: float,
) -> RiskCheck:
    """
    Apply all risk rules to a single Claude decision.

    Returns RiskCheck with approved=True and a (possibly adjusted) decision,
    or approved=False with the rejection reason.
    """
    asset = decision["asset"]
    action = decision["action"]
    confidence = decision["confidence"]
    size_pct = decision["size_pct"]

    logger.info(
        "risk_manager: evaluating %s  action=%s  conf=%.2f  size=%.0f%%",
        asset, action, confidence, size_pct,
    )

    # ── 1. Kill switch ────────────────────────────────────────────────────────
    if _kill_switch_active():
        reason = "Kill switch is active — all trading halted."
        logger.warning("risk_manager: KILL SWITCH — blocking %s", asset)
        return RiskCheck(
            approved=False,
            reason=reason,
            adjusted_decision=_hold_decision(decision, reason),
        )

    # ── 2. HOLD passes immediately ────────────────────────────────────────────
    if action == "HOLD":
        logger.info("risk_manager: %s HOLD — approved", asset)
        return RiskCheck(approved=True, reason="HOLD requires no action.", adjusted_decision=decision)

    # ── 3. Confidence threshold ───────────────────────────────────────────────
    if confidence < MIN_CONFIDENCE:
        reason = (
            f"Confidence {confidence:.2f} below minimum {MIN_CONFIDENCE:.2f} — converting to HOLD."
        )
        logger.warning("risk_manager: %s low confidence → HOLD", asset)
        return RiskCheck(
            approved=True,  # not rejected; just demoted
            reason=reason,
            adjusted_decision=_hold_decision(decision, reason),
        )

    # ── 4. Per-asset position cap ─────────────────────────────────────────────
    if size_pct > MAX_POSITION_PCT:
        logger.warning(
            "risk_manager: %s size %.0f%% > max %.0f%% — capping", asset, size_pct, MAX_POSITION_PCT
        )
        size_pct = MAX_POSITION_PCT

    # ── 5. Total exposure cap ─────────────────────────────────────────────────
    current_exposure = get_total_exposure_pct()
    if action == "BUY":
        headroom = MAX_TOTAL_EXPOSURE_PCT - current_exposure
        if headroom <= 0:
            reason = (
                f"Total exposure {current_exposure:.1f}% already at cap "
                f"{MAX_TOTAL_EXPOSURE_PCT:.1f}% — cannot open new position for {asset}."
            )
            logger.warning("risk_manager: exposure cap reached — blocking %s BUY", asset)
            return RiskCheck(
                approved=True,
                reason=reason,
                adjusted_decision=_hold_decision(decision, reason),
            )
        if size_pct > headroom:
            logger.warning(
                "risk_manager: %s size capped from %.0f%% to %.0f%% (exposure headroom)",
                asset, size_pct, headroom,
            )
            size_pct = headroom

    # ── 6. Build approved decision (with any capped size) ────────────────────
    approved_decision = {**decision, "size_pct": int(size_pct)}
    logger.info(
        "risk_manager: %s %s APPROVED  size=%.0f%%", asset, action, size_pct
    )
    return RiskCheck(approved=True, reason="Passed all risk checks.", adjusted_decision=approved_decision)


def check_stop_losses(market_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Scan open positions for stop-loss breaches.

    Returns a list of SELL decisions for assets that have fallen more than
    STOP_LOSS_PCT below their entry price.
    """
    update_current_prices(market_data)
    stop_orders: list[dict[str, Any]] = []

    for asset, pos in list(_positions.items()):
        if pos.entry_price <= 0 or pos.current_price <= 0:
            continue
        drawdown_pct = (pos.entry_price - pos.current_price) / pos.entry_price * 100
        logger.debug(
            "stop_loss check %s: entry=%.4f current=%.4f drawdown=%.2f%%",
            asset, pos.entry_price, pos.current_price, drawdown_pct,
        )
        if drawdown_pct >= STOP_LOSS_PCT:
            logger.warning(
                "STOP LOSS TRIGGERED — %s  entry=%.4f  current=%.4f  drawdown=%.2f%%",
                asset, pos.entry_price, pos.current_price, drawdown_pct,
            )
            stop_orders.append(
                {
                    "asset": asset,
                    "action": "SELL",
                    "confidence": 1.0,
                    "size_pct": int(pos.size_pct),
                    "reasoning": (
                        f"Stop-loss triggered: price fell {drawdown_pct:.2f}% "
                        f"below entry (threshold: {STOP_LOSS_PCT}%)."
                    ),
                }
            )

    return stop_orders


def filter_decisions(
    decisions: list[dict[str, Any]],
    market_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Run all decisions through risk checks and return the final approved set.

    Also injects any stop-loss SELL orders.
    """
    # Stop-loss check first — these are non-negotiable
    stop_orders = check_stop_losses(market_data)
    for order in stop_orders:
        logger.info("risk_manager: injecting stop-loss order for %s", order["asset"])

    final: list[dict[str, Any]] = []

    # Build a set of assets already handled by stop-loss
    stop_assets = {o["asset"] for o in stop_orders}

    for decision in decisions:
        asset = decision["asset"]
        md = market_data.get(asset, {})
        current_price = float(md.get("last_price", 0))

        if asset in stop_assets:
            # Override Claude's decision with stop-loss sell
            sl = next(o for o in stop_orders if o["asset"] == asset)
            logger.info("risk_manager: %s stop-loss overrides Claude decision", asset)
            final.append(sl)
            continue

        check = evaluate_decision(decision, current_price)
        final.append(check.adjusted_decision)

    logger.info(
        "risk_manager: filter_decisions summary — %s",
        [{d["asset"]: d["action"]} for d in final],
    )
    return final
