"""
sports/odds_monitor.py — Sports betting engine.

Key behaviors:
  - Loop every 120s. API fetch every 10 min (every 5 cycles).
  - Claude called every 3rd qualifying cycle, only if best edge >= 3.5%.
  - Max 6 opportunities sent to Claude per call.
  - tracked_bets: signals we posted — only these are result-tracked.
  - processed_results: dedup set — each matchup resolved once per day.
  - daily_bets: ordered list of {matchup, play, result} for recap/content.
  - 3-way markets rejected before arb processing.
  - Daily recap posted at 23:00 UTC. Parlay generated at 12:00 UTC.
  - All daily state resets on calendar day change.
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
    def is_two_way_market(*args, **kwargs):
        return True
from sports.data_client import (
    fetch_sportsbook_odds, fetch_sportsbook_scores, fetch_polymarket_sports, minutes_until,
)
from sports.results_tracker import record_result
from sports.performance_tracker import record_bet_result
from sports.parlay import generate_daily_parlay

log = logging.getLogger(__name__)

# ── In-memory state ────────────────────────────────────────────────────────────
_fingerprints:      dict[str, str] = {}    # game_id → MD5 of books data
_tracked_bets:      set            = set() # matchups for signals we posted
_processed_results: set            = set() # matchups already resolved today
_daily_bets:        list           = []    # [{matchup, play, result}] for recap
_last_state_date:   date | None    = None


def _reset_daily_state():
    """Clear all per-day tracking on calendar rollover."""
    global _tracked_bets, _processed_results, _daily_bets, _last_state_date
    _tracked_bets.clear()
    _processed_results.clear()
    _daily_bets.clear()
    _last_state_date = date.today()
    log.info("Daily tracking state reset for %s", _last_state_date)


def _check_daily_reset():
    """Call at the top of each cycle. Resets state on new day."""
    if _last_state_date != date.today():
        _reset_daily_state()


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
                        "type":         "POSITIVE_EV",
                        "source":       "SPORTSBOOK",
                        "matchup":      matchup,
                        "play":         f"{name} ML",
                        "book":         bk,
                        "odds":         str(odds),
                        "implied_prob": round(implied_prob(odds) * 100, 2),
                        "true_prob":    round(true_p * 100, 2),
                        "edge":         round(edge, 2),
                        "timing":       f"{mins:.0f} min to start",
                        "confidence":   0,
                    })
    return opps


def _scan_arb(game: dict) -> list:
    books   = game["books"]
    home    = game["home_team"]
    away    = game["away_team"]
    matchup = f"{away} @ {home}"
    mins    = minutes_until(game["commence_time"])

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
        q       = m["question"].lower()
        yes     = m["yes_price"]
        matched = next((p for k, p in sb_implied.items() if k in q), None)
        if matched is None:
            continue
        div = abs(yes - matched) * 100
        if div < 4.0:
            continue
        opps.append({
            "type":         "POSITIVE_EV",
            "source":       "CROSS_MARKET",
            "matchup":      m["question"][:80],
            "play":         f"Buy {'YES' if yes < matched else 'NO'} on Polymarket",
            "book":         "Polymarket",
            "odds":         f"{yes:.3f}",
            "implied_prob": round(yes * 100, 2),
            "true_prob":    round(matched * 100, 2),
            "edge":         round(div, 2),
            "timing":       f"Closes: {m.get('end_date','N/A')}",
            "confidence":   0,
        })
    return opps


# ── Signal validation ──────────────────────────────────────────────────────────

def _local_validate(signal: dict) -> bool:
    try:
        edge = float(signal.get("edge", 0))
    except (TypeError, ValueError):
        log.info("Signal rejected — edge not numeric")
        return False
    try:
        timing_raw = str(signal.get("timing", "0")).split()[0]
        timing = int(float(timing_raw))
    except (TypeError, ValueError, IndexError):
        timing = -1
    if edge <= 0 or edge > 15:
        log.info("Signal rejected — edge %.2f%% out of range [1,15]", edge)
        return False
    if timing <= 0 or timing > 1440:
        log.info("Signal rejected — timing %d min invalid", timing)
        return False
    return True


# ── Result resolution ──────────────────────────────────────────────────────────

def _resolve_results():
    """
    Match active signals to completed game scores.
    Only resolves signals in tracked_bets (signals we actually posted).
    Each matchup is processed exactly once per day (processed_results dedup).
    """
    active = get_active_sports_signals()
    if not active:
        return

    scores = fetch_sportsbook_scores()
    if not scores:
        return

    for sig in active:
        matchup     = (sig.get("matchup") or "").lower()
        matchup_key = matchup.strip()

        # Skip: already resolved today
        if matchup_key in _processed_results:
            continue

        # Skip: we never posted a signal for this game
        if matchup_key not in _tracked_bets:
            continue

        for game in scores:
            home = (game.get("home_team") or "").lower()
            away = (game.get("away_team") or "").lower()
            if home not in matchup and away not in matchup:
                continue

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

            bet_team = None
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

            # Mark resolved — prevents duplicate processing
            _processed_results.add(matchup_key)

            resolve_sports_signal(sig["id"], result, note)
            record_result(result)
            record_bet_result(result)
            log.info("Result: %s — %s (%s)", sig.get("matchup"), result, note)

            # Record in daily bets list
            _daily_bets.append({
                "matchup": sig.get("matchup", ""),
                "play":    sig.get("play", ""),
                "result":  result,
                "edge":    float(sig.get("edge_pct", sig.get("edge", 0))),
                "is_arb":  (sig.get("signal_type") or "").upper() == "ARBITRAGE",
            })

            if result == "WIN" and not (sig.get("signal_type") or "").upper() == "ARBITRAGE":
                record_win(
                    play = sig.get("play", ""),
                    edge = float(sig.get("edge_pct", sig.get("edge", 0))),
                )
            break


# ── Daily results post ─────────────────────────────────────────────────────────

def post_daily_results():
    """
    Post the full list of today's bets and results to #sports-results.
    """
    from sports.performance_tracker import get_performance
    perf     = get_performance()
    bk_pct   = perf.get("bankroll_pct", 0.0)
    wins     = sum(1 for b in _daily_bets if b["result"] == "WIN")
    losses   = sum(1 for b in _daily_bets if b["result"] == "LOSS")

    if not _daily_bets:
        log.debug("post_daily_results: no bets today — skipping")
        return

    sep   = "─────────────────────────────"
    lines = "\n".join(
        f"{'✅' if b['result'] == 'WIN' else '❌'} {b['matchup']}"
        for b in _daily_bets
    )
    today = date.today().strftime("%B %d, %Y")
    bk_str = f"+{bk_pct:.1f}%" if bk_pct >= 0 else f"{bk_pct:.1f}%"

    message = (
        f"{sep}\n"
        f"📊 **DAILY RESULTS — {today}**\n\n"
        f"{lines}\n\n"
        f"Record: **{wins}–{losses}**\n"
        f"Bankroll: **{bk_str}**\n"
        f"{sep}"
    )

    send(WEBHOOKS.get("sports_results", ""), message)
    log.info("Daily results posted: %dW %dL bankroll %s", wins, losses, bk_str)


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_odds_monitor():
    log.info("Odds monitor started — loop %ds, API fetch every 10 min, Claude every %d cycles",
             SPORTS_LOOP_SECONDS, SPORTS_CLAUDE_EVERY_N_CYCLES)

    cycle_count        = 0
    idle_cycles        = 0
    ODDS_FETCH_EVERY_N = 5
    _last_sb_games     = []

    while True:
        loop_start     = time.time()
        opportunities  = []
        cycle_count   += 1
        sleep_interval = SPORTS_LOOP_SECONDS   # always defined before try

        try:
            _check_daily_reset()

            # ── API fetch gate ────────────────────────────────────────────────
            if cycle_count % ODDS_FETCH_EVERY_N == 0 or cycle_count == 1:
                sb_games       = fetch_sportsbook_odds()
                _last_sb_games = sb_games
                log.info("Odds fetched: %d games (cycle %d)", len(sb_games), cycle_count)
            else:
                sb_games = _last_sb_games
                log.debug("Skipping API call — cached games (cycle %d)", cycle_count)

            poly_mkts    = fetch_polymarket_sports()
            data_quality = "GOOD" if sb_games else "STALE"

            for game in sb_games:
                if not _changed(game["game_id"], game["books"]):
                    continue
                opportunities.extend(_scan_ev(game))
                opportunities.extend(_scan_arb(game))

            opportunities.extend(_scan_polymarket(poly_mkts, sb_games))

            if opportunities:
                idle_cycles = 0

                if cycle_count % SPORTS_CLAUDE_EVERY_N_CYCLES != 0:
                    log.debug("Claude cooldown (cycle %d)", cycle_count)
                else:
                    best_edge = max(
                        float(o.get("edge", o.get("arb_percentage", 0)))
                        for o in opportunities
                    )
                    if best_edge < SPORTS_MIN_EDGE_TO_CALL:
                        log.debug("Claude skipped — best edge %.2f%%", best_edge)
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

                            # Track this matchup for result resolution
                            matchup_key = (signal.get("matchup") or "").lower().strip()
                            if matchup_key:
                                _tracked_bets.add(matchup_key)
            else:
                idle_cycles += 1
                log.debug("No opportunities (idle streak: %d)", idle_cycles)

            _resolve_results()

            now_utc = datetime.utcnow()

            # Daily parlay at 12:00 UTC
            if now_utc.hour == 12 and opportunities:
                try:
                    generate_daily_parlay(opportunities)
                except Exception as e:
                    log.error("Parlay generation error: %s", e)

            # End-of-day recap at 23:00 UTC
            if now_utc.hour == 23:
                post_daily_results()
                try:
                    post_daily_recap(_daily_bets)
                except Exception:
                    pass

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


def get_daily_bets() -> list:
    """Return today's resolved bets list for content generation."""
    return _daily_bets.copy()
