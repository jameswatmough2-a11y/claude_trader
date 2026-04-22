from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _kill_switch_active() -> bool:
    return os.getenv("KILL_SWITCH", "false").lower() == "true"


@dataclass
class OpenPosition:
    asset: str
    entry_price: float
    size_pct: float
    current_price: float = 0.0


@dataclass
class RiskCheck:
    approved: bool
    reason: str
    adjusted_decision: dict[str, Any] = field(default_factory=dict)


class RiskService:
    """Tracks open positions in memory and enforces all risk rules."""

    def __init__(self) -> None:
        self._positions: dict[str, OpenPosition] = {}

    # ── Position state ─────────────────────────────────────────────────────────

    def record_open_position(self, asset: str, entry_price: float, size_pct: float) -> None:
        asset = asset.upper()
        self._positions[asset] = OpenPosition(
            asset=asset,
            entry_price=entry_price,
            size_pct=size_pct,
            current_price=entry_price,
        )
        logger.info("Opened position: %s @ %.4f (%.1f%% of portfolio)", asset, entry_price, size_pct)

    def close_position(self, asset: str) -> None:
        if self._positions.pop(asset.upper(), None):
            logger.info("Closed position: %s", asset.upper())

    def get_open_positions(self) -> dict[str, OpenPosition]:
        return dict(self._positions)

    def get_total_exposure_pct(self) -> float:
        return sum(p.size_pct for p in self._positions.values())

    # ── Hourly cycle: evaluate a single AI decision ────────────────────────────

    def evaluate_decision(self, decision: dict[str, Any], current_price: float) -> RiskCheck:
        asset = decision["asset"].upper()
        action = decision["action"].upper()
        confidence = float(decision["confidence"])
        size_pct = float(decision["size_pct"])

        if _kill_switch_active():
            reason = "Kill switch active — all trading halted."
            return RiskCheck(False, reason, self._to_hold(decision, reason))

        if action == "HOLD":
            return RiskCheck(True, "HOLD — no action.", {**decision, "action": "HOLD", "size_pct": 0})

        # Entry guard: prevent buying into an existing position
        if action == "BUY" and asset in self._positions:
            reason = f"Already holding {asset} — skipping BUY to avoid stacking."
            return RiskCheck(True, reason, self._to_hold(decision, reason))

        # Exit guard: prevent selling a position we don't hold
        if action == "SELL" and asset not in self._positions:
            reason = f"No open position for {asset} — ignoring SELL."
            return RiskCheck(True, reason, self._to_hold(decision, reason))

        if confidence < settings.min_confidence:
            reason = f"Confidence {confidence:.2f} below minimum {settings.min_confidence:.2f} — forcing HOLD."
            return RiskCheck(True, reason, self._to_hold(decision, reason))

        if size_pct > settings.max_position_pct:
            size_pct = settings.max_position_pct

        if action == "BUY":
            current_exposure = self.get_total_exposure_pct()
            headroom = settings.max_total_exposure_pct - current_exposure
            if headroom <= 0:
                reason = (
                    f"Exposure {current_exposure:.1f}% at cap {settings.max_total_exposure_pct:.1f}% "
                    f"— cannot open {asset}."
                )
                return RiskCheck(True, reason, self._to_hold(decision, reason))
            size_pct = min(size_pct, headroom)

        return RiskCheck(
            True,
            "Passed all risk checks.",
            {**decision, "asset": asset, "action": action, "size_pct": int(size_pct)},
        )

    # ── Real-time exit check (called on every WebSocket tick) ──────────────────

    def check_exit_conditions(self, symbol: str, price: float) -> dict[str, Any] | None:
        """Check a single symbol for stop-loss or take-profit breach.

        Removes the position immediately when triggered to prevent double-execution
        on subsequent ticks before the order is filled.
        Returns a SELL order dict if triggered, None otherwise.
        """
        pos = self._positions.get(symbol.upper())
        if pos is None or pos.entry_price <= 0:
            return None

        pos.current_price = price
        drawdown_pct = (pos.entry_price - price) / pos.entry_price * 100
        gain_pct = (price - pos.entry_price) / pos.entry_price * 100

        reason: str | None = None
        if drawdown_pct >= settings.stop_loss_pct:
            reason = (
                f"Stop-loss: price fell {drawdown_pct:.2f}% below entry "
                f"(threshold: {settings.stop_loss_pct}%)"
            )
        elif settings.take_profit_pct > 0 and gain_pct >= settings.take_profit_pct:
            reason = (
                f"Take-profit: price rose {gain_pct:.2f}% above entry "
                f"(threshold: {settings.take_profit_pct}%)"
            )

        if reason:
            size_pct = int(pos.size_pct)
            del self._positions[symbol.upper()]  # prevent re-trigger on next tick
            logger.warning("Exit triggered for %s: %s", symbol, reason)
            return {
                "asset": symbol.upper(),
                "action": "SELL",
                "confidence": 1.0,
                "size_pct": size_pct,
                "reasoning": reason,
                "trigger_price": price,
            }
        return None

    # ── Hourly cycle: batch exit check across all positions ────────────────────

    def check_stop_losses(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Batch exit check used by the hourly cycle. Delegates per-symbol to check_exit_conditions."""
        results: list[dict[str, Any]] = []
        for symbol, md in market_data.items():
            price = float(md.get("last_price", 0) or 0)
            if price > 0:
                order = self.check_exit_conditions(symbol, price)
                if order:
                    results.append(order)
        return results

    def filter_decisions(
        self,
        decisions: list[dict[str, Any]],
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        stop_orders = self.check_stop_losses(market_data)
        stop_assets = {o["asset"] for o in stop_orders}
        final: list[dict[str, Any]] = []

        for decision in decisions:
            asset = decision["asset"].upper()
            if asset in stop_assets:
                final.append(next(o for o in stop_orders if o["asset"] == asset))
                continue
            md = market_data.get(asset, {})
            current_price = float(md.get("last_price", 0) or 0)
            check = self.evaluate_decision({**decision, "asset": asset}, current_price)
            final.append(check.adjusted_decision)

        return final

    def _to_hold(self, decision: dict[str, Any], reason: str) -> dict[str, Any]:
        return {**decision, "action": "HOLD", "size_pct": 0, "reasoning": reason}
