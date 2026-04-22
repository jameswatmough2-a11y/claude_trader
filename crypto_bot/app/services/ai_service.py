from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a disciplined crypto trading analyst. Evaluate the market and
sentiment data provided, then return a single JSON array of trading decisions — one object
per asset. Never deviate from the schema below.

Decision schema:
{
  "asset":      "<SYMBOL>",
  "action":     "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0–1.0>,
  "size_pct":   <int 0–20>,
  "reasoning":  "<one-sentence rationale>"
}

Rules:
- Return ONLY a valid JSON array. No markdown, no extra text.
- size_pct must be 0 when action is HOLD.
- size_pct maximum is 20 for any single asset.
- If confidence < 0.7, set action to HOLD and size_pct to 0.
- Base decisions solely on the data provided.
"""

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_prompt(market_data: dict[str, Any], sentiment_data: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"=== HOURLY TRADING ANALYSIS — {now} ===",
        "",
        "Analyse the following data and return one JSON decision per asset.",
        "",
    ]

    symbols = list(market_data.keys()) or settings.tracked_symbols
    for symbol in symbols:
        lines.append(f"--- {symbol} ---")
        md = market_data.get(symbol, {})

        if "error" in md:
            lines.append(f"Market data unavailable: {md['error']}")
        else:
            ohlcv = md.get("ohlcv", {})
            lines.append(
                f"Price: ${float(md.get('last_price', 0) or 0):,.4f}  "
                f"Bid: ${float(md.get('bid', 0) or 0):,.4f}  "
                f"Ask: ${float(md.get('ask', 0) or 0):,.4f}"
            )
            lines.append(
                f"24h High: ${float(ohlcv.get('high_24h', 0) or 0):,.4f}  "
                f"24h Low: ${float(ohlcv.get('low_24h', 0) or 0):,.4f}  "
                f"Change: {ohlcv.get('price_change_pct_24h', 'N/A')}%"
            )
            lines.append(
                f"Avg 24h Volume: {float(ohlcv.get('avg_volume_24h', 0) or 0):,.0f}  "
                f"Quote Volume: ${float(md.get('quote_volume_24h', 0) or 0):,.0f}"
            )

        candles = ohlcv.get("candles", [])
        if candles:
            recent_closes = " → ".join(f"${c['close']:,.2f}" for c in candles[-6:])
            lines.append(f"Last 6h closes: {recent_closes}")

        sent = sentiment_data.get(symbol)
        if sent:
            fg = f" | Fear & Greed: {sent.fear_greed_index}/100" if sent.fear_greed_index is not None else ""
            lines.append(f"Sentiment: {sent.score:.3f}{fg}")
            if sent.top_headlines:
                for i, h in enumerate(sent.top_headlines, 1):
                    lines.append(f"  {i}. {h[:180]}")
        else:
            lines.append("Sentiment: unavailable")

        lines.append("")

    lines.append("Return ONLY a JSON array.")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Strip markdown fences and extract the JSON array from a response."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith("```")
        ).strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text


def _validate_decisions(raw: list[Any], symbols: list[str]) -> list[dict[str, Any]]:
    required = {"asset", "action", "confidence", "size_pct", "reasoning"}
    valid_actions = {"BUY", "SELL", "HOLD"}
    valid_assets = {s.upper() for s in symbols}
    validated: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict) or not required.issubset(item.keys()):
            continue

        asset = str(item["asset"]).upper()
        action = str(item["action"]).upper()
        confidence = max(0.0, min(1.0, float(item["confidence"])))
        size_pct = max(0, min(20, int(item["size_pct"])))
        reasoning = str(item["reasoning"])

        if asset not in valid_assets:
            continue
        if action not in valid_actions:
            action = "HOLD"
        if confidence < settings.min_confidence:
            action, size_pct = "HOLD", 0
        if action == "HOLD":
            size_pct = 0

        validated.append({
            "asset": asset,
            "action": action,
            "confidence": round(confidence, 4),
            "size_pct": size_pct,
            "reasoning": reasoning,
        })

    covered = {d["asset"] for d in validated}
    for s in symbols:
        if s.upper() not in covered:
            validated.append({
                "asset": s.upper(),
                "action": "HOLD",
                "confidence": 0.0,
                "size_pct": 0,
                "reasoning": "Missing from model response — defaulted to HOLD.",
            })

    return validated


def get_trading_decisions(
    market_data: dict[str, Any],
    sentiment_data: dict[str, Any],
) -> list[dict[str, Any]]:
    symbols = list(market_data.keys()) or settings.tracked_symbols
    client = _get_client()
    prompt = _build_prompt(market_data, sentiment_data)

    response = client.messages.create(
        model=settings.model_name,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text_blocks = [b.text for b in response.content if b.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    raw_text = _extract_json(text_blocks[0])
    parsed = json.loads(raw_text)
    if not isinstance(parsed, list):
        raise ValueError("Claude response is not a JSON array")

    return _validate_decisions(parsed, symbols)
