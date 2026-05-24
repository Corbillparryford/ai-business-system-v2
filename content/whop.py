"""
content/whop.py — Whop promotional posting.

Posts teaser messages to Whop when signals fire and on scheduled cadence.
Requires WHOP_WEBHOOK_URL pointed at a Zapier/Make webhook that creates
Whop community posts. Leave blank to disable silently.
"""

import logging
import time

import requests

from core.config import WHOP_WEBHOOK_URL, WHOP_STORE_URL

log = logging.getLogger(__name__)


def _post(message: str) -> bool:
    if not WHOP_WEBHOOK_URL:
        return False
    try:
        r = requests.post(WHOP_WEBHOOK_URL, json={"content": message, "text": message}, timeout=10)
        if r.status_code in (200, 201, 204):
            return True
        log.debug("Whop webhook HTTP %d", r.status_code)
        return False
    except Exception as e:
        log.debug("Whop post failed: %s", e)
        return False


def post_signal_teaser(signal_type: str, detail: str):
    """Post a signal teaser (no sensitive details)."""
    emoji = "📈" if signal_type == "trading" else "🎯"
    _post(
        f"{emoji} **New signal live for Premium members.**\n\n"
        f"{detail}\n\n"
        f"🔓 Get full access:\n{WHOP_STORE_URL}"
    )


def post_win(play: str, edge: float):
    """Post a win announcement."""
    _post(
        f"✅ **Winning signal closed.**\n\n"
        f"🎯 {play} (+{edge:.1f}% edge)\n\n"
        f"Premium members had the full details.\n"
        f"🔓 Join here:\n{WHOP_STORE_URL}"
    )


def run_scheduled_post():
    """Post a promotional message. Called by content cycle (every 3h)."""
    _post(
        f"🚨 **Signals are live.**\n\n"
        f"📊 +EV bets  📈 Trading setups  ⚖️ Arbitrage\n\n"
        f"AI-driven. Data-backed. Updated continuously.\n\n"
        f"🔓 Get full access:\n{WHOP_STORE_URL}"
    )


def send_daily_content():
    """
    Generate and send social media content to #content Discord channel.
    Pulls live win/loss stats from results_tracker, asks Claude to write
    Instagram + Twitter copy in aggressive short-line style.
    Posts to content webhook for manual publishing.
    Called every ~3 hours by the content engine loop.
    Skips silently if no wins or losses recorded yet.
    """
    import requests as _requests
    from core.config import DISCORD_WEBHOOK_HEALTH   # reuse health webhook pattern
    from discord.poster import send, WEBHOOKS

    content_webhook = WEBHOOKS.get("content", "")
    if not content_webhook:
        log.debug("send_daily_content: no content webhook configured")
        return

    try:
        from sports.results_tracker import get_results
        results = get_results()
    except Exception as e:
        log.error("send_daily_content: could not get results — %s", e)
        return

    wins   = results.get("sports_wins",   0)
    losses = results.get("sports_losses", 0)

    if wins == 0 and losses == 0:
        log.debug("send_daily_content: no activity today — skipping")
        return

    # Include parlay reference if one was posted today
    parlay_line = ""
    try:
        from sports.parlay import get_todays_parlay_result
        pr = get_todays_parlay_result()
        if pr:
            parlay_line = f"Longshot Parlay: {pr}\n"
    except Exception:
        pass

    prompt = (
        "You are generating social media content for an AI sports betting system.\n\n"
        f"Sports Wins: {wins}\n"
        f"Sports Losses: {losses}\n"
        f"{parlay_line}\n"
        "Generate content with this EXACT format:\n\n"
        "IMAGE PROMPT:\n"
        "[dark finance dashboard style, green for wins, red for losses, "
        "show score and THE SHARP MARGIN wordmark, Instagram square]\n\n"
        "INSTAGRAM:\n"
        "[2-4 short punchy lines, each line = impact, "
        "confident tone, no emojis spam, end with https://whop.com/@thesharpmargin]\n\n"
        "TWITTER:\n"
        "[max 2 lines, sharp and direct, end with https://whop.com/@thesharpmargin]\n\n"
        "RULES:\n"
        "- break lines, do not write paragraphs\n"
        "- confident and slightly aggressive tone\n"
        "- contrast: us vs average bettors\n"
        "- no educational language\n"
        "- no corporate phrases\n"
        "- if parlay was posted today, reference it subtly\n"
        "- no long explanations\n"
    )

    try:
        from core.claude_client import call_claude
        response = call_claude(prompt, max_tokens=600, temperature=0.7)
    except Exception as e:
        log.error("send_daily_content: Claude call failed — %s", e)
        return

    if not response:
        log.warning("send_daily_content: empty Claude response")
        return

    sep  = "─────────────────────────────"
    date_str = __import__("datetime").date.today().strftime("%B %d, %Y")
    message  = (
        f"📊 **DAILY CONTENT READY — {date_str}**\n"
        f"Results: {wins}W / {losses}L\n\n"
        f"{sep}\n"
        f"{response.strip()}\n"
        f"{sep}"
    )

    send(content_webhook, message)
    log.info("Daily content sent to #content channel (%dW/%dL)", wins, losses)
