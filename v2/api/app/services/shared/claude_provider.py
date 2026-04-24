"""Claude implementation of DecisionProvider. Port of v1 ai_service.py.

Responsibilities:
  - Build a system prompt from context.min_confidence / context.max_position_pct
  - Build a user prompt with markets, positions, sentiment, previous decisions
  - Call the Anthropic SDK
  - Parse + validate the JSON response

TODO: port system-prompt builder and parser from v1 ai_service.py, but with
fields coming from DecisionContext (not globals / settings).
"""
from __future__ import annotations

import logging

from app.config import settings
from app.services.shared.decision_provider import (
    Decision,
    DecisionContext,
    DecisionProvider,
)

logger = logging.getLogger(__name__)


class ClaudeDecisionProvider(DecisionProvider):
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model
        self._client = None  # lazy-init anthropic.Anthropic

    def name(self) -> str:
        return f"claude:{self.model}"

    async def decide(self, context: DecisionContext) -> list[Decision]:
        # TODO: build system prompt (include context.min_confidence etc.)
        # TODO: build user prompt (markets, positions, sentiment, prev decisions)
        # TODO: call client.messages.create(...)
        # TODO: parse JSON array -> list[Decision]
        # TODO: enforce: open position -> SELL|HOLD; flat -> BUY|HOLD
        return []
