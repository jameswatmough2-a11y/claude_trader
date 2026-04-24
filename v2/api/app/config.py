"""Settings loaded from .env at startup.

Unlike v1, this is loaded once and treated as immutable. Per-tenant runtime
config lives in the `BotConfig` rows keyed by `tenant_id` — mutated via the
bot_control routes, read by `trading_cycle` per cycle.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI
    anthropic_api_key: str = ""

    # Clerk
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""
    clerk_webhook_secret: str = ""

    # Database
    database_url: str = "sqlite:///./crypto_bot_v2.db"

    # Trading defaults (BotConfig rows override at runtime)
    paper_trading: bool = True
    default_paper_balance: float = 10_000.0
    taker_fee_rate: float = 0.001

    # Reddit (shared sentiment)
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "claude-trader-v2"

    # Live trading (placeholder — v2.x per-tenant keys)
    binance_api_key: str = ""
    binance_api_secret: str = ""


settings = Settings()
