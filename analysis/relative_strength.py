"""
analysis/relative_strength.py
=============================
Relative Strength (RS) — is this stock LEADING or LAGGING the market & its sector?

WHAT "RELATIVE STRENGTH" MEANS HERE
    RS = a stock's price performance MINUS a benchmark's, over a period:
        RS = (stock % return) − (benchmark % return)
    The result is in percentage points. Positive = the stock outperformed;
    negative = it lagged. This is NOT the RSI oscillator (a bounded 0–100
    overbought/oversold gauge) — completely different tool.

    WHY A DIFFERENCE, NOT A RATIO? An earlier version divided the two returns,
    but that's fragile: dividing by a small or NEGATIVE benchmark return flips
    the sign and makes outperformers look weak (and vice versa). The difference
    is always stable — no division, no sign flips:
        Stock +15%, SPY +10%  → RS = +5.0  (outperforming)
        Stock  −5%, SPY −10%  → RS = +5.0  (fell less → still outperforming)
        Stock  +3%, SPY +10%  → RS = −7.0  (lagging)

HOW IT FITS IN
    Third pillar of the trend engine (after moving averages and stage analysis).
    A Stage 2 stock that's also a strong RS leader vs both SPY and its sector is a
    high-conviction long. Reads price data from the database only.
"""

# ~21 trading days in a month — our unit for all lookback windows.
TRADING_DAYS_PER_MONTH = 21

# Import data-layer helpers. With PYTHONPATH=<project root> the first import
# works; the fallback inserts the project root so the file runs standalone too.
try:
    from data.db_reader import get_price_bars
    from data.database import TickerUniverse, get_session
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.db_reader import get_price_bars
    from data.database import TickerUniverse, get_session


def calculate_rs_diff(
    ticker: str,
    benchmark_ticker: str,
    months: int,
    skip_recent_month: bool = True,
):
    """Return the RS DIFFERENCE of `ticker` vs `benchmark_ticker` over `months`.

    RS difference = stock % return − benchmark % return, in percentage points.
    Positive means the stock outperformed the benchmark over the window.

    WHY SKIP THE MOST RECENT MONTH (skip_recent_month=True)?
        Very short-term price moves tend to mean-revert — this month's hottest
        names are often next month's laggards. Skipping the latest ~21 trading
        days removes that noisy, reversal-prone window and isolates the more
        DURABLE intermediate-term trend (the classic "12-minus-1-month"
        momentum construction used in academic studies).

    Returns:
        The RS difference (float, percentage points, rounded to 2dp), or None
        (with a warning) if either ticker lacks enough history.
    """
    lookback_days = months * TRADING_DAYS_PER_MONTH
    offset = TRADING_DAYS_PER_MONTH if skip_recent_month else 0
    needed = lookback_days + offset + 1  # rows required to reach back far enough

    # Pull just a bit more than we need (small buffer). get_price_bars returns
    # the most recent rows in chronological (oldest→newest) order.
    stock_df = get_price_bars(ticker, days=needed + 10)
    bench_df = get_price_bars(benchmark_ticker, days=needed + 10)

    if stock_df is None or bench_df is None:
        print(
            f"⚠️  calculate_rs_diff: missing price data "
            f"({ticker} or {benchmark_ticker})."
        )
        return None

    if len(stock_df) < needed or len(bench_df) < needed:
        print(
            f"⚠️  calculate_rs_diff: not enough history for {months}-month RS "
            f"of {ticker} vs {benchmark_ticker}."
        )
        return None

    def period_return_pct(df):
        """Percentage return between the window's start and end bars."""
        df = df.sort_values("Date").reset_index(drop=True)
        # end is `offset` bars before the latest; start is `lookback_days` before end.
        end_idx = len(df) - 1 - offset
        start_idx = end_idx - lookback_days
        if start_idx < 0:
            return None
        start_close = float(df["Close"].iloc[start_idx])
        end_close = float(df["Close"].iloc[end_idx])
        if start_close == 0:
            return None
        return (end_close - start_close) / start_close * 100  # percent

    stock_ret = period_return_pct(stock_df)
    bench_ret = period_return_pct(bench_df)

    if stock_ret is None or bench_ret is None:
        print(f"⚠️  calculate_rs_diff: could not compute returns for {ticker}.")
        return None

    # The whole point of the difference: no division, so no sign-flip / blow-up.
    return round(stock_ret - bench_ret, 2)


def _lookup_sector_etf(ticker: str) -> str:
    """Find a ticker's sector ETF from TickerUniverse; fall back to SPY."""
    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker.strip().upper())
            .first()
        )
        if row is not None and row.sector_etf:
            return row.sector_etf
        print(
            f"note: '{ticker}' not found in TickerUniverse (or no sector ETF) — "
            "using SPY for the sector comparison."
        )
        return "SPY"
    finally:
        session.close()


def _composite(rs_3m, rs_6m, rs_12m):
    """Weighted-average composite of the RS differences (heavier on longer terms).

    Result is in percentage points, same units as the inputs.
    """
    if rs_3m is None or rs_6m is None or rs_12m is None:
        return None
    return round(rs_3m * 0.25 + rs_6m * 0.35 + rs_12m * 0.40, 2)


def get_rs_profile(ticker: str) -> dict:
    """Build a full RS profile vs SPY and vs the stock's sector ETF.

    Returns a dict with the 3/6/12-month RS differences and composite scores
    against both SPY and the sector ETF, plus which sector ETF was used.
    (Values are percentage-point spreads; positive = outperforming.)
    """
    ticker = ticker.strip().upper()
    sector_etf = _lookup_sector_etf(ticker)

    # RS vs the broad market.
    rs_3m_vs_spy = calculate_rs_diff(ticker, "SPY", 3)
    rs_6m_vs_spy = calculate_rs_diff(ticker, "SPY", 6)
    rs_12m_vs_spy = calculate_rs_diff(ticker, "SPY", 12)

    # RS vs the stock's own sector.
    rs_3m_vs_sector = calculate_rs_diff(ticker, sector_etf, 3)
    rs_6m_vs_sector = calculate_rs_diff(ticker, sector_etf, 6)
    rs_12m_vs_sector = calculate_rs_diff(ticker, sector_etf, 12)

    return {
        "rs_3m_vs_spy": rs_3m_vs_spy,
        "rs_6m_vs_spy": rs_6m_vs_spy,
        "rs_12m_vs_spy": rs_12m_vs_spy,
        "composite_vs_spy": _composite(rs_3m_vs_spy, rs_6m_vs_spy, rs_12m_vs_spy),
        "rs_3m_vs_sector": rs_3m_vs_sector,
        "rs_6m_vs_sector": rs_6m_vs_sector,
        "rs_12m_vs_sector": rs_12m_vs_sector,
        "composite_vs_sector": _composite(
            rs_3m_vs_sector, rs_6m_vs_sector, rs_12m_vs_sector
        ),
        "sector_etf_used": sector_etf,
    }


def get_rs_rating(composite_score) -> str:
    """Turn a composite RS difference (percentage points) into a plain label.

    Thresholds are now in percentage-point spreads vs the benchmark:
        Strong Leader (>= +8.0): outpacing by a wide margin — top-tier LONGs.
        Leader        (>= +3.0): clearly outperforming — favorable for longs.
        In Line       (>= -3.0): roughly matching the benchmark — neutral.
        Laggard       (>= -8.0): underperforming — avoid longs; possible short.
        Weak Laggard  (<  -8.0): badly trailing — strong SHORT candidate.
    """
    if composite_score is None:
        return "N/A"
    if composite_score >= 8.0:
        return "Strong Leader"
    if composite_score >= 3.0:
        return "Leader"
    if composite_score >= -3.0:
        return "In Line"
    if composite_score >= -8.0:
        return "Laggard"
    return "Weak Laggard"


def _fmt(value) -> str:
    """Format an RS value with a leading sign to 1dp, or 'N/A' if missing."""
    return f"{value:+.1f}" if value is not None else "N/A"


def get_rs_summary(ticker: str) -> dict:
    """Print a clean two-line RS summary (vs SPY and vs sector) and return the dict."""
    ticker = ticker.strip().upper()
    profile = get_rs_profile(ticker)

    spy_rating = get_rs_rating(profile["composite_vs_spy"])
    sector_rating = get_rs_rating(profile["composite_vs_sector"])
    sector_etf = profile["sector_etf_used"]

    # Composite carries a "pp" (percentage points) unit label for clarity.
    spy_comp = profile["composite_vs_spy"]
    sector_comp = profile["composite_vs_sector"]
    spy_comp_str = f"{spy_comp:+.1f}pp" if spy_comp is not None else "N/A"
    sector_comp_str = f"{sector_comp:+.1f}pp" if sector_comp is not None else "N/A"

    print(
        f"{ticker} | RS vs SPY: "
        f"3m: {_fmt(profile['rs_3m_vs_spy'])} | "
        f"6m: {_fmt(profile['rs_6m_vs_spy'])} | "
        f"12m: {_fmt(profile['rs_12m_vs_spy'])} | "
        f"Composite: {spy_comp_str} → {spy_rating}"
    )
    print(
        f"{ticker} | RS vs {sector_etf}: "
        f"3m: {_fmt(profile['rs_3m_vs_sector'])} | "
        f"6m: {_fmt(profile['rs_6m_vs_sector'])} | "
        f"12m: {_fmt(profile['rs_12m_vs_sector'])} | "
        f"Composite: {sector_comp_str} → {sector_rating}"
    )

    return profile


if __name__ == "__main__":
    # AAPL: a real S&P 500 stock → compared vs SPY and vs its sector (XLK).
    print("--- AAPL ---")
    get_rs_summary("AAPL")

    # SPY: sanity check — SPY vs itself must be ~0.0 across every window.
    print("\n--- SPY (sanity: should be ~0.0 everywhere) ---")
    get_rs_summary("SPY")

    # XLF: the Stage 4 (declining) sector from last step — expect negative RS.
    print("\n--- XLF (Stage 4 sector: expect negative RS) ---")
    get_rs_summary("XLF")
