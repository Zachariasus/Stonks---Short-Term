"""
analysis/volume_analysis.py
===========================
Volume analysis — the CONFIRMATION layer of the trend engine.

WHY VOLUME MATTERS
    Price tells you WHAT happened; volume tells you how much CONVICTION was
    behind it. A price rise on heavy volume means real buyers are committing
    capital (accumulation); the same rise on thin volume is suspect and prone to
    reversal. By comparing volume on up days vs down days — and tracking the
    DIRECTION of On-Balance Volume — we can see whether big money is quietly
    accumulating or distributing a stock, something price alone can't reveal.

HOW IT FITS IN
    Fourth pillar of the trend engine (after MAs, stage, and relative strength).
    A Stage 2 stock with accumulation behind it is a far stronger long than one
    drifting up on no volume. Reads price+volume from the database only.

NOTE ON OBV
    OBV is a signed CUMULATIVE total that can pass through zero, so any metric
    that DIVIDES by an OBV value (a % slope, OBV-vs-its-MA, etc.) is unstable and
    can explode. We therefore use only OBV's DIRECTION (did it finish higher or
    lower over the lookback?) — a pure comparison that can never blow up. The
    up/down volume ratio supplies the magnitude.
"""

import pandas as pd

# Import the DB reader. With PYTHONPATH=<project root> the first import works;
# the fallback inserts the project root so the file runs standalone too.
try:
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.db_reader import get_price_bars


def calculate_volume_metrics(ticker: str, days: int = 100):
    """Load price data and add volume-analysis columns.

    Returns:
        The DataFrame with is_up_day, up_volume, down_volume, obv, vol_sma_20,
        vol_sma_50, and vol_ratio columns added — or None if <50 rows.
    """
    df = get_price_bars(ticker, days=days)

    if df is None or len(df) < 50:
        have = 0 if df is None else len(df)
        print(
            f"⚠️  calculate_volume_metrics: not enough history for '{ticker}' "
            f"({have} rows; need ≥50)."
        )
        return None

    df = df.sort_values("Date").reset_index(drop=True).copy()

    # An "up day" is one that closed >= the prior close. The first row has no
    # prior close (diff is NaN → comparison is False), which we handle for OBV.
    prev_close = df["Close"].shift(1)
    df["is_up_day"] = df["Close"] >= prev_close

    # Split each day's volume into the "up" bucket or the "down" bucket.
    df["up_volume"] = df["Volume"].where(df["is_up_day"], 0)
    df["down_volume"] = df["Volume"].where(~df["is_up_day"], 0)

    # --- On-Balance Volume (OBV) ---
    # Created by Joe Granville (1963). OBV is a running total that ADDS the day's
    # volume on up days and SUBTRACTS it on down days. Rising OBV = buying
    # pressure accumulating; falling OBV = distribution. We use only its
    # DIRECTION (see the module note) — never a ratio of its level.
    signed_volume = df["up_volume"] - df["down_volume"]  # +V on up days, −V on down
    signed_volume.iloc[0] = 0  # first row has no prior close → contributes 0
    df["obv"] = signed_volume.cumsum()

    # --- Volume moving averages and today's volume vs its recent norm ---
    df["vol_sma_20"] = df["Volume"].rolling(window=20).mean()
    df["vol_sma_50"] = df["Volume"].rolling(window=50).mean()
    # vol_ratio > 1.5 = unusually heavy day; < 0.5 = unusually light day.
    df["vol_ratio"] = df["Volume"] / df["vol_sma_20"]

    return df


def get_volume_profile(ticker: str, lookback_days: int = 50):
    """Summarize accumulation/distribution behavior over the recent lookback.

    Returns:
        {up_down_vol_ratio, obv_direction, vol_trend, avg_vol_ratio,
         accumulation_label, lookback_days}, or None if data is insufficient.
    """
    # Load enough history that the 50-day volume SMA is valid across the lookback.
    df = calculate_volume_metrics(ticker, days=lookback_days + 50)
    if df is None:
        print(f"⚠️  get_volume_profile: no data for '{ticker}'.")
        return None

    recent = df.tail(lookback_days)

    # --- Up-volume vs down-volume over the window ---
    # >1.0 → more volume traded on up days than down days = accumulation.
    up_total = float(recent["up_volume"].sum())
    down_total = float(recent["down_volume"].sum())
    if down_total > 0:
        up_down_vol_ratio = round(up_total / down_total, 2)
    else:
        # No down-volume at all in the window → effectively pure accumulation.
        up_down_vol_ratio = 99.0

    # --- OBV direction over the lookback ---
    # Pure comparison: did OBV finish higher than it started? No division, so it
    # can never blow up (unlike a % slope or OBV-vs-MA, which fail when OBV is
    # near zero). This is the sign we actually need for the label.
    obv_start = float(recent["obv"].iloc[0])
    obv_end = float(recent["obv"].iloc[-1])
    obv_direction = "up" if obv_end > obv_start else "down"

    # --- Volume trend: is recent volume heavier than the longer-term average? ---
    v20 = recent["vol_sma_20"].iloc[-1]
    v50 = recent["vol_sma_50"].iloc[-1]
    if pd.isna(v20) or pd.isna(v50) or v50 == 0:
        vol_trend = "Neutral"
    elif abs(v20 - v50) / v50 <= 0.05:  # within 5% of each other
        vol_trend = "Neutral"
    elif v20 > v50:
        vol_trend = "Expanding"   # recent volume heavier → adds conviction
    else:
        vol_trend = "Contracting"

    # Average of today's-volume-vs-20d-mean across the window (overall activity).
    avg_vol_ratio = round(float(recent["vol_ratio"].mean()), 2)

    # --- Accumulation / distribution label (checked in priority order) ---
    # Combines the up/down volume ratio (magnitude) with OBV direction (the sign).
    if up_down_vol_ratio >= 1.2 and obv_direction == "up":
        accumulation_label = "Accumulation"
    elif up_down_vol_ratio >= 1.0 and obv_direction == "up":
        accumulation_label = "Mild Accumulation"
    elif up_down_vol_ratio <= 0.8 and obv_direction == "down":
        accumulation_label = "Distribution"
    elif up_down_vol_ratio <= 1.0 and obv_direction == "down":
        accumulation_label = "Mild Distribution"
    else:
        accumulation_label = "Neutral"

    return {
        "up_down_vol_ratio": up_down_vol_ratio,
        "obv_direction": obv_direction,
        "vol_trend": vol_trend,
        "avg_vol_ratio": avg_vol_ratio,
        "accumulation_label": accumulation_label,
        "lookback_days": lookback_days,
    }


def get_volume_summary(ticker: str):
    """Print a clean volume block for a ticker and return the profile dict."""
    profile = get_volume_profile(ticker)
    if profile is None:
        print(f"{ticker} | (insufficient data)")
        return None

    print(f"{ticker} | Volume: {profile['accumulation_label']}")
    print(
        f"Up/Down vol ratio: {profile['up_down_vol_ratio']:.2f} | "
        f"OBV direction: {profile['obv_direction'].capitalize()}"
    )
    print(
        f"Volume trend: {profile['vol_trend']} | "
        f"Avg vol vs 20d mean: {profile['avg_vol_ratio']:.2f}x"
    )

    return profile


if __name__ == "__main__":
    results = {}
    for ticker in ["AAPL", "SPY", "XLF"]:
        results[ticker] = get_volume_summary(ticker)
        print()

    # One-line consistency check against what we already know:
    #   AAPL & SPY are Stage 2 (uptrends); XLF is Stage 4 with weak RS (downtrend).
    def _label(t):
        return results[t]["accumulation_label"] if results[t] else "N/A"

    distribution_labels = {"Distribution", "Mild Distribution"}
    xlf_label = _label("XLF")
    aapl_label = _label("AAPL")
    spy_label = _label("SPY")

    xlf_shows_distribution = xlf_label in distribution_labels
    uptrends_not_distribution = (
        aapl_label not in distribution_labels and spy_label not in distribution_labels
    )
    verdict = (
        "consistent with their stages"
        if (xlf_shows_distribution and uptrends_not_distribution)
        else "a mixed read — some labels diverge from the stage/RS picture"
    )

    print(
        f"Observation: volume labels are {verdict} — "
        f"XLF (Stage 4 / weak RS) → {xlf_label}; "
        f"AAPL (Stage 2) → {aapl_label}; SPY (Stage 2) → {spy_label}."
    )
