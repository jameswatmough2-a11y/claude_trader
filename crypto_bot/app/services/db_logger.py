"""Structured DB logging helper.

All key events are written to the system_logs table for API/frontend visibility.
Each call opens its own short-lived session so logs persist even when the
caller's main transaction rolls back.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def log_event(
    level: str,
    component: str,
    event_type: str,
    message: str,
    symbol: Optional[str] = None,
    cycle_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    from app.db.session import SessionLocal
    from app.models.system_log import SystemLog

    db = SessionLocal()
    try:
        entry = SystemLog(
            created_at=datetime.now(timezone.utc),
            level=level.upper(),
            component=component,
            event_type=event_type,
            symbol=symbol.upper() if symbol else None,
            cycle_id=cycle_id,
            message=message,
            details_json=json.dumps(details, default=str) if details else None,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        logger.warning("DB log write failed (%s): %s", event_type, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def log_info(component: str, event_type: str, message: str, **kwargs: Any) -> None:
    log_event("INFO", component, event_type, message, **kwargs)


def log_warning(component: str, event_type: str, message: str, **kwargs: Any) -> None:
    log_event("WARNING", component, event_type, message, **kwargs)


def log_error(component: str, event_type: str, message: str, **kwargs: Any) -> None:
    log_event("ERROR", component, event_type, message, **kwargs)
