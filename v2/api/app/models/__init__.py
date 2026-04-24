"""Import every model here so init_db.create_all sees them all."""
from app.models.user import User
from app.models.tenant import Tenant
from app.models.bot_config import BotConfig
from app.models.session import TradingSession
from app.models.ai_decision import AIDecision
from app.models.execution import Execution
from app.models.trade import Trade
from app.models.hourly_snapshot import HourlyMarketSnapshot
from app.models.sentiment_cache import SentimentCacheEntry

__all__ = [
    "User",
    "Tenant",
    "BotConfig",
    "TradingSession",
    "AIDecision",
    "Execution",
    "Trade",
    "HourlyMarketSnapshot",
    "SentimentCacheEntry",
]
