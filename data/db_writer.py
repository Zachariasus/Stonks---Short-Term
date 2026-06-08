"""
data/db_writer.py
=================
Write helpers — take the data our fetchers return and persist it into stonks.db.

HOW IT FITS IN
    fetcher_price.py / fetcher_fundamentals.py  →  (this module)  →  stonks.db

    The fetchers grab fresh data from the internet; these functions store it so
    future runs can read from disk instead of re-downloading. Every function is
    duplicate-safe: re-saving the same data inserts only the genuinely new rows
    (thanks to the UNIQUE constraints defined in database.py).
"""

from datetime import date

import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# Support both `python -m data.db_writer` (package) and `python data/db_writer.py`.
try:
    from data.database import (
        EarningsHistory,
        Fundamentals,
        PriceBar,
        get_session,
    )
except ImportError:  # pragma: no cover
    from database import (  # type: ignore
        EarningsHistory,
        Fundamentals,
        PriceBar,
        get_session,
    )

# The fundamentals metric columns, in one place so save_fundamentals stays tidy.
_FUND_FIELDS = [
    "forward_pe", "trailing_pe", "ev_to_ebitda", "price_to_fcf",
    "forward_eps", "trailing_eps", "revenue_growth_yoy", "earnings_growth_yoy",
    "gross_margins", "operating_margins", "profit_margins", "return_on_equity",
    "current_ratio", "debt_to_equity",
]


# ---------------------------------------------------------------------------
# Small helpers: convert pandas/NaN values into clean Python types or None.
# ---------------------------------------------------------------------------
def _to_float(value):
    """Return a float, or None if the value is missing/NaN."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def _to_int(value):
    """Return an int, or None if the value is missing/NaN."""
    if value is None or pd.isna(value):
        return None
    return int(value)


def _to_date(value):
    """Normalize a pandas Timestamp / datetime into a plain datetime.date."""
    # pandas Timestamp and datetime both have .date(); a plain date does not.
    return value.date() if hasattr(value, "date") else value


def save_price_bars(ticker: str, df: "pd.DataFrame") -> dict:
    """Insert OHLCV rows (from get_ohlcv) into the price_bars table.

    Duplicate rows (same ticker + date) are skipped automatically using the
    table's UNIQUE constraint via SQLite's "INSERT ... ON CONFLICT DO NOTHING".

    Returns:
        {"inserted": <new rows>, "skipped": <already-present rows>}
    """
    ticker = ticker.strip().upper()

    if df is None or df.empty:
        print(f"⚠️  save_price_bars: nothing to save for '{ticker}'.")
        return {"inserted": 0, "skipped": 0}

    # Turn each DataFrame row into a plain dict matching the PriceBar columns.
    rows = [
        {
            "ticker": ticker,
            "date": _to_date(r["Date"]),
            "open": _to_float(r["Open"]),
            "high": _to_float(r["High"]),
            "low": _to_float(r["Low"]),
            "close": _to_float(r["Close"]),
            "volume": _to_int(r["Volume"]),
        }
        for _, r in df.iterrows()
    ]
    total = len(rows)

    session = get_session()
    try:
        # Count this ticker's rows before and after so we know exactly how many
        # were genuinely new (ON CONFLICT silently drops the duplicates).
        before = session.query(PriceBar).filter(PriceBar.ticker == ticker).count()

        stmt = sqlite_insert(PriceBar).on_conflict_do_nothing(
            index_elements=["ticker", "date"]
        )
        session.execute(stmt, rows)
        session.commit()

        after = session.query(PriceBar).filter(PriceBar.ticker == ticker).count()
        inserted = after - before
        skipped = total - inserted

        print(f"save_price_bars: {ticker} → {inserted} inserted, {skipped} skipped.")
        return {"inserted": inserted, "skipped": skipped}
    finally:
        session.close()


def save_fundamentals(ticker: str, fundamentals_dict: dict) -> dict:
    """Insert one fundamentals snapshot (from get_fundamentals) for today.

    If a snapshot for this ticker already exists for today, it is skipped (we
    only want one snapshot per ticker per day).

    Returns:
        {"inserted": 0 or 1, "skipped": 0 or 1}
    """
    ticker = ticker.strip().upper()

    if not fundamentals_dict:
        print(f"⚠️  save_fundamentals: empty data for '{ticker}', nothing saved.")
        return {"inserted": 0, "skipped": 0}

    today = date.today()
    session = get_session()
    try:
        # Already have a snapshot for this ticker today? Then skip.
        exists = (
            session.query(Fundamentals)
            .filter_by(ticker=ticker, fetched_date=today)
            .first()
        )
        if exists is not None:
            print(f"save_fundamentals: {ticker} already saved today — skipped.")
            return {"inserted": 0, "skipped": 1}

        # Map only the known metric fields (ignore any extras in the dict),
        # converting each to a clean float/None.
        metrics = {f: _to_float(fundamentals_dict.get(f)) for f in _FUND_FIELDS}
        row = Fundamentals(ticker=ticker, fetched_date=today, **metrics)
        session.add(row)
        session.commit()

        print(f"save_fundamentals: {ticker} snapshot saved for {today}.")
        return {"inserted": 1, "skipped": 0}
    finally:
        session.close()


def save_earnings_history(ticker: str, df: "pd.DataFrame") -> dict:
    """Insert earnings rows (from get_earnings_history) into earnings_history.

    Duplicate quarters (same ticker + report_date) are skipped via the table's
    UNIQUE constraint.

    Returns:
        {"inserted": <new rows>, "skipped": <already-present rows>}
    """
    ticker = ticker.strip().upper()

    if df is None or df.empty:
        print(f"⚠️  save_earnings_history: nothing to save for '{ticker}'.")
        return {"inserted": 0, "skipped": 0}

    rows = [
        {
            "ticker": ticker,
            "report_date": _to_date(r["Date"]),
            "eps_estimate": _to_float(r["EPS_Estimate"]),
            "eps_actual": _to_float(r["EPS_Actual"]),
            "surprise_pct": _to_float(r["Surprise_Pct"]),
        }
        for _, r in df.iterrows()
    ]
    total = len(rows)

    session = get_session()
    try:
        before = (
            session.query(EarningsHistory)
            .filter(EarningsHistory.ticker == ticker)
            .count()
        )

        stmt = sqlite_insert(EarningsHistory).on_conflict_do_nothing(
            index_elements=["ticker", "report_date"]
        )
        session.execute(stmt, rows)
        session.commit()

        after = (
            session.query(EarningsHistory)
            .filter(EarningsHistory.ticker == ticker)
            .count()
        )
        inserted = after - before
        skipped = total - inserted

        print(f"save_earnings_history: {ticker} → {inserted} inserted, {skipped} skipped.")
        return {"inserted": inserted, "skipped": skipped}
    finally:
        session.close()
