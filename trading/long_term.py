"""
trading/long_term.py
====================
Long-term position engine — multi-week accumulation strategy.

Scans for strong fundamental setups on weekly timeframe.
Targets: AAPL, NVDA, MSFT, SPY, QQQ, AMZN, GOOGL.
Max 1–2 long-term setups per week.

Each setup includes:
  - buy zone (scale in gradually)
  - two profit targets with partial exit instructions
  - risk level (close below X = full exit)
  - timeframe note

Called from market_monitor once per week (Monday morning).
"""

import logging
import time
from datetime import date

import requests

from core.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
from core.db import save_trading_signal, get_active_trading_signals
from discord.poster import send, WEBHOOKS
from trading.signal_types import fmt_long_term

log = logging.getLogger(__name__)

# Long-term watchlist — focus on liquid, large-cap names
LONGTERM_WATCHLIST = ["AAPL", "NVDA", "MSFT", "SPY", "QQQ", "AMZN", "GOOGL"]

# State — max 2 long-term setups per week
_lt_posted_this_week: str | None = None   # ISO week string
_lt_count_this_week:  int        = 0
LT_MAX_PER_WEEK = 2


def _iso_week() -> str:
    return date.today().strftime("%Y-W%V")


def _fetch_weekly_bars(ticker: str, limit: int = 52) -> list:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    try:
        r = requests.get(
            f"{ALPACA_BASE_URL}/stocks/{ticker}/bars",
            headers={
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            params={"timeframe": "1Week", "limit": limit, "feed": "iex"},
            timeout=8,
        )
        return r.json().get("bars", []) if r.status_code == 200 else []
    except Exception:
        return []


def _detect_longterm_setup(ticker: str, bars: list) -> dict | None:
    """
    Detect long-term accumulation opportunities on weekly bars.
    Focuses on: pullbacks to major support, trend continuation,
    and breakouts above multi-month resistance.
    Returns setup dict or None.
    """
    if len(bars) < 20:
        return None

    closes = [b["c"] for b in bars]
    highs  = [b["h"] for b in bars]
    lows   = [b["l"] for b in bars]
    vols   = [b["v"] for b in bars]

    current = closes[-1]

    # 20-week EMA (trend direction)
    k   = 2.0 / 21
    ema = sum(closes[:20]) / 20
    for p in closes[20:]:
        ema = p * k + ema * (1 - k)

    # 52-week high and low
    high_52w = max(highs[-52:]) if len(highs) >= 52 else max(highs)
    low_52w  = min(lows[-52:])  if len(lows)  >= 52 else min(lows)
    range_52 = high_52w - low_52w or 1

    # Position within 52-week range (0 = at low, 1 = at high)
    position = (current - low_52w) / range_52

    # Volume trend
    avg_vol_12w = sum(vols[-12:]) / 12 or 1
    vol_ratio   = vols[-1] / avg_vol_12w

    # ── Long-term bullish scenario: strong uptrend with a pullback ────────────
    long_term_uptrend = closes[-1] > closes[-4] > closes[-8] > ema
    healthy_pullback  = 0.3 < position < 0.75   # not at extremes

    if long_term_uptrend and healthy_pullback and vol_ratio >= 0.8:
        entry = round(current, 2)
        stop  = round(low_52w * 1.01, 2)     # just above 52-week low
        risk  = entry - stop
        t1    = round(entry + risk * 1.5, 2)
        t2    = round(entry + risk * 3.0, 2)
        rr    = round(risk * 1.5 / max(risk, 0.01), 1)

        return {
            "ticker":       ticker,
            "signal_type":  "BUY",
            "trade_type":   "long_term",
            "pattern":      "TREND_CONTINUATION",
            "entry_price":  entry,
            "target_1":     t1,
            "target_2":     t2,
            "stop_loss":    stop,
            "risk_reward":  rr,
            "confidence":   8,
            "timeframe":    "4–12 weeks",
            "reasoning":    (
                f"Weekly uptrend intact. Price in healthy position "
                f"({position*100:.0f}% of 52w range). Above 20-week EMA ({ema:.2f})."
            ),
        }

    # ── Breakout above multi-month resistance (52-week high area) ────────────
    near_52w_high = current > high_52w * 0.97
    if near_52w_high and vol_ratio > 1.3 and closes[-1] > ema:
        entry = round(current, 2)
        stop  = round(high_52w * 0.92, 2)    # below breakout level
        risk  = entry - stop
        t1    = round(entry + risk * 1.5, 2)
        t2    = round(entry + risk * 3.0, 2)
        rr    = round(risk * 1.5 / max(risk, 0.01), 1)

        return {
            "ticker":       ticker,
            "signal_type":  "BUY",
            "trade_type":   "long_term",
            "pattern":      "52W_BREAKOUT",
            "entry_price":  entry,
            "target_1":     t1,
            "target_2":     t2,
            "stop_loss":    stop,
            "risk_reward":  rr,
            "confidence":   9,
            "timeframe":    "4–12 weeks",
            "reasoning":    (
                f"Approaching 52-week high ({high_52w:.2f}) on {vol_ratio:.1f}x volume. "
                f"Breakout continuation setup."
            ),
        }

    return None


def _post_long_term(signal: dict):
    message = fmt_long_term(signal)
    webhook = WEBHOOKS.get("trading", "")
    if webhook:
        send(webhook, message)
    log.info("Long-term position posted: %s entry=$%s timeframe=%s",
             signal["ticker"], signal["entry_price"], signal["timeframe"])


def run_longterm_scan() -> int:
    """
    Scan long-term watchlist for accumulation setups on weekly bars.
    Returns number of positions posted.
    Called once per week from market_monitor (Monday open).
    """
    global _lt_posted_this_week, _lt_count_this_week

    week = _iso_week()
    if _lt_posted_this_week == week and _lt_count_this_week >= LT_MAX_PER_WEEK:
        log.debug("Long-term scan: weekly limit reached (%d/%d)", _lt_count_this_week, LT_MAX_PER_WEEK)
        return 0

    if _lt_posted_this_week != week:
        _lt_posted_this_week = week
        _lt_count_this_week  = 0

    active_tickers = {s["ticker"] for s in get_active_trading_signals()}
    posted = 0

    for ticker in LONGTERM_WATCHLIST:
        if posted >= LT_MAX_PER_WEEK:
            break
        if ticker in active_tickers:
            continue
        bars  = _fetch_weekly_bars(ticker)
        setup = _detect_longterm_setup(ticker, bars)
        if setup:
            try:
                save_trading_signal(setup)
                _post_long_term(setup)
                active_tickers.add(ticker)
                _lt_count_this_week += 1
                posted += 1
                time.sleep(1)
            except Exception as e:
                log.error("Long-term post failed %s: %s", ticker, e)

    if posted:
        log.info("Long-term scan: %d setup(s) posted", posted)
    else:
        log.debug("Long-term scan: no qualifying setups this week")

    return posted


def get_active_longterm_summary() -> str:
    """
    Returns a one-line summary of active long-term positions.
    Used by content/whop.py for daily content generation.
    """
    active = get_active_trading_signals()
    lt = [s for s in active if s.get("signal_type") == "BUY"]
    if not lt:
        return ""
    tickers = ", ".join(s["ticker"] for s in lt[:3])
    return f"Long-term positions holding: {tickers}."
