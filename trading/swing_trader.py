"""
trading/swing_trader.py
=======================
Swing trade engine — 3 to 14 day positions.

Runs on a separate daily schedule (not intraday).
Scans daily bars for breakouts, pullbacks, and momentum setups.
Posts 2–5 swing trades per week maximum.
Each trade includes buy zone, two targets, stop loss, and exact
instructions for partial exit at Target 1.

Called from market_monitor once per day (at market open).
"""

import logging
import time
from datetime import date

import requests

from core.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, TRADING_WATCHLIST
from core.claude_client import call_trading_brain
from core.db import save_trading_signal, get_active_trading_signals
from discord.poster import send, WEBHOOKS
from trading.signal_types import fmt_swing_trade

log = logging.getLogger(__name__)

# State — max 2 swing trades per day to prevent spam
_swing_posted_today: date | None = None
_swing_count_today:  int         = 0
SWING_MAX_PER_DAY = 2


def _fetch_daily_bars(ticker: str, limit: int = 30) -> list:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    try:
        r = requests.get(
            f"{ALPACA_BASE_URL}/stocks/{ticker}/bars",
            headers={
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            params={"timeframe": "1Day", "limit": limit, "feed": "iex"},
            timeout=8,
        )
        return r.json().get("bars", []) if r.status_code == 200 else []
    except Exception:
        return []


def _detect_swing_setup(ticker: str, bars: list) -> dict | None:
    """
    Detect swing-worthy setups on daily bars.
    Returns setup dict or None.
    """
    if len(bars) < 10:
        return None

    closes  = [b["c"] for b in bars]
    highs   = [b["h"] for b in bars]
    lows    = [b["l"] for b in bars]
    vols    = [b["v"] for b in bars]
    current = closes[-1]

    # Calculate 10-day EMA
    ema_period = 10
    k   = 2.0 / (ema_period + 1)
    ema = sum(closes[:ema_period]) / ema_period
    for p in closes[ema_period:]:
        ema = p * k + ema * (1 - k)

    # Volume check
    avg_vol = sum(vols[-10:]) / 10 or 1
    vol_ratio = vols[-1] / avg_vol

    # Resistance level (20-day high excluding today)
    resistance = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])

    # Support level (10-day low)
    support = min(lows[-10:])

    # ── Breakout setup ────────────────────────────────────────────────────────
    if current > resistance * 1.005 and vol_ratio > 1.5 and current > ema:
        entry = round(current, 2)
        stop  = round(resistance * 0.985, 2)
        risk  = entry - stop
        t1    = round(entry + risk * 2.0, 2)
        t2    = round(entry + risk * 3.5, 2)
        rr    = round(risk * 2.0 / max(risk, 0.01), 1)
        return {
            "ticker":       ticker,
            "signal_type":  "BUY",
            "trade_type":   "swing",
            "pattern":      "BREAKOUT",
            "entry_price":  entry,
            "target_1":     t1,
            "target_2":     t2,
            "stop_loss":    stop,
            "risk_reward":  rr,
            "confidence":   8 if vol_ratio > 2.0 else 7,
            "timeframe":    "1–2 weeks",
            "reasoning":    f"Daily breakout above {resistance:.2f} on {vol_ratio:.1f}x volume. EMA trending up.",
        }

    # ── Pullback to EMA setup ─────────────────────────────────────────────────
    ema_touch = abs(current - ema) / ema < 0.008
    uptrend   = closes[-1] > closes[-5] > closes[-10]
    if ema_touch and uptrend and vol_ratio < 1.0:
        entry = round(current, 2)
        stop  = round(support * 0.99, 2)
        risk  = entry - stop
        t1    = round(entry + risk * 2.0, 2)
        t2    = round(entry + risk * 3.5, 2)
        rr    = round(risk * 2.0 / max(risk, 0.01), 1)
        return {
            "ticker":       ticker,
            "signal_type":  "BUY",
            "trade_type":   "swing",
            "pattern":      "EMA_PULLBACK",
            "entry_price":  entry,
            "target_1":     t1,
            "target_2":     t2,
            "stop_loss":    stop,
            "risk_reward":  rr,
            "confidence":   7,
            "timeframe":    "1–2 weeks",
            "reasoning":    f"Pullback to 10-day EMA ({ema:.2f}) in an established uptrend. Low-volume consolidation.",
        }

    return None


def _post_swing_trade(signal: dict):
    """Format and post a swing trade to #trading channel."""
    message = fmt_swing_trade(signal)
    webhook = WEBHOOKS.get("trading", "")
    if webhook:
        send(webhook, message)
    log.info("Swing trade posted: %s %s entry=$%s",
             signal["signal_type"], signal["ticker"], signal["entry_price"])


def run_swing_scan() -> int:
    """
    Scan watchlist for swing setups on daily bars.
    Returns number of swing trades posted.
    Called once per day from market_monitor.
    """
    global _swing_posted_today, _swing_count_today

    today = date.today()
    if _swing_posted_today == today and _swing_count_today >= SWING_MAX_PER_DAY:
        log.debug("Swing scan: daily limit reached (%d/%d)", _swing_count_today, SWING_MAX_PER_DAY)
        return 0

    if _swing_posted_today != today:
        _swing_posted_today = today
        _swing_count_today  = 0

    active_tickers = {s["ticker"] for s in get_active_trading_signals()}
    posted = 0

    for ticker in TRADING_WATCHLIST:
        if posted >= SWING_MAX_PER_DAY:
            break
        if ticker in active_tickers:
            continue
        bars  = _fetch_daily_bars(ticker)
        setup = _detect_swing_setup(ticker, bars)
        if setup:
            try:
                save_trading_signal(setup)
                _post_swing_trade(setup)
                active_tickers.add(ticker)
                _swing_count_today += 1
                posted += 1
                time.sleep(1)   # small delay between posts
            except Exception as e:
                log.error("Swing trade post failed %s: %s", ticker, e)

    if posted:
        log.info("Swing scan complete: %d setup(s) posted", posted)
    else:
        log.debug("Swing scan: no qualifying setups today")

    return posted
