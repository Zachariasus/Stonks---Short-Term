"""
analysis/relative_strength.py
=============================
Relative Strength (RS) — is this stock LEADING or LAGGING the market & its sector?

WHAT "RELATIVE STRENGTH" MEANS HERE
    RS = a stock's price performance compared to a benchmark over a period.
    RS ratio = (stock's % return) / (benchmark's % return). Above 1.0 = the stock
    outperformed; below 1.0 = it lagged. This is NOT the RSI oscillator — RSI is a
    bounded 0–100 overbought/oversold momentum gauge and is a completely different
    tool. Here, "relative strength" purely means out/under-performance vs a peer.

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


def calculate_rs_ratio(
    ticker: str,
    benchmark_ticker: str,
    months: int,
    skip_recent_month: bool = True,
):
    """Return the RS ratio of `ticker` vs `benchmark_ticker` over `months`.

    RS ratio = stock % return / benchmark % return over the same window.
    > 1.0 means the stock outperformed the benchmark.

    WHY SKIP THE MOST RECENT MONTH (skip_recent_month=True)?
        Very short-term price moves tend to mean-revert — this month's hottest
        names are often next month's laggards. Skipping the latest ~21 trading
        days removes that noisy, reversal-prone window and isolates the more
        DURABLE intermediate-term trend (the classic "12-minus-1-month"
        momentum construction used in academic studies).

    Returns:
        The RS ratio (float, rounded to 2dp), or None (with a warning) if either
        ticker lacks enough history.
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
            f"⚠️  calculate_rs_ratio: missing price data "
            f"({ticker} or {benchmark_ticker})."
        )
        return None

    if len(stock_df) < needed or len(bench_df) < needed:
        print(
            f"⚠️  calculate_rs_ratio: not enough history for {months}-month RS "
            f"of {ticker} vs {benchmark_ticker}."
        )
        return None

    def period_return(df):
        """Fractional return between the window's start and end bars."""
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
        return (end_close - start_close) / start_close

    stock_ret = period_return(stock_df)
    bench_ret = period_return(bench_df)

    if stock_ret is None or bench_ret is None:
        print(f"⚠️  calculate_rs_ratio: could not compute returns for {ticker}.")
        return None

    # Benchmark return of ~0 makes the ratio meaningless / explode — guard it.
    if bench_ret == 0:
        print(
            f"⚠️  calculate_rs_ratio: benchmark {benchmark_ticker} had ~0 return "
            f"over {months}m; RS ratio undefined."
        )
        return None

    return round(stock_ret / bench_ret, 2)


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
    """Weighted-average composite (heavier weight on the longer, more durable RS)."""
    if rs_3m is None or rs_6m is None or rs_12m is None:
        return None
    return round(rs_3m * 0.25 + rs_6m * 0.35 + rs_12m * 0.40, 2)


def get_rs_profile(ticker: str) -> dict:
    """Build a full RS profile vs SPY and vs the stock's sector ETF.

    Returns a dict with the 3/6/12-month RS ratios and composite scores against
    both SPY and the sector ETF, plus which sector ETF was used.
    """
    ticker = ticker.strip().upper()
    sector_etf = _lookup_sector_etf(ticker)

    # RS vs the broad market.
    rs_3m_vs_spy = calculate_rs_ratio(ticker, "SPY", 3)
    rs_6m_vs_spy = calculate_rs_ratio(ticker, "SPY", 6)
    rs_12m_vs_spy = calculate_rs_ratio(ticker, "SPY", 12)

    # RS vs the stock's own sector.
    rs_3m_vs_sector = calculate_rs_ratio(ticker, sector_etf, 3)
    rs_6m_vs_sector = calculate_rs_ratio(ticker, sector_etf, 6)
    rs_12m_vs_sector = calculate_rs_ratio(ticker, sector_etf, 12)

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
    """Turn a composite RS score into a plain label for trade selection.

    Strong Leader (>=1.15): meaningfully outpacing — top-tier LONG candidates.
    Leader        (>=1.05): outperforming — favorable for longs.
    In Line       (>=0.95): roughly matching the benchmark — neutral, no edge.
    Laggard       (>=0.85): underperforming — avoid for longs; possible short.
    Weak Laggard  (< 0.85): badly trailing — strong SHORT candidate / avoid long.
    """
    if composite_score is None:
        return "N/A"
    if composite_score >= 1.15:
        return "Strong Leader"
    if composite_score >= 1.05:
        return "Leader"
    if composite_score >= 0.95:
        return "In Line"
    if composite_score >= 0.85:
        return "Laggard"
    return "Weak Laggard"


def _fmt(value) -> str:
    """Format an RS value to 2dp, or 'N/A' if missing."""
    return f"{value:.2f}" if value is not None else "N/A"


def get_rs_summary(ticker: str) -> dict:
    """Print a clean two-line RS summary (vs SPY and vs sector) and return the dict."""
    ticker = ticker.strip().upper()
    profile = get_rs_profile(ticker)

    spy_rating = get_rs_rating(profile["composite_vs_spy"])
    sector_rating = get_rs_rating(profile["composite_vs_sector"])
    sector_etf = profile["sector_etf_used"]

    print(
        f"{ticker} | RS vs SPY: "
        f"3m: {_fmt(profile['rs_3m_vs_spy'])} | "
        f"6m: {_fmt(profile['rs_6m_vs_spy'])} | "
        f"12m: {_fmt(profile['rs_12m_vs_spy'])} | "
        f"Composite: {_fmt(profile['composite_vs_spy'])} → {spy_rating}"
    )
    print(
        f"{ticker} | RS vs {sector_etf}: "
        f"3m: {_fmt(profile['rs_3m_vs_sector'])} | "
        f"6m: {_fmt(profile['rs_6m_vs_sector'])} | "
        f"12m: {_fmt(profile['rs_12m_vs_sector'])} | "
        f"Composite: {_fmt(profile['composite_vs_sector'])} → {sector_rating}"
    )

    return profile


if __name__ == "__main__":
    # AAPL: a real S&P 500 stock → compared vs SPY and vs its sector (XLK).
    print("--- AAPL ---")
    get_rs_summary("AAPL")

    # SPY: sanity check — SPY vs itself must be 1.0 across every window.
    print("\n--- SPY (sanity: should be 1.00 everywhere) ---")
    get_rs_summary("SPY")

    # XLF: the Stage 4 (declining) sector from last step — expect weak RS.
    print("\n--- XLF (Stage 4 sector: expect weak RS) ---")
    get_rs_summary("XLF")
