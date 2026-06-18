"""
data/scheduler.py
=================
Daily auto-refresh scheduler — keeps the database current without manual runs.

WHAT THIS DOES
    Once a day, after the US market closes, it re-fetches and stores everything
    we track for every ticker: prices, fundamentals, earnings history, forward-
    estimate snapshots, and the next earnings date. Because saves are
    duplicate-safe, a refresh only adds genuinely new rows (e.g. today's bar).

HOW IT FITS IN
    This is the automation layer on top of the Phase 2 data pipeline. It reuses
    the fetchers, the writers, and get_active_universe() so it always knows what
    to refresh: the maintained S&P 500 universe plus the reference ETFs/benchmarks.
    Adding/removing stocks from the universe automatically changes what gets
    refreshed — no edits to this file needed.

USAGE
    python data/scheduler.py --now        # run one refresh right now (testing)
    python data/scheduler.py --schedule   # start the daily 16:30 loop
"""

import sys
import time
from datetime import datetime

import schedule

# Support both `python -m data.scheduler` and `python data/scheduler.py`
# (and inline runs with PYTHONPATH=<project root>).
try:
    from analysis.earnings_calendar import fetch_and_store_earnings_date
    from analysis.estimate_revisions import save_estimate_snapshot
    from data.database import init_db
    from data.db_writer import (
        save_earnings_history,
        save_fundamentals,
        save_price_bars,
    )
    from data.fetcher_fundamentals import get_earnings_history, get_fundamentals
    from data.fetcher_price import get_ohlcv_bulk
    from data.universe_etfs import ALL_REFERENCE_TICKERS
    from data.universe_stocks import get_active_universe
except ImportError:  # pragma: no cover
    # Running as a bare script: put the project root on the path, then import
    # in package form so both data.* and analysis.* resolve from the root.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.earnings_calendar import fetch_and_store_earnings_date
    from analysis.estimate_revisions import save_estimate_snapshot
    from data.database import init_db
    from data.db_writer import (
        save_earnings_history,
        save_fundamentals,
        save_price_bars,
    )
    from data.fetcher_fundamentals import get_earnings_history, get_fundamentals
    from data.fetcher_price import get_ohlcv_bulk
    from data.universe_etfs import ALL_REFERENCE_TICKERS
    from data.universe_stocks import get_active_universe

# How far back each refresh re-fetches. 2y matches the reference data so the
# whole table stays on a consistent 2-year window for relative-strength math.
REFRESH_PERIOD = "2y"

# Daily run time (local system clock). 16:30 = 30 min after the 16:00 US close,
# giving the data providers time to finalize the day's official bar.
DAILY_RUN_TIME = "16:30"


def refresh_all_data() -> dict:
    """Re-fetch and store price + fundamental data for every tracked ticker.

    Built to be resilient: each ticker's save is wrapped in its own try/except,
    so one bad/delisted symbol can never abort the whole nightly run.

    Returns:
        A small summary dict of what happened.
    """
    started = datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] === Daily data refresh starting ===")

    # Make sure the tables exist (harmless if they already do).
    init_db()

    # Phase 2 Step 6 change: the refresh list is now driven by the maintained
    # S&P 500 ticker universe (get_active_universe) rather than just whatever
    # happens to be stored already — so daily refreshes cover the whole universe.
    # We still always include the reference ETFs/benchmarks. De-duplicated.
    universe = get_active_universe()
    tickers = sorted(set(universe) | set(ALL_REFERENCE_TICKERS))
    print(
        f"Tickers to refresh: {len(tickers)} "
        f"({len(universe)} universe + {len(ALL_REFERENCE_TICKERS)} reference)\n"
    )

    price_rows_inserted = 0
    price_rows_skipped = 0
    prices_ok = 0
    fundamentals_saved = 0

    # ---- Prices: one bulk download, then save each result ----
    try:
        price_data = get_ohlcv_bulk(tickers, period=REFRESH_PERIOD, interval="1d")
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  Bulk price fetch failed entirely: {err}")
        price_data = {}

    print()  # spacer
    for ticker in tickers:
        try:
            df = price_data.get(ticker)
            if df is None:
                continue  # fetch failed for this one; already warned upstream
            counts = save_price_bars(ticker, df)
            price_rows_inserted += counts["inserted"]
            price_rows_skipped += counts["skipped"]
            prices_ok += 1
        except Exception as err:  # noqa: BLE001 - never let one ticker kill the run
            print(f"⚠️  Price save failed for {ticker}: {err}")

    # ---- Fundamentals + earnings history + estimate snapshots + earnings dates ----
    # All fetched one ticker at a time (no bulk endpoints). Each persistence step
    # gets its OWN try/except so one failure can't abort the others or the loop.
    earnings_dates_updated = 0
    print()  # spacer
    for ticker in tickers:
        # 1) Fundamentals snapshot (valuation / margins / growth)
        try:
            fundamentals = get_fundamentals(ticker)
            result = save_fundamentals(ticker, fundamentals)
            fundamentals_saved += result["inserted"]
        except Exception as err:  # noqa: BLE001
            print(f"⚠️  Fundamentals refresh failed for {ticker}: {err}")

        # 2) Earnings history (estimate vs actual) → feeds the beat/raise tracker
        try:
            earnings_df = get_earnings_history(ticker)
            if earnings_df is not None:
                save_earnings_history(ticker, earnings_df)
        except Exception as err:  # noqa: BLE001
            print(f"⚠️  Earnings-history refresh failed for {ticker}: {err}")

        # 3) Forward-estimate snapshot → feeds the revision tracker (drift over time)
        try:
            save_estimate_snapshot(ticker)
        except Exception as err:  # noqa: BLE001
            print(f"⚠️  Estimate-snapshot refresh failed for {ticker}: {err}")

        # 4) Next earnings date → feeds the earnings calendar
        try:
            if fetch_and_store_earnings_date(ticker) is not None:
                earnings_dates_updated += 1
        except Exception as err:  # noqa: BLE001
            print(f"⚠️  Earnings-date refresh failed for {ticker}: {err}")

    # ---- Timestamped summary ----
    finished = datetime.now()
    elapsed = (finished - started).total_seconds()
    print(f"\n[{finished:%Y-%m-%d %H:%M:%S}] === Refresh complete in {elapsed:.0f}s ===")
    print(f"  Tickers refreshed (prices)   : {prices_ok}/{len(tickers)}")
    print(f"  Price rows inserted          : {price_rows_inserted}")
    print(f"  Price rows skipped (existing): {price_rows_skipped}")
    print(f"  Fundamentals snapshots saved : {fundamentals_saved}")
    print(f"  Earnings dates updated       : {earnings_dates_updated}")

    # ---- Validate price data + auto-correct bad bars ----
    # Runs after prices are saved and BEFORE screening, so a corrupt yfinance bar
    # (reverting spike, NaN, OHLC violation) can't manufacture a fake flag. Bad
    # bars a re-fetch resolves are corrected in place; anything it can't resolve is
    # logged for review. Own try/except so a validation error can't break the run.
    try:
        from data.data_validator import run_validation

        print()  # spacer
        run_validation()
    except Exception as err:  # noqa: BLE001 - refresh must survive a validation error
        print(f"⚠️  Data validation failed (refresh unaffected): {err}")

    # ---- Screen the refreshed universe and (re)generate flags ----
    # This is the step that turns fresh data into the Flagged Stocks list: score
    # every stock with enough price history through the 4-engine confluence model,
    # then flag the ones that clear the long/short thresholds. Runs BEFORE the news
    # pass so news is fetched for the just-generated flags. Own try/except so a
    # screening error can never break the data refresh.
    screen_summary = None
    try:
        from screener.flag_generator import save_screen_snapshot, sync_flags_from_screen
        from screener.screener import run_screener

        print()  # spacer
        screen_df = run_screener()  # scores the full scoreable universe
        snapshot_rows = save_screen_snapshot(screen_df)  # full universe → Stocks page
        sync = sync_flags_from_screen(screen_df)  # extend / reset / close / create
        screen_summary = {
            "scored": 0 if screen_df is None else len(screen_df),
            "snapshot_rows": snapshot_rows,
            **sync,
        }
        print(
            f"  Stocks scored                : {screen_summary['scored']}\n"
            f"  New flags                    : {sync['new']}\n"
            f"  Spans extended (same stage)  : {sync['extended']}\n"
            f"  Spans reset (stage changed)  : {sync['reset']}\n"
            f"  Flags closed (dropped out)   : {sync['closed']}"
        )
    except Exception as err:  # noqa: BLE001 - data refresh must survive a screening error
        print(f"⚠️  Screening/flagging failed (data refresh unaffected): {err}")

    # ---- News: one daily pass for free (flagged tickers only) ----
    # Piggybacking on the nightly data run gives a single daily news refresh at no
    # extra plumbing cost; the standalone news_scheduler adds the HOURLY layer on
    # top. Imported lazily and wrapped in its own try/except so a news failure
    # (or a missing NEWS_API_KEY) can never break the price/fundamentals refresh.
    news_summary = None
    try:
        from news.news_scheduler import run_news_refresh

        print()  # spacer before the news block
        news_summary = run_news_refresh(days_back=1)
    except Exception as err:  # noqa: BLE001 - data refresh must survive any news error
        print(f"⚠️  Daily news refresh failed (data refresh unaffected): {err}")

    return {
        "tickers": len(tickers),
        "prices_ok": prices_ok,
        "price_rows_inserted": price_rows_inserted,
        "price_rows_skipped": price_rows_skipped,
        "fundamentals_saved": fundamentals_saved,
        "earnings_dates_updated": earnings_dates_updated,
        "screen": screen_summary,
        "news": news_summary,
    }


def run_scheduler() -> None:
    """Start the daily loop that refreshes data after market close."""
    # Register the job. NOTE: the `schedule` library uses the LOCAL system clock,
    # so this fires at 16:30 in whatever timezone this machine is set to.
    schedule.every().day.at(DAILY_RUN_TIME).do(refresh_all_data)

    print(
        f"Scheduler started — next run at {DAILY_RUN_TIME} ET daily. "
        "Press Ctrl+C to stop."
    )

    # Keep the process alive and check once a minute whether a job is due.
    # The sleep prevents this loop from pegging the CPU at 100%.
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    # Two modes, chosen by a command-line flag:
    #   --now      → run a single refresh immediately. Use this for testing, or
    #                any time you want a manual one-off update.
    #   --schedule → start the long-running daily loop (leave it running, or
    #                later install it as a background service).
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if mode == "--now":
        refresh_all_data()
    elif mode == "--schedule":
        run_scheduler()
    else:
        print("Usage: python data/scheduler.py [--now | --schedule]")
        print("  --now       run one refresh immediately (for testing)")
        print("  --schedule  start the daily 16:30 auto-refresh loop")
        sys.exit(1)
