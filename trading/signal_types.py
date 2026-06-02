"""
trading/signal_types.py
========================
Discord formatters for all three trade categories.

Every format tells the user EXACTLY:
  - when to buy (entry / buy zone)
  - when to sell (take profit levels)
  - how to manage risk (stop loss)
  - how long to hold (timeframe)
  - what to do at each level (partial exits for swing/long-term)
"""

SEP = "─────────────────────────────"


def fmt_short_term(signal: dict) -> str:
    """
    Intraday / same-day trade.
    Clear single entry, single TP, single SL.
    """
    arrow  = "📈" if signal.get("signal_type") == "BUY" else "📉"
    action = signal.get("signal_type", "BUY")
    return (
        f"{SEP}\n"
        f"⚡ **SHORT-TERM TRADE — {signal.get('ticker', '?')} {arrow}**\n\n"
        f"**Action:** {action}\n"
        f"**Entry:** `${signal.get('entry_price', '?')}`\n"
        f"**Take Profit:** `${signal.get('target_1', '?')}`\n"
        f"**Stop Loss:** `${signal.get('stop_loss', '?')}`\n"
        f"**Timeframe:** Same day / intraday\n"
        f"**R/R:** {signal.get('risk_reward', '?')}:1  |  "
        f"**Confidence:** {signal.get('confidence', '?')}/10\n\n"
        f"📋 **Trade Plan:**\n"
        f"- Enter at or within 0.3% of entry price\n"
        f"- Set stop loss immediately after entering\n"
        f"- Close position at take profit OR end of session\n"
        f"- Do not hold overnight\n\n"
        f"💡 {signal.get('reasoning', '')}\n"
        f"{SEP}"
    )


def fmt_swing_trade(signal: dict) -> str:
    """
    Multi-day hold (3–14 days).
    Buy zone, two targets with partial exit instructions.
    """
    t1  = signal.get("target_1", "?")
    t2  = signal.get("target_2", "?")
    sl  = signal.get("stop_loss", "?")
    ent = signal.get("entry_price", "?")

    # Calculate approximate partial profit % if prices are numeric
    try:
        partial_pct = round((float(t1) - float(ent)) / float(ent) * 100, 1)
        full_pct    = round((float(t2) - float(ent)) / float(ent) * 100, 1)
        partial_str = f"~+{partial_pct}%"
        full_str    = f"~+{full_pct}%"
    except (TypeError, ValueError):
        partial_str = ""
        full_str    = ""

    arrow = "📈" if signal.get("signal_type", "BUY") == "BUY" else "📉"
    return (
        f"{SEP}\n"
        f"📈 **SWING TRADE — {signal.get('ticker', '?')} {arrow}**\n\n"
        f"**Buy Zone:** `${ent}` ± 0.5%\n"
        f"**Take Profit 1:** `${t1}` {partial_str}\n"
        f"**Take Profit 2:** `${t2}` {full_str}\n"
        f"**Stop Loss:** `${sl}`\n"
        f"**Timeframe:** {signal.get('timeframe', '1–2 weeks')}\n"
        f"**Pattern:** {signal.get('pattern', 'N/A')}  |  "
        f"**Confidence:** {signal.get('confidence', '?')}/10\n\n"
        f"📋 **Trade Plan:**\n"
        f"- Enter within the buy zone\n"
        f"- Take **50% off** at Target 1 ({partial_str})\n"
        f"- Let remaining 50% run to Target 2 ({full_str})\n"
        f"- If price closes below `${sl}` → exit full position\n"
        f"- Do not move stop loss further away\n\n"
        f"💡 {signal.get('reasoning', '')}\n"
        f"{SEP}"
    )


def fmt_long_term(signal: dict) -> str:
    """
    Multi-week accumulation position.
    Buy zone, scaling plan, gradual exit levels.
    """
    ent = signal.get("entry_price", "?")
    t1  = signal.get("target_1", "?")
    t2  = signal.get("target_2", "?")
    sl  = signal.get("stop_loss", "?")

    try:
        t1_pct = round((float(t1) - float(ent)) / float(ent) * 100, 1)
        t2_pct = round((float(t2) - float(ent)) / float(ent) * 100, 1)
        t1_str = f"+{t1_pct}%"
        t2_str = f"+{t2_pct}%"
    except (TypeError, ValueError):
        t1_str = ""
        t2_str = ""

    return (
        f"{SEP}\n"
        f"🪙 **LONG-TERM POSITION — {signal.get('ticker', '?')}**\n\n"
        f"**Buy Zone:** `${ent}` (scale in gradually)\n"
        f"**Position Type:** Scaling / accumulation\n"
        f"**Timeframe:** Multi-week / long-term\n\n"
        f"📋 **Sell Plan:**\n"
        f"- Take **30% off** at `${t1}` ({t1_str})\n"
        f"- Take another **30% off** at `${t2}` ({t2_str})\n"
        f"- Hold remaining 40% for extended trend\n"
        f"- Re-evaluate if price closes above Target 2\n\n"
        f"🛡 **Risk Plan:**\n"
        f"- If price closes below `${sl}` → exit full position\n"
        f"- Risk only 1–2% of capital on this position\n\n"
        f"**Confidence:** {signal.get('confidence', '?')}/10  |  "
        f"**Pattern:** {signal.get('pattern', 'N/A')}\n\n"
        f"💡 {signal.get('reasoning', '')}\n"
        f"{SEP}"
    )


def fmt_trade_exit(ticker: str, exit_price: float, outcome: str, pnl_pct: float,
                   trade_type: str = "short_term") -> str:
    """Clean SELL confirmation with P&L."""
    win     = outcome in ("TARGET_1", "TARGET_2")
    arrow   = "✅" if win else "❌"
    result  = "WIN" if win else "LOSS"
    pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"

    label_map = {
        "short_term": "⚡ SHORT-TERM",
        "swing":      "📈 SWING TRADE",
        "long_term":  "🪙 LONG-TERM",
    }
    label = label_map.get(trade_type, "📊 TRADE")

    return (
        f"{SEP}\n"
        f"{arrow} **{label} CLOSED — {ticker}**\n\n"
        f"**SELL** @ `${exit_price}`\n"
        f"**Result:** {result}  |  **P&L:** `{pnl_str}`\n"
        f"**Outcome:** {outcome.replace('_', ' ')}\n"
        f"{SEP}"
    )


def fmt_t1_hit(ticker: str, price: float, remaining_target: float) -> str:
    """
    Target 1 hit on swing/long-term — tells user exactly what to do next.
    This posts for swing and long-term trades (informational, not a close).
    """
    return (
        f"{SEP}\n"
        f"🎯 **TARGET 1 HIT — {ticker}**\n\n"
        f"Price reached `${price:.2f}`\n\n"
        f"📋 **Action required:**\n"
        f"- Take **50% off** your position now\n"
        f"- Move stop loss to your entry price (breakeven)\n"
        f"- Let remaining position run to Target 2: `${remaining_target}`\n"
        f"{SEP}"
    )
