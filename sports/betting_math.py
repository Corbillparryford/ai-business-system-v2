"""sports/betting_math.py — pure betting math, no API calls."""

from __future__ import annotations
from typing import Optional


def american_to_decimal(odds: int) -> float:
    return (odds / 100 + 1.0) if odds > 0 else (100 / abs(odds) + 1.0)


def implied_prob(odds: int) -> float:
    d = american_to_decimal(odds)
    return 1.0 / d if d else 0.0


def remove_vig(odds_a: int, odds_b: int) -> tuple[float, float]:
    ia, ib = implied_prob(odds_a), implied_prob(odds_b)
    total  = ia + ib or 1.0
    return ia / total, ib / total


def ev_edge_pct(true_p: float, soft_odds: int) -> float:
    return (true_p - implied_prob(soft_odds)) * 100


def calc_arbitrage(legs: list[dict]) -> Optional[dict]:
    if len(legs) < 2:
        return None
    probs = [implied_prob(l["odds_american"]) for l in legs]
    total = sum(probs)
    if total >= 1.0:
        return None
    stake = 1000.0
    return {
        "arb_percentage":  round((1 - total) * 100, 3),
        "profit_per_1000": round(stake * (1 / total - 1), 2),
        "legs": [{**l, "stake": round(probs[i] / total * stake, 2)}
                 for i, l in enumerate(legs)],
        "fees_applied": False,
    }


def best_per_side(books_data: dict, home: str, away: str, book_keys: list[str]) -> dict:
    best = {
        home: {"odds": -999_999, "book": None},
        away: {"odds": -999_999, "book": None},
    }
    for bk in book_keys:
        entry = books_data.get(bk, {})
        if not isinstance(entry, dict):
            continue
        for mkt in entry.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                nm = o["name"]
                if nm in best and o["price"] > best[nm]["odds"]:
                    best[nm] = {"odds": o["price"], "book": bk}
    return best


def is_two_way_market(books_data_or_market, book_keys: list[str] | None = None) -> bool:
    """
    Accepts two call styles:

    Style A — books dict (used internally by _scan_arb):
        is_two_way_market(books_data: dict, book_keys: list[str]) -> bool
        Returns True only if all h2h markets have exactly 2 outcomes and no Draw.

    Style B — market name string (spec-compatible fallback):
        is_two_way_market(market: str) -> bool
        Returns True if the market string matches a known 2-way market type.
    """
    # Style B: single string argument
    if isinstance(books_data_or_market, str):
        market = books_data_or_market.lower()
        two_way_keywords = [
            "moneyline", "spread", "run line", "puck line",
            "team total", "player prop", "h2h",
        ]
        return any(kw in market for kw in two_way_keywords)

    # Style A: books dict + book_keys list
    books_data = books_data_or_market
    if book_keys is None:
        return True
    for bk in book_keys:
        entry = books_data.get(bk, {})
        if not isinstance(entry, dict):
            continue
        for mkt in entry.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            outcomes = mkt.get("outcomes", [])
            names    = [o["name"].lower() for o in outcomes]
            if len(outcomes) != 2:
                return False
            if any(n in ("draw", "tie", "push") for n in names):
                return False
    return True
    
