"""
sports/performance_tracker.py
==============================
Bankroll performance tracker.

Tracks wins, losses, and simulated bankroll from a base of $10,000.
Unit size: 1% of initial bankroll per bet = $100.
Resets daily alongside results_tracker.

Usage:
    from sports.performance_tracker import record_bet_result, get_performance

    record_bet_result("WIN", profit=100)
    record_bet_result("LOSS", profit=100)
    perf = get_performance()
    # {"wins": 1, "losses": 1, "bankroll": 10000.0, "bankroll_pct": 0.0}
"""

import logging
from datetime import date

log = logging.getLogger(__name__)

INITIAL_BANKROLL: float = 10_000.0
UNIT_SIZE:        float = 100.0      # 1% of initial bankroll per bet

_bankroll:      float       = INITIAL_BANKROLL
_wins:          int         = 0
_losses:        int         = 0
_current_day:   date | None = None


def _reset_if_new_day():
    global _bankroll, _wins, _losses, _current_day
    today = date.today()
    if _current_day != today:
        _bankroll    = INITIAL_BANKROLL
        _wins        = 0
        _losses      = 0
        _current_day = today
        log.debug("Performance tracker reset for %s", today)


def record_bet_result(result: str, profit: float = UNIT_SIZE):
    """
    Record a bet result and update the simulated bankroll.

    result: "WIN" | "LOSS" | "PUSH"
    profit: P&L in dollars for this bet (default: 1 unit = $100)
    """
    global _bankroll, _wins, _losses
    _reset_if_new_day()

    if result == "WIN":
        _bankroll += profit
        _wins     += 1
        log.debug("Bankroll +$%.2f → $%.2f", profit, _bankroll)
    elif result == "LOSS":
        _bankroll -= profit
        _losses   += 1
        log.debug("Bankroll -$%.2f → $%.2f", profit, _bankroll)


def get_performance() -> dict:
    """
    Return current performance snapshot.

    Returns:
        wins:         int   — wins today
        losses:       int   — losses today
        bankroll:     float — current simulated bankroll ($)
        bankroll_pct: float — % change from INITIAL_BANKROLL (can be negative)
    """
    _reset_if_new_day()
    pct = round((_bankroll - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100, 2)
    return {
        "wins":         _wins,
        "losses":       _losses,
        "bankroll":     round(_bankroll, 2),
        "bankroll_pct": pct,
    }


def reset():
    """Manually reset — called by tests or end-of-day logic."""
    global _bankroll, _wins, _losses, _current_day
    _bankroll    = INITIAL_BANKROLL
    _wins        = 0
    _losses      = 0
    _current_day = date.today()
