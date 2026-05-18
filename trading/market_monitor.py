"""
trading/market_monitor.py
=========================
TradeFinder AI engine — runs every 30 seconds during market hours.

Per cycle:
  1. Check market hours
  2. Fetch OHLCV bars via Alpaca
  3. Run pattern detection (Python)
  4. Claude validates signals + checks active signals for target/stop hits
  5. New signals → save DB → #trading-signals + #free-signals teaser
  6. Target/stop hits → #trade-updates + #trade-results + #results-preview
  7. Invalidations → mark DB + #trade-updates
"""

import logging
import time
from datetime import datetime

import pytz
import requests

from core.config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    TRADING_WATCHLIST, TRADING_LOOP_SECONDS,
)
from core.claude_client import call_trading_brain, validate_signal
from core.db import (
    save_trading_signal, get_active_trading_signals,
    close_trading_signal, invalidate_trading_signal,
    get_trading_signal_by_ticker,
)
from discord.poster import post_signal, post_health_alert
from trading.pattern_engine import analyse_ticker

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


# ── Market helpers ────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)


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


# ── Alpaca fetch ──────────────────────────────────────────────────────────────

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
    except Exception as e:
        log.debug("fetch_bars(%s): %s", ticker, e)
        return []


# ── Result processing ─────────────────────────────────────────────────────────

def _process_target_hit(hit: dict):
    """Handle a target or stop hit reported by Claude."""
    ticker      = hit.get("ticker", "")
    outcome     = hit.get("outcome", "")
    close_price = hit.get("close_price", 0.0)
    pnl_pct     = hit.get("pnl_pct", 0.0)

    # Fetch signal from DB
    sig = get_trading_signal_by_ticker(ticker)
    if not sig:
        log.debug("No active signal found for %s (already closed?)", ticker)
        return

    # Close in DB
    close_trading_signal(sig["id"], outcome, close_price, pnl_pct)

    # Only post to Discord on terminal outcomes (full exit, not intermediate target)
    # TARGET_1 is an intermediate milestone — suppressed to avoid noise.
    # TARGET_2 and STOP are the final exit — post the clean SELL format.
    if outcome in ("TARGET_2", "STOP"):
        exit_signal = {
            "ticker":      ticker,
            "outcome":     outcome,
            "close_price": close_price,
            "pnl_pct":     pnl_pct,
            "update_text": f"{ticker} closed at ${close_price}",
        }
        post_signal(exit_signal, "update")   # routes to _fmt_trade_exit
        log.info("Trade exit posted: %s %s @ $%s (P&L: %.2f%%)",
                 ticker, outcome, close_price, pnl_pct)
    else:
        log.info("TARGET_1 hit for %s @ $%s — holding for TARGET_2 (no Discord post)",
                 ticker, close_price)

    _push_ws({"type": "SIGNAL_INVALIDATED", "ticker": ticker})


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_market_monitor():
    log.info("Market monitor started")

    cycle_count         = 0
    CLAUDE_CALL_EVERY_N = 3   # call Claude at most once every N cycles
    MIN_PATTERNS        = 1   # minimum pattern count to justify a Claude call

    while True:
        loop_start  = time.time()
        cycle_count += 1
        try:
            if not is_market_hours():
                time.sleep(60)
                continue

            active = get_active_trading_signals()

            # Build analysis payload — log bar fetch results for diagnostics
            analysis = []
            bars_found = 0
            patterns_found = 0
            for ticker in TRADING_WATCHLIST:
                bars   = _fetch_bars(ticker)
                if bars:
                    bars_found += 1
                result = analyse_ticker(ticker, bars)
                if result.get("has_patterns"):
                    analysis.append(result)
                    patterns_found += 1

            log.info("Bar data: %d/%d tickers | Patterns: %d | Active signals: %d",
                     bars_found, len(TRADING_WATCHLIST), patterns_found, len(active))

            # Call Claude only when there is something actionable:
            # patterns detected OR active signals needing invalidation checks.
            # Also enforce cooldown: skip Claude on non-qualifying cycles.
            has_work    = bool(analysis) or bool(active)
            on_schedule = (cycle_count % CLAUDE_CALL_EVERY_N == 0)
            enough_patterns = patterns_found >= MIN_PATTERNS or bool(active)

            should_call = has_work and on_schedule and enough_patterns

            if has_work and not on_schedule:
                log.debug("Trading Claude cooldown — cycle %d (calls every %d)",
                          cycle_count, CLAUDE_CALL_EVERY_N)

            if should_call:
                result = call_trading_brain(
                    analysis, active,
                    market_condition=get_market_condition(),
                    vix=get_vix(),
                )

                log.info("Trading brain returned: %d signals, %d hits, %d invalidations",
                         len(result.get("signals", [])),
                         len(result.get("targets_hit", [])),
                         len(result.get("signals_invalidated", [])))

                # ── Process target / stop hits ────────────────────────────────
                for hit in result.get("targets_hit", []):
                    try:
                        _process_target_hit(hit)
                    except Exception as e:
                        log.error("Error processing target hit %s: %s", hit.get("ticker"), e)

                # ── Process invalidations ─────────────────────────────────────
                for ticker in result.get("signals_invalidated", []):
                    invalidate_trading_signal(ticker)
                    post_signal(
                        {"ticker": ticker, "outcome": "INVALIDATED",
                         "close_price": 0.0, "pnl_pct": 0.0,
                         "update_text": f"⛔ {ticker} — signal invalidated (stop breached)"},
                        "update",
                    )
                    _push_ws({"type": "SIGNAL_INVALIDATED", "ticker": ticker})
                    log.info("Invalidated: %s", ticker)

                # ── Process new signals ───────────────────────────────────────
                active_tickers = {s["ticker"] for s in active if s["status"] == "ACTIVE"}
                for signal in result.get("signals", []):
                    if signal.get("ticker") in active_tickers:
                        log.debug("Skipping %s — already have active signal", signal.get("ticker"))
                        continue
                    check = validate_signal(signal)
                    if not check.get("approved", True):
                        log.info("Signal rejected by validation: %s — %s",
                                 signal.get("ticker"), check.get("reason"))
                        continue
                    signal["confidence"] = check.get("adjusted_confidence",
                                                      signal.get("confidence", 5))
                    save_trading_signal(signal)
                    post_signal(signal, "trading")
                    # Post teaser to Whop (entry/stop details excluded)
                    try:
                        from content.publisher import whop_post_trading_signal
                        whop_post_trading_signal(
                            ticker     = signal.get("ticker", ""),
                            action     = signal.get("signal_type", "BUY"),
                            confidence = int(signal.get("confidence", 5)),
                        )
                    except Exception:
                        pass
                    _push_ws({"type": "NEW_SIGNAL", "signal": signal})
                    log.info("✅ Trading signal posted: %s %s @ $%s",
                             signal.get("signal_type"), signal.get("ticker"),
                             signal.get("entry_price"))

            else:
                log.info("No bar data, no patterns, no active signals — skipping Claude call")

        except Exception as e:
            log.error("market_monitor error: %s", e)
            try:
                post_health_alert("market_monitor", str(e))
            except Exception:
                pass

        elapsed   = time.time() - loop_start
        sleep_for = max(0.0, TRADING_LOOP_SECONDS - elapsed)
        time.sleep(sleep_for)
