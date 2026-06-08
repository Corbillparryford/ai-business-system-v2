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


# ── Marketing layer — automated Whop posts ────────────────────────────────────
# Triggers: win (meaningful odds only), daily recap, bankroll milestones.
# Spam control: 30-min global cooldown across all marketing posts.
# All copy generated by Claude — calm, human, no hype.

import time as _time

_last_whop_post:     float = 0.0
_WHOP_COOLDOWN_SECS: int   = 1800   # 30 minutes between any marketing post
_MILESTONE_POSTED:   set   = set()  # tracks which % milestones have fired


def _can_post_to_whop() -> bool:
    global _last_whop_post
    now = _time.time()
    if now - _last_whop_post < _WHOP_COOLDOWN_SECS:
        log.debug("Whop marketing: cooldown active (%ds remaining)",
                  int(_WHOP_COOLDOWN_SECS - (now - _last_whop_post)))
        return False
    return True


def _record_whop_post():
    global _last_whop_post
    _last_whop_post = _time.time()


def _claude_text(prompt: str, max_tokens: int = 200) -> str:
    try:
        from core.claude_client import call_claude
        return call_claude(prompt, max_tokens=max_tokens, temperature=0.75).strip()
    except Exception as e:
        log.warning("Whop marketing: Claude unavailable — %s", e)
        return ""


def send_to_whop(message: str) -> bool:
    """
    Public entry point for all marketing posts.
    Enforces 30-min cooldown. Logs every send attempt.
    """
    if not message or not message.strip():
        return False
    if not _can_post_to_whop():
        return False
    result = _post_whop(message)
    if result:
        _record_whop_post()
        log.info("Whop post sent: %s", message[:80].replace("\n", " "))
    return result


# ── Win post ───────────────────────────────────────────────────────────────────

def post_win_marketing(play: str, edge: float, odds: float = 0.0,
                       bankroll_change: float = 0.0):
    """
    Post a win to Whop. Only fires for meaningful wins (odds >= 1.7 decimal,
    i.e. roughly +70 american or better). Claude writes the copy.

    Called from sports/odds_monitor._resolve_results() on WIN.
    """
    # Only post meaningful wins — skip near-even favourites
    MIN_DECIMAL_ODDS = 1.7
    if odds > 0 and odds < MIN_DECIMAL_ODDS:
        log.debug("Win Whop post skipped — odds %.2f below threshold %.2f", odds, MIN_DECIMAL_ODDS)
        return

    prompt = (
        "Write a short betting update for a community post.\n\n"
        f"Context:\n"
        f"- Play: {play}\n"
        f"- Edge: +{edge:.1f}%\n"
        + (f"- Odds: {odds:.2f} (decimal)\n" if odds else "") +
        (f"- Bankroll change: +{bankroll_change:.1f}%\n" if bankroll_change else "") +
        "\nStyle:\n"
        "- Calm and realistic, not excited\n"
        "- Slightly conversational\n"
        "- No hype words (no LOCK, INSANE, FIRE, guaranteed)\n"
        "- Sound like a person logging a result, not a brand\n"
        "- Maximum 3 lines\n"
        "- End with the store link on its own line\n\n"
        f"End with: {WHOP_STORE_URL}"
    )

    message = _claude_text(prompt)
    if not message:
        # Fallback template if Claude unavailable
        message = (
            f"✅ Win logged.\n"
            f"{play} came through (+{edge:.1f}% edge).\n"
            f"Steady as it goes.\n{WHOP_STORE_URL}"
        )

    send_to_whop(message)


# ── Daily recap ────────────────────────────────────────────────────────────────

def post_daily_recap_whop(wins: int, losses: int, bankroll_change: float,
                          arb_used: bool = False):
    """
    Post an end-of-day recap to Whop. Claude writes the copy.
    Called from main.py or odds_monitor at end of day.
    """
    total = wins + losses
    if total == 0:
        log.debug("Daily recap Whop post skipped — no bets today")
        return

    arb_line = (
        "Arbitrage helped keep things balanced." if arb_used else ""
    )
    bk_str = f"+{bankroll_change:.1f}%" if bankroll_change >= 0 else f"{bankroll_change:.1f}%"

    prompt = (
        "Write a short daily betting recap for a community post.\n\n"
        f"Context:\n"
        f"- Wins: {wins}\n"
        f"- Losses: {losses}\n"
        f"- Total bets: {total}\n"
        f"- Bankroll change: {bk_str}\n"
        + (f"- Note: {arb_line}\n" if arb_line else "") +
        "\nStyle:\n"
        "- Calm and professional\n"
        "- Slightly conversational\n"
        "- Honest — don't oversell a bad day\n"
        "- No hype, no excuses\n"
        "- Maximum 4 lines\n"
        "- Break lines (do not write paragraphs)\n"
        "- End with the store link on its own line\n\n"
        f"End with: {WHOP_STORE_URL}"
    )

    message = _claude_text(prompt, max_tokens=250)
    if not message:
        message = (
            f"📊 {wins}–{losses} today. Bankroll {bk_str}.\n"
            f"Long-term edge is still intact.\n{WHOP_STORE_URL}"
        )

    send_to_whop(message)


# ── Bankroll milestone posts ───────────────────────────────────────────────────

_MILESTONES = [5, 10, 15, 20, 25, 30, 50]


def check_and_post_milestone(bankroll_pct: float):
    """
    Post a milestone update when the bankroll crosses key % thresholds.
    Each milestone only fires once per session (tracked in _MILESTONE_POSTED).
    Called from content engine after get_performance().
    """
    global _MILESTONE_POSTED

    for milestone in _MILESTONES:
        if bankroll_pct >= milestone and milestone not in _MILESTONE_POSTED:
            prompt = (
                "Write a short bankroll growth update for a community post.\n\n"
                f"Context:\n"
                f"- Bankroll growth: +{bankroll_pct:.1f}%\n"
                f"- Milestone crossed: +{milestone}%\n\n"
                "Style:\n"
                "- Calm and confident\n"
                "- No hype\n"
                "- Sound like someone who expected this\n"
                "- 2–3 lines maximum\n"
                "- End with the store link on its own line\n\n"
                f"End with: {WHOP_STORE_URL}"
            )

            message = _claude_text(prompt)
            if not message:
                message = (
                    f"📈 Bankroll up +{bankroll_pct:.1f}%.\n"
                    f"Compounding as expected.\n{WHOP_STORE_URL}"
                )

            if send_to_whop(message):
                _MILESTONE_POSTED.add(milestone)
                log.info("Milestone post fired: +%d%%", milestone)
            break   # only one milestone per call
