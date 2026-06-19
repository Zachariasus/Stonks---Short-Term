"""
analysis/confluence_scorer.py
=============================
Four-engine confluence scorer — the payoff of Phases 3–5.

WHAT THIS DOES
    Calls every analysis engine, converts each output into points on a
    transparent 100-point scorecard, and produces a total score + a
    High/Medium/Low confidence label. This is what the Phase 6 screener runs
    across the whole universe.

    Breakdown: Engine 1 Trend & Momentum (35) + Engine 2 Fundamentals (25)
    + Engine 3 Top-Down/Rotation (25) + Engine 4 Valuation (15) = 100.

SPEED / SAFETY
    DB reads only — NO network calls. Market-wide pieces (sector rankings, macro
    phase) are cached so scoring many stocks doesn't recompute them. Each engine
    is wrapped in its own try/except: a failing engine scores 0 and is noted,
    never crashing the whole score.
"""

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.beat_raise_tracker import score_beat_raise
    from analysis.entry_signals import get_entry_signals
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.macro_cycle import classify_cycle_phase, get_macro_snapshot
    from analysis.margin_trends import analyze_margin_trend
    from analysis.market_regime import get_market_regime
    from analysis.price_target import (
        calculate_price_target,
        calculate_reward_risk,
        calculate_short_reward_risk,
        calculate_short_target,
    )
    from analysis.relative_strength import get_rs_profile, get_rs_rating
    from analysis.sector_ranker import rank_sectors
    from analysis.stage_classifier import classify_stage
    from analysis.valuation import assess_valuation_room
    from analysis.volume_analysis import get_volume_profile
    from data.database import TickerUniverse, get_session
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.beat_raise_tracker import score_beat_raise
    from analysis.entry_signals import get_entry_signals
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.macro_cycle import classify_cycle_phase, get_macro_snapshot
    from analysis.margin_trends import analyze_margin_trend
    from analysis.market_regime import get_market_regime
    from analysis.price_target import (
        calculate_price_target,
        calculate_reward_risk,
        calculate_short_reward_risk,
        calculate_short_target,
    )
    from analysis.relative_strength import get_rs_profile, get_rs_rating
    from analysis.sector_ranker import rank_sectors
    from analysis.stage_classifier import classify_stage
    from analysis.valuation import assess_valuation_room
    from analysis.volume_analysis import get_volume_profile
    from data.database import TickerUniverse, get_session

# Max points per engine (for the "engines firing" 60% threshold).
ENGINE_MAX = {"e1": 35, "e2": 25, "e3": 25, "e4": 15}

# Cache the market-wide macro phase (same for every stock; computed once).
_macro_cache = None


def _get_macro_phase():
    """Cached macro classification (non-printing, computed once per process)."""
    global _macro_cache
    if _macro_cache is None:
        try:
            _macro_cache = classify_cycle_phase(get_macro_snapshot())
        except Exception:  # noqa: BLE001
            _macro_cache = {"phase": "Unknown", "favored_sectors": []}
    return _macro_cache


def _lookup_sector_etf(ticker):
    """The stock's sector ETF from TickerUniverse (single-row DB read), or None."""
    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker.strip().upper())
            .first()
        )
        return row.sector_etf if row else None
    finally:
        session.close()


def _resolve_sector_entry(ticker, sector_rankings):
    """Sector-rank entry for a stock (via its sector ETF) OR for a sector ETF itself.

    Uses the PRE-COMPUTED sector_rankings list (passed in by the screener) so we
    never recompute the 11-sector ranking per ticker.
    """
    sector_etf = _lookup_sector_etf(ticker)
    if sector_etf:
        for sector in sector_rankings:
            if sector["etf_ticker"] == sector_etf:
                return sector
    # The ticker might BE a sector ETF (e.g. XLF) — find it directly.
    upper = ticker.strip().upper()
    for sector in sector_rankings:
        if sector["etf_ticker"] == upper:
            return sector
    return None


def _rr_label(rr):
    if rr is None:
        return "n/a"
    if rr >= 3.0:
        return "Excellent"
    if rr >= 2.0:
        return "Good"
    if rr >= 1.5:
        return "Marginal"
    return "Poor"


def _rr_points(rr):
    if rr is None:
        return 0
    if rr >= 3.0:
        return 5
    if rr >= 2.0:
        return 3
    if rr >= 1.5:
        return 1
    return 0


def score_stock(ticker: str, market_context=None) -> dict:
    """Score one ticker 0–100 across all four engines. DB reads only.

    market_context (optional): {"sector_rankings": [...], "cycle_summary": {...}}
    pre-computed ONCE by the screener so market-wide data isn't recomputed per
    ticker. If None, cached internal versions are used — single-ticker calls work
    unchanged (non-breaking).
    """
    ticker = ticker.strip().upper()

    # Resolve market-wide context: use what the screener passed in, else compute
    # (cached) versions here so a standalone score_stock(ticker) still works.
    if market_context is not None:
        sector_rankings = market_context.get("sector_rankings") or rank_sectors()
        cycle_dict = market_context.get("cycle_summary") or _get_macro_phase()
        regime = market_context.get("regime") or get_market_regime()
    else:
        sector_rankings = rank_sectors()      # cached internally
        cycle_dict = _get_macro_phase()        # cached, non-printing
        regime = get_market_regime()           # cached, DB-only (SPY 200-day)
    regime_label = regime.get("regime", "Unknown")

    # ====================================================================
    # GATHER SIGNALS — each engine guarded; safe neutral defaults on failure.
    # ====================================================================
    # --- Engine 1 inputs ---
    stage = None
    try:
        info = classify_stage(ticker)
        stage = info["stage"] if info else None
    except Exception:  # noqa: BLE001
        stage = None

    rs_label = "N/A"
    rs_composite = None
    try:
        prof = get_rs_profile(ticker)
        rs_composite = prof.get("composite_vs_spy")
        rs_label = get_rs_rating(rs_composite)
    except Exception:  # noqa: BLE001
        pass

    volume_label = "N/A"
    try:
        vp = get_volume_profile(ticker)
        if vp:
            volume_label = vp["accumulation_label"]
    except Exception:  # noqa: BLE001
        pass

    long_entry = short_entry = False
    try:
        es = get_entry_signals(ticker)
        long_entry = es["long_breakout"]["triggered"] or es["long_pullback"]["triggered"]
        short_entry = es["short_breakdown"]["triggered"] or es["short_failed_rally"]["triggered"]
    except Exception:  # noqa: BLE001
        pass

    # --- Engine 2 inputs ---
    revision_direction = "Insufficient history"
    try:
        rt = analyze_revision_trend(ticker)
        revision_direction = rt["revision_direction"]
    except Exception:  # noqa: BLE001
        pass

    margin_signal = "No data"
    try:
        mt = analyze_margin_trend(ticker)
        if mt:
            seq = mt["operating_margin_sequential"]
            direction = mt["operating_margin_direction"]
            if seq in ("Consistently Expanding", "Consistently Compressing"):
                margin_signal = seq
            else:
                margin_signal = direction  # Expanding / Stable / Compressing
    except Exception:  # noqa: BLE001
        pass

    beat_pattern = "Insufficient Data"
    try:
        br = score_beat_raise(ticker)
        beat_pattern = br["pattern_label"]
    except Exception:  # noqa: BLE001
        pass

    # --- Engine 3 inputs ---
    sector_rotation_label = "N/A"
    sector_etf = None
    try:
        se = _resolve_sector_entry(ticker, sector_rankings)
        if se:
            sector_rotation_label = se["rotation_label"]
            sector_etf = se["etf_ticker"]
    except Exception:  # noqa: BLE001
        pass

    macro_phase = cycle_dict.get("phase", "Unknown")
    favored = cycle_dict.get("favored_sectors", [])
    if macro_phase == "Unknown":
        macro_fit = "Unknown"
    elif sector_etf and any(f.startswith(sector_etf) for f in favored):
        macro_fit = "Favored"
    else:
        macro_fit = "Not favored"

    # --- Engine 4 inputs ---
    valuation_room = "Unknown"
    try:
        vr = assess_valuation_room(ticker)
        valuation_room = vr["room_to_expand"]
    except Exception:  # noqa: BLE001
        pass

    # R:R is direction-specific (different target + stop side), so it's computed
    # inside each branch below rather than here.
    rr_ratio = None

    # ====================================================================
    # DIRECTION: Stage 4 + weak RS → evaluate as a SHORT candidate instead.
    # ====================================================================
    direction = (
        "Short"
        if (stage == 4 and rs_label in ("Laggard", "Weak Laggard"))
        else "Long"
    )

    if direction == "Long":
        # ---- ENGINE 1: Trend & Momentum (35) ----
        s_stage = {2: 15, 1: 5}.get(stage, 0)
        s_rs = {"Strong Leader": 10, "Leader": 7, "In Line": 4}.get(rs_label, 0)
        s_vol = {"Accumulation": 5, "Mild Accumulation": 3, "Neutral": 1}.get(volume_label, 0)
        s_entry = 5 if long_entry else 0
        e1 = s_stage + s_rs + s_vol + s_entry
        d1 = (
            f"Stage {stage} (+{s_stage}) | RS {rs_label} (+{s_rs}) | "
            f"{volume_label} (+{s_vol}) | "
            f"{'Entry ✓' if long_entry else 'No signal'} (+{s_entry})"
        )

        # ---- ENGINE 2: Fundamentals (25) ----
        s_rev = {"Rising": 10, "Flat": 4, "Falling": 0, "Insufficient history": 5}.get(revision_direction, 5)
        s_marg = {"Consistently Expanding": 8, "Expanding": 6, "Stable": 3}.get(margin_signal, 0)
        s_beat = {
            "Beat-and-Raise Cycle": 7, "Consistent Beater": 5, "Recent Beat": 3,
            "Mixed": 1, "Chronic Misser": 0, "Insufficient Data": 3,
        }.get(beat_pattern, 3)
        e2 = s_rev + s_marg + s_beat
        d2 = (
            f"Revisions {revision_direction} (+{s_rev}) | "
            f"Margin {margin_signal} (+{s_marg}) | {beat_pattern} (+{s_beat})"
        )

        # ---- ENGINE 3: Top-Down / Rotation (25) ----
        s_rot = {"Leading": 15, "Neutral": 8, "Lagging": 0}.get(sector_rotation_label, 0)
        s_macro = {"Favored": 10, "Unknown": 5, "Not favored": 2}.get(macro_fit, 5)
        e3 = s_rot + s_macro
        d3 = f"{sector_rotation_label} sector (+{s_rot}) | Macro {macro_fit} (+{s_macro})"

        # ---- ENGINE 4: Valuation (15) ----
        s_room = {
            "Yes — compressed vs history and peers": 10,
            "Partial — compressed on one dimension": 6,
            "Limited — already extended": 2, "Unknown": 5,
        }.get(valuation_room, 5)
        # Long R:R against a placeholder −8% stop (the grader uses the real ATR stop).
        try:
            pt = calculate_price_target(ticker)
            if pt:
                rr = calculate_reward_risk(ticker, round(pt["current_close"] * 0.92, 2))
                if rr:
                    rr_ratio = rr["rr_ratio"]
        except Exception:  # noqa: BLE001
            pass
        s_rr = _rr_points(rr_ratio)
        e4 = s_room + s_rr
        d4 = (
            f"Room: {valuation_room} (+{s_room}) | "
            f"R:R {_rr_label(rr_ratio)} (+{s_rr}) [placeholder stop]"
        )
        entry_triggered = long_entry
        note = None

    else:
        # ---- SIMPLE SHORT RUBRIC (v1) — award points for bearish signals. ----
        # Deliberately lightweight: enough to flag and rank short candidates,
        # not a full mirror of every long nuance.
        s_stage = 15 if stage == 4 else 0
        s_rs = {"Weak Laggard": 10, "Laggard": 7}.get(rs_label, 0)
        s_vol = {"Distribution": 5, "Mild Distribution": 3, "Neutral": 1}.get(volume_label, 0)
        s_entry = 5 if short_entry else 0
        e1 = s_stage + s_rs + s_vol + s_entry
        d1 = (
            f"Stage {stage} (+{s_stage}) | RS {rs_label} (+{s_rs}) | "
            f"{volume_label} (+{s_vol}) | "
            f"{'Short entry ✓' if short_entry else 'No short signal'} (+{s_entry})"
        )

        s_rev = {"Falling": 10, "Flat": 4, "Rising": 0, "Insufficient history": 5}.get(revision_direction, 5)
        s_marg = {"Consistently Compressing": 8, "Compressing": 6, "Stable": 3}.get(margin_signal, 0)
        s_beat = {
            "Chronic Misser": 7, "Mixed": 5, "Recent Beat": 3,
            "Insufficient Data": 3, "Consistent Beater": 0, "Beat-and-Raise Cycle": 0,
        }.get(beat_pattern, 3)
        e2 = s_rev + s_marg + s_beat
        d2 = (
            f"Revisions {revision_direction} (+{s_rev}) | "
            f"Margin {margin_signal} (+{s_marg}) | {beat_pattern} (+{s_beat})"
        )

        # Top-down for a SHORT: a lagging sector + a RISK-OFF tape. The SHORT
        # playbook's rule #1 is the market-trend filter (shorts need the tide), so
        # the macro slot here is driven by the SPY-200-day regime — not the
        # FRED cycle phase (which is often dark without a key).
        s_rot = {"Lagging": 15, "Neutral": 8, "Leading": 0}.get(sector_rotation_label, 0)
        s_macro = {"Risk-Off": 10, "Neutral": 5, "Risk-On": 0}.get(regime_label, 5)
        e3 = s_rot + s_macro
        d3 = f"{sector_rotation_label} sector (+{s_rot}) | {regime_label} market (+{s_macro})"

        # For shorts, an ALREADY-EXTENDED multiple is the bearish-favorable case.
        s_room = {
            "Limited — already extended": 10,
            "Partial — compressed on one dimension": 6,
            "Unknown": 5,
            "Yes — compressed vs history and peers": 2,
        }.get(valuation_room, 5)
        # Real short R:R now: a downside (prior-support / measured-move) target vs a
        # placeholder +8% stop ABOVE entry (the grader uses the real ATR stop).
        try:
            pt_s = calculate_short_target(ticker)
            if pt_s:
                rr_s = calculate_short_reward_risk(ticker, round(pt_s["current_close"] * 1.08, 2))
                if rr_s:
                    rr_ratio = rr_s["rr_ratio"]
        except Exception:  # noqa: BLE001
            pass
        s_rr = _rr_points(rr_ratio)
        e4 = s_room + s_rr
        d4 = (
            f"Room: {valuation_room} (+{s_room}) | "
            f"R:R {_rr_label(rr_ratio)} (+{s_rr}) [placeholder stop]"
        )
        entry_triggered = short_entry
        note = (
            "Potential SHORT candidate (Stage 4 + weak RS). "
            f"Market regime: {regime_label}"
            + (" — shorting is an uphill fight in a strong bull tape." if regime_label == "Risk-On" else "")
        )

    total = e1 + e2 + e3 + e4

    # Engines "firing" = scoring ≥ 60% of their max.
    engines_firing = sum(
        pts >= 0.6 * mx
        for pts, mx in zip((e1, e2, e3, e4), (35, 25, 25, 15))
    )

    if total >= 70 and engines_firing >= 3:
        confidence = "High"
    elif total >= 50 or engines_firing >= 2:
        confidence = "Medium"
    else:
        confidence = "Low"

    return {
        "ticker": ticker,
        "direction": direction,
        "engine_1_pts": e1, "engine_2_pts": e2, "engine_3_pts": e3, "engine_4_pts": e4,
        "total_score": total,
        "stage": stage, "rs_label": rs_label, "volume_label": volume_label,
        "entry_triggered": entry_triggered,
        "revision_direction": revision_direction, "margin_direction": margin_signal,
        "beat_pattern": beat_pattern,
        "sector_rotation_label": sector_rotation_label, "sector_etf": sector_etf,
        "macro_fit": macro_fit, "market_regime": regime_label,
        "valuation_room": valuation_room, "rr_ratio": rr_ratio,
        "engines_firing": engines_firing, "confidence_label": confidence,
        "engine_1_detail": d1, "engine_2_detail": d2,
        "engine_3_detail": d3, "engine_4_detail": d4,
        "note": note,
    }


def get_confluence_summary(ticker: str) -> dict:
    """Print a transparent scorecard for a ticker and return the score dict."""
    s = score_stock(ticker)

    bar = "═" * 55
    print(bar)
    print(
        f"{s['ticker']}  |  Score: {s['total_score']}/100  |  "
        f"{s['confidence_label']} confidence  |  {s['direction']}"
    )
    print(bar)
    print(f"Engine 1 — Trend & Momentum:    {s['engine_1_pts']}/35")
    print(f"  {s['engine_1_detail']}")
    print(f"Engine 2 — Fundamentals:        {s['engine_2_pts']}/25")
    print(f"  {s['engine_2_detail']}")
    print(f"Engine 3 — Top-Down:            {s['engine_3_pts']}/25")
    print(f"  {s['engine_3_detail']}")
    print(f"Engine 4 — Valuation:           {s['engine_4_pts']}/15")
    print(f"  {s['engine_4_detail']}")
    if s["note"]:
        print(f"  ⚑ {s['note']}")
    print("─" * 55)

    return s


if __name__ == "__main__":
    for ticker in ["AAPL", "MSFT", "XLF"]:
        get_confluence_summary(ticker)
        print()
