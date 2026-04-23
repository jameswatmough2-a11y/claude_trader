from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    # timeout: wait up to 30 s for a write lock instead of failing immediately
    connect_args = {"check_same_thread": False, "timeout": 60}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# WAL mode: allows concurrent reads while a write is in progress, reducing
# "database is locked" errors when db_logger sessions write alongside the
# trading cycle's main session.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_wal_mode(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

