"""
scripts/check_config.py
=======================
Pre-deploy configuration checker.
Usage: python scripts/check_config.py
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

results: list[tuple[bool, str]] = []

def record(ok: bool, label: str, hint: str = "", required: bool = True):
    sym  = "  ✅" if ok else ("  ❌" if required else "  ⚠️ ")
    line = f"{sym}  {label}"
    if not ok and hint:
        line += f"\n       → {hint}"
    results.append((ok or not required, line))
    print(line)

def check_env(var: str, required: bool = True) -> str:
    val = os.environ.get(var, "")
    record(bool(val), f"{var}", required=required)
    return val

def check_import(pkg: str):
    try:
        __import__(pkg)
        record(True, f"import {pkg}")
    except ImportError:
        record(False, f"import {pkg}", f"pip install {pkg}")

def check_anthropic(key: str):
    if not key:
        return
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        c.messages.create(model="claude-haiku-4-5-20251001", max_tokens=5,
                          messages=[{"role":"user","content":"ok"}])
        record(True, "Claude API reachable")
    except Exception as e:
        record(False, "Claude API reachable", str(e))

def check_odds(key: str):
    if not key:
        return
    try:
        import requests
        r = requests.get("https://api.the-odds-api.com/v4/sports/",
                         params={"apiKey": key}, timeout=8)
        record(r.status_code == 200, "OddsAPI reachable", f"HTTP {r.status_code}")
    except Exception as e:
        record(False, "OddsAPI reachable", str(e))

def check_alpaca(key: str, secret: str):
    if not key or not secret:
        return
    try:
        import requests
        r = requests.get(
            "https://data.alpaca.markets/v2/stocks/AAPL/bars",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"timeframe": "1Day", "limit": 1, "feed": "iex"}, timeout=8,
        )
        record(r.status_code == 200, "Alpaca API reachable", f"HTTP {r.status_code}")
    except Exception as e:
        record(False, "Alpaca API reachable", str(e))

def check_webhook(name: str, url: str):
    if not url:
        return
    try:
        import requests
        r = requests.get(url, timeout=5)
        record(r.status_code == 200, f"Webhook: {name}", "URL invalid or deleted")
    except Exception as e:
        record(False, f"Webhook: {name}", str(e))


print("\n" + "="*58)
print("  AI Business System v2 — Pre-Deploy Config Check")
print("="*58 + "\n")

print("── Python packages ──────────────────────────────────────")
for pkg in ["anthropic","requests","websockets","pytz","dotenv"]:
    check_import(pkg)

print("\n── Required ─────────────────────────────────────────────")
anthropic_key = check_env("ANTHROPIC_API_KEY", required=True)

print("\n── Sports APIs ──────────────────────────────────────────")
odds_key  = check_env("ODDS_API_KEY",   required=False)
check_env("KALSHI_API_KEY", required=False)

print("\n── Trading APIs ─────────────────────────────────────────")
alp_key = check_env("ALPACA_API_KEY",    required=False)
alp_sec = check_env("ALPACA_SECRET_KEY", required=False)

print("\n── Discord Webhooks — Premium Sports ────────────────────")
wh_ev  = check_env("DISCORD_WEBHOOK_EV",             required=False)
wh_arb = check_env("DISCORD_WEBHOOK_ARB",            required=False)
wh_sr  = check_env("DISCORD_WEBHOOK_SPORTS_RESULTS", required=False)

print("\n── Discord Webhooks — Premium Trading ───────────────────")
wh_tr  = check_env("DISCORD_WEBHOOK_TRADING",       required=False)
wh_tu  = check_env("DISCORD_WEBHOOK_TRADE_UPDATES", required=False)
wh_tre = check_env("DISCORD_WEBHOOK_TRADE_RESULTS", required=False)

print("\n── Discord Webhooks — Free Funnel ───────────────────────")
wh_f   = check_env("DISCORD_WEBHOOK_FREE",            required=False)
wh_rp  = check_env("DISCORD_WEBHOOK_RESULTS_PREVIEW", required=False)
wh_an  = check_env("DISCORD_WEBHOOK_ANNOUNCEMENTS",   required=False)

print("\n── Discord Webhooks — System ────────────────────────────")
wh_h   = check_env("DISCORD_WEBHOOK_HEALTH", required=False)

print("\n── Content (optional) ───────────────────────────────────")
check_env("ELEVENLABS_API_KEY",  required=False)
check_env("PEXELS_KEY",          required=False)
check_env("TIKTOK_ACCESS_TOKEN", required=False)
check_env("AMAZON_AFFILIATE_TAG",required=False)

print("\n── Live connectivity ────────────────────────────────────")
check_anthropic(anthropic_key)
check_odds(odds_key)
check_alpaca(alp_key, alp_sec)
for name, url in [("EV", wh_ev), ("ARB", wh_arb), ("TRADING", wh_tr),
                   ("FREE", wh_f), ("HEALTH", wh_h)]:
    check_webhook(name, url)

total  = len(results)
passed = sum(1 for ok, _ in results if ok)
print(f"\n{'='*58}")
print(f"  {passed}/{total} checks passed")
print("  ✅  Ready to deploy!" if passed == total else f"  ⚠️   {total-passed} issue(s) to fix")
print(f"{'='*58}\n")
sys.exit(0 if passed == total else 1)
