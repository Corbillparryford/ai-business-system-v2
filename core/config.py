"""
core/config.py
==============
Single source of truth for all configuration.
Every module imports from here — never reads os.environ directly.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise SystemExit(
            f"\n[FATAL] Required env var '{key}' is not set.\n"
            f"Add it to .env or Railway Variables.\n"
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# ── Required ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")

# ── Sports APIs ────────────────────────────────────────────────────────────────
ODDS_API_KEY   = _optional("ODDS_API_KEY")
KALSHI_API_KEY = _optional("KALSHI_API_KEY")

# ── Trading APIs ───────────────────────────────────────────────────────────────
ALPACA_API_KEY    = _optional("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _optional("ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = "https://data.alpaca.markets/v2"

# ── Discord — PREMIUM SPORTS ───────────────────────────────────────────────────
DISCORD_WEBHOOK_EV             = _optional("DISCORD_WEBHOOK_EV")
DISCORD_WEBHOOK_ARB            = _optional("DISCORD_WEBHOOK_ARB")
DISCORD_WEBHOOK_SPORTS_RESULTS = _optional("DISCORD_WEBHOOK_SPORTS_RESULTS")

# ── Discord — PREMIUM TRADING ──────────────────────────────────────────────────
DISCORD_WEBHOOK_TRADING        = _optional("DISCORD_WEBHOOK_TRADING")
DISCORD_WEBHOOK_TRADE_UPDATES  = _optional("DISCORD_WEBHOOK_TRADE_UPDATES")
DISCORD_WEBHOOK_TRADE_RESULTS  = _optional("DISCORD_WEBHOOK_TRADE_RESULTS")

# ── Discord — FREE FUNNEL ──────────────────────────────────────────────────────
DISCORD_WEBHOOK_FREE            = _optional("DISCORD_WEBHOOK_FREE")
DISCORD_WEBHOOK_RESULTS_PREVIEW = _optional("DISCORD_WEBHOOK_RESULTS_PREVIEW")
DISCORD_WEBHOOK_ANNOUNCEMENTS   = _optional("DISCORD_WEBHOOK_ANNOUNCEMENTS")

# ── Discord — SYSTEM ───────────────────────────────────────────────────────────
DISCORD_WEBHOOK_HEALTH = _optional("DISCORD_WEBHOOK_HEALTH")

# ── Content (optional) ─────────────────────────────────────────────────────────
ELEVENLABS_API_KEY     = _optional("ELEVENLABS_API_KEY")
PEXELS_KEY             = _optional("PEXELS_KEY")
TIKTOK_ACCESS_TOKEN    = _optional("TIKTOK_ACCESS_TOKEN")
INSTAGRAM_ACCESS_TOKEN = _optional("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_USER_ID      = _optional("INSTAGRAM_USER_ID")
AMAZON_AFFILIATE_TAG   = _optional("AMAZON_AFFILIATE_TAG")

# ── Engine timing ──────────────────────────────────────────────────────────────
SPORTS_LOOP_SECONDS  = 60
TRADING_LOOP_SECONDS = 30
CONTENT_LOOP_HOURS   = 3

# ── Sports thresholds ──────────────────────────────────────────────────────────
SPORTS_EV_MIN_EDGE = 3.0
SPORTS_ARB_MIN_PCT = 1.5
SPORTS_GAME_WINDOW = (0, 100000)   # no time restriction — scan all games

# ── Cache / dedup ──────────────────────────────────────────────────────────────
CACHE_TTL_SPORTS      = 30
CACHE_TTL_TRADING     = 60
EDGE_REPOST_THRESHOLD = 1.5

# ── Lists ──────────────────────────────────────────────────────────────────────
TRADING_WATCHLIST = [
    "AAPL", "NVDA", "TSLA", "META", "MSFT",
    "AMZN", "AMD",  "GOOGL", "SPY",  "QQQ",
    "SOFI", "PLTR", "HOOD", "COIN", "ARM",
]

SPORTS_KEYS = [
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "basketball_ncaab",
]

SOFT_BOOKS  = ["draftkings", "fanduel", "betmgm", "caesars"]
SHARP_BOOKS = ["pinnacle"]

DB_PATH    = "signals.db"
CACHE_FILE = "signal_cache.json"
