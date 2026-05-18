"""
scripts/discord_messages.py
============================
Run this script to print all Discord-ready messages to the console.
Copy-paste each block directly into Discord.

Usage: python scripts/discord_messages.py
"""

SEP  = "─────────────────────────────────────────────────"
SEP2 = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ── BANKROLL MANAGEMENT ───────────────────────────────────────────────────────

BANKROLL_MESSAGE = f"""
{SEP2}
💰 **BANKROLL MANAGEMENT — HOW TO BET SMART**
{SEP2}

Managing your bankroll correctly is **the single most important factor** in long-term profitability. Here is how to do it properly.

**📌 Recommended Starting Bankroll**
Start with a dedicated betting bankroll of **$500 – $2,000**.
This is money you are prepared to work with over weeks and months — not money you need for anything else.

**📌 Unit System**
A "unit" is a fixed percentage of your bankroll bet per signal.

> ✅ Recommended: **1% – 3% per bet**
> ❌ Never: bet more than 5% on a single signal

**Example with $1,000 bankroll:**
```
1 unit  = $10   (conservative)
2 units = $20   (standard)
3 units = $30   (high confidence only)
```

**📌 Why Units Matter**
Flat-unit betting protects you from losing streaks. Even a 60% win rate has cold stretches. Betting 20%+ per play wipes out bankrolls before the edge compounds.

**📌 Risk Control Rules**
- Never chase losses with bigger bets
- Never bet more than 3 units on any single signal regardless of confidence
- Do not combine multiple bets into parlays — this destroys EV
- Treat each signal independently

**📌 Consistency Is Everything**
The edge only shows up **over volume**. A 5% edge means nothing over 10 bets. Over 500 bets, it compounds into measurable profit. Consistency beats intensity every time.

**📌 Scaling Up**
Once your bankroll grows, your unit size grows with it. This is how a $1,000 bankroll becomes $5,000 naturally — without adding new deposits.

{SEP}
*Follow the unit system. Protect your bankroll. Let the edge work over time.*
{SEP}
"""


# ── POSITIVE EV GUIDE ─────────────────────────────────────────────────────────

EV_GUIDE_MESSAGE = f"""
{SEP2}
📈 **WHAT IS POSITIVE EV BETTING?**
{SEP2}

**EV** stands for **Expected Value**. A +EV bet is one where the odds offered are better than the true probability of the outcome — meaning you have a mathematical edge.

**📌 Simple Example**
Imagine a coin flip. True probability of heads = 50%.
- A book offers you +110 (52.4% implied) on heads
- Your true edge = 50% win rate vs 47.6% implied probability
- **That gap = +EV**

The book is mispricing the odds. You exploit that gap.

**📌 Why Do Sportsbooks Misprice Odds?**
Books set lines to balance action, not to perfectly reflect true probabilities. Sharp books (Pinnacle, exchanges) are the most accurate. Soft books (DraftKings, FanDuel, BetMGM) frequently lag behind, especially on:
- Player props
- Early posted lines
- Niche markets

We compare soft book odds against sharp book no-vig lines to find the gap.

**📌 How Edge Creates Long-Term Profit**
A +EV bet does **not** guarantee winning any single bet.
It guarantees that if you place the bet many times, you profit.

> 5% edge × 100 bets × $20/bet = **$100 expected profit**

This is math, not gambling. The variance smooths out over volume.

**📌 What You Need**
- 🏦 Access to multiple sportsbooks (shop for the best line)
- 📊 Volume — place bets consistently over time
- 🧘 Patience — do not judge results over 10 or 20 bets

**📌 What We Do**
Every signal we post has been validated against sharp market prices. We only post when the edge is **≥ 3%** after accounting for vig.

{SEP}
*+EV betting is a long game. Stay disciplined. Let the math work.*
{SEP}
"""


# ── ARBITRAGE GUIDE ───────────────────────────────────────────────────────────

ARBITRAGE_GUIDE_MESSAGE = f"""
{SEP2}
⚡ **ARBITRAGE BETTING — GUARANTEED PROFIT EXPLAINED**
{SEP2}

**Arbitrage betting** (arbing) is placing bets on all possible outcomes of an event across different sportsbooks, at odds that guarantee a profit regardless of the result.

**📌 How It Works**
When different books price a game at odds that collectively imply less than 100% probability — there is a guaranteed profit window.

**Example:**
```
FanDuel:   Team A ML  +130  (implied 43.5%)
BetMGM:    Team B ML  -105  (implied 51.3%)
Total implied = 94.8% → 5.2% guaranteed profit margin
```

Bet both sides in the correct proportions → profit no matter who wins.

**📌 Stake Allocation**
We calculate the exact stake for each leg automatically. Each arb signal includes:
- Which book to use for each side
- How much to stake on each leg
- Guaranteed profit per $1,000 staked

**📌 Requirements for Arbing**
- ✅ Accounts at **multiple sportsbooks** (minimum 3–4)
- ✅ Sufficient **balance at each book** to place both legs simultaneously
- ✅ Fast execution — arb windows close quickly
- ✅ Larger bankroll than pure +EV betting

**📌 The Catch**
Arbing requires **more capital** and **more accounts** than +EV betting. Books also limit or ban accounts that arb consistently — manage this by mixing in recreational bets.

**📌 Our Recommendation**
> 🏗️ **Build your bankroll first through +EV betting.**
> Once you have $2,000+ and accounts at 4+ books, layer in arbitrage signals.

Starting with arbing before you have the infrastructure often leads to missed legs, rejected bets, and frustration. Master +EV first.

**📌 When Arb Windows Are Valid**
We only post arbitrage signals where:
- Guaranteed profit ≥ 1.5% after fees
- Both books have sufficient liquidity
- Time to game start allows placement

{SEP}
*Arbing is a precision tool. Build the foundation first.*
{SEP}
"""


# ── LEGAL DISCLAIMER ──────────────────────────────────────────────────────────

LEGAL_DISCLAIMER = f"""
{SEP2}
⚖️ **THE SHARP MARGIN — LEGAL DISCLAIMER**
{SEP2}

**Please read before using any signals from this service.**

📋 **Educational Purposes Only**
All content, signals, picks, analysis, and information provided by The Sharp Margin are for **educational and informational purposes only**. Nothing posted here constitutes financial, investment, or betting advice.

📋 **No Guarantee of Profit**
Sports betting and trading involve substantial risk of financial loss. Past performance of signals is not indicative of future results. There is **no guarantee** that any signal will be profitable. Expected value is a statistical concept — individual bet outcomes vary significantly.

📋 **You Are Responsible**
By using this service, you accept full personal responsibility for any bets or trades you place. The Sharp Margin and its operators are not liable for any financial losses incurred as a result of acting on information posted in this server.

📋 **Know Your Local Laws**
Sports betting laws vary by jurisdiction. It is your responsibility to ensure that sports betting is legal in your region before participating. Do not use this service where sports betting is prohibited.

📋 **Bet Responsibly**
If you or someone you know has a gambling problem:
- **National Problem Gambling Helpline:** 1-800-522-4700
- **Text:** "HELLO" to 741741
- **Online:** ncpgambling.org

Never bet money you cannot afford to lose. Set limits. Take breaks.

📋 **Affiliate Disclosure**
The Sharp Margin may earn a commission from referred sportsbook sign-ups. This does not influence signal selection or analysis.

{SEP}
*The Sharp Margin — responsible, data-driven, educational.*
{SEP}
"""


# ── PRINT ALL ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sections = [
        ("BANKROLL MANAGEMENT",    BANKROLL_MESSAGE),
        ("POSITIVE EV GUIDE",      EV_GUIDE_MESSAGE),
        ("ARBITRAGE GUIDE",        ARBITRAGE_GUIDE_MESSAGE),
        ("LEGAL DISCLAIMER",       LEGAL_DISCLAIMER),
    ]

    for title, content in sections:
        print(f"\n{'='*60}")
        print(f"  COPY THIS TO DISCORD: {title}")
        print('='*60)
        print(content)
        print()
