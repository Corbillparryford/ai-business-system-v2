"""
core/claude_client.py
=====================
All Claude API calls for the AI Business System.

Public functions:
  call_betting_brain(opportunities, data_quality)  -> {"signals": [...]}
  call_trading_brain(analysis, active, ...)        -> {"signals": [...], ...}
  call_content_brain(products)                     -> {"content_batch": [...]}
  validate_signal(signal)                          -> {"approved": bool, ...}

Design:
  - No _extract_json — parsing done inline with try/except json.loads
  - Never crashes on bad responses — every parse wrapped, safe defaults returned
  - Minimal tokens: max_tokens tuned per call type
  - Retry logic: up to RETRIES attempts with backoff
  - Betting brain uses plain text to avoid JSON truncation on large inputs
"""

import json
import logging
import time
from datetime import datetime

import anthropic

from core.config import ANTHROPIC_API_KEY
from core.prompts import (
    BETTING_BRAIN_PROMPT,
    TRADING_BRAIN_PROMPT,
    VALIDATION_PROMPT,
)

log     = logging.getLogger(__name__)
MODEL   = "claude-sonnet-4-20250514"
RETRIES = 2

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Core API call ──────────────────────────────────────────────────────────────

def _call_claude(prompt: str, max_tokens: int = 800, temperature: float = 0.0) -> str:
    """
    Call Claude and return raw response text.
    Retries up to RETRIES times with exponential backoff.
    Returns empty string on total failure — never raises.
    """
    client = _get_client()
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except anthropic.RateLimitError:
            wait = 15 * attempt
            log.warning("Rate limited — waiting %ds (attempt %d/%d)", wait, attempt, RETRIES)
            time.sleep(wait)
        except anthropic.APIConnectionError as e:
            log.warning("Connection error (attempt %d/%d): %s", attempt, RETRIES, e)
        except anthropic.APIError as e:
            log.warning("API error (attempt %d/%d): %s", attempt, RETRIES, e)
        except Exception as e:
            log.warning("Unexpected error (attempt %d/%d): %s", attempt, RETRIES, e)
        if attempt < RETRIES:
            time.sleep(4 ** attempt)
    log.error("Claude call failed after %d attempts", RETRIES)
    return ""


# ── Safe JSON parse ────────────────────────────────────────────────────────────

def _safe_json(text: str) -> dict | None:
    """
    Parse a JSON object from Claude's response text.
    Strips markdown fences, finds outermost { }, calls json.loads.
    Returns dict or None — never raises.
    """
    if not text:
        return None
    cleaned = text.strip()
    # Strip markdown fences
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break
    # Find outermost JSON object
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed
    except Exception:
        return None


# ── Plain-text signal parser (betting brain only) ──────────────────────────────

def _parse_signal_text(text: str) -> list[dict]:
    """
    Parse Claude's plain-text betting response into signal dicts.
    Expected block format:
        Signal:
        Type: POSITIVE_EV
        Matchup: ...
        Play: ...
        Book: ...
        Odds: ...
        Edge: 6.4
        Confidence: 8
        Timing: ...
        Reason: one sentence
    Returns list — never raises.
    """
    signals = []
    if not text:
        return signals
    try:
        for block in text.split("Signal:")[1:]:
            if "Play:" not in block:
                continue
            s = {}
            for line in block.split("\n"):
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if not val:
                    continue
                if key == "Type":
                    s["type"] = "ARBITRAGE" if "ARB" in val.upper() else "POSITIVE_EV"
                elif key == "Matchup":   s["matchup"]   = val
                elif key == "Play":      s["play"]      = val
                elif key == "Book":      s["book"]      = val
                elif key == "Odds":      s["odds"]      = val
                elif key == "Edge":
                    try:   s["edge"] = float(val.replace("%", "").split()[0])
                    except: s["edge"] = 0.0
                elif key == "Confidence":
                    try:   s["confidence"] = int(float(val.split()[0].rstrip("./")))
                    except: s["confidence"] = 5
                elif key == "Timing":    s["timing"]    = val
                elif key == "Reason":    s["reasoning"] = val
            if all(k in s for k in ("type", "matchup", "play", "book")):
                s.setdefault("edge", 0.0)
                s.setdefault("confidence", 5)
                s.setdefault("timing", "N/A")
                s.setdefault("reasoning", "")
                s.setdefault("source", "SPORTSBOOK")
                s.setdefault("arb_percentage",
                             s["edge"] if s["type"] == "ARBITRAGE" else 0.0)
                s.setdefault("profit_per_1000", 0.0)
                s.setdefault("legs", [])
                s.setdefault("fees_applied", False)
                signals.append(s)
    except Exception as e:
        log.error("Signal text parse error: %s", e)
    return signals


# ── Public functions ───────────────────────────────────────────────────────────

def call_betting_brain(opportunities: list, data_quality: str = "GOOD") -> dict:
    """
    Validate +EV and arbitrage opportunities via Claude.
    Returns {"signals": [...]} always — never raises.
    """
    if not opportunities:
        return {"signals": []}

    trimmed = sorted(
        opportunities,
        key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
        reverse=True,
    )[:6]

    response_text = _call_claude(
        BETTING_BRAIN_PROMPT.format(
            utc_timestamp=datetime.utcnow().isoformat(),
            data_quality=data_quality,
            opportunities_json=json.dumps(trimmed, indent=2),
        ),
        max_tokens=800,
    )

    signals = _parse_signal_text(response_text)
    log.info("Betting brain: %d signal(s) from %d opportunities",
             len(signals), len(trimmed))
    return {"signals": signals}


def call_trading_brain(analysis: list, active_signals: list,
                       market_condition: str = "UNKNOWN", vix: str = "N/A") -> dict:
    """
    Generate trading signals from technical analysis.
    Returns structured dict always — never raises.
    """
    _default = {
        "signals": [],
        "targets_hit": [],
        "signals_invalidated": [],
        "market_condition": "UNKNOWN",
    }

    response_text = _call_claude(
        TRADING_BRAIN_PROMPT.format(
            market_condition=market_condition,
            vix=vix,
            analysis_json=json.dumps(analysis, indent=2),
            active_signals_json=json.dumps(active_signals, indent=2),
        ),
        max_tokens=600,
    )

    try:
        parsed = json.loads(response_text)
    except Exception:
        parsed = _safe_json(response_text)

    if not parsed or "signals" not in parsed:
        log.error("Trading brain: could not parse response")
        return _default

    log.info("Trading brain: %d signal(s), %d hit(s)",
             len(parsed.get("signals", [])),
             len(parsed.get("targets_hit", [])))
    return parsed


def call_content_brain(products: list) -> dict:
    """
    Generate viral content packages for trending products.
    Returns {"content_batch": [...]} always — never raises.
    """
    _default = {"content_batch": []}

    if not products:
        return _default

    # Inline content prompt — kept minimal to save tokens
    prompt = (
        "Return ONLY valid JSON. No markdown. No extra text.\n"
        "Double quotes only. No trailing commas.\n\n"
        "You are a viral TikTok content strategist.\n"
        "Generate 3 viral content packages for these trending products.\n\n"
        f"DATE: {datetime.utcnow().date().isoformat()}\n"
        f"PRODUCTS:\n{json.dumps(products[:5], indent=2)}\n\n"
        "Rules:\n"
        "1. Hook must cause pattern interrupt in 0-2 seconds.\n"
        "2. Script: 4 segments of 5 seconds each (20 sec total).\n"
        "3. priority_score = (viral * conversion) / 10, max 10.0.\n\n"
        'Return: {"content_batch": [{"product_name": "string", '
        '"affiliate_url": "string or null", "hook": "string", '
        '"script": {"0_5": "string", "5_10": "string", '
        '"10_15": "string", "15_20": "string"}, '
        '"caption": "string", "cta": "string", '
        '"hashtags": ["#tag"], "priority_score": 8.5}]}'
    )

    response_text = _call_claude(prompt, max_tokens=1000, temperature=0.7)

    try:
        parsed = json.loads(response_text)
    except Exception:
        parsed = _safe_json(response_text)

    if not parsed or "content_batch" not in parsed:
        log.error("Content brain: could not parse response")
        return _default

    log.info("Content brain: %d package(s)", len(parsed.get("content_batch", [])))
    return parsed


def validate_signal(signal: dict) -> dict:
    """
    Quick quality-control check before posting a signal.
    Approves by default if Claude is unavailable — keeps system running.
    Returns {"approved": bool, ...} always — never raises.
    """
    _default_approve = {
        "approved": True,
        "reason": "validation unavailable — passed through",
        "adjusted_confidence": signal.get("confidence", 5),
        "warnings": [],
    }

    response_text = _call_claude(
        VALIDATION_PROMPT.format(
            signal_json=json.dumps(signal, indent=2),
            current_time=datetime.utcnow().isoformat(),
        ),
        max_tokens=150,
    )

    try:
        parsed = json.loads(response_text)
    except Exception:
        parsed = _safe_json(response_text)

    if not parsed or "approved" not in parsed:
        return _default_approve

    return parsed


# ── Public alias ───────────────────────────────────────────────────────────────
# Expose _call_claude as a stable public name for use by content and other modules.
# Both `call_claude` and `_call_claude` work — callers can use either.

def call_claude(prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:
    """Public wrapper around _call_claude. Returns raw response text."""
    return _call_claude(prompt, max_tokens=max_tokens, temperature=temperature)
