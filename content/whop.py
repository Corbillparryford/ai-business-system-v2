"""
content/whop.py
===============
Whop promotional posting + Discord daily content generation.

send_daily_content():
  - Fetches wins/losses, bankroll %, and today's exact bet list
  - Sends Claude a detailed prompt for human-toned Instagram + Twitter copy
  - Image prompt lists EACH game result exactly
  - Posts to #content Discord channel for manual publishing

run_scheduled_post():
  - General promo post to Whop community, every ~3h

post_win(play, edge):
  - Posts a win announcement to Whop community
"""

import logging
from datetime import date

import requests

from core.config import WHOP_WEBHOOK_URL, WHOP_STORE_URL

log = logging.getLogger(__name__)
SEP = "─────────────────────────────"


# ── Whop community posts ───────────────────────────────────────────────────────

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

    Pulls:
      - wins/losses from results_tracker
      - bankroll % from performance_tracker
      - exact bet list from odds_monitor.get_daily_bets()
      - parlay summary from parlay.get_todays_parlay_result()

    Generates via Claude:
      - image prompt (lists each game result)
      - Instagram caption (human tone, mentions bankroll %)
      - Twitter post (short version)

    Skips silently if no activity today.
    """
    from discord.poster import send, WEBHOOKS

    content_webhook = WEBHOOKS.get("content", "")
    if not content_webhook:
        log.debug("send_daily_content: DISCORD_WEBHOOK_CONTENT not set")
        return

    # ── Gather data ────────────────────────────────────────────────────────────
    wins   = 0
    losses = 0
    try:
        from sports.results_tracker import get_results
        r    = get_results()
        wins = r.get("sports_wins",   0)
        losses = r.get("sports_losses", 0)
    except Exception as e:
        log.error("send_daily_content: results_tracker failed — %s", e)
        return

    if wins == 0 and losses == 0:
        log.debug("send_daily_content: no activity today — skipping")
        return

    bankroll_pct = 0.0
    try:
        from sports.performance_tracker import get_performance
        bankroll_pct = get_performance().get("bankroll_pct", 0.0)
    except Exception as e:
        log.warning("send_daily_content: performance_tracker unavailable — %s", e)

    daily_bets: list = []
    try:
        from sports.odds_monitor import get_daily_bets
        daily_bets = get_daily_bets()
    except Exception:
        pass

    parlay_note = ""
    try:
        from sports.parlay import get_todays_parlay_result
        pr = get_todays_parlay_result()
        if pr:
            parlay_note = pr
    except Exception:
        pass

    # Check for arb activity
    had_arb = any(b.get("is_arb") for b in daily_bets)

    # ── Build per-bet result list for image prompt ─────────────────────────────
    bet_lines_for_image = "\n".join(
        f"{b['matchup']} — {'✅' if b['result'] == 'WIN' else '❌'}"
        for b in daily_bets
    ) or f"Record: {wins}–{losses}"

    # ── Build bankroll string ──────────────────────────────────────────────────
    bk_str = f"+{bankroll_pct:.1f}%" if bankroll_pct >= 0 else f"{bankroll_pct:.1f}%"

    # ── Arb context ───────────────────────────────────────────────────────────
    arb_context = (
        "Arb bets: yes — mention naturally e.g. 'arb helped smooth things out'\n"
        if had_arb else
        "Arb bets: none today\n"
    )

    # ── Parlay context ────────────────────────────────────────────────────────
    parlay_context = (
        f"Parlay: {parlay_note}\n"
        if parlay_note else
        "Parlay: none today\n"
    )

    # ── Claude prompt ──────────────────────────────────────────────────────────
    prompt = (
        "You are writing daily social media content for a sports betting signal service.\n\n"
        f"Daily record: {wins}W – {losses}L\n"
        f"Bankroll change: {bk_str}\n"
        f"{arb_context}"
        f"{parlay_context}\n"
        "Write the following three sections. Label each exactly as shown.\n\n"
        "IMAGE PROMPT:\n"
        "Dark charcoal background. Clean dashboard style. "
        "List each bet result below exactly as given:\n"
        f"{bet_lines_for_image}\n"
        f"Below the list show: Record {wins}–{losses} | Bankroll {bk_str}\n"
        "THE SHARP MARGIN wordmark bottom right. No photos. Square format.\n\n"
        "INSTAGRAM:\n"
        f"Write 3–5 short lines about today being {wins}–{losses}. "
        "Human tone, calm confidence, not robotic. "
        f"Mention the bankroll is at {bk_str} — say it naturally. "
        + ("Mention arb helped balance things. " if had_arb else "") +
        (f"Mention parlay: '{parlay_note}'. " if parlay_note else "Mention parlay didn't get there. ") +
        "End with https://whop.com/@thesharpmargin\n"
        "Style: 'casual like someone logging results, not a brand posting content'\n\n"
        "TWITTER:\n"
        f"1-2 lines. Mention {wins}–{losses} and bankroll {bk_str}. "
        "End with https://whop.com/@thesharpmargin\n\n"
        "TONE RULES:\n"
        "- Sound like a real person, not a company\n"
        "- Calm and honest\n"
        "- No fake motivation\n"
        "- No excuses on bad days\n"
        "- Break lines — no paragraphs\n"
    )

    # ── Call Claude ────────────────────────────────────────────────────────────
    response = ""
    try:
        from core.claude_client import call_claude
        response = call_claude(prompt, max_tokens=700, temperature=0.7)
    except Exception as e:
        log.error("send_daily_content: Claude call failed — %s", e)
        return

    if not response or not response.strip():
        log.warning("send_daily_content: empty Claude response")
        return

    # ── Format Discord message ─────────────────────────────────────────────────
    today_str = date.today().strftime("%B %d, %Y")
    message = (
        f"📊 **CONTENT READY — {today_str}**\n"
        f"Results: {wins}W / {losses}L  |  Bankroll: {bk_str}\n\n"
        f"{SEP}\n"
        f"{response.strip()}\n"
        f"{SEP}"
    )

    if send(content_webhook, message):
        log.info("Daily content posted to #content (%dW/%dL bk=%s)", wins, losses, bk_str)
    else:
        log.warning("send_daily_content: Discord send failed")
