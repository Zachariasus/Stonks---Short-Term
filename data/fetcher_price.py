"""
data/fetcher_price.py
=====================
Price & volume (OHLCV) fetcher — the entry point for all market data in Stonks.

WHAT THIS MODULE DOES
    Pulls historical Open / High / Low / Close / Volume bars for one or many
    tickers from Yahoo Finance (via the `yfinance` library) and returns them as
    tidy pandas DataFrames.

HOW IT FITS INTO THE PROJECT
    This is the very first link in the data pipeline. Almost everything
    downstream depends on the price history it produces:
        - analysis/  → trend, valuation, and other engines read these bars
        - screener/  → scans many tickers' price/volume to flag setups
        - grader/    → deep single-stock analysis starts from this data
    Later steps in Phase 2 will take the DataFrames produced here and persist
    them into a local SQLite database (via SQLAlchemy) so we don't re-download
    the same history repeatedly.

DESIGN NOTES
    - We use `yfinance.Ticker(...).history(...)` rather than `yf.download(...)`
      because it returns a clean, single-level-column DataFrame for one ticker,
      which is easier to reason about.
    - Failures are handled *gracefully*: a bad/delisted ticker prints a warning
      and returns None instead of crashing, so a bulk run never dies on one bad
      symbol.
"""

from typing import Optional

import pandas as pd
import yfinance as yf

# The exact columns (and order) every DataFrame this module returns will have.
OHLCV_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "Ticker"]


def get_ohlcv(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV price/volume history for a single ticker.

    Args:
        ticker:   Stock symbol, e.g. "AAPL". Case-insensitive (we normalize it).
        period:   How far back to fetch. yfinance shorthand such as
                  "1d", "5d", "1mo", "6mo", "1y" (default), "5y", "max".
        interval: Bar size, e.g. "1d" (default, daily), "1h", "1wk", "1mo".

    Returns:
        A pandas DataFrame with columns
        [Date, Open, High, Low, Close, Volume, Ticker],
        or None if no data could be fetched (bad symbol, delisted, no data
        for the requested window, or a network error).
    """
    # Normalize the symbol so "aapl" and " AAPL " behave the same.
    ticker = ticker.strip().upper()

    # yfinance can raise on network issues or odd symbols — never let that
    # bubble up and kill the caller; turn it into a warning + None instead.
    try:
        # auto_adjust=True returns split/dividend-adjusted OHLC, which is what
        # we want for analysis. We set it explicitly so the behavior is locked
        # in and doesn't shift if yfinance changes its default.
        raw = yf.Ticker(ticker).history(
            period=period,
            interval=interval,
            auto_adjust=True,
        )
    except Exception as err:  # noqa: BLE001 - we intentionally catch everything
        print(f"⚠️  Warning: failed to fetch '{ticker}': {err}")
        return None

    # An unknown/delisted ticker, or a period with no trading data, comes back
    # as an empty DataFrame. Treat that as a soft failure.
    if raw is None or raw.empty:
        print(
            f"⚠️  Warning: no data returned for '{ticker}' "
            "(check the symbol — it may be wrong, delisted, or have no data "
            "for this period)."
        )
        return None

    # history() puts the timestamp in the index. Move it into a real column.
    df = raw.reset_index()

    # The date column is named "Date" for daily+ bars but "Datetime" for
    # intraday bars (e.g. interval="1h"). Normalize to "Date" either way.
    if "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "Date"})

    # Tag every row with its ticker so DataFrames can later be safely combined.
    df["Ticker"] = ticker

    # Keep only the columns we promised, in a stable order.
    return df[OHLCV_COLUMNS]


def get_ohlcv_bulk(
    tickers: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, Optional[pd.DataFrame]]:
    """Fetch OHLCV history for a list of tickers, one after another.

    Args:
        tickers:  List of stock symbols, e.g. ["AAPL", "MSFT", "NVDA"].
        period:   Same meaning as in get_ohlcv() (default "1y").
        interval: Same meaning as in get_ohlcv() (default "1d").

    Returns:
        A dict mapping each ticker to its DataFrame (or None if that one
        failed): {"AAPL": <DataFrame>, "MSFT": <DataFrame>, ...}.
    """
    results: dict[str, Optional[pd.DataFrame]] = {}

    for ticker in tickers:
        # Show progress on a single line: "Fetching AAPL... done"
        print(f"Fetching {ticker}...", end=" ", flush=True)
        df = get_ohlcv(ticker, period=period, interval=interval)
        # If get_ohlcv failed it already printed a warning (on its own line);
        # report a short status here either way.
        print("done" if df is not None else "FAILED")
        results[ticker] = df

    return results


if __name__ == "__main__":
    # Quick self-test: fetch 1 year of daily data for three large-cap names.
    sample_tickers = ["AAPL", "MSFT", "NVDA"]
    data = get_ohlcv_bulk(sample_tickers, period="1y", interval="1d")

    # By default pandas hides middle columns when the terminal is narrow;
    # widen the display so all 7 OHLCV columns are visible in the self-test.
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    # Show what a result looks like.
    print("\n--- First 5 rows of AAPL ---")
    aapl = data.get("AAPL")
    if aapl is not None:
        print(aapl.head())
    else:
        print("No AAPL data was returned.")

    # Confirm data actually came back for each ticker.
    print("\n--- Shape of each result (rows x columns) ---")
    for ticker, df in data.items():
        if df is not None:
            print(f"{ticker}: {df.shape[0]} rows x {df.shape[1]} columns")
        else:
            print(f"{ticker}: no data")
