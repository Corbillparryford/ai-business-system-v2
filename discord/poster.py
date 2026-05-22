"""
discord/poster.py — THE SHARP MARGIN Discord delivery engine.
 
Channel routing:
  #ev-signals          ← DISCORD_WEBHOOK_EV
  #arb-signals         ← DISCORD_WEBHOOK_ARB
  #sports-results      ← DISCORD_WEBHOOK_SPORTS_RESULTS
  #trading-signals     ← DISCORD_WEBHOOK_TRADING
  #trade-updates       ← DISCORD_WEBHOOK_TRADE_UPDATES
  #free-signals        ← DISCORD_WEBHOOK_FREE
  #results-preview     ← DISCORD_WEBHOOK_RESULTS_PREVIEW
  #system-health       ← DISCORD_WEBHOOK_HEALTH
 
Free channel control:
  Only posts on standout signals (conf >= 8, edge >= 6%) or big-win moments.
  Hard cap: FREE_MAX_POSTS_PER_DAY per day.
  Big-win blast fires after FREE_WIN_TRIGGER wins accumulate.
"""
 
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, date
 
import requests
 
from core.config import (
    DISCORD_WEBHOOK_EV, DISCORD_WEBHOOK_ARB, DISCORD_WEBHOOK_SPORTS_RESULTS,
    DISCORD_WEBHOOK_TRADING, DISCORD_WEBHOOK_TRADE_UPDATES,
    DISCORD_WEBHOOK_FREE, DISCORD_WEBHOOK_RESULTS_PREVIEW,
    DISCORD_WEBHOOK_HEALTH,
    CACHE_FILE,
    FREE_MIN_CONF_SPORTS, FREE_MIN_EDGE_SPORTS,
    FREE_MIN_CONF_TRADING,
    FREE_WIN_TRIGGER, FREE_HIGH_EDGE_TRIGGER, FREE_MAX_POSTS_PER_DAY,
    WHOP_STORE_URL,
)
 
log = logging.getLogger(__name__)
 
WEBHOOKS = {
    "sports_ev":       DISCORD_WEBHOOK_EV,
    "sports_arb":      DISCORD_WEBHOOK_ARB,
    "sports_results":  DISCORD_WEBHOOK_SPORTS_RESULTS,
    "trading":         DISCORD_WEBHOOK_TRADING,
    "trade_updates":   DISCORD_WEBHOOK_TRADE_UPDATES,
    "free":            DISCORD_WEBHOOK_FREE,
    "results_preview": DISCORD_WEBHOOK_RESULTS_PREVIEW,
    "health":          DISCORD_WEBHOOK_HEALTH,
}
 
SEP = "─────────────────────────────"
 
# ── Free channel state ─────────────────────────────────────────────────────────
_free_posts_today: int = 0
_free_posts_date: date | None = None
_win_buffer: list[dict] = []
 
 
def _reset_free_if_new_day():
    global _free_posts_today, _free_posts_date
    today = date.today()
    if _free_posts_date != today:
        _free_posts_today = 0
        _free_posts_date  = today
 
 
def _can_post_free() -> bool:
    _reset_free_if_new_day()
    return _free_posts_today < FREE_MAX_POSTS_PER_DAY
 
 
def _record_free_post():
    global _free_posts_today
    _free_posts_today += 1
 
 
# ── Cache (dedup) ──────────────────────────────────────────────────────────────
 
def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}
 
 
def _save_cache(cache: dict):
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
        return now_dt - posted < timedelta(minutes=entry.get("ttl_minutes", 30))
    except Exception:
        return False
 
 
def _signal_key(signal: dict) -> str:
    """
    Stable dedup key based on matchup + play + odds only.
    Ignores confidence, reasoning, timing fields that change between cycles
    without the underlying opportunity changing.
    """
    matchup = (signal.get("matchup") or "").strip().lower()
    play    = (signal.get("play")    or "").strip().lower()
    odds    = (signal.get("odds")    or "").strip().lower()
    ticker  = (signal.get("ticker")  or "").strip().lower()
    key_str = f"{matchup}|{play}|{odds}|{ticker}"
    return hashlib.md5(key_str.encode()).hexdigest()
 
 
def _should_post(signal: dict, cache: dict) -> tuple[bool, str]:
    """
    Dedup gate. Returns (should_post, cache_key).
    Only reposts if this is a new signal OR edge changed by >= 1.0%.
    """
    sig_key = _signal_key(signal)
    if sig_key not in cache:
        return True, sig_key
    old_edge = cache[sig_key]["signal"].get("edge", 0)
    new_edge = signal.get("edge", signal.get("arb_percentage", 0))
    if abs(float(new_edge) - float(old_edge)) >= 1.0:
        return True, sig_key
    return False, sig_key
 
 
def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
 
 
# ── Formatters ─────────────────────────────────────────────────────────────────
 
def _fmt_ev(signal: dict) -> str:
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
 
 
def _fmt_arb(signal: dict) -> str:
    legs      = signal.get("legs", [])
    leg_lines = []
    colours   = ["🔵", "🔴"]
    for i, leg in enumerate(legs):
        c = colours[i] if i < len(colours) else "⚪"
        leg_lines.append(
            f"{c} **Bet {i+1}:** {leg.get('side','?')} @ **{leg.get('book','?')}** "
            f"`{leg.get('odds','?')}` → ${leg.get('stake','?')}"
        )
    if not leg_lines:
        leg_lines = [f"📊 {signal.get('play','N/A')} @ {signal.get('book','N/A')}"]
    legs_block  = "\n".join(leg_lines)
    profit_pct  = signal.get("arb_percentage", signal.get("edge", 0))
    profit_amt  = signal.get("profit_per_1000", 0)
    return (
        f"{SEP}\n"
        f"⚖️ **ARBITRAGE — {signal.get('matchup','N/A')}**\n\n"
        f"{legs_block}\n\n"
        f"💰 Guaranteed: **{profit_pct}%** (${profit_amt:.2f} per $1,000)\n"
        f"✅ Place BOTH bets simultaneously\n"
        f"⏱ {signal.get('timing','N/A')}\n"
        f"{SEP}"
    )
 
 
def _fmt_trading_entry(signal: dict) -> str:
    arrow  = "📈" if signal.get("signal_type") == "BUY" else "📉"
    action = signal.get("signal_type", "BUY")
    return (
        f"{SEP}\n"
        f"{arrow} **{signal.get('ticker','?')} — {action}**\n"
        f"💰 Entry: `${signal.get('entry_price','?')}`\n"
        f"🎯 Targets: `${signal.get('target_1','?')}` / `${signal.get('target_2','?')}`\n"
        f"🛑 Stop: `${signal.get('stop_loss','?')}`\n"
        f"📊 R/R: {signal.get('risk_reward','?')}:1 | Confidence: {signal.get('confidence','?')}/10\n"
        f"💡 {signal.get('reasoning','')}\n"
        f"{SEP}"
    )
 
 
def _fmt_trade_exit(ticker: str, exit_price: float, outcome: str, pnl_pct: float) -> str:
    win     = outcome in ("TARGET_1", "TARGET_2")
    arrow   = "📈" if win else "📉"
    result  = "WIN" if win else "LOSS"
    pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"
    return (
        f"{SEP}\n"
        f"{arrow} **{ticker} — SELL**\n"
        f"💰 Exit: `${exit_price}` | Result: **{result}** | P&L: `{pnl_str}`\n"
        f"{SEP}"
    )
 
 
def _fmt_free_sports(signal: dict) -> str:
    teaser = signal.get(
        "teaser_text",
        f"+EV signal detected on {signal.get('matchup','an upcoming game')}."
    )
    return (
        f"{SEP}\n"
        f"🔒 **SIGNAL DETECTED — PREMIUM**\n"
        f"{teaser}\n\n"
        f"🔓 Unlock full signals:\n{WHOP_STORE_URL}\n"
        f"{SEP}"
    )
 
 
def _fmt_free_trading(signal: dict) -> str:
    teaser = signal.get(
        "teaser_text",
        f"{signal.get('signal_type','BUY')} signal on `{signal.get('ticker','a stock')}`."
    )
    return (
        f"{SEP}\n"
        f"🔒 **TRADING SIGNAL — PREMIUM**\n"
        f"{teaser}\n\n"
        f"🔓 Unlock full signals:\n{WHOP_STORE_URL}\n"
        f"{SEP}"
    )
 
 
def _fmt_daily_recap(wins: int, losses: int, plays: list[dict]) -> str:
    today = date.today().strftime("%B %d, %Y")
    lines = "\n".join(
        f"{'✅' if p['result'] == 'WIN' else '❌'} {p['play']} (+{p['edge']:.1f}%)"
        for p in sorted(plays, key=lambda x: x["edge"], reverse=True)[:5]
    )
    return (
        f"{SEP}\n"
        f"📊 **DAILY RESULTS — {today}**\n"
        f"✅ EV Wins: **{wins}**  |  ❌ Losses: **{losses}**\n\n"
        f"📈 Notable +EV plays:\n{lines if lines else 'No plays today.'}\n"
        f"{SEP}"
    )
 
 
def _fmt_health(system: str, error: str) -> str:
    return (
        f"⚠️ **SYSTEM ALERT** — {_now()}\n"
        f"**System:** `{system}`\n**Error:** {error}"
    )
 
 
# ── Send ───────────────────────────────────────────────────────────────────────
 
def send(webhook: str, message: str, retries: int = 2) -> bool:
    if not webhook:
        return False
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(webhook, json={"content": message}, timeout=10)
            if r.status_code in (200, 204):
                return True
            log.warning("Discord HTTP %d (attempt %d)", r.status_code, attempt)
        except requests.RequestException as e:
            log.warning("send() attempt %d: %s", attempt, e)
        if attempt < retries:
            time.sleep(2 ** attempt)
    log.error("send() failed permanently")
    return False
 
 
# ── Big-win trigger ─────────────────────────────────────────────────────────────
 
def record_win(play: str, edge: float):
    """
    Call on every confirmed WIN.
    Only accumulates wins where edge >= 4% (meaningful wins only).
    Fires a free channel blast when WIN_TRIGGER accumulates
    OR a single high-edge win occurs.
    Free channel cap: FREE_MAX_POSTS_PER_DAY.
    """
    # Minimum edge to be worth marketing — small edges are not impressive
    MIN_WIN_EDGE = 4.0
    if edge < MIN_WIN_EDGE:
        log.debug("Win not posted to free (edge %.1f%% < %.1f%%)", edge, MIN_WIN_EDGE)
        return
 
    global _win_buffer
    _win_buffer.append({"play": play, "edge": edge})
 
    if edge >= FREE_HIGH_EDGE_TRIGGER:
        _fire_big_win_blast(f"high-edge win (+{edge:.1f}%)")
    elif len(_win_buffer) >= FREE_WIN_TRIGGER:
        _fire_big_win_blast(f"{len(_win_buffer)}-win streak")
 
 
def _fire_big_win_blast(reason: str):
    global _win_buffer
    wins = _win_buffer[:]
    if not wins or not _can_post_free():
        _win_buffer = []
        return
    lines = "\n".join(
        f"✅ {w['play']} (+{w['edge']:.1f}% edge)"
        for w in sorted(wins, key=lambda x: x["edge"], reverse=True)[:3]
    )
    msg = (
        f"{SEP}\n"
        f"🔥 **{len(wins)} WIN{'S' if len(wins) > 1 else ''} CONFIRMED**\n\n"
        f"{lines}\n\n"
        f"Premium members caught all of these.\n"
        f"🔓 Join before the next signal drops:\n{WHOP_STORE_URL}\n"
        f"{SEP}"
    )
    if send(WEBHOOKS["free"], msg):
        _record_free_post()
        log.info("Big-win blast posted (%s): %d wins", reason, len(wins))
    _win_buffer = []
 
 
# ── Daily recap ─────────────────────────────────────────────────────────────────
 
def post_daily_recap(ev_results: list[dict]):
    """
    Post one daily recap to #sports-results and #results-preview.
    ev_results: list of {"play", "result", "edge"} — EV only, no arb.
    Only posts if there is meaningful activity (at least 1 WIN or LOSS).
    """
    wins   = [r for r in ev_results if r["result"] == "WIN"]
    losses = [r for r in ev_results if r["result"] == "LOSS"]
 
    if not wins and not losses:
        log.debug("Daily recap skipped — no activity")
        return
 
    summary = _fmt_daily_recap(len(wins), len(losses), ev_results)
    preview = (
        f"📊 Daily recap: **{len(wins)}W / {len(losses)}L** today.\n"
        f"Full breakdown in #sports-results.\n"
        f"🔓 {WHOP_STORE_URL}"
    )
    send(WEBHOOKS["sports_results"], summary)
    send(WEBHOOKS["results_preview"], preview)
    log.info("Daily recap posted: %dW %dL", len(wins), len(losses))
 
 
# ── Trade exit posting ──────────────────────────────────────────────────────────
 
def post_trade_exit(ticker: str, exit_price: float, outcome: str, pnl_pct: float):
    """Post clean trade exit to #trade-updates. Only for terminal outcomes."""
    if outcome not in ("TARGET_2", "STOP"):
        log.debug("Trade exit suppressed (non-terminal outcome: %s)", outcome)
        return
    msg = _fmt_trade_exit(ticker, exit_price, outcome, pnl_pct)
    send(WEBHOOKS["trade_updates"], msg)
    log.info("Trade exit posted: %s %s P&L=%.2f%%", ticker, outcome, pnl_pct)
 
 
# ── Main entry point ────────────────────────────────────────────────────────────
 
def post_signal(signal: dict, signal_type: str):
    """
    Route a signal to the correct channel(s).
    signal_type: "sports" | "trading"
    """
    cache  = _load_cache()
    cache  = _purge_expired(cache)
 
    allowed, sig_key = _should_post(signal, cache)
    if not allowed:
        log.debug("Duplicate suppressed: %s", sig_key[:8])
        return
 
    if signal_type == "sports":
        is_arb  = signal.get("type") == "ARBITRAGE"
        webhook = WEBHOOKS["sports_arb"] if is_arb else WEBHOOKS["sports_ev"]
        message = _fmt_arb(signal) if is_arb else _fmt_ev(signal)
        teaser  = _fmt_free_sports(signal)
        ttl     = 30
 
    elif signal_type == "trading":
        webhook = WEBHOOKS["trading"]
        message = _fmt_trading_entry(signal)
        teaser  = _fmt_free_trading(signal)
        ttl     = 60
 
    else:
        log.warning("Unknown signal_type: %s", signal_type)
        return
 
    send(webhook, message)
 
    # Free channel: NO teasers from new signals.
    # Free channel is a proof/marketing channel — wins only.
    # It is triggered exclusively by record_win() when a bet resolves WIN.
 
    cache[sig_key] = {"signal": signal, "posted_at": _now(), "ttl_minutes": ttl}
    _save_cache(cache)
    log.info("Posted [%s]: %s", signal_type,
             signal.get("matchup") or signal.get("ticker") or "")
 
 
def post_health_alert(system: str, error: str):
    send(WEBHOOKS["health"], _fmt_health(system, error))
