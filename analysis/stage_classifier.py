"""
analysis/stage_classifier.py
============================
Weinstein Stage 1–4 classifier — the system's first real judgment on a stock.

THE FOUR STAGES (Stan Weinstein's stage analysis)
    Stage 1 — Basing:     flat MA, price churning sideways after a decline. Wait.
    Stage 2 — Advancing:  rising MA, price above it. OUR PRIMARY LONG SETUP.
    Stage 3 — Topping:    MA flattening/rolling over after an advance. Avoid longs.
    Stage 4 — Declining:  falling MA, price below it. OUR PRIMARY SHORT SETUP.

HOW IT FITS IN
    Reads price + moving averages from analysis/moving_averages.py (which in turn
    reads the database). It adds no new data access — it's pure judgment on top of
    the MAs. Downstream, the screener uses the stage to decide long vs. short vs.
    skip.
"""

import pandas as pd

# Import the moving-average helpers. With PYTHONPATH=<project root> the first
# import works; the fallback inserts the project root so the file also runs
# standalone.
try:
    from analysis.moving_averages import (
        are_mas_stacked_bullish,
        calculate_moving_averages,
    )
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.moving_averages import (
        are_mas_stacked_bullish,
        calculate_moving_averages,
    )

# 10 weeks ≈ 50 trading days. We measure the 30-week MA's slope over this span.
SLOPE_LOOKBACK_DAYS = 50

# Human-readable name for each stage number.
STAGE_LABELS = {1: "Basing", 2: "Advancing", 3: "Topping", 4: "Declining"}


def classify_stage(ticker: str):
    """Classify a ticker into Weinstein Stage 1–4 based on its trend structure.

    Returns:
        A dict of the stage plus all the intermediate values used to reach it
        (for transparency), or None if there isn't enough data.
    """
    df = calculate_moving_averages(ticker)
    if df is None:
        print(f"⚠️  classify_stage: not enough data for '{ticker}'.")
        return None

    df = df.reset_index(drop=True)
    last = df.iloc[-1]
    close = float(last["Close"])
    sma_30w = last["sma_30w"]
    sma_200 = last["sma_200"]

    # The 30-week and 200-day lines anchor the whole classification, so if either
    # is missing for the latest day we can't responsibly assign a stage.
    if pd.isna(sma_30w) or pd.isna(sma_200):
        print(f"⚠️  classify_stage: MAs not yet valid for '{ticker}'.")
        return None
    sma_30w = float(sma_30w)
    sma_200 = float(sma_200)

    # --- 30-week MA slope over the past ~10 weeks ---
    # Why look back 10 weeks instead of just the last day? A single day's MA
    # value wiggles with daily noise and can't tell you the trend's DIRECTION.
    # The *slope* over ~10 weeks reveals whether the 30-week line is genuinely
    # rising, flat, or falling — and that direction is the heart of what makes a
    # stock Stage 2 (rising) vs Stage 4 (falling) vs Stage 1/3 (flat).
    if len(df) > SLOPE_LOOKBACK_DAYS:
        past_30w = df["sma_30w"].iloc[-1 - SLOPE_LOOKBACK_DAYS]
    else:
        past_30w = df["sma_30w"].iloc[0]

    if pd.isna(past_30w) or past_30w == 0:
        ma_slope_pct = 0.0
    else:
        ma_slope_pct = round((sma_30w - float(past_30w)) / float(past_30w) * 100, 2)

    # --- 52-week high / low (last 252 trading days) and how far price is from each ---
    recent_closes = df["Close"].tail(252)
    high_52w = float(recent_closes.max())
    low_52w = float(recent_closes.min())
    pct_off_high = round((close - high_52w) / high_52w * 100, 2)  # ≤ 0 (below the high)
    pct_off_low = round((close - low_52w) / low_52w * 100, 2)     # ≥ 0 (above the low)

    # --- Simple structural flags ---
    price_vs_30w = "above" if close > sma_30w else "below"
    price_vs_200 = "above" if close > sma_200 else "below"
    mas_stacked = are_mas_stacked_bullish(ticker)
    near_30w = abs((close - sma_30w) / sma_30w * 100) <= 4  # within ±4% of the 30-week

    # ------------------------------------------------------------------
    # CLASSIFICATION LOGIC (checked in priority order)
    #
    #   Stage 2 (Advancing): price above a clearly RISING 30-week MA, and above
    #       the 200-day. The textbook uptrend — what we want to go long.
    #   Stage 4 (Declining): price below a clearly FALLING 30-week MA, and below
    #       the 200-day. The textbook downtrend — what we want to short.
    #   Stage 3 (Topping): still above the 30-week, but the MA has stopped rising
    #       (flat/negative slope) AND price has dropped >10% off its 52-week high
    #       — i.e. it was advancing and is now rolling over. Avoid new longs.
    #   Stage 1 (Basing): price hugging a flat 30-week MA (±4%, slope near zero)
    #       — consolidating, neither advancing nor declining.
    #   Anything that fits none of the above defaults to Stage 1 (treat as
    #       indeterminate / basing rather than risk a false trend signal).
    # ------------------------------------------------------------------
    if close > sma_30w and ma_slope_pct > 0.5 and close > sma_200:
        stage = 2
    elif close < sma_30w and ma_slope_pct < -0.5 and close < sma_200:
        stage = 4
    elif close > sma_30w and ma_slope_pct <= 0.5 and pct_off_high < -10:
        stage = 3
    elif near_30w and -0.5 <= ma_slope_pct <= 0.5:
        stage = 1
    else:
        stage = 1  # default / edge cases

    return {
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "ma_slope_pct": ma_slope_pct,
        "price_vs_30w": price_vs_30w,
        "price_vs_200": price_vs_200,
        "mas_stacked": mas_stacked,
        "pct_off_high": pct_off_high,
        "pct_off_low": pct_off_low,
        "close": round(close, 2),
        "sma_30w": round(sma_30w, 2),
        "sma_200": round(sma_200, 2),
    }


def get_stage_summary(ticker: str):
    """Print a clean one-block stage summary for a ticker and return the dict."""
    result = classify_stage(ticker)
    if result is None:
        print(f"{ticker} | (insufficient data)")
        return None

    stacked = "Yes" if result["mas_stacked"] else "No"

    print(f"{ticker} | Stage {result['stage']} — {result['stage_label']}")
    print(
        f"Close: ${result['close']} | "
        f"30-week MA: ${result['sma_30w']} | "
        f"200-day MA: ${result['sma_200']}"
    )
    print(
        f"MA slope (10w): {result['ma_slope_pct']:+.1f}% | "
        f"Off 52w high: {result['pct_off_high']:+.1f}% | "
        f"Off 52w low: {result['pct_off_low']:+.1f}%"
    )
    print(f"MAs stacked bullish: {stacked}")

    return result


if __name__ == "__main__":
    # Pull in the sector ETF list for the breadth check.
    from data.universe_etfs import SECTOR_ETFS

    tickers = ["AAPL", "SPY"] + list(SECTOR_ETFS.keys())

    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for ticker in tickers:
        result = get_stage_summary(ticker)
        print()  # blank line between blocks
        if result is not None:
            counts[result["stage"]] += 1

    # Mini market-breadth read: how many of these tickers are in each stage.
    print("=== Stage breakdown ===")
    print(f"Stage 1 (Basing):    {counts[1]} tickers")
    print(f"Stage 2 (Advancing): {counts[2]} tickers")
    print(f"Stage 3 (Topping):   {counts[3]} tickers")
    print(f"Stage 4 (Declining): {counts[4]} tickers")
