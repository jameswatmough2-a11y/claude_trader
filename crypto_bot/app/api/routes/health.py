from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    from app.state import ws_service, bot_state, market_store
    from app.models.system_log import SystemLog
    from app.models.ai_decision import AIDecision
    from sqlalchemy import desc

    # WebSocket status: check if we have any live market data
    ws_symbols = list(market_store.all().keys())
    ws_connected = len(ws_symbols) > 0

    # Last AI decision time
    last_ai = db.query(AIDecision).order_by(desc(AIDecision.created_at)).first()
    last_ai_time = last_ai.created_at.isoformat() if last_ai else None

    # Last cycle completion from system_logs
    last_cycle_log = (
        db.query(SystemLog)
        .filter(SystemLog.event_type == "cycle_end")
        .order_by(desc(SystemLog.created_at))
        .first()
    )
    last_cycle_time = last_cycle_log.created_at.isoformat() if last_cycle_log else None

    # Last AI failure from system_logs
    last_ai_fail = (
        db.query(SystemLog)
        .filter(SystemLog.event_type == "ai_request_failed")
        .order_by(desc(SystemLog.created_at))
        .first()
    )
    last_ai_failure = last_ai_fail.created_at.isoformat() if last_ai_fail else None

    # Sentiment cache status
    from app.services.sentiment_service import get_sentiment_cache_status
    sentiment_status = get_sentiment_cache_status()

    return {
        "status": "ok",
        "paper_trading": settings.paper_trading,
        "tracked_symbols": settings.tracked_symbols,
        "model": settings.model_name,
        "ohlcv_interval": settings.ohlcv_interval,
        "taker_fee_rate": settings.taker_fee_rate,
        "bot_running": bot_state.running,
        "ws_connected": ws_connected,
        "ws_symbols": ws_symbols,
        "last_cycle_time": last_cycle_time,
        "last_ai_time": last_ai_time,
        "last_ai_failure": last_ai_failure,
        "sentiment_cache": sentiment_status,
    }
