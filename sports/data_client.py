"""sports/data_client.py — OddsAPI and Polymarket fetchers."""

import logging
from datetime import datetime

import requests

from core.config import ODDS_API_KEY, SPORTS_KEYS, SOFT_BOOKS, SHARP_BOOKS

log = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"
POLY_BASE = "https://gamma-api.polymarket.com"

_auth_failed = False   # set True on 401 — stops repeated error spam


def fetch_sportsbook_odds() -> list:
    global _auth_failed
    if not ODDS_API_KEY or _auth_failed:
        return []

    all_books = ",".join(SOFT_BOOKS + SHARP_BOOKS)
    games     = []

    for sport in SPORTS_KEYS:
        try:
            r = requests.get(
                f"{ODDS_BASE}/sports/{sport}/odds/",
                params={
                    "apiKey":     ODDS_API_KEY,
                    "regions":    "us",
                    "markets":    "h2h",
                    "oddsFormat": "american",
                    "bookmakers": all_books,
                },
                timeout=10,
            )
            if r.status_code == 401:
                log.error("OddsAPI: invalid API key — disabling sports fetch")
                _auth_failed = True
                return []
            if r.status_code == 200:
                for g in r.json():
                    books = {bm["key"]: bm for bm in g.get("bookmakers", [])}
                    games.append({
                        "game_id":       g["id"],
                        "sport":         sport,
                        "home_team":     g["home_team"],
                        "away_team":     g["away_team"],
                        "commence_time": g["commence_time"],
                        "books":         books,
                    })
            elif r.status_code == 429:
                log.warning("OddsAPI rate limit hit")
                break
        except Exception as e:
            log.debug("OddsAPI (%s): %s", sport, e)

    return games


def fetch_sportsbook_scores() -> list:
    """Fetch recently completed game scores for result tracking."""
    global _auth_failed
    if not ODDS_API_KEY or _auth_failed:
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
                completed.extend(g for g in r.json() if g.get("completed"))
            elif r.status_code == 401:
                _auth_failed = True
                return []
        except Exception as e:
            log.debug("Scores fetch (%s): %s", sport, e)
    return completed


def fetch_polymarket_sports() -> list:
    try:
        r = requests.get(
            f"{POLY_BASE}/markets",
            params={"active": True, "closed": False, "tag_slug": "sports", "limit": 30},
            timeout=10,
        )
        if r.status_code != 200:
            return []
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
                "market_id": m.get("id", ""),
                "question":  m.get("question", ""),
                "yes_price": yes_price,
                "no_price":  no_price,
                "volume_24h": volume,
                "end_date":  m.get("endDate"),
                "source":    "POLYMARKET",
            })
        return out
    except Exception as e:
        log.debug("Polymarket fetch: %s", e)
        return []


def minutes_until(iso_time: str) -> float:
    try:
        t = datetime.fromisoformat(iso_time.replace("Z", ""))
        return (t - datetime.utcnow()).total_seconds() / 60
    except Exception:
        return -1
