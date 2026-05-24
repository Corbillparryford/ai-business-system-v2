"""
sports/results_tracker.py
=========================
In-memory daily results tracker for sports signals.
Resets automatically at the start of each new day.
Thread-safe enough for single-process use (one writer: odds_monitor).

Usage:
    from sports.results_tracker import record_result, get_results

    record_result("WIN")
    record_result("LOSS")
    stats = get_results()  # {"sports_wins": 1, "sports_losses": 1}
"""

import logging
from datetime import date

log = logging.getLogger(__name__)

DAILY_RESULTS: dict = {
    "sports_wins":   0,
    "sports_losses": 0,
}
CURRENT_DAY: date = date.today()


def record_result(result: str):
    """
    Record a WIN or LOSS for today.
    Resets counters automatically if the calendar day has changed.
    """
    global CURRENT_DAY
    if date.today() != CURRENT_DAY:
        reset_day()
    if result == "WIN":
        DAILY_RESULTS["sports_wins"] += 1
        log.debug("Result recorded: WIN (today: %dW/%dL)",
                  DAILY_RESULTS["sports_wins"], DAILY_RESULTS["sports_losses"])
    elif result == "LOSS":
        DAILY_RESULTS["sports_losses"] += 1
        log.debug("Result recorded: LOSS (today: %dW/%dL)",
                  DAILY_RESULTS["sports_wins"], DAILY_RESULTS["sports_losses"])


def get_results() -> dict:
    """Return a copy of today's running totals."""
    global CURRENT_DAY
    if date.today() != CURRENT_DAY:
        reset_day()
    return DAILY_RESULTS.copy()


def reset_day():
    """Reset counters for a new calendar day."""
    global DAILY_RESULTS, CURRENT_DAY
    DAILY_RESULTS = {
        "sports_wins":   0,
        "sports_losses": 0,
    }
    CURRENT_DAY = date.today()
    log.info("Results tracker reset for %s", CURRENT_DAY)
