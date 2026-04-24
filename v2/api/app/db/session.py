"""SQLAlchemy engine + SessionLocal factory.

Used by:
  - FastAPI request handlers via the `get_db` dependency (see auth/dependencies.py)
  - Background services that need their own short-lived sessions
    (trigger_executor, tick_scheduler) — they call `SessionLocal()` directly.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


# SQLite needs `check_same_thread=False` because background coroutines
# on the default thread pool open their own sessions.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass
