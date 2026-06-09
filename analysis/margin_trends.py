"""
analysis/margin_trends.py
=========================
Margin trend calculator — second module of the Fundamental Trajectory engine.

WHAT THIS DOES
    Pulls a company's quarterly income statement (via yfinance), computes gross /
    operating / net margins per quarter, stores them, and detects the DIRECTION
    of the margin cycle: expanding (operating leverage kicking in) or compressing.

HOW IT FITS IN
    Fundamentals signal #2 (after estimate revisions). Margin expansion is a
    powerful 4–6 month tailwind; compression is a short thesis. This module
    fetches fresh statement data (yfinance) and stores it through the DB layer.
"""

import pandas as pd
import yfinance as yf

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.database import MarginSnapshot, get_session, init_db
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.database import MarginSnapshot, get_session, init_db

# Income-statement line items vary in label across companies / yfinance versions,
# so we try several candidate names for each and take the first that exists.
REVENUE_NAMES = ["Total Revenue", "TotalRevenue", "Revenue", "Operating Revenue"]
OPERATING_INCOME_NAMES = [
    "Operating Income", "OperatingIncome", "Total Operating Income As Reported",
]
GROSS_PROFIT_NAMES = ["Gross Profit", "GrossProfit"]
NET_INCOME_NAMES = [
    "Net Income", "NetIncome", "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
    "Net Income Continuous Operations",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _safe_float(value):
    """Parse to float; return None for missing/NaN/unparseable values."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_value(df, candidate_names, column):
    """Return a line-item value for a given quarter column, trying each candidate name."""
    for name in candidate_names:
        if name in df.index:
            return _safe_float(df.loc[name, column])
    return None


def _safe_margin(numerator, revenue):
    """numerator / revenue, guarding against missing values and divide-by-zero."""
    if numerator is None or revenue in (None, 0):
        return None
    return numerator / revenue


# ---------------------------------------------------------------------------
# Fetch + store
# ---------------------------------------------------------------------------
def fetch_and_store_margins(ticker: str) -> pd.DataFrame:
    """Fetch quarterly income-statement margins from yfinance and store them.

    Returns:
        A DataFrame (oldest→newest) of what was fetched
        [report_date, revenue, operating_income, gross_margin, operating_margin,
        net_margin]. Empty DataFrame if nothing came back.
    """
    ticker = ticker.strip().upper()
    init_db()

    stock = yf.Ticker(ticker)
    try:
        # Columns are quarter-end dates; rows are income-statement line items.
        fin = stock.quarterly_financials
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  fetch_and_store_margins: yfinance error for {ticker}: {err}")
        return pd.DataFrame()

    if fin is None or fin.empty:
        print(f"⚠️  fetch_and_store_margins: no quarterly financials for {ticker}.")
        return pd.DataFrame()

    records = []
    for column in fin.columns:
        revenue = _row_value(fin, REVENUE_NAMES, column)
        operating_income = _row_value(fin, OPERATING_INCOME_NAMES, column)
        gross_profit = _row_value(fin, GROSS_PROFIT_NAMES, column)
        net_income = _row_value(fin, NET_INCOME_NAMES, column)

        records.append(
            {
                "report_date": pd.Timestamp(column).date(),
                "revenue": revenue,
                "operating_income": operating_income,
                "gross_margin": _safe_margin(gross_profit, revenue),
                "operating_margin": _safe_margin(operating_income, revenue),
                "net_margin": _safe_margin(net_income, revenue),
            }
        )

    # Persist each quarter (skip duplicates via the unique constraint check).
    session = get_session()
    try:
        for rec in records:
            exists = (
                session.query(MarginSnapshot)
                .filter_by(
                    ticker=ticker,
                    report_date=rec["report_date"],
                    period_type="quarterly",
                )
                .first()
            )
            if exists is not None:
                continue
            session.add(
                MarginSnapshot(
                    ticker=ticker,
                    report_date=rec["report_date"],
                    period_type="quarterly",
                    gross_margin=rec["gross_margin"],
                    operating_margin=rec["operating_margin"],
                    net_margin=rec["net_margin"],
                    revenue=rec["revenue"],
                    operating_income=rec["operating_income"],
                )
            )
        session.commit()
    finally:
        session.close()

    out = pd.DataFrame(records).sort_values("report_date").reset_index(drop=True)
    return out[
        ["report_date", "revenue", "operating_income",
         "gross_margin", "operating_margin", "net_margin"]
    ]


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------
def _margin_metrics(series_oldest_to_newest):
    """Direction/sequential metrics for one margin series (fractions, oldest→newest)."""
    vals = [v for v in series_oldest_to_newest if v is not None]

    if len(vals) < 2:
        latest_pct = round(vals[-1] * 100, 1) if vals else None
        return {"latest": latest_pct, "earliest": latest_pct, "change_pp": None,
                "direction": "Unknown", "sequential": "Mixed"}

    latest, earliest = vals[-1], vals[0]
    # Use percentage POINTS (difference), not a % change of a baseline — avoids
    # the divide-by-baseline instability we hit earlier with RS and OBV.
    change_pp = round((latest - earliest) * 100, 1)

    if change_pp > 0.5:
        direction = "Expanding"
    elif change_pp < -0.5:
        direction = "Compressing"
    else:
        direction = "Stable"

    # Sequential read over the last 3 (non-null) quarters.
    last3 = vals[-3:]
    if len(last3) == 3 and last3[0] < last3[1] < last3[2]:
        sequential = "Consistently Expanding"
    elif len(last3) == 3 and last3[0] > last3[1] > last3[2]:
        sequential = "Consistently Compressing"
    else:
        sequential = "Mixed"

    return {"latest": round(latest * 100, 1), "earliest": round(earliest * 100, 1),
            "change_pp": change_pp, "direction": direction, "sequential": sequential}


def analyze_margin_trend(ticker: str, num_quarters: int = 6):
    """Analyze the gross & operating margin trend from stored quarters.

    Returns a dict of latest values / change (pp) / direction / sequential trend
    for both margins, or None if fewer than 3 quarters are stored.
    """
    ticker = ticker.strip().upper()

    session = get_session()
    try:
        rows = (
            session.query(MarginSnapshot)
            .filter(
                MarginSnapshot.ticker == ticker,
                MarginSnapshot.period_type == "quarterly",
            )
            .order_by(MarginSnapshot.report_date.desc())
            .limit(num_quarters)
            .all()
        )
        # Pull the values out before the session closes.
        gross = [r.gross_margin for r in rows]
        operating = [r.operating_margin for r in rows]
    finally:
        session.close()

    if len(rows) < 3:
        print(
            f"⚠️  analyze_margin_trend: only {len(rows)} quarters stored for "
            f"{ticker} (need ≥3)."
        )
        return None

    # Query was newest-first; reverse to oldest→newest for trend math.
    gross.reverse()
    operating.reverse()

    gm = _margin_metrics(gross)
    om = _margin_metrics(operating)

    return {
        "gross_margin_latest": gm["latest"],
        "gross_margin_earliest": gm["earliest"],
        "gross_margin_change_pp": gm["change_pp"],
        "gross_margin_direction": gm["direction"],
        "gross_margin_sequential": gm["sequential"],
        "operating_margin_latest": om["latest"],
        "operating_margin_earliest": om["earliest"],
        "operating_margin_change_pp": om["change_pp"],
        "operating_margin_direction": om["direction"],
        "operating_margin_sequential": om["sequential"],
        "num_quarters_analyzed": len(rows),
        "ticker": ticker,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def _format_margin_line(label, earliest, latest, change_pp, direction, sequential):
    """Format one 'earliest → latest (direction, ±pp) Sequential: ...' line."""
    if latest is None:
        return f"{label:<18}n/a"
    if change_pp is None or earliest is None:
        return f"{label:<18}{latest:.1f}%  (direction unknown)   Sequential: {sequential}"
    return (
        f"{label:<18}{earliest:.1f}%  →  {latest:.1f}%  "
        f"({direction}, {change_pp:+.1f}pp)   Sequential: {sequential}"
    )


def get_margin_summary(ticker: str):
    """Fetch+store margins, analyze the trend, print a clean block, return the dict."""
    ticker = ticker.strip().upper()
    fetch_and_store_margins(ticker)
    analysis = analyze_margin_trend(ticker)

    if analysis is None:
        print(f"{ticker} | Margin Trends — insufficient data")
        return None

    print(f"{ticker} | Margin Trends (last {analysis['num_quarters_analyzed']} quarters)")
    print(
        _format_margin_line(
            "Gross margin:",
            analysis["gross_margin_earliest"],
            analysis["gross_margin_latest"],
            analysis["gross_margin_change_pp"],
            analysis["gross_margin_direction"],
            analysis["gross_margin_sequential"],
        )
    )
    print(
        _format_margin_line(
            "Operating margin:",
            analysis["operating_margin_earliest"],
            analysis["operating_margin_latest"],
            analysis["operating_margin_change_pp"],
            analysis["operating_margin_direction"],
            analysis["operating_margin_sequential"],
        )
    )

    return analysis


if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    # Show AAPL's raw quarterly margin table first, then the summaries.
    print("=== AAPL raw quarterly margins (oldest → newest) ===")
    aapl_table = fetch_and_store_margins("AAPL")
    if not aapl_table.empty:
        print(aapl_table.to_string(index=False))
    print()

    get_margin_summary("AAPL")
    print()
    get_margin_summary("MSFT")
