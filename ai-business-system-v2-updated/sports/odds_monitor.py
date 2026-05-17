"""
sports/odds_monitor.py
======================
Sports betting engine — runs every 120 seconds (base interval).

Per cycle:
  1. Fetch odds (OddsAPI + Polymarket + Kalshi)
  2. Skip unchanged games (fingerprint cache)
  3. Run EV and arb math
  4. Claude validates + enriches
  5. Post to #ev-signals / #arb-signals + #free-signals teaser
  6. Check previously posted signals for results (game started → resolve)

API budget control:
  - Base loop: 120s → ~720 OddsAPI calls/day max (well under 666/day budget)
  - Dynamic throttle: after IDLE_CYCLES_THRESHOLD consecutive empty cycles,
    sleep doubles to SPORTS_LOOP_SECONDS * IDLE_BACKOFF_MULTIPLIER.
    Resets to base immediately when opportunities are found.
  - Fingerprint cache skips processing of unchanged odds, reducing Claude calls.
"""

import hashlib
import json
import logging
import time
from datetime import datetime

from core.config import (
    SOFT_BOOKS, SHARP_BOOKS,
    SPORTS_EV_MIN_EDGE, SPORTS_ARB_MIN_PCT, SPORTS_GAME_WINDOW,
    SPORTS_LOOP_SECONDS, IDLE_CYCLES_THRESHOLD, IDLE_BACKOFF_MULTIPLIER,
)
from core.claude_client import call_betting_brain, call_result_summary, validate_signal
from core.db import (
    save_sports_signal, get_active_sports_signals, resolve_sports_signal,
)
from discord.poster import post_signal, post_health_alert
from sports.betting_math import (
    implied_prob, remove_vig, ev_edge_pct, calc_arbitrage, best_per_side,
)
from sports.data_clients import (
    fetch_sportsbook_odds, fetch_polymarket_sports,
    fetch_kalshi_sports, minutes_until,
)

log = logging.getLogger(__name__)

_fingerprints: dict[str, str] = {}


def _fp(data: dict) -> str:
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _changed(game_id: str, books: dict) -> bool:
    fp = _fp(books)
    if _fingerprints.get(game_id) == fp:
        return False
    _fingerprints[game_id] = fp
    return True


# ── Opportunity scanners ──────────────────────────────────────────────────────

def _scan_ev(game: dict, mins: float) -> list:
    books   = {bm["key"]: bm for bm in game.get("bookmakers", [])} if "bookmakers" in game else game.get("books", {})
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"

    # Get Pinnacle no-vig true probs
    pinnacle = {}
    for bk in SHARP_BOOKS:
        entry = books.get(bk, {})
        mkts  = entry.get("markets", []) if isinstance(entry, dict) else []
        for mkt in mkts:
            if mkt.get("key") == "h2h":
                pinnacle = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                break

    if home not in pinnacle or away not in pinnacle:
        return []

    true_h, true_a = remove_vig(pinnacle[home], pinnacle[away])
    opportunities  = []

    for bk in SOFT_BOOKS:
        entry = books.get(bk, {})
        mkts  = entry.get("markets", []) if isinstance(entry, dict) else []
        for mkt in mkts:
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                name, odds = o["name"], o["price"]
                true_p = true_h if name == home else (true_a if name == away else None)
                if true_p is None:
                    continue
                edge = ev_edge_pct(true_p, odds)
                if edge >= SPORTS_EV_MIN_EDGE:
                    opportunities.append({
                        "type":          "POSITIVE_EV",
                        "source":        "SPORTSBOOK",
                        "matchup":       matchup,
                        "play":          f"{name} ML",
                        "book":          bk,
                        "odds":          str(odds),
                        "implied_prob":  round(implied_prob(odds) * 100, 2),
                        "true_prob":     round(true_p * 100, 2),
                        "edge":          round(edge, 2),
                        "arb_percentage": 0.0,
                        "profit_per_1000": 0.0,
                        "legs":          [],
                        "fees_applied":  False,
                        "confidence":    0,
                        "timing":        f"{mins:.0f} min to start",
                        "provisional":   False,
                    })
    return opportunities


def _scan_arb(game: dict, mins: float) -> list:
    books   = {bm["key"]: bm for bm in game.get("bookmakers", [])} if "bookmakers" in game else game.get("books", {})
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"
    best    = best_per_side(books, home, away, SOFT_BOOKS)
    bh, ba  = best[home], best[away]

    if not bh["book"] or not ba["book"] or bh["book"] == ba["book"]:
        return []

    arb = calc_arbitrage([
        {"book": bh["book"], "side": home, "odds_american": bh["odds"]},
        {"book": ba["book"], "side": away, "odds_american": ba["odds"]},
    ])

    if arb and arb["arb_percentage"] >= SPORTS_ARB_MIN_PCT:
        return [{
            "type":            "ARBITRAGE",
            "source":          "SPORTSBOOK",
            "matchup":         matchup,
            "play":            f"Arb: {home} + {away}",
            "book":            f"{bh['book']} / {ba['book']}",
            "odds":            f"{bh['odds']} / {ba['odds']}",
            "implied_prob":    arb["total_implied_pct"],
            "true_prob":       100.0,
            "edge":            arb["arb_percentage"],
            "arb_percentage":  arb["arb_percentage"],
            "profit_per_1000": arb["profit_per_1000"],
            "legs":            arb["legs"],
            "fees_applied":    False,
            "confidence":      0,
            "timing":          f"{mins:.0f} min to start",
            "provisional":     False,
        }]
    return []


def _scan_polymarket(poly: list, sb_games: list) -> list:
    # Build implied prob lookup from sportsbooks
    sb_implied: dict[str, float] = {}
    for g in sb_games:
        books = {bm["key"]: bm for bm in g.get("bookmakers", [])} if "bookmakers" in g else g.get("books", {})
        for entry in books.values():
            mkts = entry.get("markets", []) if isinstance(entry, dict) else []
            for mkt in mkts:
                if mkt.get("key") == "h2h":
                    for o in mkt.get("outcomes", []):
                        key = o["name"].lower().split()[-1]
                        sb_implied[key] = implied_prob(o["price"])

    opps = []
    for m in poly:
        q   = m["question"].lower()
        yes = m["yes_price"]
        matched = next((p for k, p in sb_implied.items() if k in q), None)
        if matched is None:
            continue
        div = abs(yes - matched) * 100
        if div < 4.0:
            continue
        play = (
            f"Buy YES ({yes:.2f}) — SB implies {matched:.1%}"
            if yes < matched else
            f"Buy NO ({1-yes:.2f}) — SB implies {1-matched:.1%}"
        )
        opps.append({
            "type":            "POSITIVE_EV",
            "source":          "CROSS_MARKET",
            "matchup":         m["question"][:80],
            "play":            play,
            "book":            "Polymarket",
            "odds":            f"{yes:.3f} (decimal)",
            "implied_prob":    round(yes * 100, 2),
            "true_prob":       round(matched * 100, 2),
            "edge":            round(div, 2),
            "arb_percentage":  0.0,
            "profit_per_1000": round(div * 10 * 0.98, 2),
            "legs":            [],
            "fees_applied":    True,
            "confidence":      0,
            "timing":          f"Closes: {m.get('end_date','N/A')}",
            "provisional":     False,
        })
    return opps


# ── Result resolution ─────────────────────────────────────────────────────────

def _check_results():
    """
    For active sports signals where the game has now started (timing expired),
    we can't automatically know the result without a scores API.
    This function marks them EXPIRED after 4 hours so they don't clog the DB.
    In production, integrate a scores API (e.g. The Odds API scores endpoint)
    to auto-resolve WIN/LOSS.
    """
    active = get_active_sports_signals()
    expired = []
    for sig in active:
        created = datetime.fromisoformat(sig["created_at"])
        age_hrs = (datetime.utcnow() - created).total_seconds() / 3600
        if age_hrs > 4:
            expired.append(sig)

    if expired:
        summaries = []
        for sig in expired:
            resolve_sports_signal(sig["id"], "VOID", "Auto-expired after 4h")
            summaries.append({
                "signal_id":    sig["id"],
                "matchup":      sig["matchup"],
                "play":         sig["play"],
                "odds":         sig["odds"],
                "result":       "VOID",
                "note":         "Expired — scores API not integrated",
                "result_kind":  "sports",
            })

        # Generate result summaries via Claude
        if summaries:
            result_data = call_result_summary(summaries, "sports")
            for r in result_data.get("results", []):
                r["result_kind"] = "sports"
                post_signal(r, "result")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_odds_monitor():
    log.info("Odds monitor started — base interval %ds", SPORTS_LOOP_SECONDS)

    idle_cycles = 0   # consecutive cycles with zero opportunities found

    while True:
        loop_start    = time.time()
        opportunities = []

        try:
            sb_games   = fetch_sportsbook_odds()
            poly_mkts  = fetch_polymarket_sports()
            _kalshi    = fetch_kalshi_sports()

            data_quality = "GOOD" if sb_games else "STALE"

            for game in sb_games:
                mins = minutes_until(game["commence_time"])
                # Always allow all games (no time restriction)
                if mins is None:
                    continue

                books = {bm["key"]: bm for bm in game.get("bookmakers", [])} \
                    if "bookmakers" in game else game.get("books", {})
                # Skip aggressively if odds are unchanged — no API value in reprocessing
                if not _changed(game["game_id"], books):
                    continue

                opportunities.extend(_scan_ev(game, mins))
                opportunities.extend(_scan_arb(game, mins))

            opportunities.extend(_scan_polymarket(poly_mkts, sb_games))

            if opportunities:
                # Reset idle counter — active market, use base interval
                idle_cycles = 0

                # Deduplicate: keep highest-edge entry per matchup+book+play combo.
                # Collapses 100+ raw opportunities down to unique actionable entries.
                seen: dict[str, dict] = {}
                for opp in opportunities:
                    key = f"{opp.get('type')}|{opp.get('matchup')}|{opp.get('book')}|{opp.get('play')}"
                    existing = float(seen[key].get("edge", seen[key].get("arb_percentage", 0))) if key in seen else -1
                    this     = float(opp.get("edge", opp.get("arb_percentage", 0)))
                    if this > existing:
                        seen[key] = opp
                deduped = list(seen.values())

                # Hard cap at 10 — ensures Claude output stays small and parseable.
                # call_betting_brain also sorts and caps at 20 internally; this
                # cap comes first and is the stronger constraint.
                limited = sorted(
                    deduped,
                    key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
                    reverse=True,
                )[:10]

                log.info("Opportunities raw: %d → deduped: %d → capped: %d → sending to Claude",
                         len(opportunities), len(deduped), len(limited))
                result = call_betting_brain(limited, data_quality)

                for signal in result.get("signals", []):
                    check = validate_signal(signal)
                    if not check.get("approved", True):
                        log.info("Signal rejected: %s", check.get("reason"))
                        continue
                    signal["confidence"] = check.get("adjusted_confidence", signal.get("confidence", 5))
                    save_sports_signal(signal)
                    post_signal(signal, "sports")
            else:
                idle_cycles += 1
                log.debug("No opportunities this cycle (idle streak: %d)", idle_cycles)

            # Check for results / expirations
            _check_results()

        except Exception as e:
            log.error("odds_monitor error: %s", e)
            try:
                post_health_alert("odds_monitor", str(e))
            except Exception:
                pass

        # ── Dynamic throttle ──────────────────────────────────────────────────
        # If idle long enough, back off to conserve API budget.
        # Snaps back to base interval the moment an opportunity is found.
        if idle_cycles >= IDLE_CYCLES_THRESHOLD:
            sleep_interval = SPORTS_LOOP_SECONDS * IDLE_BACKOFF_MULTIPLIER
        else:
            sleep_interval = SPORTS_LOOP_SECONDS

        elapsed   = time.time() - loop_start
        sleep_for = max(0.0, sleep_interval - elapsed)
        time.sleep(sleep_for)
