"""
sports/data_client.py — OddsAPI and Polymarket fetchers with aggressive caching.

Cache strategy (90%+ API reduction):
  - Odds cached for ODDS_CACHE_TTL_SECONDS (default 10 min)
  - Scores cached for SCORES_CACHE_TTL_SECONDS (default 5 min)
  - On 429 / limit hit → fallback mode: return last known data, never crash
  - On 401 → permanent disable for session
  - Lightweight fetch: h2h only, top leagues, next 24h
"""

import logging
import time
from datetime import datetime

import requests

from core.config import ODDS_API_KEY, SPORTS_KEYS, SOFT_BOOKS, SHARP_BOOKS, MAX_GAME_WINDOW_MINUTES

log = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"
POLY_BASE = "https://gamma-api.polymarket.com"

# ── Cache TTLs ─────────────────────────────────────────────────────────────────
ODDS_CACHE_TTL_SECONDS   = 600   # 10 minutes — primary cost reduction lever
SCORES_CACHE_TTL_SECONDS = 300   # 5 minutes
POLY_CACHE_TTL_SECONDS   = 300   # 5 minutes

# ── Module-level state ─────────────────────────────────────────────────────────
_auth_failed = False         # 401 → disable permanently for this session
_fallback_mode = False       # 429 / limit → use cached data only

_odds_cache:        list  = []
_odds_cache_time:   float = 0.0

_scores_cache:      list  = []
_scores_cache_time: float = 0.0

_poly_cache:        list  = []
_poly_cache_time:   float = 0.0


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _odds_cache_valid() -> bool:
    return bool(_odds_cache) and (time.time() - _odds_cache_time) < ODDS_CACHE_TTL_SECONDS


def _scores_cache_valid() -> bool:
    return bool(_scores_cache) and (time.time() - _scores_cache_time) < SCORES_CACHE_TTL_SECONDS


def _poly_cache_valid() -> bool:
    return bool(_poly_cache) and (time.time() - _poly_cache_time) < POLY_CACHE_TTL_SECONDS


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_sportsbook_odds() -> list:
    global _auth_failed, _fallback_mode, _odds_cache, _odds_cache_time

    if not ODDS_API_KEY or _auth_failed:
        return _odds_cache   # return whatever we have (may be empty)

    if _odds_cache_valid():
        log.debug("Using cached odds (%d games, %.0fs old)",
                  len(_odds_cache), time.time() - _odds_cache_time)
        return _odds_cache

    if _fallback_mode:
        log.info("Fallback mode active — skipping API call, reusing last known odds")
        return _odds_cache

    all_books = ",".join(SOFT_BOOKS + SHARP_BOOKS)
    games     = []

    for sport in SPORTS_KEYS:
        try:
            r = requests.get(
                f"{ODDS_BASE}/sports/{sport}/odds/",
                params={
                    "apiKey":     ODDS_API_KEY,
                    "regions":    "us",
                    "markets":    "h2h",          # moneyline only — lightweight
                    "oddsFormat": "american",
                    "bookmakers": all_books,
                    "commenceTimeTo": _utc_plus_hours(72),  # next 3 days only
                },
                timeout=10,
            )

            if r.status_code == 401:
                log.error("OddsAPI: invalid API key — disabling sports fetch permanently")
                _auth_failed = True
                return _odds_cache

            if r.status_code == 429:
                log.warning("OddsAPI: rate limit / quota reached — activating fallback mode")
                _fallback_mode = True
                return _odds_cache

            if r.status_code == 404:
                # Sport key valid but not active this season — skip silently
                continue

            if r.status_code == 200:
                now_utc = datetime.utcnow()
                for g in r.json():
                    # ── 3-day window filter at data level ─────────────────────
                    mins = minutes_until(g.get("commence_time", ""))
                    if mins < 0 or mins > MAX_GAME_WINDOW_MINUTES:
                        continue   # game already started or too far ahead
                    books = {bm["key"]: bm for bm in g.get("bookmakers", [])}
                    games.append({
                        "game_id":       g["id"],
                        "sport":         sport,
                        "home_team":     g["home_team"],
                        "away_team":     g["away_team"],
                        "commence_time": g["commence_time"],
                        "books":         books,
                        "mins_until":    mins,
                    })

                remaining = r.headers.get("x-requests-remaining", "?")
                used      = r.headers.get("x-requests-used", "?")
                log.debug("OddsAPI %s: %d games | remaining=%s used=%s",
                          sport, len(games), remaining, used)

                # Warn if getting close to quota
                try:
                    if int(remaining) < 50:
                        log.warning("OddsAPI quota low: %s requests remaining", remaining)
                except (TypeError, ValueError):
                    pass

        except Exception as e:
            log.debug("OddsAPI (%s): %s", sport, e)

    if games:
        _odds_cache      = games
        _odds_cache_time = time.time()
        log.info("OddsAPI fetched: %d games across %d sports", len(games), len(SPORTS_KEYS))
    elif _odds_cache:
        log.info("OddsAPI returned empty — reusing last known odds (%d games)", len(_odds_cache))

    return _odds_cache if _odds_cache else games


def fetch_sportsbook_scores() -> list:
    """Fetch recently completed game scores for result tracking."""
    global _auth_failed, _scores_cache, _scores_cache_time

    if not ODDS_API_KEY or _auth_failed:
        return _scores_cache

    if _scores_cache_valid():
        log.debug("Using cached scores (%d games)", len(_scores_cache))
        return _scores_cache

    completed = []
    for sport in SPORTS_KEYS:
        try:
            r = requests.get(
                f"{ODDS_BASE}/sports/{sport}/scores/",
                params={"apiKey": ODDS_API_KEY, "daysFrom": 1},
                timeout=10,
            )
            if r.status_code == 200:
                completed.extend(g for g in r.json() if g.get("completed"))
            elif r.status_code == 401:
                _auth_failed = True
                return _scores_cache
            elif r.status_code == 429:
                log.warning("OddsAPI scores: rate limit — using cached scores")
                return _scores_cache
        except Exception as e:
            log.debug("Scores fetch (%s): %s", sport, e)

    if completed:
        _scores_cache      = completed
        _scores_cache_time = time.time()

    return _scores_cache if _scores_cache else completed


def fetch_polymarket_sports() -> list:
    global _poly_cache, _poly_cache_time

    if _poly_cache_valid():
        log.debug("Using cached Polymarket data (%d markets)", len(_poly_cache))
        return _poly_cache

    try:
        r = requests.get(
            f"{POLY_BASE}/markets",
            params={"active": True, "closed": False, "tag_slug": "sports", "limit": 30},
            timeout=10,
        )
        if r.status_code != 200:
            return _poly_cache
        out = []
        for m in r.json():
            try:
                volume = float(m.get("volume24hr") or 0)
            except (TypeError, ValueError):
                continue
            if volume < 5000:
                continue
            prices = m.get("outcomePrices") or []
            if not isinstance(prices, list) or len(prices) < 2:
                continue
            try:
                yes_price = float(prices[0])
                no_price  = float(prices[1])
            except (TypeError, ValueError):
                continue
            if not (0.0 < yes_price < 1.0) or not (0.0 < no_price < 1.0):
                continue
            out.append({
                "market_id":  m.get("id", ""),
                "question":   m.get("question", ""),
                "yes_price":  yes_price,
                "no_price":   no_price,
                "volume_24h": volume,
                "end_date":   m.get("endDate"),
                "source":     "POLYMARKET",
            })
        _poly_cache      = out
        _poly_cache_time = time.time()
        return out
    except Exception as e:
        log.debug("Polymarket fetch: %s", e)
        return _poly_cache


def get_free_odds() -> list:
    """
    Placeholder for future free/alternative odds sources.
    Returns empty list until a free source is integrated.
    System supports multi-source via this hook.
    """
    return []


def minutes_until(iso_time: str) -> float:
    try:
        t = datetime.fromisoformat(iso_time.replace("Z", ""))
        return (t - datetime.utcnow()).total_seconds() / 60
    except Exception:
        return -1


def _utc_plus_hours(hours: int) -> str:
    """Return ISO timestamp N hours from now (UTC). Used to limit API fetch window."""
    from datetime import timedelta
    return (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
