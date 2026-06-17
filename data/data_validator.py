"""
data/data_validator.py
======================
Automated price-data sanity checks + self-correction.

WHY
    Free data (yfinance) occasionally returns a corrupt bar — a one-day spike that
    reverts, a NaN/zero/negative price, or an OHLC violation (close outside the
    high/low). A single bad bar can manufacture a fake breakout or a nonsense stop
    level, so we screen for these BEFORE the screener runs and auto-correct what we
    can by re-fetching from the source.

WHAT IS *NOT* BAD DATA  (the MU lesson)
    A merely HIGH or low price is NOT an anomaly — real prices can be anything
    (MU legitimately traded >$1,000; BRK.A trades in the hundreds of thousands).
    Micron's ~$1,020 print was internally consistent (a continuous series, normal
    volume, matching a fresh re-fetch), so it is real, not a glitch. We therefore
    only flag STRUCTURAL anomalies, never "looks too high". That keeps the
    validator from ever "correcting" a genuine move.

CORRECTION STRATEGY
    For each flagged bar we re-fetch the ticker fresh. If the fresh bar at that
    date is clean (a transient glitch that has resolved), we overwrite the stored
    bar. If the fresh data is ALSO anomalous, we DON'T guess — we leave it and
    report it as "unresolved" for review (and it can be excluded from screening).
"""

import math

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.database import PriceBar, get_session
    from data.db_reader import get_price_bars
    from data.fetcher_price import get_ohlcv
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.database import PriceBar, get_session
    from data.db_reader import get_price_bars
    from data.fetcher_price import get_ohlcv

# A bar is a "reverting spike" if its close sits more than this fraction away from
# BOTH the previous AND next bar. 0.50 = 50% — far above any normal daily move or
# earnings gap, so this only catches glitch bars (which revert), not real action
# (a real gap moves one way and stays, so it's close to its NEXT neighbour).
SPIKE_PCT = 0.50

# OHLC bounds get a small relative tolerance: yfinance's live/most-recent bar can
# report the open slightly outside [low, high] (the open and the high/low come from
# different real-time snapshots). 2% absorbs those feed artifacts while still
# catching genuine corruption, which is always gross (a dropped digit / 10x error /
# a close at 2x the high) — never a ~1-2% miss. The open field isn't used by the
# analysis anyway (ATR/MAs/stage all key off high/low/close).
REL_TOL = 0.02


def _as_date(value):
    """Normalize a Timestamp/datetime/date to a plain date for matching."""
    return value.date() if hasattr(value, "date") else value


def _bar_is_invalid(o, h, l, c):
    """True (+reason) if a single bar's OHLC values are impossible."""
    vals = [o, h, l, c]
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return True, "non-finite price"
    if any(v <= 0 for v in vals):
        return True, "non-positive price"
    lo, hi = l * (1 - REL_TOL), h * (1 + REL_TOL)
    if h < l * (1 - REL_TOL):
        return True, "high < low"
    if not (lo <= c <= hi):
        return True, "close outside [low, high]"
    if not (lo <= o <= hi):
        return True, "open outside [low, high]"
    return False, None


def find_anomalies(ticker, df=None):
    """Return a list of anomalous bars: [{date, kind, detail, close}, ...]."""
    if df is None:
        df = get_price_bars(ticker, days=400)
    if df is None or df.empty:
        return []

    df = df.sort_values("Date").reset_index(drop=True)
    closes = df["Close"].tolist()
    anomalies = []

    for i in range(len(df)):
        row = df.iloc[i]
        # 1) Structural OHLC validity.
        bad, detail = _bar_is_invalid(row.get("Open"), row.get("High"), row.get("Low"), row["Close"])
        if bad:
            anomalies.append({"date": _as_date(row["Date"]), "kind": "invalid_ohlc",
                              "detail": detail, "close": row["Close"]})
            continue

        # 2) Reverting single-bar spike (far from BOTH neighbours).
        c = closes[i]
        prev = closes[i - 1] if i > 0 else None
        nxt = closes[i + 1] if i < len(closes) - 1 else None
        if prev and nxt and prev > 0 and nxt > 0 and c > 0:
            from_prev = abs(c / prev - 1)
            from_next = abs(c / nxt - 1)
            if from_prev > SPIKE_PCT and from_next > SPIKE_PCT:
                anomalies.append({
                    "date": _as_date(row["Date"]),
                    "kind": "revert_spike",
                    "detail": f"{from_prev * 100:.0f}% from prev, {from_next * 100:.0f}% from next",
                    "close": c,
                })

    return anomalies


def validate_ticker(ticker, auto_correct=True):
    """Detect anomalies for one ticker and (optionally) auto-correct from a re-fetch.

    Returns {ticker, anomalies, corrected, unresolved, details}.
    """
    ticker = ticker.strip().upper()
    anomalies = find_anomalies(ticker)
    report = {
        "ticker": ticker,
        "anomalies": len(anomalies),
        "corrected": 0,
        "unresolved": [],
        "details": anomalies,
    }

    if not anomalies:
        return report
    if not auto_correct:
        report["unresolved"] = [str(a["date"]) for a in anomalies]
        return report

    # Re-fetch fresh from the source and see which dates are clean now.
    fresh = get_ohlcv(ticker, period="2y", interval="1d")
    if fresh is None or fresh.empty:
        report["unresolved"] = [str(a["date"]) for a in anomalies]
        return report

    fresh = fresh.sort_values("Date").reset_index(drop=True)
    fresh_bad_dates = {a["date"] for a in find_anomalies(ticker, df=fresh)}
    fresh_by_date = {_as_date(r["Date"]): r for r in fresh.to_dict("records")}

    session = get_session()
    try:
        for a in anomalies:
            target = a["date"]
            fr = fresh_by_date.get(target)
            # Can't resolve if fresh lacks the date or fresh is also anomalous there.
            if fr is None or target in fresh_bad_dates:
                report["unresolved"].append(str(target))
                continue

            bar = (
                session.query(PriceBar)
                .filter(PriceBar.ticker == ticker, PriceBar.date == target)
                .first()
            )
            if bar is None:
                report["unresolved"].append(str(target))
                continue

            # Overwrite the bad stored bar with the fresh (clean) values.
            bar.open = float(fr["Open"])
            bar.high = float(fr["High"])
            bar.low = float(fr["Low"])
            bar.close = float(fr["Close"])
            bar.volume = int(fr["Volume"]) if fr.get("Volume") is not None else bar.volume
            report["corrected"] += 1

        session.commit()
    finally:
        session.close()

    return report


def run_validation(tickers=None, auto_correct=True) -> dict:
    """Validate (and auto-correct) every stored ticker; print a summary.

    Designed to run in the daily scheduler AFTER prices are saved and BEFORE the
    screener, so a corrupt bar never manufactures a fake flag. Clean tickers (the
    vast majority) cost only a DB read + in-memory checks; a re-fetch happens ONLY
    when an anomaly is actually found.
    """
    if tickers is None:
        try:
            from data.db_utils import get_all_stored_tickers
        except ImportError:  # pragma: no cover
            from db_utils import get_all_stored_tickers  # type: ignore
        tickers = get_all_stored_tickers()

    flagged = []
    total_anomalies = 0
    total_corrected = 0

    for ticker in tickers:
        try:
            r = validate_ticker(ticker, auto_correct=auto_correct)
        except Exception as err:  # noqa: BLE001 - one bad ticker can't abort the sweep
            print(f"⚠️  validate_ticker failed for {ticker}: {err}")
            continue
        if r["anomalies"] > 0:
            flagged.append(r)
            total_anomalies += r["anomalies"]
            total_corrected += r["corrected"]

    print("=== Data validation ===")
    print(f"Tickers checked:      {len(tickers)}")
    print(f"Anomalous bars found: {total_anomalies} (across {len(flagged)} tickers)")
    print(f"Auto-corrected:       {total_corrected}")

    unresolved = [(r["ticker"], r["unresolved"]) for r in flagged if r["unresolved"]]
    if unresolved:
        print("⚠️  Unresolved (re-fetch did not fix — review these):")
        for tk, dates in unresolved:
            print(f"     {tk}: {dates}")
    elif flagged:
        print("All detected anomalies were auto-corrected. ✓")

    return {
        "checked": len(tickers),
        "anomalies": total_anomalies,
        "corrected": total_corrected,
        "unresolved": unresolved,
        "flagged": flagged,
    }


if __name__ == "__main__":
    # Quick demo: MU should come back CLEAN (its high price is real, not a glitch).
    print("Spot-check MU (should be clean — high price is real, not an anomaly):")
    print(" ", validate_ticker("MU", auto_correct=False))
    print()
    run_validation()
