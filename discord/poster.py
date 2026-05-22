
"""
discord/poster.py — THE SHARP MARGIN Discord delivery engine
+ CONTENT QUEUE SYSTEM (PRIVATE CHANNEL FOR SOCIAL POSTS)
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, date
import requests

from core.config import (
    DISCORD_WEBHOOK_EV,
    DISCORD_WEBHOOK_ARB,
    DISCORD_WEBHOOK_SPORTS_RESULTS,
    DISCORD_WEBHOOK_TRADING,
    DISCORD_WEBHOOK_TRADE_UPDATES,
    DISCORD_WEBHOOK_FREE,
    DISCORD_WEBHOOK_RESULTS_PREVIEW,
    DISCORD_WEBHOOK_HEALTH,
    DISCORD_WEBHOOK_CONTENT,
    CACHE_FILE,
    FREE_WIN_TRIGGER,
    FREE_HIGH_EDGE_TRIGGER,
    FREE_MAX_POSTS_PER_DAY,
    WHOP_STORE_URL,
)

log = logging.getLogger(__name__)
SEP = "─────────────────────────────"

# ── Webhooks ─────────────────────────────────────────────

WEBHOOKS = {
    "sports_ev": DISCORD_WEBHOOK_EV,
    "sports_arb": DISCORD_WEBHOOK_ARB,
    "sports_results": DISCORD_WEBHOOK_SPORTS_RESULTS,
    "trading": DISCORD_WEBHOOK_TRADING,
    "trade_updates": DISCORD_WEBHOOK_TRADE_UPDATES,
    "free": DISCORD_WEBHOOK_FREE,
    "results_preview": DISCORD_WEBHOOK_RESULTS_PREVIEW,
    "health": DISCORD_WEBHOOK_HEALTH,
    "content": DISCORD_WEBHOOK_CONTENT,   # ✅ NEW
}

# ── Generic sender ───────────────────────────────────────

def send(webhook: str, message: str):
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=10)
    except Exception as e:
        log.error(f"Send failed: {e}")

# ── CONTENT QUEUE SYSTEM (NEW CORE FEATURE) ───────────────

def send_content_queue(wins: int, losses: int, best_play: str):
    """
    Sends daily content (image prompt + captions) to your private Discord channel.
    """

    message = f"""
📊 **DAILY CONTENT READY**

{SEP}

🎨 **IMAGE PROMPT (Nano Banana):**
Create a sleek dark-themed financial results graphic.

- Title: "Daily Results"
- Wins: {wins} (green)
- Losses: {losses} (red)
- Modern fintech UI style
- Clean typography
- Minimal layout
- Subtle upward performance chart
- Premium, high-contrast Instagram-ready design

{SEP}

📸 **INSTAGRAM:**
{wins} wins. {losses} losses.

Another profitable day 📊

Most people guess — we follow data.

🔓 Join free:
https://whop.com/@thesharpmargin

{SEP}

🐦 **TWITTER/X:**
{wins}W / {losses}L.

Edge > luck.

https://whop.com/@thesharpmargin

{SEP}
"""

    send(WEBHOOKS["content"], message)


# ── FREE CHANNEL (WIN SYSTEM) ───────────────────────────

FREE_POST_COUNT = 0
FREE_LAST_POST_TIME = 0
FREE_POSTED_KEYS = set()
_win_buffer = []

def _can_post_free(key: str):
    global FREE_POST_COUNT, FREE_LAST_POST_TIME

    if FREE_POST_COUNT >= FREE_MAX_POSTS_PER_DAY:
        return False

    if time.time() - FREE_LAST_POST_TIME < 1800:
        return False

    if key in FREE_POSTED_KEYS:
        return False

    return True


def _record_free_post(key: str):
    global FREE_POST_COUNT, FREE_LAST_POST_TIME
    FREE_POST_COUNT += 1
    FREE_LAST_POST_TIME = time.time()
    FREE_POSTED_KEYS.add(key)


def record_win(play: str, edge: float):
    """
    Triggered when a bet wins.
    Handles both FREE CHANNEL + CONTENT CREATION.
    """

    if edge < 4:
        return

    global _win_buffer
    _win_buffer.append({"play": play, "edge": edge})

    if edge >= FREE_HIGH_EDGE_TRIGGER or len(_win_buffer) >= FREE_WIN_TRIGGER:
        _fire_win_post()


def _fire_win_post():
    global _win_buffer
    wins = _win_buffer
    _win_buffer = []

    if not wins:
        return

    key = "|".join([w["play"] for w in wins])

    if not _can_post_free(key):
        return

    lines = "\n".join(
        f"✅ {w['play']} (+{w['edge']:.1f}%)"
        for w in wins[:3]
    )

    msg = f"""
{SEP}
🔥 **{len(wins)} WIN{'S' if len(wins)>1 else ''}**

{lines}

🔓 Get full signals:
https://whop.com/@thesharpmargin
{SEP}
"""

    send(WEBHOOKS["free"], msg)
    _record_free_post(key)

    # ✅ ALSO SEND CONTENT FOR SOCIAL MEDIA
    send_content_queue(
        wins=len(wins),
        losses=0,
        best_play=wins[0]["play"]
    )


# ── SIGNAL POSTING (UNCHANGED CORE) ──────────────────────

def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return {}

def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except:
        pass

def _signal_key(signal):
    return hashlib.md5(
        (signal.get("matchup","") + signal.get("play","")).encode()
    ).hexdigest()


def post_signal(signal: dict, signal_type: str):

    cache = _load_cache()

    key = _signal_key(signal)

    # ✅ BLOCK DUPLICATES
    if key in cache:
        return

    cache[key] = True
    _save_cache(cache)

    # ── NORMAL POSTING ──

    if signal_type == "sports":
        webhook = WEBHOOKS["sports_ev"]
        msg = f"""
{SEP}
🎯 **{signal.get('matchup')}**
💰 {signal.get('play')}
Edge: +{signal.get('edge')}%
{SEP}
"""

    elif signal_type == "trading":
        webhook = WEBHOOKS["trading"]
        msg = f"""
{SEP}
📈 **{signal.get('ticker')}**
Entry: {signal.get('entry_price')}
{SEP}
"""

    else:
        return

    send(webhook, msg)



# ── DAILY RECAP (ENHANCED WITH CONTENT) ──────────────────

def post_daily_recap(results: list[dict]):

    wins = len([r for r in results if r["result"] == "WIN"])
    losses = len([r for r in results if r["result"] == "LOSS"])

    recap = f"""
{SEP}
📊 DAILY RESULTS

✅ Wins: {wins}
❌ Losses: {losses}

🔓 https://whop.com/@thesharpmargin
{SEP}
"""

    send(WEBHOOKS["sports_results"], recap)
    send(WEBHOOKS["results_preview"], recap)

    # ✅ ALSO SEND CONTENT FOR SOCIALS
    best = results[0]["play"] if results else "No standout play"

    send_content_queue(wins, losses, best)


# ── HEALTH ──────────────────────────────────────────────

def post_health_alert(system: str, error: str):
    send(WEBHOOKS["health"], f"⚠️ {system}: {error}")
