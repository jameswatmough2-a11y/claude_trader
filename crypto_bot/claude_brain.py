"""
claude_brain.py — Build an hourly trading prompt from market + sentiment data,
call the Anthropic API (claude-sonnet-4-6), and return validated JSON decisions.

Decision schema per asset:
  {
    "asset":      "BTC/USDT",
    "action":     "BUY" | "SELL" | "HOLD",
    "confidence": 0.0–1.0,
    "size_pct":   0–20,          # % of portfolio to allocate; max 20
    "reasoning":  "..."
  }
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a disciplined crypto trading analyst. Your role is to evaluate
market data and aggregated sentiment signals, then produce a single JSON array of trading
decisions—one per asset—using the exact schema below. Never deviate from the schema.

Decision schema (one object per asset):
{
  "asset":      "<symbol>",           // e.g. "BTC/USDT"
  "action":     "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0–1.0>,      // your certainty in the decision
  "size_pct":   <int 0–20>,           // % of total portfolio; 0 when HOLD
  "reasoning":  "<concise rationale>"
}

Rules you must follow:
- Return ONLY a valid JSON array — no markdown, no prose outside the array.
- size_pct must be 0 when action is HOLD.
- size_pct maximum is 20 for any single asset.
- If confidence is below 0.7, set action to HOLD and size_pct to 0.
- Base decisions on the provided data only — do not speculate beyond it.
"""


def _build_prompt(market_data: dict[str, Any], sentiment_data: dict[str, Any]) -> str:
    """Construct the user-turn prompt from current market and sentiment snapshots."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"=== HOURLY TRADING ANALYSIS — {now} ===\n",
        "Analyse the following market and sentiment data for BTC/USDT, ETH/USDT, "
        "and SOL/USDT. Return a JSON array with one decision object per asset.\n",
    ]

    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        lines.append(f"--- {symbol} ---")

        # Market data section
        md = market_data.get(symbol, {})
        if "error" in md:
            lines.append(f"Market data unavailable: {md['error']}")
        else:
            ohlcv = md.get("ohlcv", {})
            lines.append(
                f"Price: ${md.get('last_price', 'N/A'):,.2f}  "
                f"Bid: ${md.get('bid', 'N/A'):,.2f}  "
                f"Ask: ${md.get('ask', 'N/A'):,.2f}"
            )
            lines.append(
                f"24h High: ${ohlcv.get('high_24h', 'N/A'):,.2f}  "
                f"24h Low: ${ohlcv.get('low_24h', 'N/A'):,.2f}  "
                f"Change: {ohlcv.get('price_change_pct_24h', 'N/A')}%"
            )
            lines.append(
                f"Avg 24h Volume: {ohlcv.get('avg_volume_24h', 'N/A'):,.2f}  "
                f"Quote Volume: ${md.get('quote_volume_24h', 'N/A'):,.2f}"
            )
            ob = md.get("orderbook", {})
            lines.append(
                f"Orderbook spread: {ob.get('spread', 'N/A')}"
            )

        # Sentiment section
        sent = sentiment_data.get(symbol)
        if sent:
            fg = sent.fear_greed_index
            fg_str = f"  Fear & Greed Index: {fg}/100" if fg is not None else ""
            lines.append(
                f"Sentiment score: {sent.score:.3f} (range −1.0 to +1.0){fg_str}  "
                f"Sources: {json.dumps({k: round(v, 3) for k, v in sent.source_scores.items()})}"
            )
            if sent.top_headlines:
                lines.append("Top headlines:")
                for i, h in enumerate(sent.top_headlines, 1):
                    lines.append(f"  {i}. {h[:200]}")
        else:
            lines.append("Sentiment data unavailable.")

        lines.append("")  # blank line between assets

    lines.append(
        "Based on the above, return ONLY a JSON array with three decision objects "
        "(one per asset). Remember: confidence < 0.7 → HOLD with size_pct 0."
    )
    return "\n".join(lines)


def _validate_decisions(raw: list[Any]) -> list[dict[str, Any]]:
    """Validate and sanitise the decisions returned by Claude."""
    required_keys = {"asset", "action", "confidence", "size_pct", "reasoning"}
    valid_actions = {"BUY", "SELL", "HOLD"}
    valid_assets = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    validated: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict):
            logger.warning("decision is not a dict: %s", item)
            continue
        if not required_keys.issubset(item.keys()):
            missing = required_keys - item.keys()
            logger.warning("decision missing keys %s: %s", missing, item)
            continue

        asset = item["asset"]
        action = str(item["action"]).upper()
        confidence = float(item["confidence"])
        size_pct = int(item["size_pct"])
        reasoning = str(item["reasoning"])

        if asset not in valid_assets:
            logger.warning("unknown asset '%s' — skipping", asset)
            continue
        if action not in valid_actions:
            logger.warning("unknown action '%s' for %s — defaulting to HOLD", action, asset)
            action = "HOLD"
        confidence = max(0.0, min(1.0, confidence))
        size_pct = max(0, min(20, size_pct))

        # Enforce confidence threshold
        if confidence < 0.7 and action != "HOLD":
            logger.info(
                "%s confidence %.2f < 0.7 — overriding %s to HOLD", asset, confidence, action
            )
            action = "HOLD"
            size_pct = 0

        if action == "HOLD":
            size_pct = 0

        validated.append(
            {
                "asset": asset,
                "action": action,
                "confidence": round(confidence, 4),
                "size_pct": size_pct,
                "reasoning": reasoning,
            }
        )

    logger.info("validated %d / %d decision(s)", len(validated), len(raw))
    return validated


def get_trading_decisions(
    market_data: dict[str, Any],
    sentiment_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build hourly prompt, call Claude, parse and validate the JSON response.

    Returns a list of validated decision dicts. Raises on unrecoverable API errors.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(market_data, sentiment_data)
    logger.info("claude_brain: sending prompt to %s (%d chars)", MODEL, len(prompt))

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        logger.error("claude_brain: invalid ANTHROPIC_API_KEY")
        raise
    except anthropic.RateLimitError as exc:
        logger.error("claude_brain: rate limited — %s", exc)
        raise
    except anthropic.APIStatusError as exc:
        logger.error("claude_brain: API error %s — %s", exc.status_code, exc.message)
        raise

    # Extract text from the first text block
    text_blocks = [b.text for b in response.content if b.type == "text"]
    if not text_blocks:
        raise ValueError("claude_brain: no text content in Claude response")
    raw_text = text_blocks[0].strip()

    logger.debug("claude_brain: raw response (%d chars): %s", len(raw_text), raw_text[:500])
    logger.info(
        "claude_brain: usage — input=%d output=%d",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    # Strip optional markdown fences
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("claude_brain: JSON parse error — %s\nRaw:\n%s", exc, raw_text[:1000])
        raise ValueError(f"claude_brain: Claude returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        logger.error("claude_brain: expected JSON array, got %s", type(parsed).__name__)
        raise ValueError("claude_brain: Claude response is not a JSON array")

    decisions = _validate_decisions(parsed)

    # Ensure we have all three assets (fill missing with HOLD)
    covered = {d["asset"] for d in decisions}
    for asset in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        if asset not in covered:
            logger.warning("claude_brain: no decision for %s — inserting HOLD", asset)
            decisions.append(
                {
                    "asset": asset,
                    "action": "HOLD",
                    "confidence": 0.0,
                    "size_pct": 0,
                    "reasoning": "Missing from Claude response — defaulted to HOLD.",
                }
            )

    for d in decisions:
        logger.info(
            "decision: %s  action=%s  confidence=%.2f  size_pct=%d",
            d["asset"],
            d["action"],
            d["confidence"],
            d["size_pct"],
        )

    return decisions
