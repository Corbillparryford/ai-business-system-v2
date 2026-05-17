"""
sports/results_tracker.py
=========================
Real WIN/LOSS/PUSH resolution for sports signals.

Uses The Odds API /v4/sports/{sport}/scores endpoint to fetch completed
game scores, then matches each active signal to its game outcome and
determines the bet result.

Called by odds_monitor._check_results() each cycle.
"""

import logging
from datetime import datetime

import requests

from core.config import ODDS_API_KEY, SPORTS_KEYS
from core.db import (
    get_active_sports_signals, resolve_sports_signal,
)
from discord.poster import post_signal, post_health_alert

log = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"


# ── Scores fetcher ────────────────────────────────────────────────────────────

def fetch_completed_scores() -> list:
    """
    Fetch recently completed games from OddsAPI scores endpoint.
    Returns list of completed game dicts with scores attached.
    daysFrom=1 fetches games completed in the last 24 hours.
    """
    if not ODDS_API_KEY:
        return []

    completed = []
    for sport in SPORTS_KEYS:
        try:
            r = requests.get(
                f"{ODDS_BASE}/sports/{sport}/scores/",
                params={"apiKey": ODDS_API_KEY, "daysFrom": 1},
                timeout=10,
            )
            if r.status_code == 200:
                for game in r.json():
                    if game.get("completed"):
                        completed.append(game)
            elif r.status_code == 401:
                log.error("OddsAPI scores: invalid API key")
                return []
        except Exception as e:
            log.debug("Scores fetch (%s): %s", sport, e)

    return completed


# ── Game matching ─────────────────────────────────────────────────────────────

def _teams_match(signal_matchup: str, game: dict) -> bool:
    """
    Check if a signal's matchup string matches a game.
    Handles formats: 'Away @ Home', 'Home vs Away', team name subsets.
    """
    if not signal_matchup:
        return False

    home = (game.get("home_team") or "").lower()
    away = (game.get("away_team") or "").lower()
    matchup = signal_matchup.lower()

    # Direct name presence check — robust to different formatting
    return (home in matchup or away in matchup) and (home != away)


def _determine_result(signal: dict, game: dict) -> tuple[str, str]:
    """
    Given a resolved signal and completed game, determine WIN/LOSS/PUSH.
    Returns (result, note).

    Supports: moneyline (ML), spread, over/under.
    For arbitrage signals: always WIN (guaranteed profit structure).
    """
    play    = (signal.get("play") or "").upper()
    scores  = game.get("scores") or []
    sig_type = signal.get("signal_type") or ""

    # Arbitrage: by definition a guaranteed profit — mark WIN
    if sig_type == "ARBITRAGE" or signal.get("arb_pct", 0) > 0:
        return "WIN", "Arbitrage — guaranteed profit locked in at signal time"

    if not scores:
        return "VOID", "Game completed but scores unavailable"

    # Build score lookup: team_name → score
    score_map = {}
    for s in scores:
        name  = (s.get("name") or "").lower()
        score = s.get("score")
        if name and score is not None:
            try:
                score_map[name] = float(score)
            except (ValueError, TypeError):
                pass

    if len(score_map) < 2:
        return "VOID", "Incomplete score data"

    teams  = list(score_map.items())   # [(team_name, score), ...]
    home   = (game.get("home_team") or "").lower()
    away   = (game.get("away_team") or "").lower()

    home_score = score_map.get(home)
    away_score = score_map.get(away)

    if home_score is None or away_score is None:
        return "VOID", "Score data team name mismatch"

    # ── Moneyline ─────────────────────────────────────────────────────────────
    if "ML" in play or "MONEYLINE" in play:
        # Determine which team the bet is on
        bet_team = None
        for team_key in score_map:
            if team_key in play.lower():
                bet_team = team_key
                break
        # Also check home/away names against the play string
        if bet_team is None:
            if home in play.lower():
                bet_team = home
            elif away in play.lower():
                bet_team = away

        if bet_team is None:
            return "VOID", "Could not identify team from play string"

        bet_score = score_map.get(bet_team, 0)
        opp_score = home_score if bet_team == away else away_score

        if bet_score > opp_score:
            return "WIN", f"{bet_team.title()} won {bet_score:.0f}–{opp_score:.0f}"
        elif bet_score < opp_score:
            return "LOSS", f"{bet_team.title()} lost {bet_score:.0f}–{opp_score:.0f}"
        else:
            return "PUSH", f"Final score tied {bet_score:.0f}–{opp_score:.0f}"

    # ── Over/Under ────────────────────────────────────────────────────────────
    if "OVER" in play or "UNDER" in play:
        total = home_score + away_score
        # Extract line from play string, e.g. "Over 47.5"
        parts = play.split()
        line  = None
        for p in parts:
            try:
                line = float(p)
                break
            except ValueError:
                pass

        if line is None:
            return "VOID", "Could not parse over/under line from play"

        if "OVER" in play:
            if total > line:
                return "WIN",  f"Total {total:.0f} went OVER {line}"
            elif total < line:
                return "LOSS", f"Total {total:.0f} stayed UNDER {line}"
            else:
                return "PUSH", f"Total {total:.0f} hit line exactly"
        else:  # UNDER
            if total < line:
                return "WIN",  f"Total {total:.0f} stayed UNDER {line}"
            elif total > line:
                return "LOSS", f"Total {total:.0f} went OVER {line}"
            else:
                return "PUSH", f"Total {total:.0f} hit line exactly"

    # ── Spread (basic) ────────────────────────────────────────────────────────
    if "SPREAD" in play or "-" in play or "+" in play:
        # Attempt to extract spread value and team
        parts = play.split()
        spread = None
        bet_team = None

        for team_key in score_map:
            if team_key in play.lower():
                bet_team = team_key
                break

        for p in parts:
            try:
                spread = float(p)
                break
            except ValueError:
                pass

        if bet_team is None or spread is None:
            return "VOID", "Could not parse spread from play string"

        bet_score = score_map.get(bet_team, 0)
        opp_score = home_score if bet_team == away else away_score
        covered   = bet_score + spread

        if covered > opp_score:
            return "WIN",  f"{bet_team.title()} covered {spread:+.1f}"
        elif covered < opp_score:
            return "LOSS", f"{bet_team.title()} failed to cover {spread:+.1f}"
        else:
            return "PUSH", f"Exact cover — push"

    # Fallback for unrecognised play types
    return "VOID", f"Unrecognised play format: {play[:50]}"


# ── Discord result formatter ───────────────────────────────────────────────────

def _format_result(signal: dict, result: str, note: str) -> dict:
    """Build the result payload consumed by discord/poster.py post_signal()."""
    emoji    = {"WIN": "✅", "LOSS": "❌", "PUSH": "↩️", "VOID": "⚪"}.get(result, "📊")
    edge_str = f"{signal.get('edge_pct', signal.get('edge', 0))}%"
    sep      = "─────────────────────────────"

    full_summary = (
        f"{sep}\n"
        f"{emoji} **RESULT — {signal.get('matchup', 'N/A')}**\n"
        f"🎯 {signal.get('play', 'N/A')} @ {signal.get('book', 'N/A')}\n"
        f"💰 Outcome: **{result}**\n"
        f"📈 Edge was: {edge_str}\n"
        f"📝 {note}\n"
        f"{sep}"
    )

    preview_text = (
        f"{'✅ Winning' if result == 'WIN' else '❌ Losing' if result == 'LOSS' else '↩️'} "
        f"signal posted — full results in premium channels 👇\n"
        f"🔓 Unlock full signals instantly:\nhttps://whop.com/the-sharp-margin"
    )

    return {
        "signal_id":    signal["id"],
        "matchup":      signal.get("matchup", ""),
        "play":         signal.get("play", ""),
        "book":         signal.get("book", ""),
        "edge_pct":     signal.get("edge_pct", 0),
        "result":       result,
        "full_summary": full_summary,
        "preview_text": preview_text,
        "result_kind":  "sports",
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def resolve_completed_sports_signals():
    """
    Called each odds_monitor cycle. Fetches completed scores, matches
    active signals, determines WIN/LOSS/PUSH, posts results to Discord.
    Falls back to expiry-based VOID after 8 hours if no score data found.
    """
    active = get_active_sports_signals()
    if not active:
        return

    completed_games = fetch_completed_scores()

    for sig in active:
        resolved     = False
        created      = datetime.fromisoformat(sig["created_at"])
        age_hrs      = (datetime.utcnow() - created).total_seconds() / 3600

        # Try to match to a completed game
        for game in completed_games:
            if not _teams_match(sig.get("matchup", ""), game):
                continue

            result, note = _determine_result(sig, game)
            resolve_sports_signal(sig["id"], result, note)
            result_payload = _format_result(sig, result, note)
            post_signal(result_payload, "result")
            log.info("Sports result: %s — %s (%s)", sig.get("matchup"), result, note)
            resolved = True
            break

        # After 8 hours with no score match, expire the signal
        if not resolved and age_hrs > 8:
            resolve_sports_signal(sig["id"], "VOID", "Score data unavailable — auto-expired")
            result_payload = _format_result(sig, "VOID", "Score unavailable — could not verify outcome")
            post_signal(result_payload, "result")
            log.info("Signal expired without score data: %s", sig.get("matchup"))
