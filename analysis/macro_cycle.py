"""
analysis/macro_cycle.py
=======================
Business-cycle phase indicator — second module of the Top-Down & Valuation engine.

WHAT THIS DOES
    Reads a few FRED macro series (yield curve, credit spreads, industrial
    production, unemployment) and classifies which phase of the business cycle
    we're in — because sector rotation follows the cycle.

HOW IT FITS IN
    Provides the macro backdrop for the Phase 6 confidence scorer: it tells us
    which sectors the cycle FAVORS, which the sector ranker (Step 1) can then
    confirm. Degrades gracefully when FRED_API_KEY isn't set.
"""

# FRED series we pull, with what each measures and why it matters.
FRED_SERIES = {
    # 10Y minus 2Y Treasury yield spread. Positive = normal upward-sloping curve;
    # negative ("inverted") has preceded most recessions. A curve STEEPENING up
    # from inversion is the classic early-recovery tell.
    "yield_curve": "T10Y2Y",
    # Credit spreads (option-adjusted spread, in %). Tight/falling spreads = risk
    # appetite returning (risk-on); widening spreads = stress building (risk-off).
    # NOTE: BAMLH0A0HYM2 is actually the HIGH-YIELD OAS (typically ~3–6%), while
    # the thresholds below (1.5 / 2.0) are sized for INVESTMENT-GRADE levels. This
    # mismatch is flagged to revisit once a live FRED key is in place — either
    # switch to an IG series (e.g. BAMLC0A0CM) or retune the thresholds for HY.
    "credit_spreads": "BAMLH0A0HYM2",
    # Industrial Production Index. Broad gauge of real economic output: rising =
    # expansion, falling = contraction.
    "indus_production": "INDPRO",
    # Unemployment rate (%). A lagging confirmer: rising unemployment corroborates
    # late-cycle / contraction.
    "unemployment": "UNRATE",
}

# Import the FRED key. PYTHONPATH=<project root> makes the first import work; the
# fallback inserts the project root so the file runs standalone too.
try:
    from data.config import FRED_API_KEY
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import FRED_API_KEY


def _key_is_set() -> bool:
    """True only if a real FRED key is configured (not missing/placeholder)."""
    return bool(FRED_API_KEY) and FRED_API_KEY != "your_key_here"


def fetch_fred_series(series_id: str, periods: int = 24):
    """Fetch the most recent `periods` MONTHLY observations of a FRED series.

    Daily series (yield curve, spreads) are resampled to month-end so all four
    series align on a monthly cadence.

    Returns:
        A pandas Series, or None (with a warning) if the key isn't set or the
        request fails.
    """
    if not _key_is_set():
        print(
            "⚠️  fetch_fred_series: FRED_API_KEY not set in .env — skipping macro "
            "data (add your free key to enable cycle detection)."
        )
        return None

    try:
        from fredapi import Fred  # imported lazily so the module loads without it

        fred = Fred(api_key=FRED_API_KEY)
        series = fred.get_series(series_id)
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  fetch_fred_series: FRED request failed for {series_id}: {err}")
        return None

    if series is None or len(series) == 0:
        print(f"⚠️  fetch_fred_series: no data returned for {series_id}.")
        return None

    # Normalize to month-end (last obs per month) so daily + monthly series align.
    monthly = series.dropna().resample("ME").last().dropna()
    return monthly.tail(periods)


def get_macro_snapshot():
    """Build a {series: {latest, prev, change, direction}} snapshot from FRED.

    Returns None (with a message) if the FRED key isn't set or data is missing.
    """
    if not _key_is_set():
        print("ℹ️  get_macro_snapshot: FRED key not set — no macro snapshot available.")
        return None

    snapshot = {}
    for name, series_id in FRED_SERIES.items():
        series = fetch_fred_series(series_id)
        if series is None or len(series) < 4:
            print(f"⚠️  get_macro_snapshot: insufficient data for {name} ({series_id}).")
            return None

        latest = float(series.iloc[-1])
        prev = float(series.iloc[-4])  # ~3 months ago (monthly cadence)

        if name == "indus_production":
            # Production is an index level → use PERCENT change, ±0.5% band.
            change = round((latest - prev) / prev * 100, 2) if prev else 0.0
            if change > 0.5:
                direction = "Rising"
            elif change < -0.5:
                direction = "Falling"
            else:
                direction = "Stable"
        else:
            # Rates / spreads / unemployment → percentage-POINT change, ±0.1 band.
            change = round(latest - prev, 2)
            if change > 0.1:
                direction = "Rising"
            elif change < -0.1:
                direction = "Falling"
            else:
                direction = "Stable"

        snapshot[name] = {
            "latest": round(latest, 2),
            "prev": round(prev, 2),
            "change": change,
            "direction": direction,
        }

    return snapshot


# Sectors the cycle favors in each phase (as "ETF (Name)" strings).
_FAVORED = {
    "Early Recovery": ["XLY (Consumer Discretionary)", "XLF (Financials)", "XLI (Industrials)"],
    "Mid Cycle": ["XLK (Technology)", "XLI (Industrials)"],
    "Late Cycle": ["XLE (Energy)", "XLB (Materials)", "XLP (Consumer Staples)"],
    "Contraction": ["XLP (Consumer Staples)", "XLU (Utilities)", "XLV (Health Care)"],
    "Uncertain": [],
}


def _confidence(phase, yc_latest, yc_dir, cs_latest, cs_dir, ip_dir):
    """High if 3+ of the phase's signals agree, else Low."""
    agree = 0
    if phase == "Early Recovery":
        agree += yc_dir == "Rising"
        agree += cs_dir == "Falling"
        agree += ip_dir == "Rising"
    elif phase == "Mid Cycle":
        agree += yc_latest > 0
        agree += cs_latest < 1.5
        agree += ip_dir in ("Rising", "Stable")
    elif phase == "Late Cycle":
        agree += yc_latest < 0.5
        agree += cs_dir == "Rising"
        agree += ip_dir in ("Stable", "Falling")
    elif phase == "Contraction":
        agree += yc_latest < 0
        agree += cs_latest > 2.0
        agree += ip_dir in ("Stable", "Falling")
        agree += cs_dir == "Rising"
    return "High" if agree >= 3 else "Low"


def classify_cycle_phase(snapshot) -> dict:
    """Classify the business-cycle phase from a macro snapshot.

    Evaluation order is by precedence, NOT the cycle's natural order: we check the
    most extreme / most specific state first so, e.g., a true Contraction (inverted
    curve + wide spreads) isn't mis-caught by the looser Late-Cycle rule.
    """
    if snapshot is None:
        return {
            "phase": "Unknown",
            "favored_sectors": [],
            "phase_confidence": "N/A",
            "yield_curve_latest": None,
            "credit_spreads_latest": None,
            "indus_production_direction": None,
            "unemployment_direction": None,
        }

    yc_latest = snapshot["yield_curve"]["latest"]
    yc_dir = snapshot["yield_curve"]["direction"]
    cs_latest = snapshot["credit_spreads"]["latest"]
    cs_dir = snapshot["credit_spreads"]["direction"]
    ip_dir = snapshot["indus_production"]["direction"]
    un_dir = snapshot["unemployment"]["direction"]

    # --- Phase rules (checked most-extreme/specific first) ---
    #  Contraction : inverted curve AND wide spreads → recession / risk-off.
    #  Late Cycle  : curve flat/flattening OR spreads widening, output stalling.
    #  Early Recov.: curve steepening AND spreads tightening AND output rising.
    #  Mid Cycle   : positive curve, tight spreads, output rising/stable.
    if yc_latest < 0 and cs_latest > 2.0:
        phase = "Contraction"
    elif (yc_latest < 0.5 or cs_dir == "Rising") and ip_dir in ("Stable", "Falling"):
        phase = "Late Cycle"
    elif yc_dir == "Rising" and cs_dir == "Falling" and ip_dir == "Rising":
        phase = "Early Recovery"
    elif yc_latest > 0 and cs_latest < 1.5 and ip_dir in ("Rising", "Stable"):
        phase = "Mid Cycle"
    else:
        phase = "Uncertain"

    confidence = (
        "Low" if phase == "Uncertain"
        else _confidence(phase, yc_latest, yc_dir, cs_latest, cs_dir, ip_dir)
    )

    return {
        "phase": phase,
        "favored_sectors": _FAVORED.get(phase, []),
        "phase_confidence": confidence,
        "yield_curve_latest": yc_latest,
        "credit_spreads_latest": cs_latest,
        "indus_production_direction": ip_dir,
        "unemployment_direction": un_dir,
    }


def get_cycle_summary() -> dict:
    """Print the cycle block and return the classification (or a safe placeholder)."""
    if not _key_is_set():
        placeholder = {
            "phase": "Unknown",
            "favored_sectors": [],
            "phase_confidence": "N/A",
            "note": "Set FRED_API_KEY in .env to enable macro cycle detection",
        }
        print("=== Business Cycle Indicator ===")
        print("Phase:            Unknown")
        print(f"Note:             {placeholder['note']}")
        return placeholder

    snapshot = get_macro_snapshot()
    result = classify_cycle_phase(snapshot)

    print("=== Business Cycle Indicator ===")
    print(f"Phase:            {result['phase']}  (confidence: {result['phase_confidence']})")
    favored = ", ".join(result["favored_sectors"]) if result["favored_sectors"] else "—"
    print(f"Favored sectors:  {favored}")

    if snapshot is not None:
        yc = snapshot["yield_curve"]
        cs = snapshot["credit_spreads"]
        ip = snapshot["indus_production"]
        un = snapshot["unemployment"]
        print()
        print(f"Yield curve (10Y-2Y):  {yc['latest']:+.2f}pp  →  {yc['direction']}")
        print(f"Credit spreads:         {cs['latest']:.2f}pp  →  {cs['direction']}")
        print(f"Industrial production:  {ip['latest']:.1f}   →  {ip['direction']}")
        print(f"Unemployment:           {un['latest']:.1f}%  →  {un['direction']}")

    return result


if __name__ == "__main__":
    # With no live FRED key, this exercises the graceful placeholder path.
    get_cycle_summary()
