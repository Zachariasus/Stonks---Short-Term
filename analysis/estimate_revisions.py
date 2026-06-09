"""
analysis/estimate_revisions.py
==============================
Estimate revision tracker — first module of the Fundamental Trajectory engine.

WHAT THIS DOES
    Tracks whether analysts are RAISING or CUTTING a stock's forward earnings
    estimates over time — the single most powerful fundamental signal at the
    4–6 month horizon. It uses two complementary sources:
      - our own stored fundamentals snapshots (forward EPS over time) to detect
        the *direction* of revisions for stocks we're watching, and
      - Alpha Vantage's free EARNINGS endpoint for historical quarterly
        estimate-vs-actual (beat/miss) history.

HOW IT FITS IN
    Reads through the existing DB layer (db_reader / database). The only outside
    call is Alpha Vantage, used on demand for a single ticker (never in bulk).
"""

import pandas as pd
import requests

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.config import ALPHA_VANTAGE_API_KEY
    from data.database import EstimateSnapshot, get_session, init_db
    from data.db_reader import get_latest_fundamentals
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import ALPHA_VANTAGE_API_KEY
    from data.database import EstimateSnapshot, get_session, init_db
    from data.db_reader import get_latest_fundamentals


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clean_num(value):
    """Return a float, or None for missing/NaN values (handles pandas/numpy)."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _safe_float(value):
    """Parse a value to float; return None if it can't be parsed (AV sends 'None')."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _fiscal_quarter(date_str):
    """Turn a fiscalDateEnding like '2026-03-31' into a label like '2026-Q1'."""
    try:
        d = pd.to_datetime(date_str)
        quarter = (d.month - 1) // 3 + 1
        return f"{d.year}-Q{quarter}"
    except Exception:  # noqa: BLE001
        return str(date_str)


# ---------------------------------------------------------------------------
# A) Snapshot today's forward estimates (from our own stored fundamentals)
# ---------------------------------------------------------------------------
def save_estimate_snapshot(ticker: str):
    """Snapshot today's forward EPS / forward P/E for a ticker into EstimateSnapshot.

    Reads from our ALREADY-STORED fundamentals (get_latest_fundamentals) — we do
    NOT re-hit yfinance here. We snapshot regularly because a single reading is
    just a point; only by capturing forward EPS repeatedly over weeks and months
    can we later measure the DIRECTION of analyst revisions for a stock we hold
    or watch. Idempotent: one snapshot per ticker per day per source.

    Returns:
        The snapshot as a dict, or None if no stored fundamentals exist yet.
    """
    ticker = ticker.strip().upper()
    init_db()

    fundamentals = get_latest_fundamentals(ticker)
    if fundamentals is None:
        print(
            f"⚠️  save_estimate_snapshot: no stored fundamentals for '{ticker}' "
            "— run the data refresh first."
        )
        return None

    forward_eps = _clean_num(fundamentals.get("forward_eps"))
    forward_pe = _clean_num(fundamentals.get("forward_pe"))
    today = pd.Timestamp.today().date()
    source = "yfinance"

    session = get_session()
    try:
        existing = (
            session.query(EstimateSnapshot)
            .filter_by(ticker=ticker, snapshot_date=today, source=source)
            .first()
        )
        if existing is not None:
            print(f"save_estimate_snapshot: {ticker} already snapshotted today ({today}).")
            return {
                "ticker": ticker,
                "snapshot_date": today,
                "forward_eps": existing.forward_eps,
                "forward_pe": existing.forward_pe,
                "revenue_estimate": existing.revenue_estimate,
                "source": source,
            }

        row = EstimateSnapshot(
            ticker=ticker,
            snapshot_date=today,
            forward_eps=forward_eps,
            forward_pe=forward_pe,
            revenue_estimate=None,  # not available from our fundamentals table
            source=source,
        )
        session.add(row)
        session.commit()
        print(
            f"save_estimate_snapshot: saved {ticker} snapshot for {today} "
            f"(forward EPS = {forward_eps})."
        )
        return {
            "ticker": ticker,
            "snapshot_date": today,
            "forward_eps": forward_eps,
            "forward_pe": forward_pe,
            "revenue_estimate": None,
            "source": source,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# B) Historical quarterly earnings from Alpha Vantage
# ---------------------------------------------------------------------------
def get_alpha_vantage_earnings(ticker: str):
    """Fetch up to 8 recent quarters of EPS estimate-vs-actual from Alpha Vantage.

    RATE LIMIT: the Alpha Vantage free tier allows only ~25 requests/day, so this
    must be called ON DEMAND for a single ticker — never inside a bulk loop over
    the whole universe.

    Returns:
        DataFrame [report_date, fiscal_quarter, eps_estimate, eps_actual,
        surprise_pct] sorted newest-first, or None (with a clear warning) if the
        API key is missing or the API returns an error.
    """
    ticker = ticker.strip().upper()
    key = ALPHA_VANTAGE_API_KEY

    # Graceful handling when the key isn't configured yet.
    if not key or key == "your_key_here":
        print(
            "⚠️  get_alpha_vantage_earnings: ALPHA_VANTAGE_API_KEY is not set in "
            ".env — skipping historical earnings (add your free key to enable)."
        )
        return None

    url = (
        "https://www.alphavantage.co/query"
        f"?function=EARNINGS&symbol={ticker}&apikey={key}"
    )
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  get_alpha_vantage_earnings: request failed for {ticker}: {err}")
        return None

    # Alpha Vantage reports problems via these keys instead of returning data.
    for err_key in ("Error Message", "Note", "Information"):
        if err_key in data:
            print(f"⚠️  get_alpha_vantage_earnings: Alpha Vantage says: {data[err_key]}")
            return None

    quarterly = data.get("quarterlyEarnings")
    if not quarterly:
        print(f"⚠️  get_alpha_vantage_earnings: no quarterlyEarnings returned for {ticker}.")
        return None

    rows = [
        {
            "report_date": q.get("fiscalDateEnding"),
            "fiscal_quarter": _fiscal_quarter(q.get("fiscalDateEnding")),
            "eps_estimate": _safe_float(q.get("estimatedEPS")),
            "eps_actual": _safe_float(q.get("reportedEPS")),
            "surprise_pct": _safe_float(q.get("surprisePercentage")),
        }
        for q in quarterly
    ]

    df = pd.DataFrame(rows)
    # Sort newest-first by the (parsed) report date.
    df["_sort"] = pd.to_datetime(df["report_date"], errors="coerce")
    df = df.sort_values("_sort", ascending=False).drop(columns="_sort").reset_index(drop=True)

    return df.head(8)


# ---------------------------------------------------------------------------
# C) Forward-EPS revision trend from our own snapshots
# ---------------------------------------------------------------------------
def analyze_revision_trend(ticker: str) -> dict:
    """Measure the direction of forward-EPS revisions from our stored snapshots.

    Returns a dict with revision_direction / revision_pct_change / num_snapshots /
    days_covered / latest_forward_eps / earliest_forward_eps. With fewer than 2
    snapshots it returns an "Insufficient history" status (expected early on —
    the snapshots accumulate as the system runs over weeks).
    """
    ticker = ticker.strip().upper()

    session = get_session()
    try:
        snapshots = (
            session.query(EstimateSnapshot)
            .filter(EstimateSnapshot.ticker == ticker)
            .order_by(EstimateSnapshot.snapshot_date.asc())
            .all()
        )
        # Keep only snapshots that actually carry a forward EPS value.
        valid = [
            (s.snapshot_date, s.forward_eps)
            for s in snapshots
            if s.forward_eps is not None
        ]
    finally:
        session.close()

    num = len(valid)
    latest_eps = valid[-1][1] if num >= 1 else None

    if num < 2:
        return {
            "revision_direction": "Insufficient history",
            "revision_pct_change": None,
            "num_snapshots": num,
            "days_covered": 0,
            "latest_forward_eps": latest_eps,
            "earliest_forward_eps": None,
            "note": "needs to run for a few weeks to build snapshot history",
        }

    earliest_date, earliest_eps = valid[0]
    latest_date, latest_eps = valid[-1]

    if earliest_eps == 0:
        pct_change = 0.0
    else:
        pct_change = round((latest_eps - earliest_eps) / abs(earliest_eps) * 100, 2)

    if pct_change > 1:
        direction = "Rising"
    elif pct_change < -1:
        direction = "Falling"
    else:
        direction = "Flat"

    return {
        "revision_direction": direction,
        "revision_pct_change": pct_change,
        "num_snapshots": num,
        "days_covered": (latest_date - earliest_date).days,
        "latest_forward_eps": latest_eps,
        "earliest_forward_eps": earliest_eps,
    }


# ---------------------------------------------------------------------------
# D) Combined human-readable summary
# ---------------------------------------------------------------------------
def get_revision_summary(ticker: str):
    """Print a clean revision summary (DB trend + AV history); return both results."""
    ticker = ticker.strip().upper()
    trend = analyze_revision_trend(ticker)
    earnings = get_alpha_vantage_earnings(ticker)

    print(f"{ticker} | Estimate Revisions")

    # --- DB-snapshot revision trend ---
    if trend["revision_direction"] == "Insufficient history":
        print(
            "Forward EPS trend (DB snapshots): Insufficient history — "
            f"{trend.get('note', '')} (have {trend['num_snapshots']})"
        )
    else:
        print(
            "Forward EPS trend (DB snapshots): "
            f"{trend['revision_direction']} {trend['revision_pct_change']:+.1f}% "
            f"over {trend['days_covered']} days ({trend['num_snapshots']} snapshots)"
        )
    if trend.get("latest_forward_eps") is not None:
        print(f"Latest forward EPS: ${trend['latest_forward_eps']:.2f}")

    # --- Alpha Vantage historical beat/miss ---
    print("\nAlpha Vantage — Last 4 quarters (EPS estimate vs actual):")
    if earnings is None or earnings.empty:
        print("(no Alpha Vantage data — API key not set or request failed)")
    else:
        for _, row in earnings.head(4).iterrows():
            est, act, sp = row["eps_estimate"], row["eps_actual"], row["surprise_pct"]
            est_str = f"${est:.2f}" if pd.notna(est) else "n/a"
            act_str = f"${act:.2f}" if pd.notna(act) else "n/a"
            if pd.notna(sp):
                beat_miss = "beat" if sp >= 0 else "miss"
                sp_str = f"{sp:+.1f}% {beat_miss}"
            else:
                sp_str = "n/a"
            print(f"{row['fiscal_quarter']}: est {est_str} → actual {act_str}  ({sp_str})")

    return {"trend": trend, "earnings": earnings}


if __name__ == "__main__":
    print("=== save_estimate_snapshot('AAPL') ===")
    snap = save_estimate_snapshot("AAPL")
    print(f"Saved: {snap}\n")

    # On the FIRST run the DB trend will read "Insufficient history" (only one
    # snapshot so far) — that's expected and correct.
    get_revision_summary("AAPL")
