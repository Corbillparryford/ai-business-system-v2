"""
core/logger.py — shared logging setup.
Call setup_logging() once in main.py.
"""

import logging
import sys


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)-22s] %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    for noisy in ["urllib3", "requests", "anthropic", "websockets", "moviepy"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
