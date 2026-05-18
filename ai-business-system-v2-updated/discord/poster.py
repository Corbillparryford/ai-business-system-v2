"""
discord/poster.py
=================
THE SHARP MARGIN — Discord delivery engine.

Channel routing (NEVER change this mapping):
  PREMIUM SPORTS
    #ev-signals           ← DISCORD_WEBHOOK_EV
    #arb-signals          ← DISCORD_WEBHOOK_ARB
    #sports-results       ← DISCORD_WEBHOOK_SPORTS_RESULTS

  PREMIUM TRADING
    #trading-signals      ← DISCORD_WEBHOOK_TRADING
    #trade-updates        ← DISCORD_WEBHOOK_TRADE_UPDATES
    #trade-results        ← DISCORD_WEBHOOK_TRADE_RESULTS

  FREE FUNNEL
    #free-signals         ← DISCORD_WEBHOOK_FREE
    #results-preview      ← DISCORD_WEBHOOK_RESULTS_PREVIEW
    #announcements        ← DISCORD_WEBHOOK_ANNOUNCEMENTS

  SYSTEM
    #system-health        ← DISCORD_WEBHOOK_HEALTH

Core logic (preserved from original production code):
  - load_cache / save_cache
  - build_hash (MD5 on sorted JSON)
  - should_post (dedup + 1.5% edge-delta repost)
  - send (requests.post)
  - post_signal(signal, signal_type) — main entry point

signal_type values:
  "sports"   → premium EV/arb + free teaser
  "trading"  → premium signal + free teaser
  "update"   → #trade-updates only
  "result"   → #sports-results or #trade-results + #results-preview
  "teaser"   → #free-signals only
  "health"   → #system-health only
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta

import requests

from core.config import (
    DISCORD_WEBHOOK_EV,
    DISCORD_WEBHOOK_ARB,
    DISCORD_WEBHOOK_SPORTS_RESULTS,
    DISCORD_WEBHOOK_TRADING,
    DISCORD_WEBHOOK_TRADE_UPDATES,
    DISCORD_WEBHOOK_TRADE_RESULTS,
    DISCORD_WEBHOOK_FREE,
    DISCORD_WEBHOOK_RESULTS_PREVIEW,
    DISCORD_WEBHOOK_ANNOUNCEMENTS,
    DISCORD_WEBHOOK_HEALTH,
    CACHE_FILE,
    CACHE_TTL_SPORTS,
    CACHE_TTL_TRADING,
    EDGE_REPOST_THRESHOLD,
)

log = logging.getLogger(__name__)

WEBHOOKS = {
    # Premium sports
    "sports_ev":       DISCORD_WEBHOOK_EV,
    "sports_arb":      DISCORD_WEBHOOK_ARB,
    "sports_results":  DISCORD_WEBHOOK_SPORTS_RESULTS,
    # Premium trading
    "trading":         DISCORD_WEBHOOK_TRADING,
    "trade_updates":   DISCORD_WEBHOOK_TRADE_UPDATES,
    "trade_results":   DISCORD_WEBHOOK_TRADE_RESULTS,
    # Free funnel
    "free":            DISCORD_WEBHOOK_FREE,
    "results_preview": DISCORD_WEBHOOK_RESULTS_PREVIEW,
    "announcements":   DISCORD_WEBHOOK_ANNOUNCEMENTS,
    # System
    "health":          DISCORD_WEBHOOK_HEALTH,
}


# ── Cache (original production logic) ────────────────────────────────────────

def load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        log.error("Cache save failed: %s", e)


def _purge_expired(cache: dict) -> dict:
    now_dt = datetime.utcnow()
    return {
        k: v for k, v in cache.items()
        if _entry_alive(v, now_dt)
    }


def _entry_alive(entry: dict, now_dt: datetime) -> bool:
    try:
        posted = datetime.strptime(entry["posted_at"], "%Y-%m-%d %H:%M UTC")
        return now_dt - posted < timedelta(minutes=entry.get("ttl_minutes", CACHE_TTL_SPORTS))
    except Exception:
        return False


def build_hash(signal: dict) -> str:
    """MD5 on sorted JSON — original production logic."""
    raw = json.dumps(signal, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def should_post(signal: dict, cache: dict) -> tuple[bool, str]:
    """Original dedup logic: new hash → post. Edge delta >= 1.5% → repost."""
    sig_hash = build_hash(signal)
    if sig_hash not in cache:
        return True, sig_hash
    old_edge = cache[sig_hash]["signal"].get("edge", 0)
    new_edge = signal.get("edge", 0)
    if abs(new_edge - old_edge) >= EDGE_REPOST_THRESHOLD:
        return True, sig_hash
    return False, sig_hash


def now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


# ── Formatters ────────────────────────────────────────────────────────────────

WHOP_URL = "https://whop.com/the-sharp-margin"
SEP = "─────────────────────────────"


def _short_reason(text: str, max_len: int = 120) -> str:
    """Truncate reasoning to one compact line."""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text[:max_len].rsplit(" ", 1)[0] + "…" if len(text) > max_len else text


def _fmt_ev(signal: dict) -> str:
    return (
        f"{SEP}\n"
        f"🎯 **{signal.get('matchup', 'N/A')}**\n"
        f"📊 **Play:** {signal.get('play', 'N/A')} @ **{signal.get('book', 'N/A')}**\n"
        f"💰 Odds: `{signal.get('odds', 'N/A')}` | 📈 Edge: **+{signal.get('edge', 0)}%**\n"
        f"🧠 Confidence: {signal.get('confidence', 0)}/10 | True Prob: {signal.get('true_prob', 'N/A')}%\n"
        f"⏱ {signal.get('timing', 'N/A')}\n"
        f"💡 {_short_reason(signal.get('reasoning', ''))}\n"
        f"{SEP}"
    )


def _fmt_trading(signal: dict) -> str:
    """Clean entry signal — entry, targets, stop, confidence only."""
    action = signal.get("signal_type", "BUY")
    arrow  = "📈" if action == "BUY" else "📉"
    t1     = signal.get("target_1", "?")
    t2     = signal.get("target_2", "?")
    return (
        f"{SEP}\n"
        f"{arrow} **{signal.get('ticker','?')} — {action}**\n"
        f"💰 Entry: `${signal.get('entry_price','?')}`\n"
        f"🎯 Targets: `${t1}` / `${t2}`\n"
        f"🛑 Stop: `${signal.get('stop_loss','?')}`\n"
        f"📊 Confidence: {signal.get('confidence','?')}/10\n"
        f"{SEP}"
    )


def _fmt_trade_exit(ticker: str, exit_price: float, result: str, pnl_pct: float) -> str:
    """Clean exit signal posted only on terminal trade close."""
    arrow   = "📉" if result in ("STOP", "LOSS") else "📈"
    outcome = "WIN" if result in ("TARGET_1", "TARGET_2") else "LOSS"
    pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"
    return (
        f"{SEP}\n"
        f"{arrow} **{ticker} — SELL**\n"
        f"💰 Exit: `${exit_price}`\n"
        f"📊 Result: **{outcome}** | P&L: `{pnl_str}`\n"
        f"{SEP}"
    )


def _fmt_arb(signal: dict) -> str:
    """
    Full two-leg arbitrage breakdown. Each leg on its own line with
    sportsbook name, team, and exact odds clearly separated.
    """
    legs = signal.get("legs", [])

    # Build each leg block
    leg_lines = []
    leg_colours = ["🔵", "🔴", "🟢"]
    for i, leg in enumerate(legs):
        colour = leg_colours[i] if i < len(leg_colours) else "⚪"
        leg_lines.append(
            f"{colour} **Bet {i+1}:**\n"
            f"   Book: **{leg.get('book', '?')}**\n"
            f"   Team: {leg.get('side', '?')}\n"
            f"   Odds: `{leg.get('odds', '?')}`\n"
            f"   Stake: **${leg.get('stake', '?')}** per $1,000"
        )

    # Fallback when legs list is empty (plain-text parser didn't extract them)
    if not leg_lines:
        book_str  = signal.get("book", "N/A")
        play_str  = signal.get("play", "N/A")
        odds_str  = signal.get("odds", "N/A")
        leg_lines = [
            f"🔵 **Bet 1 / 2:**\n"
            f"   Books: **{book_str}**\n"
            f"   Play: {play_str}\n"
            f"   Odds: `{odds_str}`"
        ]

    legs_block = "\n".join(leg_lines)
    profit_pct = signal.get("arb_percentage", signal.get("edge", 0))
    profit_amt = signal.get("profit_per_1000", 0)

    return (
        f"{SEP}\n"
        f"⚖️ **ARBITRAGE OPPORTUNITY**\n"
        f"🏟 Match: **{signal.get('matchup', 'N/A')}**\n\n"
        f"{legs_block}\n\n"
        f"💰 Guaranteed Profit: **{profit_pct}%** (${profit_amt:.2f} per $1,000 staked)\n"
        f"✅ Execute BOTH bets at these exact odds simultaneously\n"
        f"⏱ {signal.get('timing', 'N/A')}\n"
        f"{SEP}"
    )


def _fmt_sports_result(result: dict) -> str:
    summary  = result.get("full_summary", "")
    matchup  = result.get("matchup", "")
    play     = result.get("play", "")
    book     = result.get("book", "")
    outcome  = result.get("result", "")
    edge     = result.get("edge_pct", result.get("edge", ""))
    # If Claude returned a full_summary, use it compressed; otherwise build from fields
    if summary:
        body = _short_reason(summary, 200)
    else:
        body = f"🎯 {play} @ {book}\n💰 Outcome: **{outcome}**"
        if edge:
            body += f"\n📈 Edge was: {edge}%"
    return (
        f"{SEP}\n"
        f"✅ **RESULT — {matchup or 'Sports'}**\n"
        f"{body}\n"
        f"{SEP}"
    )


def _fmt_trade_result(result: dict) -> str:
    summary  = result.get("full_summary", "")
    ticker   = result.get("ticker", "")
    outcome  = result.get("outcome", "")
    pnl      = result.get("pnl_pct", "")
    close_px = result.get("close_price", "")
    if summary:
        body = _short_reason(summary, 200)
    else:
        body = f"💰 Outcome: **{outcome}**"
        if close_px:
            body += f" @ ${close_px}"
        if pnl != "":
            pnl_str = f"+{pnl:.2f}%" if float(pnl) >= 0 else f"{pnl:.2f}%"
            body += f" | P&L: `{pnl_str}`"
    return (
        f"{SEP}\n"
        f"✅ **RESULT — {ticker or 'Trade'}**\n"
        f"{body}\n"
        f"{SEP}"
    )


def _fmt_free_sports(signal: dict) -> str:
    teaser = signal.get(
        "teaser_text",
        f"🔒 +EV signal detected on {signal.get('matchup', 'an upcoming game')}. "
        f"Full play, exact odds, and stake sizing in #ev-signals — Premium only."
    )
    return (
        f"{SEP}\n"
        f"🔒 **THE SHARP MARGIN — SIGNAL DETECTED**\n"
        f"{teaser}\n\n"
        f"🔓 Unlock full signals instantly:\n{WHOP_URL}\n"
        f"{SEP}"
    )


def _fmt_free_trading(signal: dict) -> str:
    teaser = signal.get(
        "teaser_text",
        f"📈 {signal.get('signal_type','BUY')} signal triggered on "
        f"`{signal.get('ticker','a stock')}`. Entry, stop, and targets in #trading-signals."
    )
    return (
        f"{SEP}\n"
        f"🔒 **TRADEFINDER AI — SIGNAL DETECTED**\n"
        f"{teaser}\n\n"
        f"🔓 Unlock full signals instantly:\n{WHOP_URL}\n"
        f"{SEP}"
    )


def _fmt_results_preview(result: dict) -> str:
    preview = result.get(
        "preview_text",
        "A signal was just closed. Full result breakdown in premium channels."
    )
    return (
        f"{SEP}\n"
        f"👀 **RESULTS PREVIEW**\n"
        f"{preview}\n\n"
        f"🔓 Unlock full signals instantly:\n{WHOP_URL}\n"
        f"{SEP}"
    )


def _fmt_content_alert(signal: dict) -> str:
    return (
        f"{SEP}\n"
        f"🎬 **CONTENT PUBLISHED**\n"
        f"**Product:** {signal.get('product_name', 'N/A')}\n"
        f"**Platform:** {signal.get('platform', 'TikTok')}\n"
        f"**Hook:** {signal.get('hook', '')}\n"
        f"**Link:** {signal.get('affiliate_url') or signal.get('tiktok_shop_url') or 'N/A'}\n"
        f"**Priority Score:** {signal.get('priority_score', 'N/A')}/10\n"
        f"**Post URL:** {signal.get('post_url', 'pending')}\n"
        f"{SEP}"
    )


def _fmt_health(system: str, error: str) -> str:
    return (
        f"⚠️ **SYSTEM ALERT** — {now()}\n"
        f"**System:** `{system}`\n"
        f"**Error:** {error}"
    )


# ── Send (with retry) ─────────────────────────────────────────────────────────

def send(webhook: str, message: str, retries: int = 3) -> bool:
    if not webhook:
        log.debug("send() skipped — no webhook URL")
        return False
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(webhook, json={"content": message}, timeout=10)
            if r.status_code in (200, 204):
                return True
            log.warning("Discord HTTP %d on attempt %d", r.status_code, attempt)
        except requests.RequestException as e:
            log.warning("send() attempt %d failed: %s", attempt, e)
        if attempt < retries:
            time.sleep(2 ** attempt)
    log.error("send() permanently failed after %d attempts", retries)
    return False


# ── Main entry point ──────────────────────────────────────────────────────────

def post_signal(signal: dict, signal_type: str):
    """
    Route a signal to the correct Discord channel(s).

    signal_type:
      "sports"  → #ev-signals or #arb-signals + #free-signals teaser
      "trading" → #trading-signals + #free-signals teaser
      "update"  → #trade-updates only
      "result"  → #sports-results or #trade-results + #results-preview
      "health"  → #system-health only
    """
    cache  = load_cache()
    cache  = _purge_expired(cache)

    # ── Results and updates skip dedup (always post) ──────────────────────────
    if signal_type == "update":
        outcome = signal.get("outcome", "")
        # Only post on terminal outcomes (full exit). TARGET_1 is suppressed —
        # it is an intermediate milestone, not an actionable exit.
        if outcome not in ("TARGET_2", "STOP", "INVALIDATED"):
            log.debug("Suppressing non-terminal update: %s %s", signal.get("ticker"), outcome)
            return
        if outcome == "INVALIDATED":
            # Simple invalidation notice — not a trade exit
            send(WEBHOOKS["trade_updates"],
                 f"{SEP}\n⛔ **{signal.get('ticker','?')} — SIGNAL CANCELLED**\n"
                 f"{signal.get('update_text','Stop level breached.')}\n{SEP}")
            return
        # Terminal exit — use clean SELL format
        msg = _fmt_trade_exit(
            ticker     = signal.get("ticker", "?"),
            exit_price = signal.get("close_price", 0.0),
            result     = outcome,
            pnl_pct    = signal.get("pnl_pct", 0.0),
        )
        send(WEBHOOKS["trade_updates"], msg)
        return

    if signal_type == "result":
        result_kind = signal.get("result_kind", "trading")
        if result_kind == "sports":
            # Individual sports results are suppressed — handled by daily recap
            log.debug("Individual sports result suppressed (daily recap handles this)")
            return
        else:
            send(WEBHOOKS["trade_results"], _fmt_trade_result(signal))
            send(WEBHOOKS["results_preview"], _fmt_results_preview(signal))
        return

    if signal_type == "health":
        send(WEBHOOKS["health"], _fmt_health(
            signal.get("system", "unknown"), signal.get("error", "")
        ))
        return

    # ── Deduplicated signal types ─────────────────────────────────────────────
    allowed, sig_hash = should_post(signal, cache)
    if not allowed:
        log.debug("Duplicate signal skipped: %s", sig_hash[:8])
        return

    if signal_type == "sports":
        is_arb = signal.get("type") == "ARBITRAGE"
        webhook = WEBHOOKS["sports_arb"] if is_arb else WEBHOOKS["sports_ev"]
        message = _fmt_arb(signal) if is_arb else _fmt_ev(signal)
        teaser  = _fmt_free_sports(signal)
        ttl     = CACHE_TTL_SPORTS

    elif signal_type == "trading":
        webhook = WEBHOOKS["trading"]
        message = _fmt_trading(signal)
        teaser  = _fmt_free_trading(signal)
        ttl     = CACHE_TTL_TRADING

    elif signal_type == "content":
        webhook = WEBHOOKS.get("content", "")
        message = _fmt_content_alert(signal)
        send(webhook, message)
        cache[sig_hash] = {"signal": signal, "posted_at": now(), "ttl_minutes": 180}
        save_cache(cache)
        log.info("Posted [content]: %s", signal.get("product_name", ""))
        return

    else:
        log.warning("Unknown signal_type passed to post_signal: %s", signal_type)
        return

    send(webhook, message)

    # Free channel: only post standout signals — high confidence AND significant edge.
    # Routine signals are suppressed to preserve free channel quality and reduce spam.
    FREE_MIN_CONFIDENCE = 8
    FREE_MIN_EDGE       = 5.0

    if signal_type == "sports":
        confidence = signal.get("confidence", 0)
        edge       = float(signal.get("edge", signal.get("arb_percentage", 0)))
        if confidence >= FREE_MIN_CONFIDENCE and edge >= FREE_MIN_EDGE:
            send(WEBHOOKS["free"], teaser)
        else:
            log.debug("Free channel suppressed (confidence=%s edge=%s): %s",
                      confidence, edge, signal.get("matchup", ""))

    elif signal_type == "trading":
        confidence = signal.get("confidence", 0)
        if confidence >= FREE_MIN_CONFIDENCE:
            send(WEBHOOKS["free"], teaser)
        else:
            log.debug("Free channel suppressed (confidence=%s): %s",
                      confidence, signal.get("ticker", ""))

    cache[sig_hash] = {"signal": signal, "posted_at": now(), "ttl_minutes": ttl}
    save_cache(cache)
    log.info("Posted [%s]: %s", signal_type,
             signal.get("matchup") or signal.get("ticker") or "")


def post_health_alert(system: str, error: str):
    send(WEBHOOKS["health"], _fmt_health(system, error))


def post_announcement(message: str):
    send(WEBHOOKS["announcements"], message)
