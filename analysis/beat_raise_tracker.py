"""
analysis/beat_raise_tracker.py
==============================
Beat-and-raise tracker — third module of the Fundamental Trajectory engine.

WHAT THIS DOES
    Scores how reliably a company BEATS EPS estimates and whether, after beating,
    forward estimates get RAISED. A streak of beat-and-raise is the fingerprint
    of a company in a durable multi-quarter earnings cycle.

HOW IT FITS IN
    Reads stored EarningsHistory (beats/misses) and EstimateSnapshot (the "raise"
    via analyze_revision_trend). No external calls — pure DB analysis.
"""

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.estimate_revisions import analyze_revision_trend
    from data.database import EarningsHistory, get_session
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.estimate_revisions import analyze_revision_trend
    from data.database import EarningsHistory, get_session


def classify_quarter(surprise_pct):
    """Label one quarter's EPS surprise.

    We use a ±3% band rather than treating ANY positive surprise as a beat:
    consensus EPS is rounded to the cent and carries natural estimate noise, so a
    tiny surprise (e.g. +0.5%) is effectively "in line" — not a real beat. ±3%
    filters that rounding/noise band out so only meaningful surprises count.
    """
    if surprise_pct is None:
        return "In Line"
    if surprise_pct > 3:
        return "Beat"
    if surprise_pct < -3:
        return "Miss"
    return "In Line"


def analyze_beat_history(ticker: str, num_quarters: int = 8):
    """Score the beat/miss record from stored EarningsHistory.

    Returns a dict {beat_rate, avg_surprise_pct, consecutive_beats,
    result_sequence, num_quarters_analyzed}, or None if <2 quarters are stored.
    """
    ticker = ticker.strip().upper()

    session = get_session()
    try:
        rows = (
            session.query(EarningsHistory)
            .filter(EarningsHistory.ticker == ticker)
            .order_by(EarningsHistory.report_date.desc())  # most recent first
            .limit(num_quarters)
            .all()
        )
        surprises_newest_first = [r.surprise_pct for r in rows]
    finally:
        session.close()

    if len(surprises_newest_first) < 2:
        print(
            f"⚠️  analyze_beat_history: only {len(surprises_newest_first)} quarters "
            f"stored for {ticker} (need ≥2)."
        )
        return None

    total = len(surprises_newest_first)
    labels_newest_first = [classify_quarter(s) for s in surprises_newest_first]

    beats = sum(1 for label in labels_newest_first if label == "Beat")
    beat_rate = round(beats / total * 100, 1)

    valid_surprises = [s for s in surprises_newest_first if s is not None]
    avg_surprise_pct = (
        round(sum(valid_surprises) / len(valid_surprises), 2) if valid_surprises else None
    )

    # Count consecutive beats from the most recent quarter backward; stop at the
    # first quarter that wasn't a beat.
    consecutive_beats = 0
    for label in labels_newest_first:
        if label == "Beat":
            consecutive_beats += 1
        else:
            break

    return {
        "beat_rate": beat_rate,
        "avg_surprise_pct": avg_surprise_pct,
        "consecutive_beats": consecutive_beats,
        "result_sequence": list(reversed(labels_newest_first)),  # oldest → newest
        "num_quarters_analyzed": total,
    }


def assess_raise_signal(ticker: str) -> dict:
    """Did forward estimates move UP after the most recent report?

    Uses analyze_revision_trend() over our stored EstimateSnapshot rows.
    Returns {raise_signal, revision_pct_change, num_snapshots} (plus a note when
    there isn't enough snapshot history yet).
    """
    ticker = ticker.strip().upper()
    trend = analyze_revision_trend(ticker)

    # analyze_revision_trend already handles the <2-snapshots case.
    if trend["revision_direction"] == "Insufficient history":
        return {
            "raise_signal": "Insufficient history",
            "note": "needs multiple weeks of snapshots to detect estimate drift",
            "revision_pct_change": None,
            "num_snapshots": trend.get("num_snapshots", 0),
        }

    direction = trend["revision_direction"]
    raise_signal = {"Rising": "Raised", "Falling": "Cut"}.get(direction, "Flat")

    return {
        "raise_signal": raise_signal,
        "revision_pct_change": trend.get("revision_pct_change"),
        "num_snapshots": trend.get("num_snapshots"),
    }


def score_beat_raise(ticker: str) -> dict:
    """Combine beat history + raise signal into one overall pattern label."""
    ticker = ticker.strip().upper()
    history = analyze_beat_history(ticker)
    raise_info = assess_raise_signal(ticker)

    # If we can't read the beat history, we can't score the pattern.
    if history is None:
        return {
            "pattern_label": "Insufficient Data",
            "beat_rate": None,
            "avg_surprise_pct": None,
            "consecutive_beats": None,
            "result_sequence": [],
            "raise_signal": raise_info["raise_signal"],
            "revision_pct_change": raise_info.get("revision_pct_change"),
            "num_snapshots": raise_info.get("num_snapshots"),
        }

    beat_rate = history["beat_rate"]
    consecutive = history["consecutive_beats"]
    raise_signal = raise_info["raise_signal"]

    # Pattern label — checked most-specific first.
    if consecutive >= 3 and raise_signal == "Raised":
        pattern_label = "Beat-and-Raise Cycle"
    elif beat_rate >= 75 and consecutive >= 2:
        pattern_label = "Consistent Beater"
    elif consecutive >= 1 and beat_rate >= 50:
        pattern_label = "Recent Beat"
    elif beat_rate < 40:
        pattern_label = "Chronic Misser"
    else:  # 40% ≤ beat_rate < 75% and not caught above
        pattern_label = "Mixed"

    return {
        "pattern_label": pattern_label,
        "beat_rate": beat_rate,
        "avg_surprise_pct": history["avg_surprise_pct"],
        "consecutive_beats": consecutive,
        "result_sequence": history["result_sequence"],
        "raise_signal": raise_signal,
        "revision_pct_change": raise_info.get("revision_pct_change"),
        "num_snapshots": raise_info.get("num_snapshots"),
    }


def get_beat_raise_summary(ticker: str) -> dict:
    """Print a clean beat-and-raise block for a ticker and return the score dict."""
    ticker = ticker.strip().upper()
    result = score_beat_raise(ticker)

    if result["pattern_label"] == "Insufficient Data":
        print(f"{ticker} | Beat-and-Raise History")
        print("Pattern:           Insufficient Data (no stored earnings history)")
        return result

    num_q = result["num_quarters_analyzed"] if "num_quarters_analyzed" in result else len(result["result_sequence"])
    avg = result["avg_surprise_pct"]
    avg_str = f"{avg:+.1f}%" if avg is not None else "n/a"

    # Build the raise-signal line (with snapshot count / drift % as appropriate).
    raise_signal = result["raise_signal"]
    n_snaps = result.get("num_snapshots") or 0
    if raise_signal == "Insufficient history":
        plural = "s" if n_snaps != 1 else ""
        raise_str = f"Insufficient history ({n_snaps} snapshot{plural} — builds over weeks)"
    elif raise_signal in ("Raised", "Cut"):
        pct = result.get("revision_pct_change")
        raise_str = f"{raise_signal} ({pct:+.1f}%)" if pct is not None else raise_signal
    else:
        raise_str = "Flat"

    print(f"{ticker} | Beat-and-Raise History (last {num_q} quarters)")
    print(f"Pattern:           {result['pattern_label']}")
    print(
        f"Beat rate:         {result['beat_rate']:g}%  |  "
        f"Avg surprise: {avg_str}  |  Consecutive beats: {result['consecutive_beats']}"
    )
    print(f"Sequence (old→new): {' · '.join(result['result_sequence'])}")
    print(f"Raise signal:      {raise_str}")

    return result


if __name__ == "__main__":
    for ticker in ["AAPL", "MSFT"]:
        result = get_beat_raise_summary(ticker)

        # One-line combined read.
        if result["pattern_label"] != "Insufficient Data":
            avg = result["avg_surprise_pct"]
            avg_str = f"{avg:+.1f}%" if avg is not None else "n/a"
            print(
                f"\n{ticker}: {result['pattern_label']} — "
                f"{result['consecutive_beats']} consecutive beats, "
                f"avg surprise {avg_str}, estimates {result['raise_signal']}"
            )
        else:
            print(f"\n{ticker}: Insufficient data — no stored earnings history.")
        print()
