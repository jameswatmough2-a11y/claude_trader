from sqlalchemy import inspect, text

from app.db.session import engine
from app.models import Base


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_bot_config()


def _migrate_bot_config() -> None:
    """Add new columns to bot_config for databases created before they existed."""
    inspector = inspect(engine)
    if "bot_config" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("bot_config")}
    new_cols = [
        ("tracked_symbols", "VARCHAR NOT NULL DEFAULT 'BTCUSDT,ETHUSDT,SOLUSDT'"),
        ("paper_balance_usdt", "FLOAT NOT NULL DEFAULT 10000.0"),
        ("chart_interval", "VARCHAR NOT NULL DEFAULT '1m'"),
        ("model_name", "VARCHAR NOT NULL DEFAULT 'claude-sonnet-4-6'"),
        ("timezone", "VARCHAR NOT NULL DEFAULT 'UTC'"),
        ("display_currency", "VARCHAR NOT NULL DEFAULT 'USD'"),
    ]

    with engine.connect() as conn:
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE bot_config ADD COLUMN {col_name} {col_def}"))
        conn.commit()
