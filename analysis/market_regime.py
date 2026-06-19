"""
analysis/market_regime.py
=========================
Market-trend regime gauge — the broad-market filter both playbooks lean on.

WHAT THIS DOES
    Reads SPY's own price history and classifies the market trend from its
    200-day moving average:
        Risk-On   — SPY above a RISING 200-day  (healthy uptrend)
        Risk-Off  — SPY below a FALLING 200-day  (downtrend)
        Neutral   — anything in between (above a falling MA, or below a rising MA)

WHY IT MATTERS
    The LONG playbook calls the market-trend filter "the single most protective
    top-down rule" — momentum longs are far safer with the index trending up.
    The SHORT playbook makes it rule #1: shorts only pay when the tide turns
    (SPY below a falling 200-day); shorting single names in a strong bull is
    "fighting the tide." So the SHORT scorer and the short flag threshold both
    gate on this.

WHY SPY AND NOT FRED
    This is the market's *price* trend, not the business cycle. It needs no API
    key — SPY is already a stored benchmark — so it works even when the FRED
    macro-cycle engine is dark. The two are complementary: cycle = which sectors
    the economy favors; regime = is the tape risk-on or risk-off right now.

SPEED / SAFETY
    DB reads only. The result is the same for every stock in a screen, so it is
    cached per process (and the screener computes it once into market_context).
    Degrades to a neutral "Unknown" result if SPY history is missing.
"""

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.db_reader import get_price_bars

# The market proxy and the trend window. 200 sessions ≈ the 200-day / 40-week MA.
MARKET_PROXY = "SPY"
SMA_WINDOW = 200
# Trading days back to measure the MA's slope (≈ one month).
SLOPE_LOOKBACK = 21

_regime_cache = None


def _classify(above: bool, rising: bool) -> str:
    """Two booleans → a regime label."""
    if above and rising:
        return "Risk-On"
    if not above and not rising:
        return "Risk-Off"
    return "Neutral"


def get_market_regime(use_cache: bool = True) -> dict:
    """Classify the market trend from SPY's 200-day moving average.

    Returns a dict:
        {regime, spy_close, sma_200, pct_from_sma, sma_rising, is_strong_bull,
         is_risk_off, note}
    `regime` is one of "Risk-On" | "Neutral" | "Risk-Off" | "Unknown".
    Never raises — missing data yields a neutral "Unknown" result.
    """
    global _regime_cache
    if use_cache and _regime_cache is not None:
        return _regime_cache

    unknown = {
        "regime": "Unknown",
        "spy_close": None,
        "sma_200": None,
        "pct_from_sma": None,
        "sma_rising": None,
        "is_strong_bull": False,
        "is_risk_off": False,
        "note": f"No {MARKET_PROXY} history — regime undetermined",
    }

    bars = get_price_bars(MARKET_PROXY, days=400)
    if bars is None or len(bars) < SMA_WINDOW + SLOPE_LOOKBACK:
        _regime_cache = unknown
        return unknown

    bars = bars.sort_values("Date").reset_index(drop=True)
    close = bars["Close"]

    sma_now = float(close.tail(SMA_WINDOW).mean())
    # The 200-day MA as it stood SLOPE_LOOKBACK sessions ago (for the slope read).
    sma_prev = float(close.iloc[-(SMA_WINDOW + SLOPE_LOOKBACK):-SLOPE_LOOKBACK].mean())
    spy_close = float(close.iloc[-1])

    if sma_now <= 0:
        _regime_cache = unknown
        return unknown

    above = spy_close > sma_now
    rising = sma_now > sma_prev
    regime = _classify(above, rising)
    pct_from_sma = round((spy_close - sma_now) / sma_now * 100, 2)

    result = {
        "regime": regime,
        "spy_close": round(spy_close, 2),
        "sma_200": round(sma_now, 2),
        "pct_from_sma": pct_from_sma,
        "sma_rising": rising,
        # "Strong bull" = above a rising 200-day → the short side's uphill case.
        "is_strong_bull": regime == "Risk-On",
        # "Risk-off" = below a falling 200-day → the short side's tailwind.
        "is_risk_off": regime == "Risk-Off",
        "note": (
            f"{MARKET_PROXY} {pct_from_sma:+.1f}% vs a "
            f"{'rising' if rising else 'falling'} 200-day → {regime}"
        ),
    }
    _regime_cache = result
    return result


if __name__ == "__main__":
    r = get_market_regime()
    print("=== Market Regime ===")
    print(f"Regime:        {r['regime']}")
    print(f"SPY close:     {r['spy_close']}")
    print(f"200-day SMA:   {r['sma_200']}  ({'rising' if r['sma_rising'] else 'falling'})")
    print(f"vs 200-day:    {r['pct_from_sma']}%")
    print(f"Note:          {r['note']}")
