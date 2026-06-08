"""
data/universe_stocks.py
=======================
Build and maintain the stock universe — the list of stocks the system scans.

WHAT THIS DOES
    Pulls the CURRENT S&P 500 constituent list, maps each company to its sector
    ETF, and stores the result in the `ticker_universe` table. Stocks that have
    dropped out of the index are flagged inactive (not deleted), so the scan list
    always reflects today's index without losing history.

HOW IT FITS IN
    get_active_universe() is the single source of truth for "which stocks do we
    track?" — the scheduler (Step 5) and the Phase 6 screener both call it.
"""

from io import StringIO

import pandas as pd
import requests

# Support both `python -m data.universe_stocks` and `python data/universe_stocks.py`.
try:
    from data.database import TickerUniverse, get_session, init_db
    from data.universe_etfs import SECTOR_ETFS
except ImportError:  # pragma: no cover
    from database import TickerUniverse, get_session, init_db  # type: ignore
    from universe_etfs import SECTOR_ETFS  # type: ignore

# Wikipedia's S&P 500 page. It's the standard free source for the constituent
# list. Tradeoff: it's community-maintained and only "good enough" — it can lag
# official index changes by a day or two and isn't real-time. For a free system
# that scans on a daily cadence that's perfectly acceptable; if we ever needed
# point-in-time accuracy we'd pay for a proper index-membership data feed.
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_tickers() -> pd.DataFrame:
    """Fetch the current S&P 500 constituents from Wikipedia.

    Returns:
        DataFrame with columns [ticker, company_name, sector].
    """
    # We fetch with requests + a browser-like User-Agent first, then hand the
    # HTML to pandas. Wikipedia returns 403 to pandas' default urllib agent, so
    # going through requests avoids that. pd.read_html still does the parsing.
    response = requests.get(
        SP500_WIKI_URL,
        headers={"User-Agent": "Mozilla/5.0 (Stonks data fetcher)"},
        timeout=30,
    )
    response.raise_for_status()

    # The first table on the page is the constituents table. Its relevant
    # columns are "Symbol", "Security" (company name), and "GICS Sector".
    tables = pd.read_html(StringIO(response.text))
    raw = tables[0]

    df = pd.DataFrame(
        {
            "ticker": raw["Symbol"].astype(str).str.strip(),
            "company_name": raw["Security"].astype(str).str.strip(),
            "sector": raw["GICS Sector"].astype(str).str.strip(),
        }
    )

    # yfinance expects hyphens, not dots, in class-share tickers
    # (e.g. Wikipedia's "BRK.B" must become "BRK-B").
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)

    return df


def map_sector_to_etf(sector_name: str):
    """Map a Wikipedia GICS sector name to its sector-ETF ticker.

    The GICS names from Wikipedia don't always match the short names in
    SECTOR_ETFS (e.g. "Information Technology" vs "Technology"), so we map them
    explicitly here. Keeping the full mapping inside this function makes it easy
    to update in one place if a sector name ever changes.

    Args:
        sector_name: A GICS sector string, e.g. "Information Technology".

    Returns:
        The matching ETF ticker (e.g. "XLK"), or None if unmapped (with a warning).
    """
    # GICS sector name (as it appears on Wikipedia) -> sector ETF ticker.
    gics_to_etf = {
        "Information Technology": "XLK",
        "Financials": "XLF",
        "Energy": "XLE",
        "Health Care": "XLV",
        "Industrials": "XLI",
        "Consumer Discretionary": "XLY",
        "Consumer Staples": "XLP",
        "Utilities": "XLU",
        "Real Estate": "XLRE",
        "Materials": "XLB",
        "Communication Services": "XLC",
    }

    etf = gics_to_etf.get(sector_name)
    if etf is None:
        print(f"⚠️  map_sector_to_etf: no ETF mapping for sector '{sector_name}'.")
        return None

    # Sanity check: the ETF we mapped to should be one we actually track.
    if etf not in SECTOR_ETFS:
        print(f"⚠️  map_sector_to_etf: '{etf}' is not in SECTOR_ETFS.")
    return etf


def build_universe() -> dict:
    """Refresh the ticker_universe table from the live S&P 500 list.

    - Inserts tickers that are new.
    - Skips tickers already present.
    - Deactivates tickers that are in the table but no longer in the index.

    Returns:
        {"added": X, "existed": Y, "deactivated": Z}
    """
    init_db()  # make sure the table exists

    df = fetch_sp500_tickers()
    # Attach the sector ETF for each row.
    df["sector_etf"] = df["sector"].apply(map_sector_to_etf)

    fetched_tickers = set(df["ticker"])

    session = get_session()
    added = 0
    existed = 0
    deactivated = 0
    try:
        # Load everything already in the table once, keyed by ticker.
        existing = {row.ticker: row for row in session.query(TickerUniverse).all()}

        # Insert new tickers; skip ones we already have.
        for _, r in df.iterrows():
            if r["ticker"] in existing:
                existed += 1
                continue
            session.add(
                TickerUniverse(
                    ticker=r["ticker"],
                    company_name=r["company_name"],
                    sector=r["sector"],
                    sector_etf=r["sector_etf"],
                    index_membership="SP500",
                    active=True,
                )
            )
            added += 1

        # Anything in the table but no longer in the index → mark inactive.
        for ticker, row in existing.items():
            if ticker not in fetched_tickers and row.active:
                row.active = False
                deactivated += 1

        session.commit()
    finally:
        session.close()

    print(
        f"Universe build complete: {added} added, {existed} already existed, "
        f"{deactivated} deactivated."
    )
    return {"added": added, "existed": existed, "deactivated": deactivated}


def get_active_universe() -> list[str]:
    """Return a sorted list of all ACTIVE ticker strings (the stocks we track)."""
    session = get_session()
    try:
        rows = (
            session.query(TickerUniverse.ticker)
            .filter(TickerUniverse.active.is_(True))
            .all()
        )
        return sorted(row[0] for row in rows)
    finally:
        session.close()


if __name__ == "__main__":
    from collections import Counter

    # Build / refresh the universe from the live list.
    build_universe()

    # Total active count.
    active = get_active_universe()
    print(f"\nTotal active tickers: {len(active)}")

    # 5 example rows.
    session = get_session()
    try:
        print("\nExample rows (ticker | company | sector | sector_etf):")
        sample = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.active.is_(True))
            .limit(5)
            .all()
        )
        for row in sample:
            print(
                f"  {row.ticker:6s} | {row.company_name[:28]:28s} | "
                f"{row.sector:24s} | {row.sector_etf}"
            )

        # Count of tickers per sector ETF.
        all_active = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.active.is_(True))
            .all()
        )
        breakdown = Counter(row.sector_etf for row in all_active)
        print("\nTickers per sector ETF:")
        for etf, count in sorted(breakdown.items(), key=lambda kv: -kv[1]):
            sector = SECTOR_ETFS.get(etf, "?")
            print(f"  {str(etf):5s} ({sector}): {count}")
    finally:
        session.close()
