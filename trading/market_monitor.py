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
from core.claude_client import call_trading_brain, call_result_summary, validate_signal
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
    update_text = hit.get("update_text", "")

    # Fetch signal from DB
    sig = get_trading_signal_by_ticker(ticker)
    if not sig:
        log.debug("No active signal found for %s (already closed?)", ticker)
        return

    # Close in DB
    close_trading_signal(sig["id"], outcome, close_price, pnl_pct)

    # Post update to #trade-updates
    update_msg = {
        "ticker":      ticker,
        "outcome":     outcome,
        "close_price": close_price,
        "pnl_pct":     pnl_pct,
        "update_text": update_text,
    }
    post_signal(update_msg, "update")

    # If terminal outcome (not just T1), post full result
    if outcome in ("TARGET_2", "STOP"):
        completed = [{
            "signal_id":   sig["id"],
            "ticker":      ticker,
            "signal_type": sig["signal_type"],
            "pattern":     sig.get("pattern"),
            "entry_price": sig.get("entry_price"),
            "close_price": close_price,
            "outcome":     outcome,
            "pnl_pct":     pnl_pct,
            "reasoning":   sig.get("reasoning"),
            "result_kind": "trading",
        }]
        result_data = call_result_summary(completed, "trading")
        for r in result_data.get("results", []):
            r["result_kind"] = "trading"
            post_signal(r, "result")

    _push_ws({"type": "SIGNAL_INVALIDATED", "ticker": ticker})


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_market_monitor():
    log.info("Market monitor started")
    while True:
        loop_start = time.time()
        try:
            if not is_market_hours():
                time.sleep(60)
                continue

            # Build analysis payload
            analysis = []
            for ticker in TRADING_WATCHLIST:
                bars   = _fetch_bars(ticker)
                result = analyse_ticker(ticker, bars)
                if result.get("has_patterns"):
                    analysis.append(result)

            active = get_active_trading_signals()

            if analysis or active:
                result = call_trading_brain(
                    analysis, active,
                    market_condition=get_market_condition(),
                    vix=get_vix(),
                )

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
                    if signal["ticker"] in active_tickers:
                        continue
                    check = validate_signal(signal)
                    if not check.get("approved", True):
                        log.info("Signal rejected: %s — %s",
                                 signal["ticker"], check.get("reason"))
                        continue
                    signal["confidence"] = check.get("adjusted_confidence",
                                                      signal.get("confidence", 5))
                    save_trading_signal(signal)
                    post_signal(signal, "trading")
                    _push_ws({"type": "NEW_SIGNAL", "signal": signal})
                    log.info("Signal posted: %s %s", signal["signal_type"], signal["ticker"])

            else:
                log.debug("No patterns and no active signals — skipping Claude call")

        except Exception as e:
            log.error("market_monitor error: %s", e)
            try:
                post_health_alert("market_monitor", str(e))
            except Exception:
                pass

        elapsed   = time.time() - loop_start
        sleep_for = max(0.0, TRADING_LOOP_SECONDS - elapsed)
        time.sleep(sleep_for)
