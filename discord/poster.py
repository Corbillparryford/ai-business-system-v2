
"""
discord/poster.py — THE SHARP MARGIN Discord delivery engine
CLEAN VERSION (Stable + Dedup + Proper Formatting + Content Queue)
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
)

log = logging.getLogger(__name__)
SEP = "─────────────────────────────"

# ── WEBHOOK MAP ─────────────────────────────────────────

WEBHOOKS = {
    "sports_ev": DISCORD_WEBHOOK_EV,
    "trading": DISCORD_WEBHOOK_TRADING,
    "free": DISCORD_WEBHOOK_FREE,
    "sports_results": DISCORD_WEBHOOK_SPORTS_RESULTS,
    "results_preview": DISCORD_WEBHOOK_RESULTS_PREVIEW,
    "health": DISCORD_WEBHOOK_HEALTH,
    "content": DISCORD_WEBHOOK_CONTENT,
}

# ── SEND HELPER ─────────────────────────────────────────

def send(webhook: str, message: str):
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=10)
    except Exception as e:
        log.error(f"Send failed: {e}")

# ── CACHE (DEDUP SYSTEM) ─────────────────────────────────

def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return {}

def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except:
        pass

def _signal_key(signal):
    """
    STRONG dedup key:
    allows different books, blocks identical duplicates
    """
    key_str = f"{signal.get('matchup','')}|{signal.get('play','')}|{signal.get('odds','')}|{signal.get('book','')}"
    return hashlib.md5(key_str.encode()).hexdigest()

def _should_post(signal, cache):
    key = _signal_key(signal)

    if key not in cache:
        return True, key

    # allow repost if edge changes significantly
    old_edge = float(cache[key].get("edge", 0))
    new_edge = float(signal.get("edge", 0))

    if abs(new_edge - old_edge) >= 1.0:
        return True, key

    return False, key

# ── FORMATTERS ──────────────────────────────────────────

def _fmt_ev(signal):
    return (
        f"{SEP}\n"
        f"🎯 **{signal.get('matchup','N/A')}**\n"
        f"📊 **Play:** {signal.get('play','N/A')} @ **{signal.get('book','N/A')}**\n"
        f"💰 Odds: `{signal.get('odds','N/A')}` | 📈 Edge: **+{signal.get('edge',0)}%**\n"
        f"🧠 Confidence: {signal.get('confidence',0)}/10\n"
        f"⏱ {signal.get('timing','N/A')}\n"
        f"💡 {signal.get('reasoning','')}\n"
        f"{SEP}"
    )

def _fmt_trading(signal):
    return (
        f"{SEP}\n"
        f"📈 **{signal.get('ticker','N/A')} — {signal.get('signal_type','BUY')}**\n"
        f"💰 Entry: `${signal.get('entry_price','N/A')}`\n"
        f"🎯 Targets: `${signal.get('target_1','N/A')}` / `${signal.get('target_2','N/A')}`\n"
        f"🛑 Stop: `${signal.get('stop_loss','N/A')}`\n"
        f"📊 Confidence: {signal.get('confidence','N/A')}/10\n"
        f"💡 {signal.get('reasoning','')}\n"
        f"{SEP}"
    )

# ── MAIN SIGNAL POST ─────────────────────────────────────

def post_signal(signal: dict, signal_type: str):

    cache = _load_cache()
    allowed, key = _should_post(signal, cache)

    if not allowed:
        return

    # store signal
    cache[key] = {
        "edge": signal.get("edge", 0),
        "time": datetime.utcnow().isoformat()
    }
    _save_cache(cache)

    if signal_type == "sports":
        msg = _fmt_ev(signal)
        send(WEBHOOKS["sports_ev"], msg)

    elif signal_type == "trading":
        msg = _fmt_trading(signal)
        send(WEBHOOKS["trading"], msg)

# ── FREE CHANNEL SYSTEM ─────────────────────────────────

FREE_POST_COUNT = 0
FREE_LAST_POST_TIME = 0
FREE_POSTED_KEYS = set()
_win_buffer = []

def _can_post_free(key):
    if FREE_POST_COUNT >= FREE_MAX_POSTS_PER_DAY:
        return False

    if time.time() - FREE_LAST_POST_TIME < 1800:
        return False

    if key in FREE_POSTED_KEYS:
        return False

    return True

def _record_free_post(key):
    global FREE_POST_COUNT, FREE_LAST_POST_TIME
    FREE_POST_COUNT += 1
    FREE_LAST_POST_TIME = time.time()
    FREE_POSTED_KEYS.add(key)

def record_win(play, edge):

    if edge < 4:
        return

    global _win_buffer
    _win_buffer.append({"play": play, "edge": edge})

    if edge >= FREE_HIGH_EDGE_TRIGGER or len(_win_buffer) >= FREE_WIN_TRIGGER:
        _fire_win_blast()

def _fire_win_blast():
    global _win_buffer

    wins = _win_buffer
    _win_buffer = []

    if not wins:
        return

    key = "|".join(sorted(w["play"] for w in wins))

    if not _can_post_free(key):
        return

    lines = "\n".join(
        f"✅ {w['play']} (+{w['edge']:.1f}%)"
        for w in wins[:3]
    )

    message = (
        f"{SEP}\n"
        f"🔥 **{len(wins)} WIN{'S' if len(wins) > 1 else ''}**\n\n"
        f"{lines}\n\n"
        f"🔓 https://whop.com/@thesharpmargin\n"
        f"{SEP}"
    )

    send(WEBHOOKS["free"], message)
    _record_free_post(key)

# ── DAILY CONTENT QUEUE ─────────────────────────────────

def send_content_queue(wins, losses):

    message = f"""
📊 CONTENT READY

{SEP}

🎨 IMAGE PROMPT:
Dark fintech dashboard showing:
Wins: {wins}
Losses: {losses}
Minimal, modern, high contrast.

{SEP}

📸 INSTAGRAM:
{wins}-{losses} today.

Most people guess.
We don’t.

https://whop.com/@thesharpmargin

{SEP}

🐦 TWITTER:
{wins}-{losses}.

Books mispriced again.

https://whop.com/@thesharpmargin
"""

    send(WEBHOOKS["content"], message)

# ── DAILY RECAP ─────────────────────────────────────────

def post_daily_recap(results):

    wins = len([r for r in results if r["result"] == "WIN"])
    losses = len([r for r in results if r["result"] == "LOSS"])

    summary = (
        f"{SEP}\n"
        f"📊 DAILY RESULTS\n\n"
        f"✅ Wins: {wins}\n"
        f"❌ Losses: {losses}\n\n"
        f"🔓 https://whop.com/@thesharpmargin\n"
        f"{SEP}"
    )

    send(WEBHOOKS["sports_results"], summary)
    send(WEBHOOKS["results_preview"], summary)

    send_content_queue(wins, losses)

# ── HEALTH ──────────────────────────────────────────────

def post_health_alert(system, error):
    send(WEBHOOKS["health"], f"⚠️ {system}: {error}")
