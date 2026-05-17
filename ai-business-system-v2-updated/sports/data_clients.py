"""
sports/data_clients.py
======================
API clients for sports data. Returns empty list on any error.
"""

import logging
from datetime import datetime

import requests

from core.config import ODDS_API_KEY, KALSHI_API_KEY, SOFT_BOOKS, SHARP_BOOKS, SPORTS_KEYS

log = logging.getLogger(__name__)

ODDS_BASE  = "https://api.the-odds-api.com/v4"
POLY_BASE  = "https://gamma-api.polymarket.com"
KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"


_odds_api_auth_failed = False


def fetch_sportsbook_odds() -> list:
    global _odds_api_auth_failed

    if not ODDS_API_KEY:
        log.debug("ODDS_API_KEY not set")
        return []

    if _odds_api_auth_failed:
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
                    "markets":    "h2h,spreads,totals",
                    "oddsFormat": "american",
                    "bookmakers": all_books,
                },
                timeout=10,
            )
            if r.status_code == 401:
                log.error("OddsAPI authentication failed — check ODDS_API_KEY")
                _odds_api_auth_failed = True
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
                log.warning("OddsAPI: rate limit hit")
                break
        except Exception as e:
            log.warning("OddsAPI (%s): %s", sport, e)

    return games


def minutes_until(iso_time: str) -> float:
    try:
        t = datetime.fromisoformat(iso_time.replace("Z", ""))
        return (t - datetime.utcnow()).total_seconds() / 60
    except Exception:
        return -1


def fetch_polymarket_sports() -> list:
    try:
        r = requests.get(
            f"{POLY_BASE}/markets",
            params={"active": True, "closed": False, "tag_slug": "sports", "limit": 50},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        out = []
        for m in r.json():
            # Safe volume parse — skip if unparseable
            try:
                volume = float(m.get("volume24hr") or 0)
            except (TypeError, ValueError):
                continue
            if volume < 5000:
                continue

            # Safe price parse — both sides must be valid floats
            prices = m.get("outcomePrices") or []
            if not isinstance(prices, list) or len(prices) < 2:
                continue
            try:
                yes_price = float(prices[0])
                no_price  = float(prices[1])
            except (TypeError, ValueError):
                continue

            # Both prices must be in valid probability range
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
        return out
    except Exception:
        return []


def fetch_kalshi_sports() -> list:
    if not KALSHI_API_KEY:
        return []
    try:
        r = requests.get(
            f"{KALSHI_BASE}/markets",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            params={"status": "open", "limit": 100},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        out = []
        for m in r.json().get("markets", []):
            if m.get("volume", 0) < 100:
                continue
            out.append({
                "ticker":     m["ticker"],
                "title":      m.get("title", ""),
                "yes_bid":    m.get("yes_bid", 0) / 100,
                "yes_ask":    m.get("yes_ask", 0) / 100,
                "no_bid":     m.get("no_bid",  0) / 100,
                "no_ask":     m.get("no_ask",  0) / 100,
                "volume":     m.get("volume", 0),
                "close_time": m.get("close_time"),
                "source":     "KALSHI",
            })
        return out
    except Exception as e:
        log.warning("Kalshi fetch: %s", e)
        return []
