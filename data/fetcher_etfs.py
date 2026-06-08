"""
data/fetcher_etfs.py
====================
Fetch & store the reference data (sector ETFs + benchmarks) used for
relative-strength analysis.

HOW IT FITS IN
    This module is pure glue — it reuses the pieces we already built:
        universe_etfs.ALL_REFERENCE_TICKERS  (which tickers)
        fetcher_price.get_ohlcv_bulk         (download prices)
        db_writer.save_price_bars            (store them)
    Reference tickers are stored in the SAME price_bars table as regular stocks,
    so Phase 3 can compare any stock against its sector ETF and the benchmarks
    using identical queries.
"""

# Support both `python -m data.fetcher_etfs` and `python data/fetcher_etfs.py`
# (and inline tests run with PYTHONPATH=<project root>).
try:
    from data.database import init_db
    from data.db_reader import get_price_bars
    from data.db_writer import save_price_bars
    from data.fetcher_price import get_ohlcv_bulk
    from data.universe_etfs import (
        ALL_REFERENCE_TICKERS,
        BENCHMARKS,
        SECTOR_ETFS,
    )
except ImportError:  # pragma: no cover
    from database import init_db  # type: ignore
    from db_reader import get_price_bars  # type: ignore
    from db_writer import save_price_bars  # type: ignore
    from fetcher_price import get_ohlcv_bulk  # type: ignore
    from universe_etfs import (  # type: ignore
        ALL_REFERENCE_TICKERS,
        BENCHMARKS,
        SECTOR_ETFS,
    )


def fetch_and_store_reference_data(period: str = "2y") -> dict:
    """Fetch and store price history for every sector ETF and benchmark.

    Why period="2y" (and not "1y")?
        Phase 3 measures relative strength over 3-, 6-, and 12-month windows.
        A 12-month lookback alone eats an entire year of data, leaving no
        history before the window starts — so trailing calculations near the
        oldest dates would be impossible. Two years gives a comfortable buffer
        so even the 12-month window always has prior data to work with, and
        leaves room for momentum/smoothing that needs a running start.

    Args:
        period: How far back to fetch (default "2y"). yfinance shorthand.

    Returns:
        {ticker: {"rows_inserted": n, "rows_skipped": n}} for every reference
        ticker (failed fetches show 0/0).
    """
    # Make sure the tables exist (idempotent — does nothing if already created).
    init_db()

    print(
        f"Fetching reference data for {len(ALL_REFERENCE_TICKERS)} tickers "
        f"(period={period})...\n"
    )

    # One bulk download for all 14 reference tickers (daily bars).
    data = get_ohlcv_bulk(ALL_REFERENCE_TICKERS, period=period, interval="1d")

    results: dict = {}
    total_inserted = 0
    total_skipped = 0
    fetched_ok = 0

    print()  # blank line between the fetch progress and the save progress
    for ticker in ALL_REFERENCE_TICKERS:
        df = data.get(ticker)

        if df is None:
            # Fetch failed for this one — record zeros and move on.
            results[ticker] = {"rows_inserted": 0, "rows_skipped": 0}
            continue

        fetched_ok += 1
        counts = save_price_bars(ticker, df)
        results[ticker] = {
            "rows_inserted": counts["inserted"],
            "rows_skipped": counts["skipped"],
        }
        total_inserted += counts["inserted"]
        total_skipped += counts["skipped"]

    # Human-readable summary.
    print("\n=== Summary ===")
    print(f"Tickers fetched successfully : {fetched_ok}/{len(ALL_REFERENCE_TICKERS)}")
    print(f"Total rows inserted          : {total_inserted}")
    print(f"Total rows skipped (existing): {total_skipped}")

    return results


if __name__ == "__main__":
    import pandas as pd

    # Show the full price table in the console.
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    # 1. Fetch & store everything.
    fetch_and_store_reference_data(period="2y")

    # 2. Read SPY back out of the database to confirm storage worked.
    print("\n=== SPY read back from DB ===")
    spy = get_price_bars("SPY", days=1000)  # large enough to pull all ~2y of rows
    if spy is not None:
        print(spy.head().to_string(index=False))
        print(f"Shape: {spy.shape[0]} rows x {spy.shape[1]} columns")
    else:
        print("SPY not found in DB.")

    # 3. Confirm every sector ETF and benchmark is now stored.
    print("\n=== Verify all reference tickers are in the database ===")
    for ticker in ALL_REFERENCE_TICKERS:
        df = get_price_bars(ticker, days=5)  # we only need to know data exists
        label = SECTOR_ETFS.get(ticker) or BENCHMARKS.get(ticker) or ""
        if df is not None and not df.empty:
            print(f"✓ {ticker:5s} ({label})")
        else:
            print(f"✗ {ticker:5s} — missing")
