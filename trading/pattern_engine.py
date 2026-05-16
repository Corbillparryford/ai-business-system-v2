"""
trading/pattern_engine.py
=========================
Pure technical analysis. No API calls. No side effects.
Bar format: {"o": float, "h": float, "l": float, "c": float, "v": int, "t": str}
"""

from __future__ import annotations
from typing import Optional


def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, period+1)]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = sum(gains) / period
    al = sum(losses) / period or 0.0001
    return round(100 - 100 / (1 + ag / al), 2)


def calc_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 4)


def calc_vwap(bars: list) -> float:
    vol = sum(b["v"] for b in bars) or 1
    tp  = [(b["h"] + b["l"] + b["c"]) / 3 for b in bars]
    return round(sum(t * b["v"] for t, b in zip(tp, bars)) / vol, 4)


def vol_profile(bars: list) -> dict:
    vols  = [b["v"] for b in bars]
    avg20 = sum(vols[-20:]) / min(20, len(vols)) or 1
    curr  = vols[-1] if vols else 0
    ratio = round(curr / avg20, 2)
    return {
        "current_volume": curr,
        "avg_volume_20":  round(avg20),
        "volume_ratio":   ratio,
        "classification": (
            "SURGE"  if ratio > 3.0  else
            "HIGH"   if ratio > 1.75 else
            "NORMAL" if ratio > 0.75 else
            "LOW"
        ),
    }


def detect_breakout(bars: list, **_) -> Optional[dict]:
    if len(bars) < 22:
        return None
    closes     = [b["c"] for b in bars]
    resistance = max(b["h"] for b in bars[-21:-1])
    vp         = vol_profile(bars)
    if closes[-1] > resistance and vp["volume_ratio"] > 1.5:
        return {
            "pattern":        "BREAKOUT",
            "breakout_level": round(resistance, 4),
            "vol_ratio":      vp["volume_ratio"],
        }
    return None


def detect_momentum(bars: list, rsi: float, ema9: float, ema21: float) -> Optional[dict]:
    if ema9 <= ema21 or not (50 <= rsi <= 72):
        return None
    curr = bars[-1]["c"]
    if ema9 and abs(curr - ema9) / ema9 <= 0.005:
        return {"pattern": "MOMENTUM_CONTINUATION", "ema9": round(ema9, 4)}
    return None


def detect_bull_reversal(bars: list, rsi: float, ema21: float) -> Optional[dict]:
    if len(bars) < 3:
        return None
    curr = bars[-1]["c"]
    prev = bars[-2]["c"]
    vp   = vol_profile(bars)
    if rsi < 35 and curr < ema21 and curr > prev and vp["volume_ratio"] > 1.8:
        return {"pattern": "BULLISH_REVERSAL", "key_level": round(ema21, 4)}
    return None


def detect_bear_reversal(bars: list, rsi: float, ema21: float) -> Optional[dict]:
    if len(bars) < 4:
        return None
    vols = [b["v"] for b in bars]
    curr = bars[-1]["c"]
    if rsi > 68 and curr > ema21 and vols[-3] > vols[-2] > vols[-1]:
        return {"pattern": "BEARISH_REVERSAL", "key_level": round(ema21, 4)}
    return None


def analyse_ticker(ticker: str, bars: list) -> dict:
    if len(bars) < 22:
        return {"ticker": ticker, "has_patterns": False}

    closes = [b["c"] for b in bars]
    rsi    = calc_rsi(closes)
    ema9   = calc_ema(closes, 9)
    ema21  = calc_ema(closes, 21)
    vwap   = calc_vwap(bars[-20:])
    vp     = vol_profile(bars)

    patterns = []
    for fn, kw in [
        (detect_breakout,    {}),
        (detect_momentum,    {"rsi": rsi, "ema9": ema9, "ema21": ema21}),
        (detect_bull_reversal, {"rsi": rsi, "ema21": ema21}),
        (detect_bear_reversal, {"rsi": rsi, "ema21": ema21}),
    ]:
        try:
            r = fn(bars, **kw)
            if r:
                patterns.append(r)
        except Exception:
            pass

    return {
        "ticker":               ticker,
        "current_price":        round(closes[-1], 4),
        "rsi":                  rsi,
        "ema9":                 ema9,
        "ema21":                ema21,
        "vwap":                 vwap,
        "above_vwap":           closes[-1] > vwap,
        "ema_cross":            "BULLISH" if ema9 > ema21 else "BEARISH",
        "volume_ratio":         vp["volume_ratio"],
        "volume_classification":vp["classification"],
        "patterns_detected":    patterns,
        "has_patterns":         len(patterns) > 0,
    }
