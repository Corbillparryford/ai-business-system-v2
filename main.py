"""
main.py — AI Business System v3 master orchestrator.

python main.py

Threads:
  sports   — odds_monitor      every 120s (base), Claude every 3rd cycle
  trading  — market_monitor    every 60s, market hours, Claude every 2nd cycle
  content  — whop scheduler    every 3 hours
  ws       — ws_broadcaster    WebSocket on :8765
  watchdog — thread health     logs every 5 min
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
            log.error("Sports crash: %s — restarting in 30s", e)
            time.sleep(30)


def _run_trading():
    while True:
        try:
            from trading.market_monitor import run_market_monitor
            run_market_monitor()
        except Exception as e:
            log.error("Trading crash: %s — restarting in 30s", e)
            time.sleep(30)


def _run_content():
    interval = 3 * 60 * 60
    while True:
        try:
            start = time.time()
            from content.whop import run_scheduled_post, send_daily_content
            run_scheduled_post()
            send_daily_content()   # generates content every cycle (~3h)
            sleep_for = max(0.0, interval - (time.time() - start))
            log.info("Content cycle done. Next in %.1fh", sleep_for / 3600)
            time.sleep(sleep_for)
        except Exception as e:
            log.error("Content crash: %s — retrying in 15 min", e)
            time.sleep(900)


def _run_ws():
    while True:
        try:
            from trading.ws_broadcaster import run_ws_server
            run_ws_server()
        except Exception as e:
            log.error("WS crash: %s — restarting in 10s", e)
            time.sleep(10)


def _run_watchdog(threads: list):
    while True:
        time.sleep(300)
        for t in threads:
            log.info("Thread [%-10s] %s", t.name, "alive" if t.is_alive() else "DEAD ⚠️")


def main():
    log.info("=" * 60)
    log.info("  AI Business System v3")
    log.info("  Claude (Brain) + Executor")
    log.info("=" * 60)

    init_db()

    # Wire WS queue into trading monitor before threads start
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

    log.info("All systems running.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutdown.")


if __name__ == "__main__":
    main()
