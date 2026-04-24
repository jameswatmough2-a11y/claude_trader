"""Create all tables on startup. Replaces v1's _migrate_bot_config dance.

Once the schema stabilises, replace this with Alembic migrations under
`v2/db/migrations/`.
"""
from __future__ import annotations

import logging

from app.db.session import Base, engine

logger = logging.getLogger(__name__)


def init_db() -> None:
    # Importing the models module registers all ORM classes on `Base.metadata`.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised")
