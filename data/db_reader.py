"""
data/db_reader.py
=================
Read helpers — pull stored data back out of stonks.db as easy-to-use objects.

HOW IT FITS IN
    stonks.db  →  (this module)  →  analysis / screener / grader code

    These functions are the counterpart to db_writer.py. The Phase 3+ analysis
    engines call these to get data straight from disk (fast, offline) instead of
    re-hitting the internet through the fetchers.
"""

import pandas as pd
from sqlalchemy import select

# Support both `python -m data.db_reader` and `python data/db_reader.py`.
try:
    from data.database import Fundamentals, PriceBar, get_engine
except ImportError:  # pragma: no cover
    from database import Fundamentals, PriceBar, get_engine  # type: ignore


def get_price_bars(ticker: str, days: int = 365):
    """Return the most recent `days` of stored price bars for a ticker.

    Args:
        ticker: Stock symbol, e.g. "AAPL".
        days:   How many of the most recent rows to return (default 365).

    Returns:
        A DataFrame with columns [Date, Open, High, Low, Close, Volume, Ticker]
        sorted oldest→newest, or None if no data is stored for this ticker.
    """
    ticker = ticker.strip().upper()
    engine = get_engine()

    # Grab the newest `days` rows (ORDER BY date DESC + LIMIT), then we'll flip
    # them back into chronological order for the caller.
    stmt = (
        select(PriceBar)
        .where(PriceBar.ticker == ticker)
        .order_by(PriceBar.date.desc())
        .limit(days)
    )

    with engine.connect() as conn:
        raw = pd.read_sql(stmt, conn)

    if raw.empty:
        print(f"⚠️  get_price_bars: no stored data for '{ticker}'.")
        return None

    # Reshape DB columns into the same tidy format get_ohlcv() produces.
    raw = raw.sort_values("date")
    out = pd.DataFrame(
        {
            "Date": pd.to_datetime(raw["date"]),
            "Open": raw["open"],
            "High": raw["high"],
            "Low": raw["low"],
            "Close": raw["close"],
            "Volume": raw["volume"],
            "Ticker": raw["ticker"],
        }
    ).reset_index(drop=True)

    return out


def get_latest_fundamentals(ticker: str):
    """Return the most recently fetched fundamentals snapshot as a dict.

    Args:
        ticker: Stock symbol, e.g. "AAPL".

    Returns:
        A dict of {column_name: value} for the newest snapshot, or None if this
        ticker has no fundamentals stored.
    """
    ticker = ticker.strip().upper()
    engine = get_engine()

    # Newest snapshot first, take one.
    stmt = (
        select(Fundamentals)
        .where(Fundamentals.ticker == ticker)
        .order_by(Fundamentals.fetched_date.desc())
        .limit(1)
    )

    with engine.connect() as conn:
        raw = pd.read_sql(stmt, conn)

    if raw.empty:
        print(f"⚠️  get_latest_fundamentals: no stored data for '{ticker}'.")
        return None

    # A single-row DataFrame → a plain dict (first/only row).
    return raw.iloc[0].to_dict()
