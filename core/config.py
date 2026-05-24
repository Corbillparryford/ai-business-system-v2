"""
core/config.py — single source of truth for all configuration.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise SystemExit(f"\n[FATAL] Required env var '{key}' is not set.\n")
    return val


def _opt(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# ── Required ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")

# ── Sports ─────────────────────────────────────────────────────────────────────
ODDS_API_KEY = _opt("ODDS_API_KEY")

# ── Trading ────────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = _opt("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _opt("ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = "https://data.alpaca.markets/v2"

# ── Discord webhooks ───────────────────────────────────────────────────────────
DISCORD_WEBHOOK_EV             = _opt("DISCORD_WEBHOOK_EV")
DISCORD_WEBHOOK_ARB            = _opt("DISCORD_WEBHOOK_ARB")
DISCORD_WEBHOOK_SPORTS_RESULTS = _opt("DISCORD_WEBHOOK_SPORTS_RESULTS")
DISCORD_WEBHOOK_TRADING        = _opt("DISCORD_WEBHOOK_TRADING")
DISCORD_WEBHOOK_TRADE_UPDATES  = _opt("DISCORD_WEBHOOK_TRADE_UPDATES")
DISCORD_WEBHOOK_FREE           = _opt("DISCORD_WEBHOOK_FREE")
DISCORD_WEBHOOK_RESULTS_PREVIEW = _opt("DISCORD_WEBHOOK_RESULTS_PREVIEW")
DISCORD_WEBHOOK_HEALTH         = _opt("DISCORD_WEBHOOK_HEALTH")
DISCORD_WEBHOOK_CONTENT        = _opt("DISCORD_WEBHOOK_CONTENT")

# ── Whop ───────────────────────────────────────────────────────────────────────
WHOP_WEBHOOK_URL = _opt("WHOP_WEBHOOK_URL")
WHOP_STORE_URL   = "https://whop.com/the-sharp-margin"

# ── Engine timing ──────────────────────────────────────────────────────────────
SPORTS_LOOP_SECONDS  = 120   # 2 min base — ~360 OddsAPI calls/day
TRADING_LOOP_SECONDS = 60    # 1 min — market hours only

# ── Claude call controls ───────────────────────────────────────────────────────
SPORTS_CLAUDE_EVERY_N_CYCLES  = 3    # call Claude every 3rd sports cycle (6 min)
TRADING_CLAUDE_EVERY_N_CYCLES = 2    # call Claude every 2nd trading cycle (2 min)
SPORTS_MIN_EDGE_TO_CALL       = 3.5  # skip Claude if best edge below this %
MAX_OPPS_TO_CLAUDE            = 6    # max opportunities sent per Claude call

# ── Signal quality thresholds ─────────────────────────────────────────────────
SPORTS_EV_MIN_EDGE   = 3.0   # minimum +EV edge to flag opportunity
SPORTS_ARB_MIN_PCT   = 1.5   # minimum arb profit % to flag opportunity
TRADING_MIN_CONF     = 7     # minimum confidence to post trading signal
TRADING_MIN_RR       = 1.8   # minimum risk/reward to post trading signal

# ── Free channel control ───────────────────────────────────────────────────────
FREE_MIN_CONF_SPORTS   = 8     # minimum confidence to post to free channel
FREE_MIN_EDGE_SPORTS   = 6.0   # minimum edge % to post to free channel
FREE_MIN_CONF_TRADING  = 9     # minimum confidence for free trading post
FREE_WIN_TRIGGER       = 2     # post big-win blast after this many wins
FREE_HIGH_EDGE_TRIGGER = 8.0   # or immediately if single win had this edge %
FREE_MAX_POSTS_PER_DAY = 2     # hard cap on free channel posts per day

# ── Sports config ──────────────────────────────────────────────────────────────
SOFT_BOOKS  = ["draftkings", "fanduel", "betmgm", "caesars"]
SHARP_BOOKS = ["pinnacle"]

SPORTS_KEYS = [
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "basketball_ncaab",
    "soccer_epl",
    "soccer_usa_mls",
]

# ── Trading watchlist ──────────────────────────────────────────────────────────
TRADING_WATCHLIST = [
    "AAPL", "NVDA", "TSLA", "META", "MSFT",
    "AMZN", "AMD",  "GOOGL", "SPY",  "QQQ",
]

# ── Storage ────────────────────────────────────────────────────────────────────
DB_PATH    = "signals.db"
CACHE_FILE = "signal_cache.json"
