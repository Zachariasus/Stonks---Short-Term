"""
analysis/earnings_calendar.py
=============================
Earnings calendar integration — final module of the Fundamental Trajectory engine.

WHAT THIS DOES
    Tracks WHEN each stock next reports earnings, so the system can flag
    positions approaching a report and warn when an entry signal fires within
    days of one. Stores the next date (+ consensus estimates) in the DB.

HOW IT FITS IN
    Fetches the date via data/fetcher_fundamentals.get_earnings_calendar (which
    reads yfinance .calendar), persists it, and exposes simple "days to earnings"
    and risk-flag helpers that entry_signals.py can call.
"""

from datetime import date

import pandas as pd

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.database import EarningsCalendar, get_session, init_db
    from data.fetcher_fundamentals import get_earnings_calendar
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.database import EarningsCalendar, get_session, init_db
    from data.fetcher_fundamentals import get_earnings_calendar


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clean_num(value):
    """Return a float, or None for missing/NaN values."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _to_date(value):
    """Normalize a date / datetime / Timestamp / string into a datetime.date."""
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date()
    except Exception:  # noqa: BLE001
        return None


def _flag_from_days(days):
    """Map days-to-earnings into a risk label."""
    if days is None:
        return "Date Unknown"
    if days < 0:
        return "Date Stale"      # the stored date is in the past → needs refresh
    if days <= 7:
        return "Imminent"        # report this week — size accordingly
    if days <= 21:
        return "Near-Term"       # within 3 weeks — be aware
    if days <= 45:
        return "Upcoming"        # within the typical hold window
    return "Not Imminent"        # no near-term earnings risk


# ---------------------------------------------------------------------------
# Fetch + store
# ---------------------------------------------------------------------------
def fetch_and_store_earnings_date(ticker: str):
    """Fetch the next earnings date for a ticker and store it.

    Returns:
        The stored dict, or None if no upcoming date is available (e.g. ETFs,
        which don't report earnings).
    """
    ticker = ticker.strip().upper()
    init_db()

    calendar = get_earnings_calendar(ticker)
    if not calendar or calendar.get("next_earnings_date") is None:
        # No date available — common for ETFs/benchmarks. Handled gracefully.
        return None

    next_date = _to_date(calendar["next_earnings_date"])
    if next_date is None:
        return None

    eps_avg = _clean_num(calendar.get("eps_estimate_avg"))
    eps_high = _clean_num(calendar.get("eps_estimate_high"))
    eps_low = _clean_num(calendar.get("eps_estimate_low"))
    rev_avg = _clean_num(calendar.get("revenue_estimate_avg"))
    today = date.today()

    session = get_session()
    try:
        existing = (
            session.query(EarningsCalendar)
            .filter_by(ticker=ticker, next_earnings_date=next_date)
            .first()
        )
        if existing is not None:
            # Already have this exact date — return it (no duplicate insert).
            return {
                "ticker": ticker,
                "next_earnings_date": next_date,
                "eps_estimate_avg": existing.eps_estimate_avg,
                "eps_estimate_high": existing.eps_estimate_high,
                "eps_estimate_low": existing.eps_estimate_low,
                "revenue_estimate_avg": existing.revenue_estimate_avg,
                "last_updated": existing.last_updated,
            }

        row = EarningsCalendar(
            ticker=ticker,
            next_earnings_date=next_date,
            eps_estimate_avg=eps_avg,
            eps_estimate_high=eps_high,
            eps_estimate_low=eps_low,
            revenue_estimate_avg=rev_avg,
            last_updated=today,
        )
        session.add(row)
        session.commit()
        return {
            "ticker": ticker,
            "next_earnings_date": next_date,
            "eps_estimate_avg": eps_avg,
            "eps_estimate_high": eps_high,
            "eps_estimate_low": eps_low,
            "revenue_estimate_avg": rev_avg,
            "last_updated": today,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Reads / flags
# ---------------------------------------------------------------------------
def get_days_to_earnings(ticker: str):
    """Calendar days from today until the ticker's next stored earnings date.

    Returns:
        int days (negative if the stored date has already passed — stale), or
        None if no date is stored.
    """
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        row = (
            session.query(EarningsCalendar)
            .filter(EarningsCalendar.ticker == ticker)
            .order_by(EarningsCalendar.next_earnings_date.desc())  # most forward date
            .first()
        )
        next_date = row.next_earnings_date if row else None
    finally:
        session.close()

    if next_date is None:
        return None
    return (next_date - date.today()).days


def get_earnings_flag(ticker: str) -> str:
    """Risk label for how close the next earnings report is."""
    return _flag_from_days(get_days_to_earnings(ticker))


def check_entry_near_earnings(ticker: str, entry_signal_dict: dict) -> dict:
    """Add an earnings warning to an entry-signal dict if a report is imminent.

    The hook entry_signals.py will eventually call: if any entry signal fired AND
    the next report is within a week, attach a warning so the trader confirms
    they intend to hold through the (volatile) report before entering.
    """
    days = get_days_to_earnings(ticker)
    if entry_signal_dict.get("any_signal_triggered") and days is not None and 0 <= days <= 7:
        entry_signal_dict["earnings_warning"] = (
            f"⚠️ Earnings in {days} days — confirm you intend to hold through "
            "the report before entering"
        )
    return entry_signal_dict


def get_earnings_summary(ticker: str) -> dict:
    """Refresh + print the next-earnings line for a ticker; return a small dict."""
    ticker = ticker.strip().upper()
    fetch_and_store_earnings_date(ticker)

    # Read the (most forward) stored date once and derive days + flag from it.
    session = get_session()
    try:
        row = (
            session.query(EarningsCalendar)
            .filter(EarningsCalendar.ticker == ticker)
            .order_by(EarningsCalendar.next_earnings_date.desc())
            .first()
        )
        next_date = row.next_earnings_date if row else None
    finally:
        session.close()

    days = (next_date - date.today()).days if next_date is not None else None
    flag = _flag_from_days(days)

    if next_date is not None:
        print(f"{ticker} | Next earnings: {next_date} ({days} days)  →  {flag}")
    else:
        print(f"{ticker} | Next earnings: none on file  →  {flag}")

    return {
        "ticker": ticker,
        "next_earnings_date": next_date,
        "days_to_earnings": days,
        "flag": flag,
    }


if __name__ == "__main__":
    # AAPL & MSFT report earnings; the 11 sector ETFs do not (should return None
    # / "Date Unknown" gracefully).
    try:
        from data.universe_etfs import SECTOR_ETFS
    except ImportError:  # pragma: no cover
        from universe_etfs import SECTOR_ETFS  # type: ignore

    tickers = ["AAPL", "MSFT"] + list(SECTOR_ETFS.keys())

    have_date = 0
    no_date = 0
    for ticker in tickers:
        result = get_earnings_summary(ticker)
        if result["next_earnings_date"] is not None:
            have_date += 1
        else:
            no_date += 1

    print(f"\nWith a date: {have_date}  |  No date (None): {no_date}")
