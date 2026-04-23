from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Asset, HourlyMarketSnapshot, Position, AIDecision, Execution
from app.services.market_state import MarketStateStore
from app.services.risk_service import RiskService
from app.services.execution_service import ExecutionService
from app.services.ai_service import get_trading_decisions
from app.services.sentiment_service import get_all_sentiment
from app.services.data_feeds import fetch_balance, fetch_ohlcv

logger = logging.getLogger(__name__)


class TradingCycleService:
    """Orchestrates the trading loop: data → AI → risk → execution → persist."""

    def __init__(
        self,
        market_store: MarketStateStore,
        risk_service: RiskService,
        execution_service: ExecutionService,
    ) -> None:
        self.market_store = market_store
        self.risk_service = risk_service
        self.execution_service = execution_service

    def run(self, db: Session) -> None:
        logger.info("Trading cycle starting")
        now = datetime.now(timezone.utc)

        market_data = self._build_market_data()
        if not market_data:
            logger.warning("No live market data available — skipping cycle (WebSocket not ready?)")
            return

        symbols = list(market_data.keys())
        logger.info("Processing %d symbols: %s", len(symbols), symbols)

        self._enrich_with_ohlcv(market_data)
        sentiment_data = self._fetch_sentiment(symbols)
        open_positions = self.risk_service.get_open_positions()
        previous_decisions = self._fetch_previous_decisions(db, symbols)
        decisions = self._fetch_decisions(market_data, sentiment_data, open_positions, previous_decisions)
        filtered = self.risk_service.filter_decisions(decisions, market_data)

        for decision in filtered:
            symbol = decision["asset"]
            md = market_data.get(symbol, {})
            try:
                self._persist_cycle(db, symbol, md, decision, now)
            except IntegrityError:
                db.rollback()
                logger.warning("Duplicate snapshot for %s at %s — skipping", symbol, now.isoformat())
            except Exception:
                db.rollback()
                logger.exception("Failed to persist cycle for %s", symbol)

        logger.info("Trading cycle complete")

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_market_data(self) -> dict[str, Any]:
        tracked = {s.upper() for s in settings.tracked_symbols}
        result: dict[str, Any] = {}
        for symbol, state in self.market_store.all().items():
            if symbol.upper() not in tracked:
                continue
            if state.last_price is None:
                continue
            price = float(state.last_price)
            result[symbol] = {
                "symbol": symbol,
                "last_price": price,
                "bid": float(state.bid) if state.bid else None,
                "ask": float(state.ask) if state.ask else None,
                "volume_24h": float(state.volume_24h) if state.volume_24h else None,
                "price_change_24h_pct": float(state.price_change_24h_pct) if state.price_change_24h_pct else None,
                "high_24h": float(state.high_24h) if state.high_24h else None,
                "low_24h": float(state.low_24h) if state.low_24h else None,
                "quote_volume_24h": float(state.volume_24h or 0),
                "ohlcv": {
                    "last_close": price,
                    "high_24h": float(state.high_24h or state.last_price),
                    "low_24h": float(state.low_24h or state.last_price),
                    "avg_volume_24h": float(state.volume_24h or 0),
                    "price_change_pct_24h": float(state.price_change_24h_pct or 0),
                    "candles": [],
                },
                "orderbook": {},
            }
        return result

    def _enrich_with_ohlcv(self, market_data: dict[str, Any]) -> None:
        for symbol in list(market_data.keys()):
            try:
                df = fetch_ohlcv(symbol, timeframe="1h", limit=24)
                if df.empty:
                    continue
                candles = [
                    {
                        "time": str(ts),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                    for ts, row in df.iterrows()
                ]
                market_data[symbol]["ohlcv"] = {
                    "last_close": float(df["close"].iloc[-1]),
                    "high_24h": float(df["high"].max()),
                    "low_24h": float(df["low"].min()),
                    "avg_volume_24h": float(df["volume"].mean()),
                    "price_change_pct_24h": round(
                        (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100,
                        2,
                    ),
                    "candles": candles,
                }
                logger.info("OHLCV enriched for %s (%d candles)", symbol, len(candles))
            except Exception:
                logger.warning("OHLCV fetch failed for %s — using WebSocket ticker data", symbol)

    def _fetch_sentiment(self, symbols: list[str]) -> dict[str, Any]:
        try:
            return get_all_sentiment(symbols)
        except Exception:
            logger.exception("Sentiment fetch failed — proceeding without sentiment data")
            return {}

    def _fetch_previous_decisions(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        from sqlalchemy import desc
        result: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            asset = db.query(Asset).filter(Asset.symbol == symbol.upper()).first()
            if asset is None:
                continue
            last = (
                db.query(AIDecision)
                .join(HourlyMarketSnapshot, AIDecision.snapshot_id == HourlyMarketSnapshot.id)
                .filter(HourlyMarketSnapshot.asset_id == asset.id)
                .order_by(desc(AIDecision.created_at))
                .first()
            )
            if last:
                result[symbol.upper()] = {
                    "action": last.action,
                    "confidence": float(last.confidence_score or 0),
                    "reasoning": last.reasoning_summary or "",
                    "time": last.created_at.isoformat() if last.created_at else None,
                }
        return result

    def _fetch_decisions(
        self,
        market_data: dict[str, Any],
        sentiment_data: dict[str, Any],
        open_positions: dict[str, Any],
        previous_decisions: dict[str, Any],
    ) -> list[dict[str, Any]]:
        symbols = list(market_data.keys())
        try:
            return get_trading_decisions(market_data, sentiment_data, open_positions, previous_decisions)
        except Exception:
            logger.exception("AI decision call failed — defaulting all symbols to HOLD")
            return [
                {
                    "asset": s,
                    "action": "HOLD",
                    "confidence": 0.0,
                    "size_pct": 0,
                    "reasoning": "AI service unavailable — defaulted to HOLD.",
                }
                for s in symbols
            ]

    def _fetch_balance(self) -> dict[str, Any]:
        if settings.paper_trading:
            bal = self.execution_service.paper_usdt
            return {"USDT": {"free": bal, "used": 0.0, "total": bal}}
        try:
            return fetch_balance()
        except Exception:
            logger.exception("Balance fetch failed — using configured paper balance")
            return {
                "USDT": {
                    "free": settings.paper_balance_usdt,
                    "used": 0.0,
                    "total": settings.paper_balance_usdt,
                }
            }

    def _persist_cycle(
        self,
        db: Session,
        symbol: str,
        md: dict[str, Any],
        decision: dict[str, Any],
        now: datetime,
    ) -> None:
        balance = self._fetch_balance()
        asset = self._get_or_create_asset(db, symbol)

        price = Decimal(str(md.get("last_price", 0) or 0))
        snapshot = HourlyMarketSnapshot(
            asset_id=asset.id,
            snapshot_time=now,
            open_price=price,
            high_price=Decimal(str(md.get("high_24h") or price)),
            low_price=Decimal(str(md.get("low_24h") or price)),
            close_price=price,
            volume=Decimal(str(md.get("volume_24h", 0) or 0)),
            price_change_1h_pct=None,
            price_change_since_entry_pct=None,
        )
        db.add(snapshot)
        db.flush()

        wallet_usdt = Decimal(str(balance.get("USDT", {}).get("total", 0)))
        position = Position(
            snapshot_id=snapshot.id,
            asset_id=asset.id,
            side="flat",
            size=Decimal("0"),
            entry_price=None,
            unrealized_pnl=None,
            wallet_balance=wallet_usdt,
        )
        db.add(position)
        db.flush()

        exec_result = self.execution_service.execute_decision(decision, {symbol: md}, balance)

        pos = self.risk_service.get_open_positions().get(symbol)
        if pos is not None:
            position.side = "long"
            position.size = Decimal(str(round(pos.size_pct, 4)))
            position.entry_price = Decimal(str(pos.entry_price))
            if pos.current_price > 0 and pos.entry_price > 0:
                unrealized_pct = (pos.current_price - pos.entry_price) / pos.entry_price * 100
                position.unrealized_pnl = Decimal(str(round(unrealized_pct, 4)))

        post_balance = self._fetch_balance()
        position.wallet_balance = Decimal(str(post_balance.get("USDT", {}).get("total", 0)))

        ai_rec = AIDecision(
            snapshot_id=snapshot.id,
            prompt_version="v1",
            model_name=settings.model_name,
            action=decision["action"],
            confidence_score=Decimal(str(decision["confidence"])),
            reasoning_summary=decision["reasoning"],
            recommended_size=Decimal(str(decision["size_pct"])),
            recommended_stop_loss=None,
            recommended_take_profit=None,
            created_at=now,
        )
        db.add(ai_rec)
        db.flush()

        order = exec_result.get("order") or {}
        if exec_result.get("error"):
            exec_status = "rejected"
        elif decision["action"] == "HOLD":
            exec_status = "none"
        elif settings.paper_trading:
            exec_status = "paper_filled"
        else:
            exec_status = "filled"

        execution = Execution(
            ai_decision_id=ai_rec.id,
            executed_action=decision["action"],
            executed_size=Decimal(str(order.get("qty", 0) or 0)),
            execution_price=Decimal(str(order.get("price", 0) or 0)) if order.get("price") else None,
            fees_paid=Decimal("0"),
            slippage=Decimal("0"),
            execution_time=now if decision["action"] != "HOLD" else None,
            status=exec_status,
        )
        db.add(execution)
        db.commit()

    def _get_or_create_asset(self, db: Session, symbol: str) -> Asset:
        asset = db.query(Asset).filter(Asset.symbol == symbol).first()
        if asset is None:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            asset = Asset(symbol=symbol, base_currency=base, quote_currency="USDT")
            db.add(asset)
            db.flush()
        return asset
