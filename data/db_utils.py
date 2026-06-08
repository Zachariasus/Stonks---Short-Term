"""
data/db_utils.py
================
Small database utility helpers that don't belong to the writer or reader.

HOW IT FITS IN
    The scheduler needs to know "what tickers do we already track?" without a
    hardcoded list. get_all_stored_tickers() answers that by asking the database
    directly, so the system automatically refreshes whatever it has stored.
"""

# Support both `python -m data.db_utils` and `python data/db_utils.py`.
try:
    from data.database import PriceBar, get_session
except ImportError:  # pragma: no cover
    from database import PriceBar, get_session  # type: ignore


def get_all_stored_tickers() -> list[str]:
    """Return a sorted, de-duplicated list of every ticker in price_bars.

    Returns:
        A list like ["AAPL", "IWM", "SPY", "XLK", ...]; empty list if the table
        has no rows yet.
    """
    session = get_session()
    try:
        # SELECT DISTINCT ticker FROM price_bars
        rows = session.query(PriceBar.ticker).distinct().all()
        # .all() returns a list of 1-tuples like ("AAPL",); unpack to plain strings.
        return sorted(row[0] for row in rows)
    finally:
        session.close()
