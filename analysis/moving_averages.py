"""
analysis/moving_averages.py
===========================
Moving-average calculations — the first piece of the Trend & Momentum engine.

WHAT THIS MODULE DOES
    Computes the key moving averages traders use to judge a stock's trend:
    the 50-, 100-, and 200-day simple moving averages (on daily closes) plus
    the 30-week SMA (on weekly closes), and a few derived trend signals.

IMPORTANT — LAYER SEPARATION
    Analysis modules read price data from the DATABASE (via db_reader), never
    directly from yfinance. The data layer (fetch/store) and the analysis layer
    (compute) are kept separate: this module just consumes whatever is stored.
"""

import pandas as pd

# Import the DB reader. With PYTHONPATH=<project root> the first import works;
# the fallback lets the file run standalone by adding the project root to the path.
try:
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.db_reader import get_price_bars


def calculate_moving_averages(ticker: str, days: int = 300):
    """Compute the 50/100/200-day and 30-week moving averages for a ticker.

    Args:
        ticker: Stock symbol, e.g. "AAPL".
        days:   How many recent daily bars to load from the DB (default 300 —
                enough to produce a valid 200-day SMA and a 30-week SMA).

    Returns:
        The price DataFrame with sma_50, sma_100, sma_200, sma_30w columns added,
        or None (with a warning) if fewer than 200 rows of history are available.
    """
    df = get_price_bars(ticker, days=days)

    # Need at least 200 daily bars or the 200-day SMA is meaningless.
    if df is None or len(df) < 200:
        have = 0 if df is None else len(df)
        print(
            f"⚠️  calculate_moving_averages: not enough history for '{ticker}' "
            f"({have} rows; need ≥200)."
        )
        return None

    df = df.sort_values("Date").reset_index(drop=True).copy()

    # --- Daily simple moving averages on the closing price ---
    df["sma_50"] = df["Close"].rolling(window=50).mean()
    df["sma_100"] = df["Close"].rolling(window=100).mean()
    df["sma_200"] = df["Close"].rolling(window=200).mean()

    # --- 30-WEEK SMA (computed on WEEKLY data, then mapped back to daily) ---
    # Why weekly, not just a 150-day SMA? A 30-week SMA averages 30 weekly
    # closes (one data point per week), whereas a 150-day SMA averages 150 daily
    # closes. They track each other closely but aren't identical: the weekly
    # version smooths out intra-week noise and is the traditional tool of
    # "stage analysis" (Weinstein), where the 30-week line on a weekly chart
    # defines the Stage 1→2 trend change. We compute it the traditional way.
    #
    # Steps: label each day with the week it belongs to, take that week's last
    # close, run a 30-week rolling mean, then map the weekly value back onto
    # every daily row so each trading day carries a 30-week MA value.
    week = df["Date"].dt.to_period("W")            # the ISO week each day falls in
    weekly_close = df.groupby(week)["Close"].last()  # last close of each week
    weekly_sma_30 = weekly_close.rolling(window=30).mean()
    df["sma_30w"] = week.map(weekly_sma_30).to_numpy()

    # Return only the columns that matter downstream, in a sensible order.
    return df[
        ["Date", "Close", "Volume", "sma_50", "sma_100", "sma_200", "sma_30w"]
    ]


def get_ma_snapshot(ticker: str):
    """Return just the most recent moving-average reading as a dict.

    Returns:
        {close, sma_50, sma_100, sma_200, sma_30w, pct_above_sma50,
         pct_above_sma200} for the latest trading day, or None if unavailable.
        pct_above_* tells us how stretched (or compressed) price is vs. its trend.
    """
    df = calculate_moving_averages(ticker)
    if df is None:
        return None

    last = df.iloc[-1]  # the most recent trading day
    close = float(last["Close"])

    def _round(value):
        return round(float(value), 2) if pd.notna(value) else None

    sma_50 = last["sma_50"]
    sma_200 = last["sma_200"]

    # % above the 50- and 200-day lines. Guard against NaN / zero divisors.
    pct_above_sma50 = (
        round((close - sma_50) / sma_50 * 100, 2)
        if pd.notna(sma_50) and sma_50
        else None
    )
    pct_above_sma200 = (
        round((close - sma_200) / sma_200 * 100, 2)
        if pd.notna(sma_200) and sma_200
        else None
    )

    return {
        "close": round(close, 2),
        "sma_50": _round(sma_50),
        "sma_100": _round(last["sma_100"]),
        "sma_200": _round(sma_200),
        "sma_30w": _round(last["sma_30w"]),
        "pct_above_sma50": pct_above_sma50,
        "pct_above_sma200": pct_above_sma200,
    }


def are_mas_stacked_bullish(ticker: str) -> bool:
    """True if the moving averages are 'stacked' bullishly: close > 50 > 100 > 200.

    "Stacked MAs" means price sits above the short MA, which sits above the
    medium MA, which sits above the long MA — each shorter (faster) average
    higher than the next. That ordering only happens when a stock has been
    rising steadily, so it's the classic fingerprint of a healthy Stage 2
    uptrend. If the order is broken, the trend is weakening or absent.
    """
    df = calculate_moving_averages(ticker)
    if df is None:
        return False

    last = df.iloc[-1]
    values = [last["Close"], last["sma_50"], last["sma_100"], last["sma_200"]]
    # Any missing average → we can't confirm the stack → not bullish.
    if any(pd.isna(v) for v in values):
        return False

    return last["Close"] > last["sma_50"] > last["sma_100"] > last["sma_200"]


if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    print("=== AAPL moving averages (last 5 rows) ===")
    aapl_df = calculate_moving_averages("AAPL")
    if aapl_df is not None:
        print(aapl_df.tail().to_string(index=False))

    print("\n=== AAPL MA snapshot ===")
    aapl_snap = get_ma_snapshot("AAPL")
    if aapl_snap:
        for key, value in aapl_snap.items():
            print(f"  {key}: {value}")

    print(f"\nAAPL MAs bullish: {are_mas_stacked_bullish('AAPL')}")

    print("\n=== SPY MA snapshot (sanity check) ===")
    spy_snap = get_ma_snapshot("SPY")
    if spy_snap:
        for key, value in spy_snap.items():
            print(f"  {key}: {value}")

    print(f"\nSPY MAs bullish: {are_mas_stacked_bullish('SPY')}")
