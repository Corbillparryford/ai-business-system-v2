"""
core/claude_client.py
=====================
All Claude API calls go through here.
Handles retry, JSON parse safety, markdown fence stripping, graceful fallback.
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
    RESULT_SUMMARY_PROMPT,
    CONTENT_BRAIN_PROMPT,
    VALIDATION_PROMPT,
)

log = logging.getLogger(__name__)

MODEL       = "claude-sonnet-4-20250514"
MAX_RETRIES = 3

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _parse_signal_text(text: str) -> dict:
    """
    Parse Claude's plain-text betting signal response.
    Format expected per block:
        Signal:
        Type: POSITIVE_EV
        Matchup: Team A vs Team B
        Play: Team A ML
        Book: DraftKings
        Odds: +145
        Edge: 6.4
        Confidence: 8
        Timing: 18 min to start
        Reason: Short explanation.

    Defensive: skips blocks missing required fields, guards all float/int casts.
    Returns {"signals": [...]} always — never raises.
    """
    signals = []
    try:
        blocks = text.split("Signal:")
        for block in blocks[1:]:   # first split before "Signal:" is preamble
            if "Play:" not in block:
                continue
            signal = {}
            for line in block.split("\n"):
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if not val:
                    continue
                if key == "Type":
                    signal["type"] = val
                    # normalise to expected values
                    if "ARB" in val.upper():
                        signal["type"] = "ARBITRAGE"
                    elif "EV" in val.upper() or "POSITIVE" in val.upper():
                        signal["type"] = "POSITIVE_EV"
                elif key == "Matchup":
                    signal["matchup"] = val
                elif key == "Play":
                    signal["play"] = val
                elif key == "Book":
                    signal["book"] = val
                elif key == "Odds":
                    signal["odds"] = val
                elif key == "Edge":
                    try:
                        # strip any trailing % or non-numeric suffix
                        clean = val.replace("%", "").split()[0]
                        signal["edge"] = float(clean)
                    except (ValueError, IndexError):
                        signal["edge"] = 0.0
                elif key == "Confidence":
                    try:
                        clean = val.split()[0].rstrip("./")
                        signal["confidence"] = int(float(clean))
                    except (ValueError, IndexError):
                        signal["confidence"] = 5
                elif key == "Timing":
                    signal["timing"] = val
                elif key == "Reason":
                    signal["reasoning"] = val

            # Require minimum fields before accepting signal
            if all(k in signal for k in ("type", "matchup", "play", "book")):
                # Fill defaults for fields the formatter expects
                signal.setdefault("source", "SPORTSBOOK")
                signal.setdefault("odds", "N/A")
                signal.setdefault("edge", 0.0)
                signal.setdefault("confidence", 5)
                signal.setdefault("timing", "N/A")
                signal.setdefault("reasoning", "")
                signal.setdefault("arb_percentage", signal["edge"] if signal["type"] == "ARBITRAGE" else 0.0)
                signal.setdefault("profit_per_1000", 0.0)
                signal.setdefault("legs", [])
                signal.setdefault("fees_applied", False)
                signal.setdefault("implied_prob", 0.0)
                signal.setdefault("true_prob", 0.0)
                signal.setdefault("teaser_text", "")
                signal.setdefault("provisional", False)
                signals.append(signal)

    except Exception as e:
        log.error("Signal text parse error: %s", e)

    return {"signals": signals}
    """
    Robustly extract and parse the JSON object from Claude's response.
    Handles: markdown fences, preamble text, trailing text, whitespace.
    Returns parsed dict or None.
    """
    text = raw.strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    # Find first { and last } — attempt parsing that substring only
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _call(prompt: str, max_tokens: int = 2000, temperature: float = 0.0) -> dict | None:
    client = _get_client()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text

            parsed = _extract_json(raw)
            if not parsed:
                log.error("Claude returned invalid or empty JSON")
                return None

            return parsed

        except anthropic.RateLimitError:
            wait = 10 * attempt
            log.warning("Rate limited — waiting %ds", wait)
            time.sleep(wait)
            continue
        except anthropic.APIConnectionError as e:
            log.warning("Connection error (attempt %d): %s", attempt, e)
        except anthropic.APIError as e:
            log.warning("API error (attempt %d): %s", attempt, e)
        except Exception as e:
            log.warning("Unexpected error (attempt %d): %s", attempt, e)

        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)

    log.error("Claude call failed after %d attempts", MAX_RETRIES)
    return None


# ── Public functions ──────────────────────────────────────────────────────────

def call_betting_brain(opportunities: list, data_quality: str = "GOOD") -> dict:
    trimmed = sorted(
        opportunities,
        key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
        reverse=True,
    )[:20]

    client = _get_client()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                temperature=0.0,
                messages=[{
                    "role": "user",
                    "content": BETTING_BRAIN_PROMPT.format(
                        utc_timestamp=datetime.utcnow().isoformat(),
                        data_quality=data_quality,
                        opportunities_json=json.dumps(trimmed, indent=2),
                    ),
                }],
            )
            raw = resp.content[0].text
            result = _parse_signal_text(raw)
            log.info("Betting brain parsed %d signals from plain text", len(result["signals"]))
            return result

        except anthropic.RateLimitError:
            wait = 10 * attempt
            log.warning("Rate limited — waiting %ds", wait)
            time.sleep(wait)
            continue
        except anthropic.APIConnectionError as e:
            log.warning("Betting brain connection error (attempt %d): %s", attempt, e)
        except anthropic.APIError as e:
            log.warning("Betting brain API error (attempt %d): %s", attempt, e)
        except Exception as e:
            log.warning("Betting brain unexpected error (attempt %d): %s", attempt, e)

        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)

    log.error("Betting brain failed after %d attempts", MAX_RETRIES)
    return {"signals": [], "rejected_count": 0,
            "rejection_reasons": ["Claude unavailable"], "data_quality": "ERROR"}


def call_trading_brain(analysis: list, active_signals: list,
                       market_condition: str = "UNKNOWN", vix: str = "N/A") -> dict:
    result = _call(
        TRADING_BRAIN_PROMPT.format(
            timestamp=datetime.utcnow().isoformat(),
            market_condition=market_condition,
            vix=vix,
            analysis_json=json.dumps(analysis, indent=2),
            active_signals_json=json.dumps(active_signals, indent=2),
        ),
        max_tokens=2000, temperature=0.0,
    )
    if not result or "signals" not in result:
        return {
            "signals": [], "targets_hit": [],
            "signals_invalidated": [], "market_condition": "UNKNOWN",
            "timestamp": datetime.utcnow().isoformat(),
        }
    return result


def call_result_summary(completed: list, signal_type: str) -> dict:
    result = _call(
        RESULT_SUMMARY_PROMPT.format(
            completed_json=json.dumps(completed, indent=2),
            signal_type=signal_type,
        ),
        max_tokens=1500, temperature=0.0,
    )
    return result or {"results": []}


def call_content_brain(products: list) -> dict:
    result = _call(
        CONTENT_BRAIN_PROMPT.format(
            current_date=datetime.utcnow().date().isoformat(),
            product_data_json=json.dumps(products, indent=2),
        ),
        max_tokens=4000, temperature=0.7,
    )
    return result or {"content_batch": []}


def validate_signal(signal: dict) -> dict:
    result = _call(
        VALIDATION_PROMPT.format(
            signal_json=json.dumps(signal, indent=2),
            current_time=datetime.utcnow().isoformat(),
        ),
        max_tokens=300, temperature=0.0,
    )
    return result or {
        "approved": True,
        "reason": "Validation unavailable — passed through",
        "adjusted_confidence": signal.get("confidence", 5),
        "warnings": ["Claude validation skipped"],
    }
