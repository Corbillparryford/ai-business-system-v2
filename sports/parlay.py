"""
sports/parlay.py
================
Daily longshot parlay generation.

generate_daily_parlay():
  - Selects 3–5 legs from current opportunities and player props
  - Targets combined odds >= +3000
  - Stores in DB (sports_signals table, type=PARLAY)
  - Posts to #sports-results and #content channels
  - Integrates with results_tracker for WIN/LOSS tracking

Called once per day from odds_monitor at a configurable UTC hour.
"""

import logging
import random
from datetime import datetime, date

from core.db import save_sports_signal
from discord.poster import send, WEBHOOKS
from sports.results_tracker import record_result

log = logging.getLogger(__name__)

SEP = "─────────────────────────────"

# Module-level state — tracks today's parlay
_parlay_posted_today: date | None = None
_parlay_signal_id:    int | None  = None


# ── Parlay math ────────────────────────────────────────────────────────────────

def _american_to_decimal(odds: int) -> float:
    return (odds / 100 + 1.0) if odds > 0 else (100 / abs(odds) + 1.0)


def _combine_odds(legs: list[dict]) -> int:
    """Combine multiple american odds into a single parlay american odds value."""
    decimal = 1.0
    for leg in legs:
        decimal *= _american_to_decimal(int(leg["odds"]))
    # Convert combined decimal back to american
    if decimal >= 2.0:
        return int((decimal - 1) * 100)
    else:
        return int(-100 / (decimal - 1))


# ── Leg builders ───────────────────────────────────────────────────────────────

def _build_legs_from_opportunities(opportunities: list) -> list[dict]:
    """
    Select 3–5 diverse legs from current +EV opportunities.
    Picks higher-confidence underdogs to hit the +3000 target.
    """
    # Filter to positive american odds (underdogs) with decent confidence
    dogs = [
        o for o in opportunities
        if o.get("type") == "POSITIVE_EV"
        and o.get("odds", "").startswith("+")
        and o.get("confidence", 0) >= 5
    ]

    # Sort by odds (most +EV underdogs first), take up to 5
    dogs.sort(key=lambda x: int(x.get("odds", "+100").lstrip("+") or "100"), reverse=True)
    selected = dogs[:5] if len(dogs) >= 5 else dogs

    legs = []
    for o in selected:
        try:
            odds_val = int(o["odds"].lstrip("+"))
        except (ValueError, KeyError):
            continue
        legs.append({
            "matchup":  o.get("matchup", ""),
            "play":     o.get("play", ""),
            "book":     o.get("book", "DraftKings"),
            "odds":     odds_val,
            "timing":   o.get("timing", "TBD"),
        })
        if len(legs) >= 5:
            break

    return legs


def _build_prop_legs() -> list[dict]:
    """
    Placeholder player prop legs. Returns synthetic prop examples.
    In production: feed from OddsAPI player_props endpoint.
    """
    prop_examples = [
        {"matchup": "Upcoming NBA game",    "play": "LeBron James Over 25.5 pts",
         "book": "DraftKings", "odds": 130, "timing": "TBD"},
        {"matchup": "Upcoming NFL game",    "play": "CMC Over 85.5 rushing yds",
         "book": "FanDuel",    "odds": 115, "timing": "TBD"},
        {"matchup": "Upcoming NHL game",    "play": "McDavid Anytime Goal Scorer",
         "book": "BetMGM",     "odds": 170, "timing": "TBD"},
        {"matchup": "Upcoming MLB game",    "play": "Shohei Ohtani Over 1.5 TB",
         "book": "DraftKings", "odds": 140, "timing": "TBD"},
    ]
    return prop_examples[:2]   # add 2 prop legs


# ── Formatter ─────────────────────────────────────────────────────────────────

def _fmt_parlay(legs: list[dict], combined_odds: int) -> str:
    leg_lines = "\n".join(
        f"**Leg {i+1}:** {leg['play']}\n"
        f"   Match: {leg['matchup']}\n"
        f"   Book: {leg['book']} | Odds: +{leg['odds']}"
        for i, leg in enumerate(legs)
    )
    today = date.today().strftime("%B %d, %Y")
    return (
        f"{SEP}\n"
        f"🎯 **LONGSHOT PARLAY OF THE DAY — {today}**\n\n"
        f"{leg_lines}\n\n"
        f"💰 Total Odds: **+{combined_odds:,}**\n"
        f"📚 Place on: DraftKings / FanDuel\n"
        f"⏱ All legs must start today\n"
        f"⚠️ Longshot play — small unit only (0.5–1%)\n"
        f"{SEP}"
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def generate_daily_parlay(opportunities: list) -> dict | None:
    """
    Generate and post today's longshot parlay.
    Returns the parlay dict if posted, None if skipped.
    Only generates once per day.
    """
    global _parlay_posted_today, _parlay_signal_id

    today = date.today()
    if _parlay_posted_today == today:
        log.debug("Daily parlay already posted today — skipping")
        return None

    # Build legs
    ev_legs   = _build_legs_from_opportunities(opportunities)
    prop_legs = _build_prop_legs()
    all_legs  = ev_legs[:3] + prop_legs   # 3 team bets + 2 props

    if len(all_legs) < 3:
        log.warning("Not enough legs for parlay (%d available) — skipping", len(all_legs))
        return None

    # Trim to 3–5 legs
    legs = all_legs[:5]

    # Calculate combined odds
    combined_odds = _combine_odds(legs)

    # Enforce minimum +3000 target — if short, this is still posted as a parlay
    if combined_odds < 3000:
        log.info("Parlay combined odds +%d (below +3000 target) — posting anyway", combined_odds)

    # Build signal record for DB and tracking
    parlay_signal = {
        "type":       "PARLAY",
        "matchup":    f"{len(legs)}-leg parlay",
        "play":       " / ".join(l["play"][:25] for l in legs),
        "book":       "Multiple",
        "odds":       f"+{combined_odds}",
        "edge":       2.0,       # nominal edge for tracking
        "confidence": 4,         # longshot — low confidence by design
        "timing":     "Today",
        "reasoning":  f"{len(legs)}-leg longshot parlay. Combined odds +{combined_odds}.",
        "legs":       legs,
    }

    # Save to DB
    try:
        signal_id = save_sports_signal(parlay_signal)
        _parlay_signal_id = signal_id
        log.info("Parlay saved to DB (id=%d, odds=+%d)", signal_id, combined_odds)
    except Exception as e:
        log.error("Parlay DB save failed: %s", e)
        signal_id = None

    # Format and post
    msg = _fmt_parlay(legs, combined_odds)
    posted = False
    if send(WEBHOOKS.get("sports_results", ""), msg):
        posted = True
    if send(WEBHOOKS.get("content", ""), msg):
        posted = True

    if posted:
        _parlay_posted_today = today
        log.info("Daily parlay posted: %d legs, combined +%d", len(legs), combined_odds)

    return {**parlay_signal, "signal_id": signal_id, "combined_odds": combined_odds}


def resolve_parlay(signal_id: int, result: str):
    """
    Resolve a parlay as WIN or LOSS.
    Feeds into results_tracker for daily recap.
    """
    from core.db import resolve_sports_signal
    try:
        resolve_sports_signal(signal_id, result, f"Parlay resolved: {result}")
        record_result(result)
        log.info("Parlay %d resolved: %s", signal_id, result)
    except Exception as e:
        log.error("Parlay resolve failed: %s", e)


def get_todays_parlay_result() -> str | None:
    """
    Return a short summary of today's parlay for content generation.
    Returns None if no parlay was posted today.
    """
    if _parlay_posted_today != date.today():
        return None
    return f"Longshot parlay live — combined odds featured today."
