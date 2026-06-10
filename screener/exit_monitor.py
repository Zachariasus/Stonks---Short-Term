"""
screener/exit_monitor.py
========================
Exit condition logic — the discipline that closes flags when setups break.

WHAT THIS DOES
    Checks every Active flag against three exit conditions and closes any that
    fire (with a reason):
      1. Decisive 50-day MA break  → the trend has ended
      2. Estimate revision reversal → the fundamental cycle has turned
      3. 2.5× ATR trailing stop     → price action has deteriorated too far

HOW IT FITS IN
    Counterpart to the flag generator. Reads Active flags, evaluates exits, and
    updates flag status to Closed. DB reads/writes only — no network.

NOTE ON SHORTS
    These checks are written for LONG flags. Short exits are mirrored and will be
    built in a future refinement; for now short flags are held with a note rather
    than evaluated against long-exit rules (which would be backwards).
"""

from datetime import date

import pandas as pd

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root (also fixes the screener-package vs screener.py
# collision when run as `python screener/exit_monitor.py`).
try:
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.moving_averages import calculate_atr, calculate_moving_averages
    from data.db_reader import get_price_bars
    from screener.flag_generator import get_active_flags, update_flag_status
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    # The failed try may have cached screener.py AS the top-level module
    # "screener"; drop it so the re-import resolves the screener PACKAGE.
    sys.modules.pop("screener", None)
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.moving_averages import calculate_atr, calculate_moving_averages
    from data.db_reader import get_price_bars
    from screener.flag_generator import get_active_flags, update_flag_status


def check_50day_break(ticker: str):
    """Exit if price is below a 50-day MA that is itself ROLLING OVER.

    A single close below the 50-day is noise; we require the MA itself to be
    declining (today < 10 days ago) to confirm the trend has actually broken.
    Returns True/False, or None if there isn't enough data.
    """
    df = calculate_moving_averages(ticker)
    if df is None or len(df) < 11:
        return None

    df = df.reset_index(drop=True)
    close = df["Close"].iloc[-1]
    sma_50_now = df["sma_50"].iloc[-1]
    sma_50_10ago = df["sma_50"].iloc[-11]

    if pd.isna(sma_50_now) or pd.isna(sma_50_10ago):
        return None

    return bool(close < sma_50_now and sma_50_now < sma_50_10ago)


def check_revision_reversal(ticker: str):
    """Exit if forward-estimate revisions have turned down ("Falling").

    Returns True if Falling, False if Rising/Flat, or None if there isn't enough
    snapshot history yet (can't confirm a reversal → don't exit on it).
    """
    trend = analyze_revision_trend(ticker)
    direction = trend.get("revision_direction")
    if direction == "Insufficient history":
        return None
    return direction == "Falling"


def check_atr_trailing_stop(flag, atr_multiple: float = 2.5):
    """Exit if price has fallen > atr_multiple ATRs below its peak SINCE the flag.

    The peak is the highest close from the flag's flagged_date onward — NOT a long
    historical lookback. A trailing stop must trail from where we ENTERED: anchoring
    to the 52-week high would close a freshly-flagged stock just for sitting below
    its yearly peak (which most stocks do). On day one the peak ≈ the entry price,
    so the stop sits ~2.5 ATR BELOW price and cannot trigger immediately; it only
    ratchets up as the trade makes new highs and then fires once price rolls over.

    Returns (exit_signal, trailing_stop_level, peak_close). Guard: (False, None,
    None) if ATR or data is unavailable — never exit on missing data.
    """
    ticker = flag.ticker
    atr, _weekly = calculate_atr(ticker)
    if atr is None:
        return (False, None, None)

    df = get_price_bars(ticker, days=365)
    if df is None or df.empty:
        return (False, None, None)

    # Highest close from the flag's entry date onward, floored at the entry price
    # (so a day-1 flag — or one whose date is newer than the latest stored bar —
    # uses the entry as its peak instead of an empty window).
    df_since = df[df["Date"] >= pd.Timestamp(flag.flagged_date)]
    since_peak = float(df_since["Close"].max()) if not df_since.empty else None
    candidates = [c for c in (since_peak, flag.entry_price) if c is not None]
    if not candidates:
        return (False, None, None)
    peak_close = round(max(candidates), 2)

    current_close = float(df["Close"].iloc[-1])
    trailing_stop = round(peak_close - atr_multiple * atr, 2)
    return (bool(current_close < trailing_stop), trailing_stop, peak_close)


def evaluate_flag_exits(flag) -> dict:
    """Run all three exit checks against a Flag and summarize the verdict."""
    ticker = flag.ticker
    result = {
        "ticker": ticker,
        "flag_id": flag.id,
        "ma_break": None,
        "revision_reversal": None,
        "atr_stop_hit": False,
        "atr_trailing_stop_level": None,
        "any_exit_triggered": False,
        "exit_reason": "Hold",
    }

    # Short flags: the long-exit rules would be backwards, so hold + note for now.
    if flag.direction == "Short":
        result["exit_reason"] = (
            "Hold: [short-side logic note: exit conditions for shorts are "
            "mirrored — full short exits in a future refinement]"
        )
        return result

    ma_break = check_50day_break(ticker)
    revision_reversal = check_revision_reversal(ticker)
    atr_hit, atr_stop_level, _peak = check_atr_trailing_stop(flag)

    result["ma_break"] = ma_break
    result["revision_reversal"] = revision_reversal
    result["atr_stop_hit"] = atr_hit
    result["atr_trailing_stop_level"] = atr_stop_level

    # Which conditions actually fired?
    fired = []
    if ma_break is True:
        fired.append("50-day MA break")
    if revision_reversal is True:
        fired.append("estimate revision reversal")
    if atr_hit is True:
        fired.append("2.5× ATR trailing stop")

    if fired:
        result["any_exit_triggered"] = True
        result["exit_reason"] = "EXIT — " + "; ".join(fired)
    else:
        # Describe the (calm) state for the display line.
        ma_str = (
            "50-day broken" if ma_break is True
            else "MA intact" if ma_break is False
            else "MA n/a"
        )
        rev_str = (
            "revisions falling" if revision_reversal is True
            else "revisions ok" if revision_reversal is False
            else "revisions insufficient"
        )
        atr_str = "ATR stop HIT" if atr_hit else "ATR stop not hit"
        result["exit_reason"] = f"Hold: {ma_str}, {rev_str}, {atr_str}"

    return result


def run_exit_monitor() -> dict:
    """Evaluate every Active flag, close any that trigger, and print a summary."""
    flags = get_active_flags()

    details = []
    closed = 0
    for flag in flags:
        verdict = evaluate_flag_exits(flag)
        details.append(verdict)
        if verdict["any_exit_triggered"]:
            update_flag_status(flag.ticker, "Closed", verdict["exit_reason"])
            closed += 1

    checked = len(flags)
    still_active = checked - closed

    print(f"=== Exit Monitor Run ({date.today()}) ===")
    print(f"Active flags checked: {checked}")
    print(f"Flags closed:         {closed}")
    print(f"Still active:         {still_active}\n")

    # Per-flag lines (re-read direction from the flag list for the label).
    by_id = {f.id: f for f in flags}
    for v in details:
        direction = by_id[v["flag_id"]].direction
        print(f"{v['ticker']:<6}({direction})  — {v['exit_reason']}")

    return {
        "checked": checked,
        "closed": closed,
        "still_active": still_active,
        "details": details,
    }


if __name__ == "__main__":
    # On the first run our flags were just created, so NO exits should fire —
    # the trends are intact, revisions don't have enough history to confirm a
    # reversal, and price hasn't fallen 2.5 ATRs off its peak. That "0 closed"
    # result is correct: exits only fire once a setup actually deteriorates.
    run_exit_monitor()
