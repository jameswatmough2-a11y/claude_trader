from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class SessionService:
    """Manages TradingSession lifecycle."""

    def create_session(self, db: "Session", starting_balance_usdt: float) -> "object":
        from app.models.trading_session import TradingSession
        from app.config import settings
        from app.models.bot_config import BotConfig

        config_row = db.get(BotConfig, 1)
        config_snapshot = json.dumps(config_row.to_dict() if config_row else {})

        session = TradingSession(
            user_id=1,
            started_at=datetime.now(timezone.utc),
            status="active",
            starting_balance_usdt=Decimal(str(round(starting_balance_usdt, 8))),
            notes=config_snapshot,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        logger.info("Session %d started — balance=%.2f USDT", session.id, starting_balance_usdt)
        return session

    def close_session(
        self,
        db: "Session",
        session_id: int,
        ending_balance_usdt: float,
        status: str = "stopped",
    ) -> Optional["object"]:
        from app.models.trading_session import TradingSession

        sess = db.get(TradingSession, session_id)
        if sess is None:
            return None
        sess.ended_at = datetime.now(timezone.utc)
        sess.status = status
        sess.ending_balance_usdt = Decimal(str(round(ending_balance_usdt, 8)))
        db.commit()
        db.refresh(sess)
        logger.info(
            "Session %d closed — balance=%.2f USDT status=%s",
            session_id, ending_balance_usdt, status,
        )
        return sess

    def get_active_session(self, db: "Session") -> Optional["object"]:
        from app.models.trading_session import TradingSession

        return (
            db.query(TradingSession)
            .filter(TradingSession.status == "active")
            .order_by(TradingSession.started_at.desc())
            .first()
        )
