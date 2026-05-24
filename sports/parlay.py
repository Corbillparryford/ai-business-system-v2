"""
sports/parlay.py
================
Daily Longshot Parlay of the Day — full content engine.

generate_daily_parlay(opportunities):
  - Builds 3–5 legs from live EV opportunities + prop placeholders
  - Targets combined odds >= +3000
  - Generates parlay card, image prompt, Instagram, Twitter via Claude
  - Posts full content pack to #content channel
  - Posts parlay card to #sports-results
  - Stores in DB for WIN/LOSS tracking

resolve_parlay(signal_id, result, leg_results):
  - Posts result card to #sports-results
  - Updates results_tracker

Called once per day from odds_monitor at 12:00 UTC.
"""

import json
import logging
from datetime import date

from core.db import save_sports_signal
from discord.poster import send, WEBHOOKS

log = logging.getLogger(__name__)
SEP = "─────────────────────────────"

# Module-level state
_parlay_posted_today: date | None = None
_parlay_signal_id:    int | None  = None
_todays_parlay:       dict | None = None


# ── Math ───────────────────────────────────────────────────────────────────────

def _to_decimal(odds: int) -> float:
    return (odds / 100 + 1.0) if odds > 0 else (100 / abs(odds) + 1.0)


def _combine_odds(legs: list[dict]) -> int:
    decimal = 1.0
    for leg in legs:
        try:
            decimal *= _to_decimal(int(leg["odds"]))
        except (TypeError, ValueError):
            decimal *= 1.5   # safe fallback for malformed odds
    if decimal >= 2.0:
        return int((decimal - 1) * 100)
    return int(-100 / max(decimal - 1, 0.001))


# ── Leg selection ──────────────────────────────────────────────────────────────

def _ev_legs(opportunities: list) -> list[dict]:
    """Pull underdog +EV legs from live opportunity pool."""
    dogs = [
        o for o in opportunities
        if o.get("type") == "POSITIVE_EV"
        and str(o.get("odds", "")).startswith("+")
        and o.get("confidence", 0) >= 5
    ]
    dogs.sort(
        key=lambda x: int(str(x.get("odds", "+100")).lstrip("+") or "100"),
        reverse=True,
    )
    legs = []
    for o in dogs[:3]:
        try:
            odds_val = int(str(o["odds"]).lstrip("+"))
        except (ValueError, KeyError):
            continue
        legs.append({
            "label":   o.get("play", ""),
            "matchup": o.get("matchup", ""),
            "book":    o.get("book", "DraftKings"),
            "odds":    odds_val,
            "timing":  o.get("timing", "TBD"),
            "type":    "team",
        })
    return legs


def _prop_legs() -> list[dict]:
    """
    Placeholder player prop legs.
    Replace with OddsAPI player_props endpoint when available.
    """
    return [
        {
            "label":   "Star player Over 24.5 points",
            "matchup": "Upcoming NBA game",
            "book":    "DraftKings",
            "odds":    125,
            "timing":  "Tonight",
            "type":    "prop",
        },
        {
            "label":   "Top forward Anytime Goal Scorer",
            "matchup": "Upcoming NHL game",
            "book":    "FanDuel",
            "odds":    160,
            "timing":  "Tonight",
            "type":    "prop",
        },
    ]


# ── Discord formatters ─────────────────────────────────────────────────────────

def _fmt_parlay_card(legs: list[dict], combined_odds: int) -> str:
    today = date.today().strftime("%B %d, %Y")
    leg_lines = "\n".join(
        f"**Leg {i+1}:** {leg['label']}\n"
        f"   {leg['matchup']} | {leg['book']} | +{leg['odds']}"
        for i, leg in enumerate(legs)
    )
    earliest = next((l["timing"] for l in legs if l["timing"] != "TBD"), "Today")
    return (
        f"{SEP}\n"
        f"🎯 **LONGSHOT PARLAY OF THE DAY — {today}**\n\n"
        f"{leg_lines}\n\n"
        f"💰 Total Odds: **+{combined_odds:,}**\n"
        f"📍 Book: DraftKings / FanDuel\n"
        f"⏱ Start Time: {earliest}\n"
        f"⚠️ Longshot — small unit only (0.5–1%)\n"
        f"{SEP}"
    )


def _fmt_result_card(legs: list[dict], leg_results: list[bool],
                     combined_odds: int, result: str) -> str:
    outcome_emoji = {"WIN": "✅", "LOSS": "❌"}.get(result, "⚪")
    leg_lines = "\n".join(
        f"Leg {i+1}: {'✅' if won else '❌'} {leg['label']}"
        for i, (leg, won) in enumerate(zip(legs, leg_results))
    )
    return (
        f"{SEP}\n"
        f"🎯 **LONGSHOT PARLAY RESULT**\n\n"
        f"{leg_lines}\n\n"
        f"Result: **{outcome_emoji} {result}**\n"
        f"Total Odds: +{combined_odds:,}\n"
        f"{SEP}"
    )


# ── Claude content generation ──────────────────────────────────────────────────

def _generate_content(legs: list[dict], combined_odds: int) -> dict:
    """
    Ask Claude to generate image prompt, Instagram, and Twitter copy.
    Returns dict with keys: image_prompt, instagram, twitter.
    Falls back to template strings if Claude is unavailable.
    """
    leg_summary = "\n".join(
        f"- Leg {i+1}: {leg['label']} ({leg['matchup']})"
        for i, leg in enumerate(legs)
    )

    prompt = (
        "You are writing social media content for a daily longshot parlay.\n\n"
        f"Parlay legs:\n{leg_summary}\n"
        f"Combined odds: +{combined_odds:,}\n\n"
        "Write the following sections with NO extra text outside them:\n\n"
        "IMAGE PROMPT:\n"
        "Write a detailed prompt for an AI image generator. Style: dark cinematic background, "
        "professional sports poster. Feature 1-2 star players from the key legs positioned "
        "facing each other with subtle glow. Midground: faint stadium. "
        "Overlay text includes: LONGSHOT PARLAY, the odds, and each leg listed cleanly. "
        "Square format, minimal clutter, premium typography, THE SHARP MARGIN branding.\n\n"
        "INSTAGRAM:\n"
        "3-5 lines. Natural and conversational. Calm confidence, no hype. "
        "Mention it's a longshot. Mention the odds naturally. "
        "End with https://whop.com/@thesharpmargin\n"
        "Style: 'Putting together a longshot for today. "
        "Couple spots we like. Parlay comes out to +3200. Let's see if it falls.'\n\n"
        "TWITTER:\n"
        "1-2 lines. Short version. Mention odds. "
        "End with https://whop.com/@thesharpmargin\n\n"
        "TONE: Human, calm, slightly reflective. No excuses. No fake hype."
    )

    try:
        from core.claude_client import call_claude
        raw = call_claude(prompt, max_tokens=700, temperature=0.75)
    except Exception as e:
        log.warning("Parlay content: Claude unavailable — %s", e)
        raw = ""

    # Parse sections from Claude response
    image_prompt = instagram = twitter = ""
    if raw:
        for section, key in [
            ("IMAGE PROMPT:", "image_prompt"),
            ("INSTAGRAM:",    "instagram"),
            ("TWITTER:",      "twitter"),
        ]:
            if section in raw:
                parts = raw.split(section, 1)
                if len(parts) > 1:
                    chunk = parts[1]
                    # Stop at next section header
                    for next_sec in ["IMAGE PROMPT:", "INSTAGRAM:", "TWITTER:"]:
                        if next_sec != section and next_sec in chunk:
                            chunk = chunk.split(next_sec)[0]
                    val = chunk.strip()
                    if key == "image_prompt": image_prompt = val
                    elif key == "instagram":  instagram    = val
                    elif key == "twitter":    twitter      = val

    # Fallbacks if Claude didn't return clean sections
    if not image_prompt:
        leg_text = " | ".join(l["label"][:30] for l in legs[:3])
        image_prompt = (
            f"Dark cinematic sports poster. Two star athletes from the featured matchups "
            f"positioned facing each other with subtle golden glow. Faint stadium in midground. "
            f"Overlay text: 'LONGSHOT PARLAY' in bold clean font, '+{combined_odds:,}' in large "
            f"green numerals, leg details listed below in small white text: {leg_text}. "
            f"THE SHARP MARGIN wordmark bottom right. Square format, minimal clutter."
        )
    if not instagram:
        instagram = (
            f"Putting together a longshot for today.\n"
            f"Mix of {len(legs)} spots across different games.\n"
            f"Parlay comes out to +{combined_odds:,}.\n"
            f"Let's see if it falls.\nhttps://whop.com/@thesharpmargin"
        )
    if not twitter:
        twitter = (
            f"+{combined_odds:,} longshot today.\n"
            f"Let's see how it plays out. https://whop.com/@thesharpmargin"
        )

    return {"image_prompt": image_prompt, "instagram": instagram, "twitter": twitter}


# ── Discord content pack formatter ────────────────────────────────────────────

def _fmt_content_pack(parlay_card: str, content: dict) -> str:
    today = date.today().strftime("%B %d, %Y")
    return (
        f"🎯 **PARLAY CONTENT — {today}**\n\n"
        f"{parlay_card}\n\n"
        f"{SEP}\n"
        f"🎨 **IMAGE PROMPT:**\n{content['image_prompt']}\n\n"
        f"{SEP}\n"
        f"📸 **INSTAGRAM:**\n{content['instagram']}\n\n"
        f"{SEP}\n"
        f"🐦 **TWITTER:**\n{content['twitter']}\n"
        f"{SEP}"
    )


# ── Main entry points ──────────────────────────────────────────────────────────

def generate_daily_parlay(opportunities: list) -> dict | None:
    """
    Generate today's longshot parlay and post to Discord.
    Only runs once per day. Returns parlay dict or None if skipped.
    """
    global _parlay_posted_today, _parlay_signal_id, _todays_parlay

    today = date.today()
    if _parlay_posted_today == today:
        log.debug("Daily parlay already posted today")
        return _todays_parlay

    # Build legs
    ev_legs   = _ev_legs(opportunities)
    prop_legs = _prop_legs()
    legs      = (ev_legs + prop_legs)[:5]

    if len(legs) < 3:
        log.warning("Not enough legs (%d) — skipping parlay", len(legs))
        return None

    combined_odds = _combine_odds(legs)
    if combined_odds < 3000:
        log.info("Combined odds +%d below +3000 target — posting anyway", combined_odds)

    # Generate full content pack via Claude
    content = _generate_content(legs, combined_odds)

    # Format cards
    parlay_card  = _fmt_parlay_card(legs, combined_odds)
    content_pack = _fmt_content_pack(parlay_card, content)

    # Post to channels
    send(WEBHOOKS.get("sports_results", ""), parlay_card)
    send(WEBHOOKS.get("content",         ""), content_pack)

    # Save to DB
    signal_id = None
    try:
        signal_id = save_sports_signal({
            "type":       "PARLAY",
            "matchup":    f"{len(legs)}-leg longshot parlay",
            "play":       " / ".join(l["label"][:25] for l in legs),
            "book":       "Multiple",
            "odds":       f"+{combined_odds}",
            "edge":       2.0,
            "confidence": 4,
            "timing":     "Today",
            "reasoning":  (
                f"{len(legs)}-leg longshot. Combined odds +{combined_odds}. "
                f"Legs: {json.dumps([l['label'] for l in legs])}"
            ),
        })
        _parlay_signal_id = signal_id
        log.info("Parlay saved to DB (id=%s, odds=+%d)", signal_id, combined_odds)
    except Exception as e:
        log.error("Parlay DB save failed: %s", e)

    _parlay_posted_today = today
    _todays_parlay = {
        "legs":          legs,
        "combined_odds": combined_odds,
        "signal_id":     signal_id,
        "content":       content,
    }

    log.info("Daily parlay posted: %d legs, combined +%d", len(legs), combined_odds)
    return _todays_parlay


def resolve_parlay(signal_id: int, result: str, leg_results: list[bool] | None = None):
    """
    Post parlay result card to #sports-results and update results_tracker.
    leg_results: list of bool per leg (True=hit, False=miss). Optional.
    """
    from sports.results_tracker import record_result

    # Retrieve stored parlay for leg labels
    legs          = []
    combined_odds = 0
    if _todays_parlay:
        legs          = _todays_parlay.get("legs", [])
        combined_odds = _todays_parlay.get("combined_odds", 0)

    # Default leg_results if not provided
    if not leg_results:
        leg_results = [result == "WIN"] * len(legs)

    # Post result card
    if legs:
        card = _fmt_result_card(legs, leg_results, combined_odds, result)
        send(WEBHOOKS.get("sports_results", ""), card)

    # Update tracking
    try:
        from core.db import resolve_sports_signal
        resolve_sports_signal(signal_id, result, f"Parlay resolved: {result}")
    except Exception as e:
        log.error("Parlay DB resolve failed: %s", e)

    record_result(result)
    log.info("Parlay %s resolved: %s", signal_id, result)


def get_todays_parlay_result() -> str | None:
    """Short summary string for content generation. None if no parlay today."""
    if _parlay_posted_today != date.today() or not _todays_parlay:
        return None
    odds = _todays_parlay.get("combined_odds", 0)
    return f"Longshot parlay live today — +{odds:,} combined odds."
