"""
screener/screener.py
=====================
Universe screener — runs the confluence scorer across the whole S&P 500.

WHAT THIS DOES
    Rank-orders the KNOWN universe by how well each stock fits our framework
    right now. It isn't hunting hidden gems in real time — it scores every stock
    we already track and sorts them, so the best long and short setups float to
    the top of a watchlist.

SPEED
    The expensive market-wide pieces (sector rankings, macro phase) are computed
    ONCE up front and passed into every score_stock() call, instead of being
    recomputed per ticker. Reads only from the DB — no network calls. Even so,
    scoring hundreds of names takes minutes (each does many DB reads), so we print
    progress every 50 tickers and the total elapsed time.
"""

from datetime import datetime

import pandas as pd
from sqlalchemy import func

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.confluence_scorer import score_stock
    from analysis.macro_cycle import get_cycle_summary
    from analysis.market_regime import get_market_regime
    from analysis.sector_ranker import rank_sectors
    from data.database import PriceBar, get_session
    from data.universe_stocks import get_active_universe
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.confluence_scorer import score_stock
    from analysis.macro_cycle import get_cycle_summary
    from analysis.market_regime import get_market_regime
    from analysis.sector_ranker import rank_sectors
    from data.database import PriceBar, get_session
    from data.universe_stocks import get_active_universe

# Minimum stored daily bars before a ticker is worth scoring (need history for
# the 200-day MA etc.).
MIN_BARS = 200


def build_market_context() -> dict:
    """Compute the market-wide context ONCE (sector rankings + macro phase + regime)."""
    sector_rankings = rank_sectors()
    cycle_summary = get_cycle_summary()
    regime = get_market_regime()
    print(
        "Market context loaded — sector rankings, macro phase, and "
        f"market regime ({regime.get('regime', 'Unknown')}) cached."
    )
    return {
        "sector_rankings": sector_rankings,
        "cycle_summary": cycle_summary,
        "regime": regime,
    }


def get_scoreable_tickers() -> list:
    """Active-universe tickers that actually have enough stored price history."""
    universe = get_active_universe()

    session = get_session()
    try:
        # One grouped query: ticker -> row count in price_bars.
        counts = dict(
            session.query(PriceBar.ticker, func.count(PriceBar.id))
            .group_by(PriceBar.ticker)
            .all()
        )
    finally:
        session.close()

    scoreable = [t for t in universe if counts.get(t, 0) >= MIN_BARS]
    skipped = len(universe) - len(scoreable)
    print(
        f"Scoreable tickers: {len(scoreable)} of {len(universe)} universe "
        f"({skipped} skipped — insufficient price data)"
    )
    return scoreable


def _elapsed(since) -> str:
    """H:MM:SS elapsed string."""
    return str(datetime.now() - since).split(".")[0]


def run_screener(tickers=None, min_score: int = 0, direction: str = "both"):
    """Score a set of tickers and return a ranked DataFrame.

    Args:
        tickers:   list of tickers, or None → use get_scoreable_tickers().
        min_score: drop rows below this total_score (0 = keep all).
        direction: "long" | "short" | "both" — filter by the direction field.
    """
    if tickers is None:
        tickers = get_scoreable_tickers()

    market_context = build_market_context()  # once, before the loop

    start = datetime.now()
    total = len(tickers)
    results = []

    for idx, ticker in enumerate(tickers, start=1):
        try:
            results.append(score_stock(ticker, market_context))
        except Exception as err:  # noqa: BLE001 - skip & log a bad ticker, keep going
            print(f"  ⚠️  {ticker} skipped — scoring error: {err}")

        if idx % 50 == 0:
            print(f"  [{idx}/{total}] scoring... (elapsed: {_elapsed(start)})")

    total_elapsed = _elapsed(start)

    if not results:
        print(f"\nScreener complete — 0 tickers scored in {total_elapsed}")
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("total_score", ascending=False).reset_index(drop=True)

    # Completion summary (counts over the full scored set, before filters).
    longs = df[(df["direction"] == "Long") & (df["total_score"] >= 60)]
    shorts = df[(df["direction"] == "Short") & (df["total_score"] >= 50)]
    print(f"\nScreener complete — {len(df)} tickers scored in {total_elapsed}")
    print(f"Long candidates (score >= 60):  {len(longs)}")
    print(f"Short candidates (score >= 50): {len(shorts)}")

    # Apply requested filters.
    if min_score > 0:
        df = df[df["total_score"] >= min_score]
    if direction == "long":
        df = df[df["direction"] == "Long"]
    elif direction == "short":
        df = df[df["direction"] == "Short"]

    return df.reset_index(drop=True)


def _stage_str(value):
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"S{int(value)}"
    except (TypeError, ValueError):
        return "—"


def _sector_str(value):
    try:
        if value is None or pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        return "—"
    return str(value)


def _print_row(rank, row):
    print(
        f"{rank:<5}{row['ticker']:<8}{int(row['total_score']):<7}"
        f"{row['confidence_label']:<9}{_stage_str(row['stage']):<7}"
        f"{str(row['rs_label']):<16}{_sector_str(row.get('sector_etf'))}"
    )


def print_screener_results(df, top_n: int = 20):
    """Print a ranked table of the top long candidates, then the top short candidates."""
    if df is None or df.empty:
        print("No results to display.")
        return

    header = (
        f"{'Rank':<5}{'Ticker':<8}{'Score':<7}{'Conf':<9}"
        f"{'Stage':<7}{'RS Label':<16}{'Sector'}"
    )

    longs = df[df["direction"] == "Long"].sort_values("total_score", ascending=False).head(top_n)
    print(f"\n=== Top {len(longs)} Long Candidates ===")
    print(header)
    print("─" * 64)
    for rank, (_, row) in enumerate(longs.iterrows(), start=1):
        _print_row(rank, row)

    shorts = df[df["direction"] == "Short"].sort_values("total_score", ascending=False).head(5)
    if not shorts.empty:
        print(f"\n=== Top {len(shorts)} Short Candidates ===")
        print(header)
        print("─" * 64)
        for rank, (_, row) in enumerate(shorts.iterrows(), start=1):
            _print_row(rank, row)


if __name__ == "__main__":
    # FOCUSED TEST — do NOT run the full 500-ticker screener here (slow, and most
    # tickers have no stored price data yet). This 15-ticker set all has data and
    # proves the loop, context caching, progress, and output table all work.
    test_tickers = [
        "AAPL", "MSFT", "XLK", "XLE", "XLF", "XLV", "XLI", "XLY",
        "XLP", "XLU", "XLRE", "XLB", "XLC", "SPY", "QQQ",
    ]
    result_df = run_screener(tickers=test_tickers)
    print_screener_results(result_df)
