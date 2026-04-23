from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Asset, HourlyMarketSnapshot, Position, AIDecision, Execution
from app.models.ohlcv_candle import OhlcvCandle
from app.services.market_state import MarketStateStore
from app.services.risk_service import RiskService
from app.services.execution_service import ExecutionService
from app.services.ai_service import get_trading_decisions
from app.services.fallback_strategy import get_fallback_decisions
from app.services.sentiment_service import get_all_sentiment
from app.services.data_feeds import fetch_balance, fetch_ohlcv
from app.services import db_logger

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
        from app.state import bot_state
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        now = datetime.now(timezone.utc)
        session_id = bot_state.current_session_id

        logger.info("Trading cycle starting (id=%s session=%s)", cycle_id, session_id)
        db_logger.log_info("cycle", "cycle_start", f"Trading cycle starting (id={cycle_id})", cycle_id=cycle_id)

        market_data = self._build_market_data()
        if not market_data:
            msg = "No live market data — skipping cycle (WebSocket not ready?)"
            logger.warning(msg)
            db_logger.log_warning("cycle", "cycle_skip", msg, cycle_id=cycle_id)
            return

        symbols = list(market_data.keys())
        logger.info("Processing %d symbols: %s", len(symbols), symbols)

        self._enrich_and_store_ohlcv(market_data, db, cycle_id)
        db.commit()  # release SQLite write lock before slow sentiment + AI phases
        sentiment_data = self._fetch_sentiment(symbols, cycle_id)
        open_positions = self.risk_service.get_open_positions()
        previous_decisions = self._fetch_previous_decisions(db, symbols)
        decisions = self._fetch_decisions(market_data, sentiment_data, open_positions, previous_decisions, cycle_id)
        filtered = self.risk_service.filter_decisions(decisions, market_data)

        for decision in filtered:
            symbol = decision["asset"]
            md = market_data.get(symbol, {})
            try:
                self._persist_cycle(db, symbol, md, decision, now, cycle_id, session_id)
            except IntegrityError:
                db.rollback()
                logger.warning("Duplicate snapshot for %s at %s — skipping", symbol, now.isoformat())
            except Exception:
                db.rollback()
                logger.exception("Failed to persist cycle for %s", symbol)
                db_logger.log_error(
                    "cycle", "persist_error",
                    f"Failed to persist cycle for {symbol}",
                    symbol=symbol, cycle_id=cycle_id,
                )

        logger.info("Trading cycle complete (id=%s)", cycle_id)
        db_logger.log_info("cycle", "cycle_end", f"Trading cycle complete (id={cycle_id})", cycle_id=cycle_id)

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
                    "high_period": float(state.high_24h or state.last_price),
                    "low_period": float(state.low_24h or state.last_price),
                    "avg_volume_period": float(state.volume_24h or 0),
                    "price_change_pct_period": float(state.price_change_24h_pct or 0),
                    "candles": [],
                },
                "orderbook": {},
            }
        return result

    def _enrich_and_store_ohlcv(
        self,
        market_data: dict[str, Any],
        db: Session,
        cycle_id: str,
    ) -> None:
        interval = settings.ohlcv_interval
        limit = settings.ohlcv_limit

        for symbol in list(market_data.keys()):
            try:
                df = fetch_ohlcv(symbol, timeframe=interval, limit=limit)
                if df.empty:
                    logger.warning("Empty OHLCV response for %s", symbol)
                    db_logger.log_warning(
                        "ohlcv", "ohlcv_empty",
                        f"Empty OHLCV response for {symbol} ({interval})",
                        symbol=symbol, cycle_id=cycle_id,
                    )
                    continue

                # Upsert candles into DB
                inserted = 0
                for ts, row in df.iterrows():
                    existing = (
                        db.query(OhlcvCandle)
                        .filter_by(symbol=symbol, timeframe=interval, open_time=ts.to_pydatetime())
                        .first()
                    )
                    if existing is None:
                        db.add(OhlcvCandle(
                            symbol=symbol,
                            timeframe=interval,
                            open_time=ts.to_pydatetime(),
                            open=Decimal(str(row["open"])),
                            high=Decimal(str(row["high"])),
                            low=Decimal(str(row["low"])),
                            close=Decimal(str(row["close"])),
                            volume=Decimal(str(row["volume"])),
                        ))
                        inserted += 1

                if inserted:
                    db.commit()  # release write lock before db_logger writes
                    logger.info("OHLCV: upserted %d new %s candles for %s", inserted, interval, symbol)

                db_logger.log_info(
                    "ohlcv", "ohlcv_fetch",
                    f"OHLCV fetched for {symbol}: {len(df)} {interval} candles ({inserted} new)",
                    symbol=symbol, cycle_id=cycle_id,
                    details={"candle_count": len(df), "new": inserted, "interval": interval},
                )

                # Build candle list for AI prompt
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
                    "high_period": float(df["high"].max()),
                    "low_period": float(df["low"].min()),
                    "avg_volume_period": float(df["volume"].mean()),
                    "price_change_pct_period": round(
                        (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100,
                        2,
                    ) if float(df["close"].iloc[0]) != 0 else 0.0,
                    "candles": candles,
                }

            except Exception:
                logger.warning("OHLCV fetch failed for %s (%s) — using WebSocket ticker data", symbol, interval)
                db_logger.log_warning(
                    "ohlcv", "ohlcv_fetch_error",
                    f"OHLCV fetch failed for {symbol} ({interval}) — using fallback ticker data",
                    symbol=symbol, cycle_id=cycle_id,
                )

    def _fetch_sentiment(self, symbols: list[str], cycle_id: str) -> dict[str, Any]:
        try:
            db_logger.log_info("sentiment", "sentiment_fetch_start", "Fetching sentiment data", cycle_id=cycle_id)
            result = get_all_sentiment(symbols)
            db_logger.log_info(
                "sentiment", "sentiment_fetch_done",
                f"Sentiment fetched for {list(result.keys())}",
                cycle_id=cycle_id,
                details={s: {"score": v.score, "sources": v.source_scores} for s, v in result.items()},
            )
            return result
        except Exception:
            logger.exception("Sentiment fetch failed — proceeding without sentiment data")
            db_logger.log_error("sentiment", "sentiment_fetch_error", "Sentiment fetch failed", cycle_id=cycle_id)
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
        cycle_id: str,
    ) -> list[dict[str, Any]]:
        symbols = list(market_data.keys())
        db_logger.log_info("ai", "ai_request_start", f"Requesting AI decisions for {symbols}", cycle_id=cycle_id)
        try:
            decisions = get_trading_decisions(market_data, sentiment_data, open_positions, previous_decisions)
            db_logger.log_info(
                "ai", "ai_request_done",
                f"AI decisions received for {[d['asset'] for d in decisions]}",
                cycle_id=cycle_id,
                details={d["asset"]: {"action": d["action"], "confidence": d["confidence"]} for d in decisions},
            )
            return decisions
        except Exception as exc:
            logger.exception("AI decision call failed — using rule-based fallback")
            db_logger.log_warning(
                "ai", "ai_request_failed",
                f"AI unavailable ({exc!s:.100}) — switching to rule-based fallback",
                cycle_id=cycle_id,
                details={"error": str(exc)},
            )
            fallback = get_fallback_decisions(market_data, open_positions)
            db_logger.log_info(
                "ai", "fallback_used",
                f"Fallback decisions produced for {[d['asset'] for d in fallback]}",
                cycle_id=cycle_id,
                details={d["asset"]: {"action": d["action"]} for d in fallback},
            )
            return fallback

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
        cycle_id: str,
        session_id: int | None = None,
    ) -> None:
        balance = self._fetch_balance()
        asset = self._get_or_create_asset(db, symbol)

        price = Decimal(str(md.get("last_price", 0) or 0))
        ohlcv = md.get("ohlcv", {})
        snapshot = HourlyMarketSnapshot(
            asset_id=asset.id,
            snapshot_time=now,
            open_price=price,
            high_price=Decimal(str(ohlcv.get("high_period") or md.get("high_24h") or price)),
            low_price=Decimal(str(ohlcv.get("low_period") or md.get("low_24h") or price)),
            close_price=price,
            volume=Decimal(str(md.get("volume_24h", 0) or 0)),
            price_change_1h_pct=None,
            price_change_since_entry_pct=None,
            session_id=session_id,
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

        decision_source = decision.get("decision_source", "ai")
        ai_rec = AIDecision(
            snapshot_id=snapshot.id,
            prompt_version="v2",
            model_name=settings.model_name,
            action=decision["action"],
            confidence_score=Decimal(str(decision["confidence"])),
            reasoning_summary=decision["reasoning"],
            recommended_size=Decimal(str(decision["size_pct"])),
            recommended_stop_loss=None,
            recommended_take_profit=None,
            created_at=now,
            decision_source=decision_source,
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

        fee_amount = Decimal(str(order.get("fee_amount", 0) or 0))
        fee_rate = Decimal(str(order.get("fee_rate", settings.taker_fee_rate) or settings.taker_fee_rate))
        fill_source = order.get("fill_source")
        verification_status = order.get("verification_status")

        execution = Execution(
            ai_decision_id=ai_rec.id,
            executed_action=decision["action"],
            executed_size=Decimal(str(order.get("qty", 0) or 0)),
            execution_price=Decimal(str(order.get("price", 0) or 0)) if order.get("price") else None,
            fees_paid=fee_amount,
            fee_rate=fee_rate,
            slippage=Decimal("0"),
            fill_source=fill_source,
            verification_status=verification_status,
            execution_time=now if decision["action"] != "HOLD" else None,
            status=exec_status,
            session_id=session_id,
        )
        db.add(execution)
        db.flush()

        # ── Trade lifecycle ────────────────────────────────────────────────────
        if session_id and exec_status in ("filled", "paper_filled"):
            from app.state import trade_service
            exec_price = float(execution.execution_price or 0)
            exec_qty = float(execution.executed_size or 0)
            exec_fee = float(execution.fees_paid or 0)
            action = decision["action"]

            if action == "BUY" and exec_price > 0:
                from app.config import settings as _settings
                sl = exec_price * (1 - _settings.stop_loss_pct / 100) if _settings.stop_loss_pct > 0 else None
                tp = exec_price * (1 + _settings.take_profit_pct / 100) if _settings.take_profit_pct > 0 else None
                trade = trade_service.open_trade(
                    db, session_id, symbol,
                    entry_execution_id=execution.id,
                    entry_price=exec_price,
                    entry_qty=exec_qty,
                    entry_fee_usdt=exec_fee,
                    size_pct=float(decision["size_pct"]),
                    opened_at=now,
                    stop_loss_price=sl,
                    take_profit_price=tp,
                )
                execution.trade_id = trade.id
                self.risk_service.update_position_levels(symbol, sl, tp)

            elif action == "SELL":
                open_trade = trade_service.get_open_trade_for_symbol(db, session_id, symbol)
                if open_trade:
                    exit_reason = decision.get("exit_reason", "ai_sell")
                    if "stop" in decision.get("reasoning", "").lower():
                        exit_reason = "stop_loss"
                    elif "take" in decision.get("reasoning", "").lower() or "profit" in decision.get("reasoning", "").lower():
                        exit_reason = "take_profit"
                    trade_service.close_trade(
                        db, open_trade.id,
                        exit_execution_id=execution.id,
                        exit_price=exec_price if exec_price > 0 else float(price),
                        exit_fee_usdt=exec_fee,
                        exit_reason=exit_reason,
                        closed_at=now,
                    )
                    execution.trade_id = open_trade.id

        db.commit()

        if decision["action"] != "HOLD":
            db_logger.log_info(
                "cycle", "decision_executed",
                f"{symbol}: {decision['action']} (source={decision_source}, "
                f"confidence={decision['confidence']:.2f}, status={exec_status})",
                symbol=symbol, cycle_id=cycle_id,
                details={
                    "action": decision["action"],
                    "source": decision_source,
                    "confidence": decision["confidence"],
                    "status": exec_status,
                    "fee_amount": float(fee_amount),
                },
            )

    def _get_or_create_asset(self, db: Session, symbol: str) -> Asset:
        asset = db.query(Asset).filter(Asset.symbol == symbol).first()
        if asset is None:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            asset = Asset(symbol=symbol, base_currency=base, quote_currency="USDT")
            db.add(asset)
            db.flush()
        return asset
