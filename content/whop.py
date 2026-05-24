"""
content/whop.py
===============
Whop promotional posting + Discord daily content generation.

Three entry points:
  run_scheduled_post()  — called every ~3h, posts to Whop community
  send_daily_content()  — called every ~3h, generates Instagram/Twitter
                          copy via Claude and posts to #content Discord channel
  post_win(play, edge)  — called on confirmed WIN, posts to Whop

Requires:
  WHOP_WEBHOOK_URL  — Zapier/Make webhook → Whop community post
  DISCORD_WEBHOOK_CONTENT — Discord #content channel webhook
"""

import logging
from datetime import date

import requests

from core.config import WHOP_WEBHOOK_URL, WHOP_STORE_URL

log = logging.getLogger(__name__)

SEP = "─────────────────────────────"


# ── Whop posting ───────────────────────────────────────────────────────────────

def _post_whop(message: str) -> bool:
    if not WHOP_WEBHOOK_URL:
        return False
    try:
        r = requests.post(
            WHOP_WEBHOOK_URL,
            json={"content": message, "text": message},
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        log.debug("Whop post failed: %s", e)
        return False


def post_signal_teaser(signal_type: str, detail: str):
    emoji = "📈" if signal_type == "trading" else "🎯"
    _post_whop(
        f"{emoji} **New signal live for Premium members.**\n\n"
        f"{detail}\n\n"
        f"🔓 Get full access:\n{WHOP_STORE_URL}"
    )


def post_win(play: str, edge: float):
    _post_whop(
        f"✅ **Winning signal closed.**\n\n"
        f"🎯 {play} (+{edge:.1f}% edge)\n\n"
        f"Premium members had the full details.\n"
        f"🔓 Join here:\n{WHOP_STORE_URL}"
    )


def run_scheduled_post():
    _post_whop(
        f"Signals are live.\n\n"
        f"📊 +EV bets  📈 Trading setups  ⚖️ Arbitrage\n\n"
        f"AI-driven. Data-backed. Updated continuously.\n\n"
        f"🔓 Get full access:\n{WHOP_STORE_URL}"
    )


# ── Daily content generation ───────────────────────────────────────────────────

def send_daily_content():
    """
    Generate and post daily social media content to #content Discord channel.

    Pulls wins/losses from results_tracker and bankroll % from performance_tracker.
    Sends a Claude prompt for human-toned Instagram + Twitter copy.
    Skips silently if no activity today.
    Called every ~3 hours by the content engine loop.
    """
    from discord.poster import send, WEBHOOKS

    content_webhook = WEBHOOKS.get("content", "")
    if not content_webhook:
        log.debug("send_daily_content: DISCORD_WEBHOOK_CONTENT not configured")
        return

    # ── Gather data ────────────────────────────────────────────────────────────
    wins   = 0
    losses = 0
    bankroll_pct = 0.0

    try:
        from sports.results_tracker import get_results
        results    = get_results()
        wins       = results.get("sports_wins",   0)
        losses     = results.get("sports_losses", 0)
    except Exception as e:
        log.error("send_daily_content: results fetch failed — %s", e)
        return

    if wins == 0 and losses == 0:
        log.debug("send_daily_content: no activity yet today — skipping")
        return

    try:
        from sports.performance_tracker import get_performance
        perf         = get_performance()
        bankroll_pct = perf.get("bankroll_pct", 0.0)
    except Exception as e:
        log.warning("send_daily_content: performance tracker unavailable — %s", e)

    # ── Optional context lines ─────────────────────────────────────────────────
    bankroll_line = (
        f"Bankroll change today: {bankroll_pct:+.1f}%\n"
        if bankroll_pct != 0.0 else ""
    )

    parlay_line = ""
    try:
        from sports.parlay import get_todays_parlay_result
        pr = get_todays_parlay_result()
        if pr:
            parlay_line = f"Parlay: {pr}\n"
    except Exception:
        pass

    # ── Claude prompt ──────────────────────────────────────────────────────────
    prompt = (
        "You are generating social media content for a sports betting signal service.\n\n"
        f"Today's record: {wins}W – {losses}L\n"
        f"{bankroll_line}"
        f"{parlay_line}\n"
        "Write content in this EXACT format and nothing else:\n\n"
        "IMAGE PROMPT:\n"
        "Dark charcoal background, clean dashboard style. "
        "List each result with team name and ✅ or ❌. "
        f"Show total record {wins}–{losses}. "
        "Subtle equity curve line on the right. "
        "THE SHARP MARGIN wordmark bottom right. No photos.\n\n"
        "INSTAGRAM:\n"
        "3–5 short lines. Human and calm. Not robotic. Not hype. "
        "Mention the daily record. "
        "Mention bankroll % naturally (e.g. 'still up X% on the bankroll'). "
        "If parlay didn't hit, say so casually. "
        "End with https://whop.com/@thesharpmargin\n\n"
        "TWITTER:\n"
        "1–2 lines. Short version of Instagram. "
        "End with https://whop.com/@thesharpmargin\n\n"
        "TONE RULES:\n"
        "- Sound like a real person logging results, not a brand\n"
        "- Calm confidence, not aggression\n"
        "- No excuses on bad days\n"
        "- No fake motivation\n"
        "- No corporate language\n"
        "- Break lines — do not write paragraphs\n\n"
        "STYLE EXAMPLES:\n"
        "Good day: '3–1 today. Nothing flashy, just consistent. "
        "Still up 4.2% on the bankroll overall. Parlay didn't get there.'\n"
        "Bad day: '1–5 today. One of those. "
        "Down 2.1% on the day but the long-term numbers are still solid.'\n"
    )

    # ── Call Claude ────────────────────────────────────────────────────────────
    try:
        from core.claude_client import call_claude
        response = call_claude(prompt, max_tokens=600, temperature=0.7)
    except Exception as e:
        log.error("send_daily_content: Claude call failed — %s", e)
        return

    if not response or not response.strip():
        log.warning("send_daily_content: empty Claude response")
        return

    # ── Format and post to Discord ─────────────────────────────────────────────
    today_str = date.today().strftime("%B %d, %Y")
    bankroll_display = f" | Bankroll: {bankroll_pct:+.1f}%" if bankroll_pct != 0.0 else ""

    message = (
        f"📊 **CONTENT READY — {today_str}**\n"
        f"Results: {wins}W / {losses}L{bankroll_display}\n\n"
        f"{SEP}\n"
        f"{response.strip()}\n"
        f"{SEP}"
    )

    if send(content_webhook, message):
        log.info("Daily content posted (%dW/%dL bankroll_pct=%+.1f%%)",
                 wins, losses, bankroll_pct)
    else:
        log.warning("send_daily_content: Discord send failed")
