from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./crypto_bot.db"))
    paper_trading: bool = field(default_factory=lambda: os.getenv("PAPER_TRADING", "true").lower() == "true")
    paper_balance_usdt: float = field(default_factory=lambda: float(os.getenv("PAPER_BALANCE_USDT", "10000")))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model_name: str = field(default_factory=lambda: os.getenv("MODEL_NAME", "claude-sonnet-4-6"))
    tracked_symbols: list[str] = field(default_factory=list)

    min_confidence: float = field(default_factory=lambda: float(os.getenv("MIN_CONFIDENCE", "0.7")))
    max_position_pct: float = field(default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "20")))
    max_total_exposure_pct: float = field(default_factory=lambda: float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "60")))
    stop_loss_pct: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "5")))
    take_profit_pct: float = field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_PCT", "0")))

    def __post_init__(self) -> None:
        raw = os.getenv("TRACKED_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
        self.tracked_symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]


settings = Settings()
