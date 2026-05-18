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
from datetime import datetime, date

from core.config import (
    SOFT_BOOKS, SHARP_BOOKS,
    SPORTS_EV_MIN_EDGE, SPORTS_ARB_MIN_PCT, SPORTS_GAME_WINDOW,
    SPORTS_LOOP_SECONDS, IDLE_CYCLES_THRESHOLD, IDLE_BACKOFF_MULTIPLIER,
)
from core.claude_client import call_betting_brain, validate_signal
from core.db import (
    save_sports_signal, get_active_sports_signals, resolve_sports_signal,
)
from discord.poster import post_signal, post_health_alert, send, WEBHOOKS
from sports.betting_math import (
    implied_prob, remove_vig, ev_edge_pct, calc_arbitrage, best_per_side,
)
from sports.results_tracker import resolve_completed_sports_signals
from sports.data_clients import (
    fetch_sportsbook_odds, fetch_polymarket_sports,
    fetch_kalshi_sports, minutes_until,
)

log = logging.getLogger(__name__)

_fingerprints: dict[str, str] = {}

# ── Daily recap accumulator ────────────────────────────────────────────────────
# Accumulates EV-only results throughout the day. Posted once at end of day
# (when UTC hour rolls to 23) then cleared. Arbitrage excluded per spec.

_daily_results: list[dict]  = []
_last_recap_date: date | None = None


def _record_ev_result(play: str, result: str, edge: float):
    """Add a resolved +EV signal to today's recap buffer. Arb excluded."""
    if result in ("WIN", "LOSS"):   # skip PUSH and VOID
        _daily_results.append({"play": play, "result": result, "edge": edge})


def _post_daily_recap():
    """
    Post the daily results summary to #sports-results and #results-preview.
    Called once per day. Only posts if there is meaningful activity.
    """
    global _daily_results, _last_recap_date

    if not _daily_results:
        log.debug("No EV results today — skipping daily recap")
        _last_recap_date = date.today()
        return

    wins   = [r for r in _daily_results if r["result"] == "WIN"]
    losses = [r for r in _daily_results if r["result"] == "LOSS"]

    notable = sorted(_daily_results, key=lambda r: r["edge"], reverse=True)[:5]
    plays_text = "\n".join(
        f"{'✅' if r['result'] == 'WIN' else '❌'} {r['play']} → {r['result']}"
        for r in notable
    )

    sep     = "─────────────────────────────"
    today   = date.today().strftime("%B %d, %Y")
    summary = (
        f"{sep}\n"
        f"📊 **DAILY RESULTS — {today}**\n"
        f"✅ Wins: **{len(wins)}**  |  ❌ Losses: **{len(losses)}**\n\n"
        f"📈 Notable +EV plays:\n{plays_text}\n"
        f"{sep}"
    )
    preview = (
        f"📊 **Daily recap is live** — {len(wins)}W / {len(losses)}L today.\n"
        f"Full breakdown in #sports-results.\n"
        f"🔓 Unlock signals: https://whop.com/the-sharp-margin"
    )

    send(WEBHOOKS.get("sports_results", ""), summary)
    send(WEBHOOKS.get("results_preview", ""), preview)
    log.info("Daily recap posted: %dW %dL", len(wins), len(losses))

    _daily_results     = []
    _last_recap_date   = date.today()


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


def _is_two_way_market(books: dict, home: str, away: str) -> bool:
    """
    Return True only if every book's h2h market has exactly 2 outcomes
    (home + away). Rejects 3-way markets (soccer Draw, etc.).
    """
    for bk in SOFT_BOOKS:
        if bk not in books:
            continue
        entry = books.get(bk, {})
        mkts  = entry.get("markets", []) if isinstance(entry, dict) else []
        for mkt in mkts:
            if mkt.get("key") != "h2h":
                continue
            outcomes = mkt.get("outcomes", [])
            names    = [o["name"].lower() for o in outcomes]
            # Reject if draw / 3-way outcome present
            if len(outcomes) != 2:
                return False
            if any(n in ("draw", "tie", "push") for n in names):
                return False
    return True


def _scan_arb(game: dict, mins: float) -> list:
    books   = {bm["key"]: bm for bm in game.get("bookmakers", [])} if "bookmakers" in game else game.get("books", {})
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"

    # Only process standard 2-way moneylines — no draws, no 3-way markets
    if not _is_two_way_market(books, home, away):
        return []

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


# ── Main loop ─────────────────────────────────────────────────────────────────

def _resolve_and_accumulate():
    """
    Check for completed game results. For +EV signals, feed WIN/LOSS into the
    daily recap buffer instead of posting individual result messages.
    Arbitrage results are intentionally excluded from the recap.
    """
    from core.db import get_active_sports_signals, resolve_sports_signal
    from sports.results_tracker import (
        fetch_completed_scores, _teams_match, _determine_result,
    )

    active = get_active_sports_signals()
    if not active:
        return

    completed_games = fetch_completed_scores()
    if not completed_games:
        return

    for sig in active:
        for game in completed_games:
            if not _teams_match(sig.get("matchup", ""), game):
                continue

            result, note = _determine_result(sig, game)
            if result == "VOID":
                break

            resolve_sports_signal(sig["id"], result, note)
            log.info("Result resolved: %s — %s", sig.get("matchup"), result)

            # Feed only +EV results into daily recap (not arbitrage)
            sig_type = (sig.get("signal_type") or "").upper()
            if sig_type != "ARBITRAGE":
                _record_ev_result(
                    play   = sig.get("play", sig.get("matchup", "")),
                    result = result,
                    edge   = float(sig.get("edge_pct", sig.get("edge", 0))),
                )
            break

def run_odds_monitor():
    log.info("Odds monitor started — base interval %ds", SPORTS_LOOP_SECONDS)

    idle_cycles        = 0
    cycle_count        = 0     # total cycles elapsed
    CLAUDE_CALL_EVERY_N = 3    # call Claude at most once every N cycles
    CLAUDE_MIN_EDGE     = 3.5  # minimum top-opportunity edge to justify a Claude call

    while True:
        loop_start    = time.time()
        opportunities = []
        cycle_count  += 1

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
                idle_cycles = 0

                # ── Cooldown: skip Claude on non-qualifying cycles ────────────
                if cycle_count % CLAUDE_CALL_EVERY_N != 0:
                    log.debug("Claude cooldown — cycle %d (calls every %d cycles)",
                              cycle_count, CLAUDE_CALL_EVERY_N)
                else:
                    # ── Edge threshold: only call if best opp clears minimum ──
                    best_edge = max(
                        float(o.get("edge", o.get("arb_percentage", 0)))
                        for o in opportunities
                    )
                    if best_edge < CLAUDE_MIN_EDGE:
                        log.debug("Claude skipped — best edge %.2f%% below %.1f%% threshold",
                                  best_edge, CLAUDE_MIN_EDGE)
                    else:
                        # ✅ SORT AND LIMIT INPUT — top 8 by edge only
                        limited_opps = sorted(
                            opportunities,
                            key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
                            reverse=True,
                        )[:8]
                        log.info("Sending %d opportunities to Claude (best edge: %.2f%%)",
                                 len(limited_opps), best_edge)
                        result = call_betting_brain(limited_opps, data_quality)

                        for signal in result.get("signals", []):
                            check = validate_signal(signal)
                            if not check.get("approved", True):
                                log.info("Signal rejected: %s", check.get("reason"))
                                continue
                            signal["confidence"] = check.get("adjusted_confidence",
                                                             signal.get("confidence", 5))
                            save_sports_signal(signal)
                            post_signal(signal, "sports")
                            try:
                                from content.publisher import whop_post_sports_signal
                                whop_post_sports_signal(
                                    matchup = signal.get("matchup", ""),
                                    play    = signal.get("play", ""),
                                    edge    = float(signal.get("edge", 0)),
                                    book    = signal.get("book", ""),
                                )
                            except Exception:
                                pass
            else:
                idle_cycles += 1
                log.debug("No opportunities this cycle (idle streak: %d)", idle_cycles)

            # Resolve completed game results — feed EV results into daily recap buffer.
            # Individual result posts are suppressed; recap posts once at end of day.
            _resolve_and_accumulate()

            # Trigger daily recap once per day at 23:00 UTC
            now_utc = datetime.utcnow()
            if (now_utc.hour == 23
                    and (_last_recap_date is None or _last_recap_date < date.today())):
                _post_daily_recap()

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
