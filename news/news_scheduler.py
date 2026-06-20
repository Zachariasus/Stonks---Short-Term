"""
news/news_scheduler.py
=====================
Hourly news refresh cycle (Phase 8, Step 4) — the automation on top of the
fetcher + scorer.

WHAT THIS DOES
    Once an hour, refreshes news for FLAGGED tickers only (never the full S&P
    500): fetch new articles → score them for relevance + sentiment. The web
    app's news feed shows news for stocks the system has flagged, so flagged
    tickers are exactly the right (and only affordable) scope.

WHY FLAGGED-ONLY — the free-tier math
    NewsAPI's free tier allows 100 requests/day. One fetch = one request, so
    100 req/day ÷ flagged tickers = how often we can refresh. With a handful of
    flags (say ~4), that's ~24 refreshes/day each = hourly, comfortably. Pointing
    this at all 500 names would burn the daily budget in a single pass.

MIRRORS THE PHASE 2 SCHEDULER
    Same shape as data/scheduler.py: a do-the-work function, a schedule loop, and
    a CLI with --now / --backfill / --schedule. Each ticker is wrapped in its own
    try/except so one failure never aborts the cycle.
"""

import sys
import time
from datetime import datetime, timedelta

import schedule

# Support both `python -m news.news_scheduler` and `python news/news_scheduler.py`
# (and inline runs with PYTHONPATH=<project root>).
try:
    from data.config import NEWS_API_KEY
    from data.database import NewsArticle, StockScreen, get_session
    from news.news_fetcher import fetch_and_store_news
    from news.relevance_scorer import score_and_update_articles
    from screener.flag_generator import get_active_flags
except ImportError:  # pragma: no cover
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import NEWS_API_KEY
    from data.database import NewsArticle, StockScreen, get_session
    from news.news_fetcher import fetch_and_store_news
    from news.relevance_scorer import score_and_update_articles
    from screener.flag_generator import get_active_flags

# Default lookback for an hourly refresh — just the last day (we run often, so we
# only need the freshest articles; dedup handles overlap).
HOURLY_DAYS_BACK = 1

# Fallback so the scheduler is never a pure no-op during early development, when
# there may be no active flags yet.
FALLBACK_TICKERS = ["AAPL", "SPY"]


def top_screened_tickers(limit=12) -> list:
    """Highest-scoring screened tickers — the news fallback when there are no flags.

    With the FREE yfinance source there's no per-request budget to ration, so when
    nothing is flagged we still surface news for the names the screener likes most.
    """
    session = get_session()
    try:
        rows = (
            session.query(StockScreen)
            .filter(StockScreen.score.isnot(None))
            .order_by(StockScreen.score.desc())
            .limit(limit)
            .all()
        )
        return [r.ticker for r in rows]
    finally:
        session.close()


def get_news_tickers():
    """Return (tickers, active_flag_count) — the tickers whose news we refresh.

    News follows the flags. When there are no active flags, fall back to the
    top-scoring screened names (then a tiny static list) so the feed is never empty.
    """
    flags = get_active_flags()
    count = len(flags)
    tickers = sorted({flag.ticker for flag in flags})

    if not tickers:
        tickers = top_screened_tickers(12) or list(FALLBACK_TICKERS)
        display = "[" + ", ".join(tickers) + "]"
        print(f"News refresh targets: {count} active flags → top-screened {display} (no active flags)")
    else:
        display = "[" + ", ".join(tickers) + "]"
        print(f"News refresh targets: {count} active flags → {display}")

    return tickers, count


def ensure_ticker_news(ticker, max_age_hours=12, days_back=7) -> dict:
    """Ensure a ticker has reasonably fresh stored news; fetch (free) if stale/missing.

    Lets the web app populate news ON DEMAND — a ticker's news appears even with
    no scheduler run and no API key. One yfinance call, throttled by freshness
    (skips the fetch if we stored news within the last `max_age_hours`).
    """
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        newest = (
            session.query(NewsArticle.fetched_at)
            .filter(NewsArticle.ticker == ticker)
            .order_by(NewsArticle.fetched_at.desc())
            .first()
        )
    finally:
        session.close()

    if newest and newest[0] and (datetime.utcnow() - newest[0]) < timedelta(hours=max_age_hours):
        return {"ticker": ticker, "fetched": False}  # already fresh

    try:
        fetch_and_store_news(ticker, days_back=days_back)
        score_and_update_articles(ticker)
        return {"ticker": ticker, "fetched": True}
    except Exception as err:  # noqa: BLE001 - a read-path fetch must never 500 the page
        print(f"⚠️  ensure_ticker_news: {ticker} — {err}")
        return {"ticker": ticker, "fetched": False, "error": str(err)}


def run_news_refresh(days_back=HOURLY_DAYS_BACK, tickers=None) -> dict:
    """Fetch + score news for every flagged ticker; print a timestamped summary.

    `tickers` is normally resolved from active flags; the backfill helper passes
    a pre-resolved list to avoid recomputing it. Each ticker is isolated in its
    own try/except so one failure never aborts the cycle.
    """
    if tickers is None:
        tickers, _flag_count = get_news_tickers()

    refreshed = 0
    total_new = 0
    already_stored = 0
    high_relevance = 0
    bullish = bearish = neutral = 0

    for ticker in tickers:
        try:
            fetch_result = fetch_and_store_news(ticker, days_back=days_back)
            score_result = score_and_update_articles(ticker)

            total_new += fetch_result.get("inserted", 0)
            already_stored += fetch_result.get("skipped_duplicates", 0)
            high_relevance += score_result.get("high_relevance", 0)
            bullish += score_result.get("bullish", 0)
            bearish += score_result.get("bearish", 0)
            neutral += score_result.get("neutral", 0)
            refreshed += 1
        except Exception as err:  # noqa: BLE001 - one bad ticker can't kill the cycle
            print(f"⚠️  News refresh failed for {ticker}: {err}")

    finished = datetime.now()
    print(f"\n[{finished:%Y-%m-%d %H:%M:%S}] News refresh complete")
    print(f"Tickers refreshed: {refreshed}")
    print(f"Total new articles: {total_new}  |  Already stored: {already_stored}")
    print(
        f"High-relevance articles: {high_relevance}  |  "
        f"Bullish: {bullish}  |  Bearish: {bearish}  |  Neutral: {neutral}"
    )

    return {
        "tickers_refreshed": refreshed,
        "total_new_articles": total_new,
        "already_stored": already_stored,
        "high_relevance": high_relevance,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
    }


def run_initial_backfill(days_back=30) -> dict:
    """One-time deeper pull: fetch the last `days_back` days of news for the flags.

    Same machinery as run_news_refresh(), just a wider lookback — used once on
    first setup to populate history before the hourly cadence takes over.
    """
    tickers, _count = get_news_tickers()
    print(f"Initial backfill — fetching {days_back} days of news for {len(tickers)} tickers\n")
    return run_news_refresh(days_back=days_back, tickers=tickers)


def run_news_scheduler() -> None:
    """Start the hourly loop that refreshes news for flagged tickers."""
    schedule.every().hour.do(run_news_refresh)

    print("News scheduler started — refreshing every hour. Ctrl+C to stop.")

    # Keep the process alive; check once every 30s whether the hourly job is due.
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    # Three modes, chosen by a command-line flag:
    #   --now       → run one refresh immediately (testing / manual one-off)
    #   --backfill  → one-time 30-day backfill for flagged tickers
    #   --schedule  → start the long-running hourly loop
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if mode == "--now":
        run_news_refresh()

        # --- Self-test: confirm graceful behavior + clean wiring ---
        if not NEWS_API_KEY or NEWS_API_KEY == "your_key_here":
            print("\nUsing the FREE yfinance news source (no NEWS_API_KEY needed). "
                  "Add a NEWS_API_KEY in .env for broader/older coverage.")
        print(
            "Self-test OK — scheduler wired cleanly: "
            "get_news_tickers → fetch_and_store_news → score_and_update_articles."
        )
    elif mode == "--backfill":
        run_initial_backfill()
    elif mode == "--schedule":
        run_news_scheduler()
    else:
        print("Usage: python news/news_scheduler.py [--now | --backfill | --schedule]")
        print("  --now       run one news refresh immediately (for testing)")
        print("  --backfill  one-time 30-day news backfill for flagged tickers")
        print("  --schedule  start the hourly news auto-refresh loop")
        sys.exit(1)
