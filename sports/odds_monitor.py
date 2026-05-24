"""
sports/odds_monitor.py — Sports betting engine.

Loop: every 120s (base). Claude called every 3rd cycle only if best edge >= 3.5%.
Max 6 opportunities sent to Claude per call.
Results tracked via OddsAPI scores. Daily recap posted at 23:00 UTC.
Individual result posts suppressed — recap only.
3-way markets (soccer draw etc.) filtered out before arb processing.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, date

from core.config import (
    SOFT_BOOKS, SHARP_BOOKS,
    SPORTS_EV_MIN_EDGE, SPORTS_ARB_MIN_PCT,
    SPORTS_LOOP_SECONDS,
    SPORTS_CLAUDE_EVERY_N_CYCLES, SPORTS_MIN_EDGE_TO_CALL, MAX_OPPS_TO_CLAUDE,
)
from core.claude_client import call_betting_brain, validate_signal
from core.db import save_sports_signal, get_active_sports_signals, resolve_sports_signal
from discord.poster import post_signal, post_health_alert, post_daily_recap, record_win, send, WEBHOOKS
from sports.betting_math import (
    implied_prob, remove_vig, ev_edge_pct, calc_arbitrage, best_per_side,
)
try:
    from sports.betting_math import is_two_way_market
except ImportError:
    # Fallback: always allow markets (safe degradation — arb filter disabled)
    def is_two_way_market(*args, **kwargs):  # type: ignore[misc]
        return True
from sports.data_client import (
    fetch_sportsbook_odds, fetch_sportsbook_scores, fetch_polymarket_sports, minutes_until,
)
from sports.results_tracker import record_result

log = logging.getLogger(__name__)

# ── In-memory state ────────────────────────────────────────────────────────────
_fingerprints: dict[str, str] = {}     # game_id → MD5 of books data
_ev_results:   list[dict]     = []     # daily accumulator for recap
_last_recap:   date | None    = None


def _fp(data: dict) -> str:
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _changed(game_id: str, books: dict) -> bool:
    fp = _fp(books)
    if _fingerprints.get(game_id) == fp:
        return False
    _fingerprints[game_id] = fp
    return True


# ── Opportunity scanners ───────────────────────────────────────────────────────

def _scan_ev(game: dict) -> list:
    books   = game["books"]
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"
    mins    = minutes_until(game["commence_time"])

    # Get Pinnacle no-vig true probs
    pinnacle_h2h = {}
    for bk in SHARP_BOOKS:
        entry = books.get(bk, {})
        for mkt in (entry.get("markets", []) if isinstance(entry, dict) else []):
            if mkt.get("key") == "h2h":
                pinnacle_h2h = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                break

    if home not in pinnacle_h2h or away not in pinnacle_h2h:
        return []

    true_h, true_a = remove_vig(pinnacle_h2h[home], pinnacle_h2h[away])
    opps = []

    for bk in SOFT_BOOKS:
        entry = books.get(bk, {})
        for mkt in (entry.get("markets", []) if isinstance(entry, dict) else []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                name, odds = o["name"], o["price"]
                true_p = true_h if name == home else (true_a if name == away else None)
                if true_p is None:
                    continue
                edge = ev_edge_pct(true_p, odds)
                if edge >= SPORTS_EV_MIN_EDGE:
                    opps.append({
                        "type":          "POSITIVE_EV",
                        "source":        "SPORTSBOOK",
                        "matchup":       matchup,
                        "play":          f"{name} ML",
                        "book":          bk,
                        "odds":          str(odds),
                        "implied_prob":  round(implied_prob(odds) * 100, 2),
                        "true_prob":     round(true_p * 100, 2),
                        "edge":          round(edge, 2),
                        "timing":        f"{mins:.0f} min to start",
                        "confidence":    0,
                    })
    return opps


def _scan_arb(game: dict) -> list:
    books   = game["books"]
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"
    mins    = minutes_until(game["commence_time"])

    # 2-way moneyline only — reject all 3-way / draw markets
    if not is_two_way_market(books, SOFT_BOOKS):
        return []

    best = best_per_side(books, home, away, SOFT_BOOKS)
    bh, ba = best[home], best[away]

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
            "edge":            arb["arb_percentage"],
            "arb_percentage":  arb["arb_percentage"],
            "profit_per_1000": arb["profit_per_1000"],
            "legs":            arb["legs"],
            "fees_applied":    False,
            "timing":          f"{mins:.0f} min to start",
            "confidence":      0,
        }]
    return []


def _scan_polymarket(poly: list, sb_games: list) -> list:
    sb_implied: dict[str, float] = {}
    for g in sb_games:
        for entry in g.get("books", {}).values():
            if not isinstance(entry, dict):
                continue
            for mkt in entry.get("markets", []):
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
        opps.append({
            "type":     "POSITIVE_EV",
            "source":   "CROSS_MARKET",
            "matchup":  m["question"][:80],
            "play":     f"Buy {'YES' if yes < matched else 'NO'} on Polymarket",
            "book":     "Polymarket",
            "odds":     f"{yes:.3f}",
            "implied_prob": round(yes * 100, 2),
            "true_prob":    round(matched * 100, 2),
            "edge":         round(div, 2),
            "timing":   f"Closes: {m.get('end_date','N/A')}",
            "confidence": 0,
        })
    return opps


# ── Result resolution ──────────────────────────────────────────────────────────

def _local_validate(signal: dict) -> bool:
    """
    Hard numeric gate applied before posting any signal.
    Rejects signals with impossible or suspicious values.
    Runs in addition to Claude's validation — catches data errors early.
    """
    try:
        edge = float(signal.get("edge", 0))
    except (TypeError, ValueError):
        log.info("Signal rejected (local validate) — edge not numeric")
        return False

    try:
        # Timing field format: "240 min to start" or "N/A"
        timing_raw = str(signal.get("timing", "0")).split()[0]
        timing = int(float(timing_raw))
    except (TypeError, ValueError, IndexError):
        timing = -1   # treat unparseable timing as invalid

    # Edge bounds: must be between 1% and 15%
    if edge <= 0 or edge > 15:
        log.info("Signal rejected (local validate) — edge %.2f%% out of range [1,15]", edge)
        return False

    # Timing: must be positive and under 1440 minutes (24 hours)
    if timing <= 0 or timing > 1440:
        log.info("Signal rejected (local validate) — timing %d min invalid", timing)
        return False

    return True
    """Check active signals against completed scores. Feed wins into daily recap."""
    active = get_active_sports_signals()
    if not active:
        return

    scores = fetch_sportsbook_scores()
    if not scores:
        return

    for sig in active:
        matchup = (sig.get("matchup") or "").lower()
        for game in scores:
            home = (game.get("home_team") or "").lower()
            away = (game.get("away_team") or "").lower()
            if home not in matchup and away not in matchup:
                continue

            # Determine result
            game_scores = game.get("scores") or []
            score_map   = {}
            for s in game_scores:
                try:
                    score_map[(s.get("name") or "").lower()] = float(s["score"])
                except (KeyError, TypeError, ValueError):
                    pass

            if len(score_map) < 2:
                break

            play      = (sig.get("play") or "").upper()
            home_s    = score_map.get(home, 0)
            away_s    = score_map.get(away, 0)
            result    = "VOID"
            note      = "Could not determine result"

            # Moneyline resolution
            bet_team  = None
            if home in play.lower():
                bet_team = home
            elif away in play.lower():
                bet_team = away

            if bet_team:
                bet_score = score_map.get(bet_team, 0)
                opp_score = away_s if bet_team == home else home_s
                if bet_score > opp_score:
                    result = "WIN"
                    note   = f"{bet_team.title()} won {bet_score:.0f}–{opp_score:.0f}"
                elif bet_score < opp_score:
                    result = "LOSS"
                    note   = f"{bet_team.title()} lost {bet_score:.0f}–{opp_score:.0f}"
                else:
                    result = "PUSH"
                    note   = f"Tied {bet_score:.0f}–{opp_score:.0f}"

            if result == "VOID":
                break

            resolve_sports_signal(sig["id"], result, note)
            log.info("Result: %s — %s (%s)", sig.get("matchup"), result, note)
            record_result(result)   # update daily win/loss counter

            # Only feed +EV (not arb) into daily recap
            if (sig.get("signal_type") or "").upper() != "ARBITRAGE":
                _ev_results.append({
                    "play":   sig.get("play", ""),
                    "result": result,
                    "edge":   float(sig.get("edge_pct", sig.get("edge", 0))),
                })
                if result == "WIN":
                    record_win(
                        play = sig.get("play", ""),
                        edge = float(sig.get("edge_pct", sig.get("edge", 0))),
                    )
            break


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_odds_monitor():
    global _ev_results, _last_recap

    log.info("Odds monitor started — base interval %ds, Claude every %d cycles",
             SPORTS_LOOP_SECONDS, SPORTS_CLAUDE_EVERY_N_CYCLES)

    cycle_count = 0
    idle_cycles = 0

    while True:
        loop_start    = time.time()
        opportunities = []
        cycle_count  += 1

        try:
            sb_games   = fetch_sportsbook_odds()
            poly_mkts  = fetch_polymarket_sports()
            data_quality = "GOOD" if sb_games else "STALE"

            for game in sb_games:
                if not _changed(game["game_id"], game["books"]):
                    continue
                opportunities.extend(_scan_ev(game))
                opportunities.extend(_scan_arb(game))

            opportunities.extend(_scan_polymarket(poly_mkts, sb_games))

            if opportunities:
                idle_cycles = 0

                # ── Claude cooldown gate ──────────────────────────────────────
                if cycle_count % SPORTS_CLAUDE_EVERY_N_CYCLES != 0:
                    log.debug("Claude cooldown (cycle %d)", cycle_count)
                else:
                    best_edge = max(
                        float(o.get("edge", o.get("arb_percentage", 0)))
                        for o in opportunities
                    )
                    if best_edge < SPORTS_MIN_EDGE_TO_CALL:
                        log.debug("Claude skipped — best edge %.2f%% below threshold", best_edge)
                    else:
                        limited = sorted(
                            opportunities,
                            key=lambda o: float(o.get("edge", o.get("arb_percentage", 0))),
                            reverse=True,
                        )[:MAX_OPPS_TO_CLAUDE]

                        log.info("Sending %d opps to Claude (best edge: %.2f%%)",
                                 len(limited), best_edge)
                        result = call_betting_brain(limited, data_quality)

                        for signal in result.get("signals", []):
                            # Hard numeric gate — reject impossible values first
                            if not _local_validate(signal):
                                continue
                            check = validate_signal(signal)
                            if not check.get("approved", True):
                                log.info("Signal rejected (Claude): %s", check.get("reason"))
                                continue
                            signal["confidence"] = check.get(
                                "adjusted_confidence", signal.get("confidence", 5)
                            )
                            save_sports_signal(signal)
                            post_signal(signal, "sports")
            else:
                idle_cycles += 1
                log.debug("No opportunities (idle streak: %d)", idle_cycles)

            # Resolve completed results
            _resolve_results()

            # Daily recap at 23:00 UTC
            now_utc = datetime.utcnow()
            if now_utc.hour == 23 and (_last_recap is None or _last_recap < date.today()):
                post_daily_recap(_ev_results)
                _ev_results  = []
                _last_recap  = date.today()

            # Dynamic sleep: back off when idle
            if idle_cycles >= 3:
                sleep_interval = SPORTS_LOOP_SECONDS * 2
            else:
                sleep_interval = SPORTS_LOOP_SECONDS

        except Exception as e:
            log.error("odds_monitor error: %s", e)
            try:
                post_health_alert("odds_monitor", str(e))
            except Exception:
                pass

        elapsed   = time.time() - loop_start
        sleep_for = max(0.0, sleep_interval - elapsed)
        time.sleep(sleep_for)
