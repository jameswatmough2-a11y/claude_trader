from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_system_prompt() -> str:
    min_conf = settings.min_confidence
    max_pos = int(settings.max_position_pct)
    return f"""You are a disciplined crypto trading analyst. Evaluate the provided market and sentiment data, then return a JSON array of trading decisions — one object per asset.

Decision schema (strict):
{{
  "asset":      "<SYMBOL>",
  "action":     "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0–1.0>,
  "size_pct":   <int 0–{max_pos}>,
  "reasoning":  "<one-sentence rationale>"
}}

Rules:
- Return ONLY a valid JSON array. No markdown, no explanation, no extra text.
- size_pct must be 0 for HOLD and SELL actions.
- size_pct maximum is {max_pos} for BUY actions.
- Only recommend BUY or SELL when confidence ≥ {min_conf:.2f}. Below that threshold, use HOLD.
- If a position is currently OPEN for an asset: you may recommend SELL (to exit) or HOLD (to keep it). Do NOT recommend BUY on an already-open position.
- If NO position is open for an asset: you may recommend BUY (to enter) or HOLD (to stay flat). Do NOT recommend SELL on an asset with no position.
- Be willing to act — HOLD everything is not a useful response if the data supports a trade."""


def _build_prompt(
    market_data: dict[str, Any],
    sentiment_data: dict[str, Any],
    open_positions: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"=== HOURLY TRADING ANALYSIS — {now} ===",
        "",
    ]

    # Current position context — critical for SELL decisions
    lines.append("=== CURRENT POSITIONS ===")
    if not open_positions:
        lines.append("No open positions — all assets are currently flat.")
    else:
        for symbol in (list(market_data.keys()) or settings.tracked_symbols):
            pos = open_positions.get(symbol.upper())
            if pos is not None:
                pnl_pct = (
                    (pos.current_price - pos.entry_price) / pos.entry_price * 100
                    if pos.entry_price > 0 else 0.0
                )
                lines.append(
                    f"  {symbol}: LONG {pos.size_pct:.0f}% @ ${pos.entry_price:,.4f} "
                    f"(current ${pos.current_price:,.4f}, {pnl_pct:+.2f}% PnL)"
                )
            else:
                lines.append(f"  {symbol}: flat — no position")
    lines.append("")

    lines.append("=== MARKET DATA ===")
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

    lines.append("Return ONLY a JSON array with one decision per asset listed above.")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
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
    max_pos = int(settings.max_position_pct)
    validated: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict) or not required.issubset(item.keys()):
            continue

        asset = str(item["asset"]).upper()
        action = str(item["action"]).upper()
        confidence = max(0.0, min(1.0, float(item["confidence"])))
        size_pct = max(0, min(max_pos, int(item["size_pct"])))
        reasoning = str(item["reasoning"])

        if asset not in valid_assets:
            continue
        if action not in valid_actions:
            action = "HOLD"

        if confidence < settings.min_confidence and action != "HOLD":
            reasoning = (
                f"Confidence {confidence:.2f} below threshold {settings.min_confidence:.2f} — overridden to HOLD."
            )
            action, size_pct = "HOLD", 0

        if action in ("HOLD", "SELL"):
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
    open_positions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    symbols = list(market_data.keys()) or settings.tracked_symbols
    client = _get_client()
    prompt = _build_prompt(market_data, sentiment_data, open_positions)

    response = client.messages.create(
        model=settings.model_name,
        max_tokens=2048,
        system=_build_system_prompt(),
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
