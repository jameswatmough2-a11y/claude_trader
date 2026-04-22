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
    """Tracks open positions in memory and enforces risk rules on trading decisions."""

    def __init__(self) -> None:
        self._positions: dict[str, OpenPosition] = {}

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

    def update_current_prices(self, market_data: dict[str, Any]) -> None:
        for asset, pos in self._positions.items():
            md = market_data.get(asset, {})
            price = md.get("last_price")
            if price is not None:
                pos.current_price = float(price)

    def get_total_exposure_pct(self) -> float:
        return sum(p.size_pct for p in self._positions.values())

    def get_open_positions(self) -> dict[str, OpenPosition]:
        return dict(self._positions)

    def evaluate_decision(self, decision: dict[str, Any], current_price: float) -> RiskCheck:
        asset = decision["asset"].upper()
        action = decision["action"].upper()
        confidence = float(decision["confidence"])
        size_pct = float(decision["size_pct"])

        if _kill_switch_active():
            reason = "Kill switch active — all trading halted."
            return RiskCheck(False, reason, self._to_hold(decision, reason))

        if action == "HOLD":
            return RiskCheck(True, "HOLD — no action needed.", {**decision, "action": "HOLD", "size_pct": 0})

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

    def check_stop_losses(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        self.update_current_prices(market_data)
        stop_orders: list[dict[str, Any]] = []

        for asset, pos in list(self._positions.items()):
            if pos.entry_price <= 0 or pos.current_price <= 0:
                continue
            drawdown_pct = (pos.entry_price - pos.current_price) / pos.entry_price * 100
            if drawdown_pct >= settings.stop_loss_pct:
                logger.warning("Stop-loss triggered for %s: %.2f%% drawdown", asset, drawdown_pct)
                stop_orders.append({
                    "asset": asset,
                    "action": "SELL",
                    "confidence": 1.0,
                    "size_pct": int(pos.size_pct),
                    "reasoning": (
                        f"Stop-loss: price fell {drawdown_pct:.2f}% from entry "
                        f"(threshold: {settings.stop_loss_pct}%)."
                    ),
                })

        return stop_orders

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
