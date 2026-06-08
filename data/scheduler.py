"""
data/scheduler.py
=================
Daily auto-refresh scheduler — keeps the database current without manual runs.

WHAT THIS DOES
    Once a day, after the US market closes, it re-fetches price and fundamental
    data for every ticker we track and saves it. Because saves are
    duplicate-safe, a refresh only adds genuinely new rows (e.g. today's bar).

HOW IT FITS IN
    This is the automation layer on top of the Phase 2 data pipeline. It reuses
    the fetchers, the writers, and get_all_stored_tickers() so it always knows
    what to refresh. When the full ticker universe (Step 6) is added, this picks
    it up automatically — the universe stocks get stored, and from then on they
    show up in get_all_stored_tickers().

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
    from data.database import init_db
    from data.db_utils import get_all_stored_tickers
    from data.db_writer import save_fundamentals, save_price_bars
    from data.fetcher_fundamentals import get_fundamentals
    from data.fetcher_price import get_ohlcv_bulk
    from data.universe_etfs import ALL_REFERENCE_TICKERS
except ImportError:  # pragma: no cover
    from database import init_db  # type: ignore
    from db_utils import get_all_stored_tickers  # type: ignore
    from db_writer import save_fundamentals, save_price_bars  # type: ignore
    from fetcher_fundamentals import get_fundamentals  # type: ignore
    from fetcher_price import get_ohlcv_bulk  # type: ignore
    from universe_etfs import ALL_REFERENCE_TICKERS  # type: ignore

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

    # Discover what to refresh: everything already stored, PLUS the reference
    # ETFs/benchmarks (in case the DB is fresh), de-duplicated.
    stored = get_all_stored_tickers()
    tickers = sorted(set(stored) | set(ALL_REFERENCE_TICKERS))
    print(f"Tickers to refresh ({len(tickers)}): {tickers}\n")

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

    # ---- Fundamentals: fetched one at a time (no bulk endpoint) ----
    print()  # spacer
    for ticker in tickers:
        try:
            fundamentals = get_fundamentals(ticker)
            result = save_fundamentals(ticker, fundamentals)
            fundamentals_saved += result["inserted"]
        except Exception as err:  # noqa: BLE001
            print(f"⚠️  Fundamentals refresh failed for {ticker}: {err}")

    # ---- Timestamped summary ----
    finished = datetime.now()
    elapsed = (finished - started).total_seconds()
    print(f"\n[{finished:%Y-%m-%d %H:%M:%S}] === Refresh complete in {elapsed:.0f}s ===")
    print(f"  Tickers refreshed (prices)   : {prices_ok}/{len(tickers)}")
    print(f"  Price rows inserted          : {price_rows_inserted}")
    print(f"  Price rows skipped (existing): {price_rows_skipped}")
    print(f"  Fundamentals snapshots saved : {fundamentals_saved}")

    return {
        "tickers": len(tickers),
        "prices_ok": prices_ok,
        "price_rows_inserted": price_rows_inserted,
        "price_rows_skipped": price_rows_skipped,
        "fundamentals_saved": fundamentals_saved,
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
