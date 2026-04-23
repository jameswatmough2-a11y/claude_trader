from sqlalchemy import inspect, text

from app.db.session import engine
from app.models import Base


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_bot_config()
    _migrate_ai_decision()
    _migrate_execution()


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
        ("ohlcv_interval", "VARCHAR NOT NULL DEFAULT '1h'"),
        ("taker_fee_rate", "FLOAT NOT NULL DEFAULT 0.001"),
    ]

    with engine.connect() as conn:
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE bot_config ADD COLUMN {col_name} {col_def}"))
        conn.commit()


def _migrate_ai_decision() -> None:
    """Add decision_source column to ai_decisions if missing."""
    inspector = inspect(engine)
    if "ai_decisions" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("ai_decisions")}
    if "decision_source" not in existing:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE ai_decisions ADD COLUMN decision_source VARCHAR(20) NOT NULL DEFAULT 'ai'"))
            conn.commit()


def _migrate_execution() -> None:
    """Add fee/fill tracking columns to executions if missing."""
    inspector = inspect(engine)
    if "executions" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("executions")}
    new_cols = [
        ("fee_rate", "NUMERIC(10, 6)"),
        ("fill_source", "VARCHAR(20)"),
        ("verification_status", "VARCHAR(20)"),
    ]

    with engine.connect() as conn:
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE executions ADD COLUMN {col_name} {col_def}"))
        conn.commit()
