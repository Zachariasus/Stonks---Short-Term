"""
analysis/entry_signals.py
=========================
Entry signal detector — the payoff of the Trend & Momentum engine.

WHAT THIS DOES
    Synthesizes the four analysis modules (moving averages, stage, relative
    strength*, volume) into concrete, actionable ENTRY signals:
        LONGS  : Breakout, Pullback-to-50-day
        SHORTS : Breakdown, Failed-Rally-to-50-day   (the mirror images)
    Each detector returns not just a yes/no, but the numbers behind it and a
    plain-English reason it did or didn't fire.

    (*RS isn't a hard gate here — it's used later for ranking; these detectors
    focus on stage + price structure + volume confirmation.)

HOW IT FITS IN
    Top of the trend-engine stack: it CALLS the other analysis modules and never
    touches yfinance or the database fetchers directly.
"""

import pandas as pd

# Import the analysis + data-reader helpers. PYTHONPATH=<project root> makes the
# first import work; the fallback inserts the root so the file runs standalone.
try:
    from analysis.moving_averages import (
        are_mas_stacked_bullish,
        calculate_moving_averages,
    )
    from analysis.stage_classifier import classify_stage
    from analysis.volume_analysis import calculate_volume_metrics
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.moving_averages import (
        are_mas_stacked_bullish,
        calculate_moving_averages,
    )
    from analysis.stage_classifier import classify_stage
    from analysis.volume_analysis import calculate_volume_metrics
    from data.db_reader import get_price_bars

TRADING_DAYS_PER_WEEK = 5


def _latest_vol_ratio(ticker: str):
    """Most recent day's volume / 20-day average volume (or None)."""
    vol_df = calculate_volume_metrics(ticker)
    if vol_df is None:
        return None
    value = vol_df["vol_ratio"].iloc[-1]
    return round(float(value), 2) if pd.notna(value) else None


def _stage_of(ticker: str):
    """Stage number (1–4) from the classifier, or None if undetermined."""
    info = classify_stage(ticker)
    return info["stage"] if info else None


# ---------------------------------------------------------------------------
# LONG setups
# ---------------------------------------------------------------------------
def detect_breakout(ticker: str, weeks: int = 8) -> dict:
    """Long breakout: a Stage 2 stock pushing above its recent base on volume."""
    ticker = ticker.strip().upper()
    result = {
        "signal": None, "close": None, "base_high": None, "breakout_pct": None,
        "vol_ratio": None, "stage": None, "triggered": False, "reason": "",
    }

    price_df = get_price_bars(ticker, days=300)
    lookback = weeks * TRADING_DAYS_PER_WEEK
    if price_df is None or len(price_df) < lookback:
        result["reason"] = "insufficient price history"
        return result

    price_df = price_df.sort_values("Date").reset_index(drop=True)
    close = round(float(price_df["Close"].iloc[-1]), 2)
    result["close"] = close

    # Base high = highest close over the past `weeks` weeks, EXCLUDING the last 5
    # trading days. We exclude those 5 because they may contain the breakout
    # itself — if the breakout day were allowed to set the base high, price could
    # never be shown to exceed its own base. Excluding them anchors the base to
    # the PRIOR consolidation, so a move above it is a genuine breakout.
    base_window = price_df["Close"].iloc[-lookback:-5]
    if len(base_window) == 0:
        result["reason"] = "not enough data to form a base"
        return result
    base_high = round(float(base_window.max()), 2)
    result["base_high"] = base_high
    breakout_pct = round((close - base_high) / base_high * 100, 2)
    result["breakout_pct"] = breakout_pct

    stage = _stage_of(ticker)
    result["stage"] = stage
    vol_ratio = _latest_vol_ratio(ticker)
    result["vol_ratio"] = vol_ratio
    stacked = are_mas_stacked_bullish(ticker)

    # All conditions must hold; the reason reports the first failing one.
    if stage is None:
        result["reason"] = "could not classify stage"
    elif stage != 2:
        result["reason"] = f"Stage {stage} (wrong stage)"
    elif close <= base_high:
        result["reason"] = f"no breakout: close ${close} ≤ base high ${base_high}"
    elif vol_ratio is None:
        result["reason"] = "volume data unavailable"
    elif vol_ratio <= 1.5:
        result["reason"] = f"breakout on light volume ({vol_ratio:.1f}× ≤ 1.5×)"
    elif not stacked:
        result["reason"] = "MAs not stacked bullish"
    else:
        result["signal"] = "Breakout"
        result["triggered"] = True
        result["reason"] = (
            f"close ${close} broke above ${base_high} base "
            f"(+{breakout_pct:.1f}%) on {vol_ratio:.1f}× volume"
        )
    return result


def detect_pullback_to_50d(ticker: str) -> dict:
    """Long pullback: a Stage 2 stock easing back to a rising 50-day on light volume."""
    ticker = ticker.strip().upper()
    result = {
        "signal": None, "close": None, "sma_50": None, "pct_from_50d": None,
        "sma_50_rising": None, "days_above_50d_last_20": None,
        "vol_ratio": None, "stage": None, "triggered": False, "reason": "",
    }

    ma_df = calculate_moving_averages(ticker)
    if ma_df is None:
        result["reason"] = "insufficient price history for MAs"
        return result
    ma_df = ma_df.reset_index(drop=True)

    close = round(float(ma_df["Close"].iloc[-1]), 2)
    result["close"] = close
    sma_50_now = ma_df["sma_50"].iloc[-1]
    if pd.isna(sma_50_now):
        result["reason"] = "50-day MA not available"
        return result
    sma_50 = round(float(sma_50_now), 2)
    result["sma_50"] = sma_50
    pct_from_50d = round((close - sma_50) / sma_50 * 100, 2)
    result["pct_from_50d"] = pct_from_50d

    # Is the 50-day rising? Compare today vs 10 trading days ago.
    sma_50_10ago = ma_df["sma_50"].iloc[-11]
    sma_50_rising = bool(pd.notna(sma_50_10ago) and sma_50_now > sma_50_10ago)
    result["sma_50_rising"] = sma_50_rising

    # How many of the last 20 days closed above the 50-day? (pullback, not breakdown)
    last20 = ma_df.tail(20)
    days_above = int((last20["Close"] > last20["sma_50"]).sum())
    result["days_above_50d_last_20"] = days_above

    stage = _stage_of(ticker)
    result["stage"] = stage
    vol_ratio = _latest_vol_ratio(ticker)
    result["vol_ratio"] = vol_ratio

    if stage is None:
        result["reason"] = "could not classify stage"
    elif stage != 2:
        result["reason"] = f"Stage {stage} (wrong stage)"
    elif not sma_50_rising:
        result["reason"] = "50-day MA not rising"
    elif pct_from_50d > 2:
        result["reason"] = f"price {pct_from_50d:.2f}% above 50-day (too extended)"
    elif pct_from_50d < -5:
        result["reason"] = f"price {pct_from_50d:.2f}% below 50-day (broke down)"
    elif days_above < 15:
        result["reason"] = f"only {days_above}/20 days above 50-day (not a clean pullback)"
    elif vol_ratio is None:
        result["reason"] = "volume data unavailable"
    elif vol_ratio >= 1.0:
        result["reason"] = f"pullback on heavy volume ({vol_ratio:.1f}× ≥ 1.0×)"
    else:
        result["signal"] = "Pullback to 50d"
        result["triggered"] = True
        result["reason"] = (
            f"pulled back to {pct_from_50d:+.1f}% of a rising 50-day "
            f"on light volume ({vol_ratio:.1f}×)"
        )
    return result


# ---------------------------------------------------------------------------
# SHORT setups (mirrors of the longs)
# ---------------------------------------------------------------------------
def detect_breakdown(ticker: str, weeks: int = 8) -> dict:
    """Short breakdown: a Stage 4 stock cracking below its recent base on volume."""
    ticker = ticker.strip().upper()
    result = {
        "signal": None, "close": None, "base_low": None, "breakdown_pct": None,
        "vol_ratio": None, "stage": None, "triggered": False, "reason": "",
    }

    price_df = get_price_bars(ticker, days=300)
    lookback = weeks * TRADING_DAYS_PER_WEEK
    if price_df is None or len(price_df) < lookback:
        result["reason"] = "insufficient price history"
        return result

    price_df = price_df.sort_values("Date").reset_index(drop=True)
    close = round(float(price_df["Close"].iloc[-1]), 2)
    result["close"] = close

    # Base low = lowest close over the past `weeks` weeks, excluding the last 5
    # days (same logic as the breakout, mirrored: don't let the breakdown day
    # define its own base).
    base_window = price_df["Close"].iloc[-lookback:-5]
    if len(base_window) == 0:
        result["reason"] = "not enough data to form a base"
        return result
    base_low = round(float(base_window.min()), 2)
    result["base_low"] = base_low
    breakdown_pct = round((close - base_low) / base_low * 100, 2)
    result["breakdown_pct"] = breakdown_pct

    stage = _stage_of(ticker)
    result["stage"] = stage
    vol_ratio = _latest_vol_ratio(ticker)
    result["vol_ratio"] = vol_ratio

    if stage is None:
        result["reason"] = "could not classify stage"
    elif stage != 4:
        result["reason"] = f"Stage {stage} (wrong stage)"
    elif close >= base_low:
        result["reason"] = f"no breakdown: close ${close} ≥ base low ${base_low}"
    elif vol_ratio is None:
        result["reason"] = "volume data unavailable"
    elif vol_ratio <= 1.5:
        result["reason"] = f"breakdown on light volume ({vol_ratio:.1f}× ≤ 1.5×)"
    else:
        result["signal"] = "Breakdown"
        result["triggered"] = True
        result["reason"] = (
            f"close ${close} broke below ${base_low} base "
            f"({breakdown_pct:.1f}%) on {vol_ratio:.1f}× volume"
        )
    return result


def detect_failed_rally(ticker: str) -> dict:
    """Short failed rally: a Stage 4 stock limping up to a declining 50-day on light volume."""
    ticker = ticker.strip().upper()
    result = {
        "signal": None, "close": None, "sma_50": None, "pct_from_50d": None,
        "sma_50_declining": None, "days_below_50d_last_20": None,
        "vol_ratio": None, "stage": None, "triggered": False, "reason": "",
    }

    ma_df = calculate_moving_averages(ticker)
    if ma_df is None:
        result["reason"] = "insufficient price history for MAs"
        return result
    ma_df = ma_df.reset_index(drop=True)

    close = round(float(ma_df["Close"].iloc[-1]), 2)
    result["close"] = close
    sma_50_now = ma_df["sma_50"].iloc[-1]
    if pd.isna(sma_50_now):
        result["reason"] = "50-day MA not available"
        return result
    sma_50 = round(float(sma_50_now), 2)
    result["sma_50"] = sma_50
    pct_from_50d = round((close - sma_50) / sma_50 * 100, 2)
    result["pct_from_50d"] = pct_from_50d

    # Is the 50-day declining? (mirror of "rising")
    sma_50_10ago = ma_df["sma_50"].iloc[-11]
    sma_50_declining = bool(pd.notna(sma_50_10ago) and sma_50_now < sma_50_10ago)
    result["sma_50_declining"] = sma_50_declining

    # How many of the last 20 days closed BELOW the 50-day? (a downtrend, not a recovery)
    last20 = ma_df.tail(20)
    days_below = int((last20["Close"] < last20["sma_50"]).sum())
    result["days_below_50d_last_20"] = days_below

    stage = _stage_of(ticker)
    result["stage"] = stage
    vol_ratio = _latest_vol_ratio(ticker)
    result["vol_ratio"] = vol_ratio

    if stage is None:
        result["reason"] = "could not classify stage"
    elif stage != 4:
        result["reason"] = f"Stage {stage} (wrong stage)"
    elif not sma_50_declining:
        result["reason"] = "50-day MA not declining"
    elif pct_from_50d < -2:
        result["reason"] = f"price {pct_from_50d:.2f}% below 50-day (hasn't rallied up)"
    elif pct_from_50d > 5:
        result["reason"] = f"price {pct_from_50d:.2f}% above 50-day (too extended above)"
    elif days_below < 15:
        result["reason"] = f"only {days_below}/20 days below 50-day (not a clean downtrend)"
    elif vol_ratio is None:
        result["reason"] = "volume data unavailable"
    elif vol_ratio >= 1.0:
        result["reason"] = f"rally on heavy volume ({vol_ratio:.1f}× ≥ 1.0×) — not weak enough"
    else:
        result["signal"] = "Failed Rally to 50d"
        result["triggered"] = True
        result["reason"] = (
            f"rallied to {pct_from_50d:+.1f}% of a declining 50-day "
            f"on light volume ({vol_ratio:.1f}×)"
        )
    return result


# ---------------------------------------------------------------------------
# Aggregation + display
# ---------------------------------------------------------------------------
def get_entry_signals(ticker: str) -> dict:
    """Run all four detectors and bundle the results."""
    ticker = ticker.strip().upper()
    long_breakout = detect_breakout(ticker)
    long_pullback = detect_pullback_to_50d(ticker)
    short_breakdown = detect_breakdown(ticker)
    short_failed_rally = detect_failed_rally(ticker)

    any_triggered = any(
        r["triggered"]
        for r in (long_breakout, long_pullback, short_breakdown, short_failed_rally)
    )

    return {
        "ticker": ticker,
        "long_breakout": long_breakout,
        "long_pullback": long_pullback,
        "short_breakdown": short_breakdown,
        "short_failed_rally": short_failed_rally,
        "any_signal_triggered": any_triggered,
    }


def print_entry_signals(ticker: str) -> dict:
    """Print a clean ✓/✗ block of all four signals with reasons, return the dict."""
    signals = get_entry_signals(ticker)
    print(f"{signals['ticker']} | Entry Signals")

    rows = [
        ("Breakout", signals["long_breakout"]),
        ("Pullback to 50d", signals["long_pullback"]),
        ("Breakdown", signals["short_breakdown"]),
        ("Failed Rally", signals["short_failed_rally"]),
    ]
    for name, res in rows:
        mark = "✓" if res["triggered"] else "✗"
        detail = res["reason"] if res["triggered"] else f"not triggered: {res['reason']}"
        print(f"{mark} {name:<16}— {detail}")

    return signals


if __name__ == "__main__":
    for ticker in ["AAPL", "SPY", "XLF"]:
        print_entry_signals(ticker)
        print()
