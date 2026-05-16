"""
main.py
=======
AI Business System v2 — Master Orchestrator.

python main.py

Starts and supervises five threads:
  sports   — odds_monitor      every 60s, always on
  trading  — market_monitor    every 30s, market hours only
  content  — content_engine    every 3h, always on
  ws       — ws_broadcaster    WebSocket server on :8765
  watchdog — thread health     logs every 5 min

Each engine thread wraps its own restart loop.
"""

import threading
import time
import logging

from core.logger import setup_logging
from core.db import init_db

setup_logging("INFO")
log = logging.getLogger("main")


def _run_sports():
    while True:
        try:
            from sports.odds_monitor import run_odds_monitor
            run_odds_monitor()
        except Exception as e:
            log.error("Sports engine crash: %s — restarting in 30s", e)
            time.sleep(30)


def _run_trading():
    while True:
        try:
            from trading.market_monitor import run_market_monitor
            run_market_monitor()
        except Exception as e:
            log.error("Trading engine crash: %s — restarting in 30s", e)
            time.sleep(30)


def _run_content():
    interval = 3 * 60 * 60
    while True:
        try:
            start = time.time()
            from content.content_engine import run_content_engine
            run_content_engine()
            sleep_for = max(0.0, interval - (time.time() - start))
            log.info("Content cycle done. Next in %.1fh", sleep_for / 3600)
            time.sleep(sleep_for)
        except Exception as e:
            log.error("Content engine crash: %s — retrying in 15 min", e)
            time.sleep(900)


def _run_ws():
    while True:
        try:
            from trading.ws_broadcaster import run_ws_server
            run_ws_server()
        except Exception as e:
            log.error("WS server crash: %s — restarting in 10s", e)
            time.sleep(10)


def _run_watchdog(threads: list):
    while True:
        time.sleep(300)
        for t in threads:
            log.info("Thread [%-10s] %s", t.name, "alive" if t.is_alive() else "DEAD ⚠️")


def main():
    log.info("=" * 62)
    log.info("  AI Business System v2")
    log.info("  Claude (Brain) + Manus (Executor)")
    log.info("  Discord: 10 channels | Whop: premium monetization")
    log.info("=" * 62)

    # Initialise all DB tables
    init_db()

    # Wire WebSocket queue into market_monitor before threads start
    try:
        from trading.ws_broadcaster import get_queue
        from trading import market_monitor
        market_monitor.set_ws_queue(get_queue())
        log.info("WS queue injected into market_monitor")
    except Exception as e:
        log.warning("WS queue injection failed: %s", e)

    threads = [
        threading.Thread(target=_run_sports,  name="sports",  daemon=True),
        threading.Thread(target=_run_trading, name="trading", daemon=True),
        threading.Thread(target=_run_content, name="content", daemon=True),
        threading.Thread(target=_run_ws,      name="ws",      daemon=True),
    ]

    for t in threads:
        t.start()
        log.info("Started: %s", t.name)
        time.sleep(1)

    watchdog = threading.Thread(
        target=_run_watchdog, args=(threads,), name="watchdog", daemon=True
    )
    watchdog.start()

    log.info("All systems running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutdown requested.")


if __name__ == "__main__":
    main()
