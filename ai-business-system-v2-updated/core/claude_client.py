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
            raw = resp.content[0].text.strip()
            # Strip accidental markdown fences
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())

        except json.JSONDecodeError as e:
            log.warning("JSON parse error (attempt %d): %s", attempt, e)
        except anthropic.RateLimitError:
            wait = 10 * attempt
            log.warning("Rate limited — waiting %ds", wait)
            time.sleep(wait)
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
    # Pre-filter: keep only the top opportunities by edge to avoid truncation.
    # Sort descending by edge, cap at 20 — enough for Claude to identify
    # the best signals without blowing the token budget.
    trimmed = sorted(
        opportunities,
        key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
        reverse=True,
    )[:20]

    result = _call(
        BETTING_BRAIN_PROMPT.format(
            utc_timestamp=datetime.utcnow().isoformat(),
            data_quality=data_quality,
            opportunities_json=json.dumps(trimmed, indent=2),
        ),
        max_tokens=4000, temperature=0.0,
    )
    return result or {
        "signals": [], "rejected_count": 0,
        "rejection_reasons": ["Claude unavailable"], "data_quality": "ERROR",
    }


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
    return result or {
        "signals": [], "targets_hit": [],
        "signals_invalidated": [], "market_condition": "UNKNOWN",
        "timestamp": datetime.utcnow().isoformat(),
    }


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
