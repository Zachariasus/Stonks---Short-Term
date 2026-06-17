"""
screener/flag_generator.py
==========================
Flag generator + storage — persists high-confidence setups for the web app.

WHAT A "FLAG" IS
    The system's timestamped NOTATION that a setup met the confluence threshold
    right now — NOT a trade recommendation. Storing flags with a date lets us
    show a setup card and track how setups evolve (Active → Watching → Closed).

HOW IT FITS IN
    Consumes the screener's ranked DataFrame, writes Flag rows to SQLite, and
    provides read/update helpers. DB reads/writes only — no network.
"""

from datetime import date

import pandas as pd

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.earnings_calendar import get_days_to_earnings, get_earnings_flag
    from analysis.price_target import calculate_reward_risk
    from analysis.stage_classifier import STAGE_LABELS
    from data.database import Flag, get_session, init_db
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.earnings_calendar import get_days_to_earnings, get_earnings_flag
    from analysis.price_target import calculate_reward_risk
    from analysis.stage_classifier import STAGE_LABELS
    from data.database import Flag, get_session, init_db
    from data.db_reader import get_price_bars


def _na_to_none(value):
    """Convert pandas NaN / None to None; leave real values (incl. strings) intact."""
    try:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None
    return value


def _flag_exists(ticker, flagged_date, direction) -> bool:
    """True if a flag for this ticker+date+direction is already stored."""
    session = get_session()
    try:
        return (
            session.query(Flag)
            .filter_by(ticker=ticker, flagged_date=flagged_date, direction=direction)
            .first()
            is not None
        )
    finally:
        session.close()


def _stage_str(score_dict) -> str:
    """Build the readable stage label ("Stage 2 — Advancing") from a score dict."""
    stage_int = _na_to_none(score_dict.get("stage"))
    try:
        stage_int = int(stage_int) if stage_int is not None else None
    except (TypeError, ValueError):
        stage_int = None
    return (
        f"Stage {stage_int} — {STAGE_LABELS[stage_int]}"
        if stage_int in STAGE_LABELS
        else "Unknown"
    )


def _apply_live_fields(flag, score_dict) -> None:
    """Refresh an existing flag's CURRENT-state fields from today's screen.

    Updates the values that legitimately move day to day (score, confidence, RS,
    sector rotation, earnings timing). Does NOT touch entry/target/stop/rr — those
    are fixed at the original entry. Stage + the date span are handled by the
    caller (sync_flags_from_screen).
    """
    flag.score = int(_na_to_none(score_dict.get("total_score")) or 0)
    flag.confidence_label = _na_to_none(score_dict.get("confidence_label"))
    flag.rs_label = _na_to_none(score_dict.get("rs_label"))
    flag.sector_etf = _na_to_none(score_dict.get("sector_etf"))
    flag.sector_rotation_label = _na_to_none(score_dict.get("sector_rotation_label"))
    flag.earnings_flag = get_earnings_flag(flag.ticker)
    flag.days_to_earnings = get_days_to_earnings(flag.ticker)


def generate_flag(score_dict, target_price=None):
    """Create (or fetch the existing) Flag row from a score_stock() dict.

    Idempotent per (ticker, today, direction): re-flagging the same setup the
    same day is skipped and the existing row is returned.
    """
    init_db()
    ticker = str(score_dict.get("ticker")).strip().upper()
    direction = score_dict.get("direction", "Long")
    today = date.today()

    session = get_session()
    try:
        existing = (
            session.query(Flag)
            .filter_by(ticker=ticker, flagged_date=today, direction=direction)
            .first()
        )
        if existing is not None:
            print(f"generate_flag: {ticker} ({direction}) — skipped, already flagged today.")
            return existing

        # Entry = latest stored close.
        bars = get_price_bars(ticker, days=5)
        entry_price = (
            round(float(bars["Close"].iloc[-1]), 2)
            if bars is not None and not bars.empty
            else None
        )

        # Placeholder protective stop: 8% below entry.
        suggested_stop = round(entry_price * 0.92, 2) if entry_price is not None else None

        # Earnings awareness.
        earnings_flag = get_earnings_flag(ticker)
        days_to_earn = get_days_to_earnings(ticker)

        # Reward:risk only if a target was supplied (else left None for now).
        rr_ratio = None
        if target_price is not None and suggested_stop is not None:
            rr = calculate_reward_risk(ticker, suggested_stop)
            if rr:
                rr_ratio = rr["rr_ratio"]

        # Readable stage label ("Stage 2 — Advancing").
        stage_str = _stage_str(score_dict)

        flag = Flag(
            ticker=ticker,
            flagged_date=today,
            stage_start_date=today,  # span start (resets on a stage change)
            last_seen_date=today,    # span end (advances each scan it still qualifies)
            score=int(score_dict.get("total_score") or 0),
            confidence_label=_na_to_none(score_dict.get("confidence_label")),
            direction=direction,
            stage=stage_str,
            rs_label=_na_to_none(score_dict.get("rs_label")),
            sector_etf=_na_to_none(score_dict.get("sector_etf")),
            sector_rotation_label=_na_to_none(score_dict.get("sector_rotation_label")),
            entry_price=entry_price,
            target_price=round(float(target_price), 2) if target_price is not None else None,
            suggested_stop=suggested_stop,
            rr_ratio=rr_ratio,
            earnings_flag=earnings_flag,
            days_to_earnings=days_to_earn,
            status="Active",
        )
        session.add(flag)
        session.commit()
        print(f"generate_flag: {ticker} ({direction}) flagged — score {flag.score}, Active.")
        return flag
    finally:
        session.close()


def flag_screener_results(screener_df, min_score_long: int = 70, min_score_short: int = 70) -> dict:
    """Flag every qualifying row of a screener DataFrame.

    Long rows need score ≥ min_score_long; Short rows need score ≥ min_score_short.
    Both default to 70 — the confluence scorer's "High confidence" score boundary —
    so the Flagged Stocks list stays a tight, high-conviction watchlist rather than
    a few-hundred-name dump. (Earlier 60/55 produced ~144 flags; 70/70 ≈ 27.)
    Returns {new_flags, skipped_existing, flagged_tickers}.
    """
    summary = {"new_flags": 0, "skipped_existing": 0, "flagged_tickers": []}
    if screener_df is None or screener_df.empty:
        return summary

    today = date.today()
    for _, row in screener_df.iterrows():
        sd = row.to_dict()
        direction = sd.get("direction")
        score = sd.get("total_score", 0)

        qualifies = (
            (direction == "Long" and score >= min_score_long)
            or (direction == "Short" and score >= min_score_short)
        )
        if not qualifies:
            continue

        ticker = str(sd.get("ticker")).strip().upper()
        already = _flag_exists(ticker, today, direction)
        generate_flag(sd)
        if already:
            summary["skipped_existing"] += 1
        else:
            summary["new_flags"] += 1
            summary["flagged_tickers"].append(ticker)

    return summary


def sync_flags_from_screen(screener_df, min_score_long: int = 70, min_score_short: int = 70) -> dict:
    """Reconcile the Active flag watchlist against today's screen (the daily flow).

    This replaces the old "one new flag row per ticker per day" behaviour with a
    living watchlist. For each stock that still qualifies (score ≥ its threshold):
      • no Active flag yet   → CREATE one (flagged_date = stage_start = last_seen = today).
      • SAME stage as before → EXTEND: advance last_seen_date to today and refresh the
                               live fields — the flagged-date span grows by a day.
      • stage CHANGED        → RESET the span: stage_start_date = today (and update the
                               stored stage), keeping the original flagged_date (the
                               stable entry the tax/exit logic depends on).
    Active flags whose ticker was screened today but no longer qualifies are CLOSED.
    A flag whose ticker wasn't screened at all (data gap) is left untouched — we
    never close on the mere absence of data.

    Returns {new, extended, reset, closed}.
    """
    summary = {"new": 0, "extended": 0, "reset": 0, "closed": 0}
    if screener_df is None or screener_df.empty:
        return summary  # don't wipe the watchlist on an empty/failed screen

    today = date.today()
    init_db()

    # Index the screen: every ticker that was scored, and the subset that qualifies.
    screened_tickers = set()
    qualifying = {}  # (ticker, direction) -> score_dict
    for _, row in screener_df.iterrows():
        sd = row.to_dict()
        ticker = str(sd.get("ticker")).strip().upper()
        screened_tickers.add(ticker)
        direction = sd.get("direction")
        score = _na_to_none(sd.get("total_score")) or 0
        if (direction == "Long" and score >= min_score_long) or (
            direction == "Short" and score >= min_score_short
        ):
            qualifying[(ticker, direction)] = sd

    session = get_session()
    handled = set()
    try:
        for flag in session.query(Flag).filter(Flag.status == "Active").all():
            key = (flag.ticker, flag.direction)
            if key in qualifying:
                sd = qualifying[key]
                new_stage = _stage_str(sd)
                _apply_live_fields(flag, sd)
                flag.last_seen_date = today
                if flag.stage != new_stage:
                    flag.stage = new_stage
                    flag.stage_start_date = today  # stage changed → restart the span
                    summary["reset"] += 1
                else:
                    summary["extended"] += 1
                handled.add(key)
            elif flag.ticker in screened_tickers:
                # Screened today but no longer qualifies (score fell / direction flipped).
                flag.status = "Closed"
                flag.close_date = today
                flag.close_reason = "No longer meets flag criteria"
                summary["closed"] += 1
            # else: not screened this run (data gap) → leave the flag untouched.
        session.commit()
    finally:
        session.close()

    # Create flags for newly-qualifying stocks that had no Active flag (own session).
    for key, sd in qualifying.items():
        if key in handled:
            continue
        generate_flag(sd)
        summary["new"] += 1

    return summary


def update_flag_status(ticker, status, reason, flagged_date=None):
    """Update the most recent Active flag for a ticker (or a specific date)."""
    ticker = str(ticker).strip().upper()
    session = get_session()
    try:
        query = session.query(Flag).filter(Flag.ticker == ticker, Flag.status == "Active")
        if flagged_date is not None:
            query = query.filter(Flag.flagged_date == flagged_date)
        flag = query.order_by(Flag.flagged_date.desc()).first()

        if flag is None:
            print(f"update_flag_status: no Active flag found for {ticker}.")
            return None

        flag.status = status
        flag.close_reason = reason
        if status == "Closed":
            flag.close_date = date.today()
        session.commit()
        print(f"update_flag_status: {ticker} → {status}.")
        return flag
    finally:
        session.close()


def get_active_flags(direction=None, min_score=None):
    """Return all Active flags (sorted by score desc), with optional filters."""
    session = get_session()
    try:
        query = session.query(Flag).filter(Flag.status == "Active")
        if direction:
            query = query.filter(Flag.direction == direction)
        if min_score is not None:
            query = query.filter(Flag.score >= min_score)
        return query.order_by(Flag.score.desc()).all()
    finally:
        session.close()


def _earnings_display(earnings_flag, days_to_earnings):
    """Compact earnings cell: '51d' if we have a date, else the flag word."""
    if days_to_earnings is not None:
        return f"{days_to_earnings}d"
    return "Unknown"


def print_active_flags():
    """Print a display table of all Active flags; return the list."""
    flags = get_active_flags()

    print(f"=== Active Flags (as of {date.today()}) ===")
    header = (
        f"{'Ticker':<8}{'Dir':<7}{'Score':<7}{'Conf':<8}"
        f"{'Entry':<10}{'Target':<10}{'Stop':<10}{'R:R':<6}{'Earnings'}"
    )
    print(header)
    print("─" * 76)

    def _money(v):
        return f"${v:.2f}" if v is not None else "—"

    for f in flags:
        rr = f"{f.rr_ratio:.1f}x" if f.rr_ratio is not None else "—"
        print(
            f"{f.ticker:<8}{f.direction:<7}{f.score:<7}{f.confidence_label or '—':<8}"
            f"{_money(f.entry_price):<10}{_money(f.target_price):<10}"
            f"{_money(f.suggested_stop):<10}{rr:<6}"
            f"{_earnings_display(f.earnings_flag, f.days_to_earnings)}"
        )

    return flags


if __name__ == "__main__":
    from analysis.confluence_scorer import score_stock

    # Robust to both run styles: `python -m screener.flag_generator` (package) and
    # `python screener/flag_generator.py` (the screener/ dir is on sys.path, so
    # screener.py imports as a top-level module instead of screener.screener).
    try:
        from screener.screener import run_screener
    except (ImportError, ModuleNotFoundError):
        from screener import run_screener

    test_tickers = [
        "AAPL", "MSFT", "XLK", "XLE", "XLF", "XLV", "XLI", "XLY",
        "XLP", "XLU", "XLRE", "XLB", "XLC", "SPY", "QQQ",
    ]

    df = run_screener(tickers=test_tickers)
    summary = flag_screener_results(df)
    print(
        f"\nNew flags created: {summary['new_flags']}  |  "
        f"Skipped (existing): {summary['skipped_existing']}"
    )
    print(f"Flagged tickers: {summary['flagged_tickers']}\n")

    print_active_flags()

    # Duplicate-skip check: flag AAPL again — should be skipped.
    print("\n--- Duplicate-skip test (AAPL again) ---")
    generate_flag(score_stock("AAPL"))
