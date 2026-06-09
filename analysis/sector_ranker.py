"""
analysis/sector_ranker.py
=========================
Sector relative-strength ranker — first module of the Top-Down & Valuation engine.

WHAT THIS DOES
    Ranks the 11 GICS sectors by how strongly they're out/under-performing the
    market (SPY), revealing which sectors are LEADING and which are LAGGING — the
    "sector rotation" picture that dominates returns at the multi-month horizon.

HOW IT FITS IN
    Reuses get_rs_profile() (relative_strength.py) for each sector ETF and
    SECTOR_ETFS (universe_etfs.py). The resulting tilt becomes top-down context
    for the Phase 6 confidence scorer: a stock in a leading sector has a tailwind.
"""

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.relative_strength import get_rs_profile
    from data.database import TickerUniverse, get_session
    from data.universe_etfs import SECTOR_ETFS
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.relative_strength import get_rs_profile
    from data.database import TickerUniverse, get_session
    from data.universe_etfs import SECTOR_ETFS

# Simple in-process cache: rank_sectors() does 11 full RS profiles, so when
# several functions call it within one run (print + tilt + lookup) we compute it
# once and reuse. Pass use_cache=False to force a fresh recompute.
_ranked_cache = None


def _rotation_label(composite):
    """Leading / Neutral / Lagging from a composite RS (percentage points)."""
    if composite is None:
        return "Neutral"
    if composite >= 3.0:
        return "Leading"
    if composite <= -3.0:
        return "Lagging"
    return "Neutral"


def rank_sectors(use_cache: bool = True):
    """Rank all 11 sectors by composite RS vs SPY (strongest first).

    Returns a list of dicts (rank 1 = strongest):
        {rank, etf_ticker, sector_name, composite_vs_spy, rs_3m, rs_6m, rs_12m,
         rotation_label}
    """
    global _ranked_cache
    if use_cache and _ranked_cache is not None:
        return _ranked_cache

    rows = []
    for etf_ticker, sector_name in SECTOR_ETFS.items():
        # get_rs_profile benchmarks vs SPY by default. (Sector ETFs aren't in
        # TickerUniverse, so its sector-comparison falls back to SPY too — we
        # only use the vs-SPY numbers here, which is exactly what we want.)
        profile = get_rs_profile(etf_ticker)
        rows.append(
            {
                "etf_ticker": etf_ticker,
                "sector_name": sector_name,
                "composite_vs_spy": profile["composite_vs_spy"],
                "rs_3m": profile["rs_3m_vs_spy"],
                "rs_6m": profile["rs_6m_vs_spy"],
                "rs_12m": profile["rs_12m_vs_spy"],
            }
        )

    # Sort strongest-first by composite; any None composite sorts to the bottom.
    rows.sort(
        key=lambda r: (r["composite_vs_spy"] is None, -(r["composite_vs_spy"] or 0.0))
    )

    ranked = []
    for position, row in enumerate(rows, start=1):
        row["rank"] = position
        row["rotation_label"] = _rotation_label(row["composite_vs_spy"])
        # Reorder keys to the documented shape.
        ranked.append(
            {
                "rank": row["rank"],
                "etf_ticker": row["etf_ticker"],
                "sector_name": row["sector_name"],
                "composite_vs_spy": row["composite_vs_spy"],
                "rs_3m": row["rs_3m"],
                "rs_6m": row["rs_6m"],
                "rs_12m": row["rs_12m"],
                "rotation_label": row["rotation_label"],
            }
        )

    _ranked_cache = ranked
    return ranked


def get_sector_rank(ticker: str):
    """Return the sector-rank entry for a stock's sector ETF, or None.

    Looks up the stock's sector_etf in TickerUniverse, then finds that ETF's
    entry in rank_sectors(). None if the ticker isn't in our universe.
    """
    ticker = ticker.strip().upper()

    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker)
            .first()
        )
        sector_etf = row.sector_etf if row else None
    finally:
        session.close()

    if not sector_etf:
        print(f"note: '{ticker}' not in TickerUniverse — can't determine sector rank.")
        return None

    for entry in rank_sectors():
        if entry["etf_ticker"] == sector_etf:
            return entry
    return None


def _fmt(value):
    """Signed 1dp, or 'n/a'."""
    return f"{value:+.1f}" if value is not None else "n/a"


def _fmt_comp(value):
    """Signed 1dp with a 'pp' unit, or 'n/a'."""
    return f"{value:+.1f}pp" if value is not None else "n/a"


def print_sector_rankings():
    """Print a clean ranked sector table and a leading/neutral/lagging count."""
    ranked = rank_sectors()

    print("=== Sector Rotation Rankings (vs SPY) ===")
    print(
        f"{'Rank':<5}{'Sector':<24}{'ETF':<6}"
        f"{'3m':>8}{'6m':>8}{'12m':>8}{'Composite':>12}  {'Label'}"
    )
    print("─" * 80)
    for r in ranked:
        print(
            f"{r['rank']:<5}{r['sector_name']:<24}{r['etf_ticker']:<6}"
            f"{_fmt(r['rs_3m']):>8}{_fmt(r['rs_6m']):>8}{_fmt(r['rs_12m']):>8}"
            f"{_fmt_comp(r['composite_vs_spy']):>12}  {r['rotation_label']}"
        )

    leading = sum(1 for r in ranked if r["rotation_label"] == "Leading")
    neutral = sum(1 for r in ranked if r["rotation_label"] == "Neutral")
    lagging = sum(1 for r in ranked if r["rotation_label"] == "Lagging")
    print(
        f"\nLeading: {leading} sectors  |  "
        f"Neutral: {neutral} sectors  |  Lagging: {lagging} sectors"
    )

    return ranked


def get_top_down_tilt() -> dict:
    """Return a one-dict market-wide read of sector breadth (for the Phase 6 scorer)."""
    ranked = rank_sectors()

    top_3 = [f"{r['etf_ticker']} ({r['sector_name']})" for r in ranked[:3]]
    bottom_3 = [f"{r['etf_ticker']} ({r['sector_name']})" for r in ranked[-3:]]

    leading = sum(1 for r in ranked if r["rotation_label"] == "Leading")
    neutral = sum(1 for r in ranked if r["rotation_label"] == "Neutral")
    lagging = sum(1 for r in ranked if r["rotation_label"] == "Lagging")

    if leading >= 7:
        breadth_label = "Broad Advance"
    elif leading >= 4:
        breadth_label = "Mixed"
    else:
        breadth_label = "Narrow/Weak"

    return {
        "top_3_sectors": top_3,
        "bottom_3_sectors": bottom_3,
        "leading_count": leading,
        "neutral_count": neutral,
        "lagging_count": lagging,
        "breadth_label": breadth_label,
    }


if __name__ == "__main__":
    print_sector_rankings()

    print("\n=== Top-Down Tilt ===")
    tilt = get_top_down_tilt()
    print(f"Breadth: {tilt['breadth_label']}")
    print(
        f"Leading: {tilt['leading_count']}  |  "
        f"Neutral: {tilt['neutral_count']}  |  Lagging: {tilt['lagging_count']}"
    )
    print(f"Top 3:    {', '.join(tilt['top_3_sectors'])}")
    print(f"Bottom 3: {', '.join(tilt['bottom_3_sectors'])}")

    print()
    rank_info = get_sector_rank("AAPL")
    if rank_info is not None:
        print(
            f"AAPL is in {rank_info['etf_ticker']} ({rank_info['sector_name']}) — "
            f"ranked {rank_info['rank']} of 11 — {rank_info['rotation_label']}"
        )
    else:
        print("AAPL sector rank unavailable")
