"""
trading/market_monitor.py — TradeFinder AI engine (multi-strategy).

Three trade categories:
  short_term  — intraday, every qualifying cycle during market hours
  swing       — 3–14 days, daily scan at market open (max 2/day)
  long_term   — multi-week, weekly scan on Monday (max 2/week)

Every trade includes: entry, take profit(s), stop loss, timeframe,
and exact instructions for partial exits on swing/long-term.

Posting:
  - New signals → #trading-signals (formatted by signal_types.py)
  - Target 1 hit (swing/long-term) → #trade-updates with action instructions
  - Terminal exit (T2 or STOP) → #trade-updates with P&L
"""

import logging
import time
from datetime import datetime, date

import pytz
import requests

from core.config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    TRADING_WATCHLIST, TRADING_LOOP_SECONDS,
    TRADING_CLAUDE_EVERY_N_CYCLES, TRADING_MIN_CONF, TRADING_MIN_RR,
)
from core.claude_client import call_trading_brain
from core.db import (
    save_trading_signal, get_active_trading_signals,
    close_trading_signal, invalidate_trading_signal, get_trading_signal_by_ticker,
)
from discord.poster import send, post_health_alert, WEBHOOKS
from trading.pattern_engine import analyse_ticker
from trading.signal_types import fmt_short_term, fmt_swing_trade, fmt_long_term, fmt_trade_exit, fmt_t1_hit

log = logging.getLogger(__name__)

_ws_queue = None


def set_ws_queue(q):
    global _ws_queue
    _ws_queue = q


def _push_ws(msg: dict):
    if _ws_queue is not None:
        try:
            _ws_queue.put_nowait(msg)
        except Exception:
            pass


# ── Market helpers ─────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)


def is_monday_open() -> bool:
    """True once per week at Monday market open (for long-term scan)."""
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    return now.weekday() == 0 and now.hour == 9 and 30 <= now.minute < 45


def is_daily_open() -> bool:
    """True once per day at market open (for swing scan)."""
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    return now.hour == 9 and 30 <= now.minute < 45


def get_market_condition() -> str:
    bars = _fetch_bars("SPY", limit=20)
    if not bars or len(bars) < 10:
        return "UNKNOWN"
    closes = [b["c"] for b in bars]
    if closes[-1] > closes[-5] > closes[-10]:
        return "TRENDING_UP"
    if closes[-1] < closes[-5] < closes[-10]:
        return "TRENDING_DOWN"
    return "RANGING"


def get_vix() -> str:
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        level = "LOW" if price < 15 else ("HIGH" if price > 25 else "NORMAL")
        return f"{price:.1f} ({level})"
    except Exception:
        return "N/A"


# ── Alpaca ─────────────────────────────────────────────────────────────────────

def _fetch_bars(ticker: str, timeframe: str = "5Min", limit: int = 50) -> list:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    try:
        r = requests.get(
            f"{ALPACA_BASE_URL}/stocks/{ticker}/bars",
            headers={
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            params={"timeframe": timeframe, "limit": limit, "feed": "iex"},
            timeout=8,
        )
        return r.json().get("bars", []) if r.status_code == 200 else []
    except Exception:
        return []


# ── Signal formatters ──────────────────────────────────────────────────────────

def _fmt_signal(signal: dict) -> str:
    """Route signal to the correct formatter based on trade_type."""
    trade_type = signal.get("trade_type", "short_term")
    if trade_type == "swing":
        return fmt_swing_trade(signal)
    elif trade_type == "long_term":
        return fmt_long_term(signal)
    else:
        return fmt_short_term(signal)


# ── Target/stop processing ─────────────────────────────────────────────────────

def _process_hit(hit: dict):
    """
    Handle target or stop hit.
    - T1 on swing/long-term: post action instructions, keep position open
    - T1 on short-term: terminal close
    - T2 or STOP: terminal close with P&L
    """
    ticker     = hit.get("ticker", "")
    outcome    = hit.get("outcome", "")
    close_p    = float(hit.get("close_price", 0))
    pnl        = float(hit.get("pnl_pct", 0))
    update_txt = hit.get("update_text", "")

    sig = get_trading_signal_by_ticker(ticker)
    if not sig:
        return

    trade_type = sig.get("signal_type", "short_term")   # stored in DB reasoning field
    webhook    = WEBHOOKS.get("trade_updates", "")

    if outcome == "TARGET_1":
        if trade_type in ("swing", "long_term") and sig.get("target_2"):
            # Non-terminal for swing/long-term — post action instructions
            t2  = sig.get("target_2", 0)
            msg = fmt_t1_hit(ticker, close_p, t2)
            send(webhook, msg)
            log.info("T1 hit %s @ $%.2f — posting action instructions (holding for T2)", ticker, close_p)
            # Don't close — let T2 or STOP resolve it
            return
        else:
            # Terminal for short-term
            close_trading_signal(sig["id"], outcome, close_p, pnl)
            msg = fmt_trade_exit(ticker, close_p, outcome, pnl, trade_type)
            send(webhook, msg)
            log.info("Short-term T1 close: %s @ $%.2f P&L=%.2f%%", ticker, close_p, pnl)

    elif outcome in ("TARGET_2", "STOP"):
        close_trading_signal(sig["id"], outcome, close_p, pnl)
        msg = fmt_trade_exit(ticker, close_p, outcome, pnl, trade_type)
        send(webhook, msg)
        log.info("Trade exit: %s %s @ $%.2f P&L=%.2f%%", ticker, outcome, close_p, pnl)

    _push_ws({"type": "SIGNAL_INVALIDATED", "ticker": ticker})


# ── Short-term signal processing ───────────────────────────────────────────────

def _post_signal(signal: dict):
    message = _fmt_signal(signal)
    webhook = WEBHOOKS.get("trading", "")
    if webhook:
        send(webhook, message)


# ── Daily/weekly scan triggers ─────────────────────────────────────────────────

_swing_scanned_today:    date | None = None
_longterm_scanned_week:  str  | None = None


def _run_swing_scan_if_due():
    global _swing_scanned_today
    today = date.today()
    if _swing_scanned_today == today:
        return
    if not is_daily_open():
        return
    try:
        from trading.swing_trader import run_swing_scan
        posted = run_swing_scan()
        _swing_scanned_today = today
        if posted:
            log.info("Swing scan: %d trade(s) posted", posted)
    except Exception as e:
        log.error("Swing scan error: %s", e)


def _run_longterm_scan_if_due():
    global _longterm_scanned_week
    from datetime import date as _date
    week = _date.today().strftime("%Y-W%V")
    if _longterm_scanned_week == week:
        return
    if not is_monday_open():
        return
    try:
        from trading.long_term import run_longterm_scan
        posted = run_longterm_scan()
        _longterm_scanned_week = week
        if posted:
            log.info("Long-term scan: %d position(s) posted", posted)
    except Exception as e:
        log.error("Long-term scan error: %s", e)


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_market_monitor():
    log.info("Market monitor started — %ds loop, Claude every %d cycles",
             TRADING_LOOP_SECONDS, TRADING_CLAUDE_EVERY_N_CYCLES)
    log.info("Trade types: short-term (intraday), swing (daily scan), long-term (weekly scan)")

    cycle_count = 0

    while True:
        loop_start   = time.time()
        cycle_count += 1

        try:
            if not is_market_hours():
                time.sleep(60)
                continue

            # ── Swing scan at daily open ───────────────────────────────────────
            _run_swing_scan_if_due()

            # ── Long-term scan on Monday open ──────────────────────────────────
            _run_longterm_scan_if_due()

            # ── Intraday / short-term signal detection ─────────────────────────
            analysis       = []
            bars_found     = 0
            patterns_found = 0

            for ticker in TRADING_WATCHLIST:
                bars = _fetch_bars(ticker)
                if bars:
                    bars_found += 1
                result = analyse_ticker(ticker, bars)
                if result.get("has_patterns"):
                    analysis.append(result)
                    patterns_found += 1

            active = get_active_trading_signals()

            log.info("Bars: %d/%d | Patterns: %d | Active: %d",
                     bars_found, len(TRADING_WATCHLIST), patterns_found, len(active))

            has_work    = bool(analysis) or bool(active)
            on_schedule = (cycle_count % TRADING_CLAUDE_EVERY_N_CYCLES == 0)

            if not has_work:
                log.debug("No patterns and no active signals — skipping Claude")
            elif not on_schedule:
                log.debug("Claude cooldown (cycle %d)", cycle_count)
            else:
                brain_result = call_trading_brain(
                    analysis, active,
                    market_condition=get_market_condition(),
                    vix=get_vix(),
                )

                log.info("Trading brain: %d signals, %d hits, %d invalidations",
                         len(brain_result.get("signals", [])),
                         len(brain_result.get("targets_hit", [])),
                         len(brain_result.get("signals_invalidated", [])))

                # Process target/stop hits
                for hit in brain_result.get("targets_hit", []):
                    try:
                        _process_hit(hit)
                    except Exception as e:
                        log.error("Hit processing error %s: %s", hit.get("ticker"), e)

                # Invalidations
                for ticker in brain_result.get("signals_invalidated", []):
                    invalidate_trading_signal(ticker)
                    _push_ws({"type": "SIGNAL_INVALIDATED", "ticker": ticker})
                    log.info("Invalidated: %s", ticker)

                # New short-term signals
                active_tickers = {s["ticker"] for s in active if s["status"] == "ACTIVE"}
                for signal in brain_result.get("signals", []):
                    ticker = signal.get("ticker", "?")
                    if ticker in active_tickers:
                        log.debug("Skipping %s — already active", ticker)
                        continue

                    conf = signal.get("confidence", 0)
                    rr   = float(signal.get("risk_reward", 0))

                    if conf < TRADING_MIN_CONF:
                        log.info("Signal skipped (conf %d < %d): %s", conf, TRADING_MIN_CONF, ticker)
                        continue
                    if rr < TRADING_MIN_RR:
                        log.info("Signal skipped (R/R %.1f < %.1f): %s", rr, TRADING_MIN_RR, ticker)
                        continue

                    # Tag as short-term
                    signal["trade_type"] = "short_term"

                    save_trading_signal(signal)
                    _post_signal(signal)
                    active_tickers.add(ticker)
                    _push_ws({"type": "NEW_SIGNAL", "signal": signal})
                    log.info("✅ Short-term signal: %s %s @ $%s (conf=%d R/R=%.1f)",
                             signal.get("signal_type"), ticker,
                             signal.get("entry_price"), conf, rr)

        except Exception as e:
            log.error("market_monitor error: %s", e)
            try:
                post_health_alert("market_monitor", str(e))
            except Exception:
                pass

        elapsed   = time.time() - loop_start
        sleep_for = max(0.0, TRADING_LOOP_SECONDS - elapsed)
        time.sleep(sleep_for)


def get_active_trades_summary() -> str:
    """
    One-line summary of active positions for content generation.
    """
    active = get_active_trading_signals()
    if not active:
        return ""
    count  = len(active)
    tickers = ", ".join(s["ticker"] for s in active[:3])
    return f"{count} active trade{'s' if count > 1 else ''}: {tickers}."
