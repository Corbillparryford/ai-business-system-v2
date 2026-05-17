"""
sports/betting_math.py
======================
Pure betting math. No API calls. No side effects.
"""

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
    """
    legs = [{"book": str, "side": str, "odds_american": int}, ...]
    Returns arb dict if guaranteed profit exists, else None.
    """
    if len(legs) < 2:
        return None
    probs = [implied_prob(l["odds_american"]) for l in legs]
    total = sum(probs)
    if total >= 1.0:
        return None
    stake = 1000.0
    return {
        "arb_percentage":      round((1 - total) * 100, 3),
        "profit_per_1000":     round(stake * (1 / total - 1), 2),
        "profit_pct_of_stake": round((stake * (1 / total - 1)) / stake * 100, 2),
        "total_implied_pct":   round(total * 100, 2),
        "legs": [{**l, "stake": round(probs[i] / total * stake, 2)} for i, l in enumerate(legs)],
        "fees_applied": False,
    }


def best_per_side(books_data: dict, home: str, away: str,
                  book_keys: list[str]) -> dict:
    best = {
        home: {"odds": -999_999, "book": None},
        away: {"odds": -999_999, "book": None},
    }
    for bk in book_keys:
        if bk not in books_data:
            continue
        for mkt in books_data[bk].get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                nm = o["name"]
                if nm in best and o["price"] > best[nm]["odds"]:
                    best[nm] = {"odds": o["price"], "book": bk}
    return best
