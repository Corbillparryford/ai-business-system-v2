
"""
discord/poster.py — CLEAN PRODUCTION VERSION
✅ Fixed formatting
✅ Fixed dedup
✅ Stable output
✅ Content queue included
"""

import hashlib
import json
import logging
import time
from datetime import datetime
import requests

from core.config import (
    DISCORD_WEBHOOK_EV,
    DISCORD_WEBHOOK_TRADING,
    DISCORD_WEBHOOK_FREE,
    DISCORD_WEBHOOK_RESULTS_PREVIEW,
    DISCORD_WEBHOOK_SPORTS_RESULTS,
    DISCORD_WEBHOOK_HEALTH,
    DISCORD_WEBHOOK_CONTENT,
    CACHE_FILE,
    FREE_WIN_TRIGGER,
    FREE_HIGH_EDGE_TRIGGER,
    FREE_MAX_POSTS_PER_DAY,
)

log = logging.getLogger(__name__)
SEP = "─────────────────────────────"

# ── WEBHOOKS ─────────────────────────

WEBHOOKS = {
    "sports_ev": DISCORD_WEBHOOK_EV,
    "trading": DISCORD_WEBHOOK_TRADING,
    "free": DISCORD_WEBHOOK_FREE,
    "results": DISCORD_WEBHOOK_SPORTS_RESULTS,
    "preview": DISCORD_WEBHOOK_RESULTS_PREVIEW,
    "health": DISCORD_WEBHOOK_HEALTH,
    "content": DISCORD_WEBHOOK_CONTENT,
}

# ── SEND ────────────────────────────

def send(webhook, message):
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=10)
    except Exception as e:
        log.error(f"Send failed: {e}")

# ── CACHE (DEDUP) ───────────────────

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
        f"{signal.get('matchup','')}|{signal.get('play','')}|{signal.get('odds','')}|{signal.get('book','')}".encode()
    ).hexdigest()

def _should_post(signal, cache):
    key = _signal_key(signal)

    if key not in cache:
        return True, key

    old_edge = float(cache[key]["edge"])
    new_edge = float(signal.get("edge", 0))

    if abs(new_edge - old_edge) >= 1.0:
        return True, key

    return False, key

# ── FORMATTERS ──────────────────────

def _fmt_ev(signal):
    matchup = signal.get("matchup", "Unknown matchup")
    play = signal.get("play", "Unknown play")
    book = signal.get("book", "market")
    odds = signal.get("odds", "N/A")
    edge = round(float(signal.get("edge", 0)), 2)
    confidence = signal.get("confidence", 8)
    timing = signal.get("timing", "N/A")
    reasoning = signal.get(
        "reasoning",
        f"True probability exceeds implied probability by {edge}%."
    )

    return (
        f"{SEP}\n"
        f"🎯 **{matchup}**\n"
        f"📊 **Play:** {play} @ **{book}**\n"
        f"💰 Odds: `{odds}` | 📈 Edge: **+{edge}%**\n"
        f"🧠 Confidence: {confidence}/10\n"
        f"⏱ {timing}\n"
        f"💡 {reasoning}\n"
        f"{SEP}"
    )

def _fmt_trading(signal):
    return (
        f"{SEP}\n"
        f"📈 **{signal.get('ticker','N/A')} — {signal.get('signal_type','BUY')}**\n"
        f"💰 Entry: `${signal.get('entry_price','N/A')}`\n"
        f"🎯 Targets: `${signal.get('target_1','N/A')}` / `${signal.get('target_2','N/A')}`\n"
        f"🛑 Stop: `${signal.get('stop_loss','N/A')}`\n"
        f"📊 Confidence: {signal.get('confidence',8)}/10\n"
        f"💡 {signal.get('reasoning','Strategy-based trade')}\n"
        f"{SEP}"
    )

# ── SIGNAL POSTING ──────────────────

def post_signal(signal, signal_type):

    cache = _load_cache()
    allowed, key = _should_post(signal, cache)

    if not allowed:
        return

    cache[key] = {
        "edge": signal.get("edge", 0),
        "time": datetime.utcnow().isoformat()
    }
    _save_cache(cache)

    if signal_type == "sports":
        send(WEBHOOKS["sports_ev"], _fmt_ev(signal))

    elif signal_type == "trading":
        send(WEBHOOKS["trading"], _fmt_trading(signal))


# ── FREE CHANNEL ────────────────────

FREE_POST_COUNT = 0
FREE_LAST_POST_TIME = 0
FREE_KEYS = set()
_win_buffer = []

def _can_post_free(key):
    if FREE_POST_COUNT >= FREE_MAX_POSTS_PER_DAY:
        return False
    if time.time() - FREE_LAST_POST_TIME < 1800:
        return False
    if key in FREE_KEYS:
        return False
    return True

def _record_free(key):
    global FREE_POST_COUNT, FREE_LAST_POST_TIME
    FREE_POST_COUNT += 1
    FREE_LAST_POST_TIME = time.time()
    FREE_KEYS.add(key)

def record_win(play, edge):

    if edge < 4:
        return

    global _win_buffer
    _win_buffer.append({"play": play, "edge": edge})

    if edge >= FREE_HIGH_EDGE_TRIGGER or len(_win_buffer) >= FREE_WIN_TRIGGER:
        _fire_free()

def _fire_free():
    global _win_buffer
    wins = _win_buffer
    _win_buffer = []

    key = "|".join(sorted(w["play"] for w in wins))

    if not _can_post_free(key):
        return

    msg = (
        f"{SEP}\n"
        f"🔥 {len(wins)} WIN{'S' if len(wins)>1 else ''}\n\n"
        + "\n".join(f"✅ {w['play']}" for w in wins[:3])
        + f"\n\nhttps://whop.com/@thesharpmargin\n"
        f"{SEP}"
    )

    send(WEBHOOKS["free"], msg)
    _record_free(key)

# ── CONTENT QUEUE ──────────────────

def send_content_queue(wins, losses):

    msg = f"""
📊 CONTENT READY

{SEP}

🎨 IMAGE PROMPT:
Dark fintech results board showing:
Wins: {wins}
Losses: {losses}

{SEP}

📸 INSTAGRAM:
{wins}-{losses} today.

Most bettors lose.
We don’t.

https://whop.com/@thesharpmargin

{SEP}

🐦 TWITTER:
{wins}-{losses}.
Edge > luck.

https://whop.com/@thesharpmargin
"""

    send(WEBHOOKS["content"], msg)

# ── DAILY RECAP ────────────────────

def post_daily_recap(results):

    wins = len([r for r in results if r["result"] == "WIN"])
    losses = len([r for r in results if r["result"] == "LOSS"])

    msg = (
        f"{SEP}\n"
        f"📊 DAILY RESULTS\n\n"
        f"✅ Wins: {wins}\n❌ Losses: {losses}\n\n"
        f"https://whop.com/@thesharpmargin\n"
        f"{SEP}"
    )

    send(WEBHOOKS["results"], msg)
    send(WEBHOOKS["preview"], msg)

    send_content_queue(wins, losses)

# ── HEALTH ─────────────────────────

def post_health_alert(system, error):
    send(WEBHOOKS["health"], f"⚠️ {system}: {error}")
