"""
grader/analysis_pipeline.py
===========================
Single-stock analysis pipeline — the first half of the grader (Phase 7).

WHAT THIS DOES
    Where the screener does a fast pass to RANK hundreds of stocks, the grader
    does a DEEP pass on ONE stock: it calls every engine we've built, assembles
    the complete picture into one structured "analysis packet", and formats a
    full human-readable report. In Step 2 that packet/report is handed to the
    Claude API to write the letter grade and narrative reasoning.

RESILIENCE
    Every engine call is wrapped in its own try/except — one failing section
    stores None and is noted, but never crashes the whole report.
"""

from datetime import date

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.confluence_scorer import score_stock
    from analysis.entry_signals import get_entry_signals
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.macro_cycle import classify_cycle_phase, get_macro_snapshot
    from analysis.margin_trends import analyze_margin_trend
    from analysis.moving_averages import are_mas_stacked_bullish, get_ma_snapshot
    from analysis.price_target import calculate_price_target
    from analysis.relative_strength import get_rs_profile, get_rs_rating
    from analysis.beat_raise_tracker import score_beat_raise
    from analysis.sector_ranker import get_sector_rank, get_top_down_tilt
    from analysis.stage_classifier import classify_stage
    from analysis.valuation import assess_valuation_room
    from analysis.volume_analysis import get_volume_profile
    from analysis.earnings_calendar import get_days_to_earnings, get_earnings_flag
    from data.database import EarningsCalendar, TickerUniverse, get_session
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.confluence_scorer import score_stock
    from analysis.entry_signals import get_entry_signals
    from analysis.estimate_revisions import analyze_revision_trend
    from analysis.macro_cycle import classify_cycle_phase, get_macro_snapshot
    from analysis.margin_trends import analyze_margin_trend
    from analysis.moving_averages import are_mas_stacked_bullish, get_ma_snapshot
    from analysis.price_target import calculate_price_target
    from analysis.relative_strength import get_rs_profile, get_rs_rating
    from analysis.beat_raise_tracker import score_beat_raise
    from analysis.sector_ranker import get_sector_rank, get_top_down_tilt
    from analysis.stage_classifier import classify_stage
    from analysis.valuation import assess_valuation_room
    from analysis.volume_analysis import get_volume_profile
    from analysis.earnings_calendar import get_days_to_earnings, get_earnings_flag
    from data.database import EarningsCalendar, TickerUniverse, get_session
    from data.db_reader import get_price_bars


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe(label, fn, *args, **kwargs):
    """Run an engine call; on any error print a note and return None."""
    try:
        return fn(*args, **kwargs)
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  analysis_pipeline: {label} failed — {err}")
        return None


def _identity_lookup(ticker):
    """(company_name, sector, sector_etf) from TickerUniverse, or (None, None, None)."""
    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker)
            .first()
        )
        if row:
            return row.company_name, row.sector, row.sector_etf
        return None, None, None
    finally:
        session.close()


def _next_earnings_date(ticker):
    """Most forward stored next_earnings_date for a ticker, or None."""
    session = get_session()
    try:
        row = (
            session.query(EarningsCalendar)
            .filter(EarningsCalendar.ticker == ticker)
            .order_by(EarningsCalendar.next_earnings_date.desc())
            .first()
        )
        return row.next_earnings_date if row else None
    finally:
        session.close()


def _margin_descriptor(direction, sequential):
    """Prefer the 'Consistently …' sequential read when present, else the direction."""
    if sequential in ("Consistently Expanding", "Consistently Compressing"):
        return sequential
    return direction


def _count_fields(obj):
    """Count leaf fields in the (possibly nested) packet."""
    if isinstance(obj, dict):
        return sum(_count_fields(v) for v in obj.values())
    return 1


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_full_analysis(ticker: str) -> dict:
    """Assemble the complete analysis packet for one stock from every engine."""
    ticker = ticker.strip().upper()

    # ---- SECTION 1 — Identity & Price ----
    company_name, sector, sector_etf = _identity_lookup(ticker)
    bars = _safe("price_bars", get_price_bars, ticker, days=5)
    current_close = (
        round(float(bars["Close"].iloc[-1]), 2)
        if bars is not None and not bars.empty
        else None
    )
    section_1 = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "sector_etf": sector_etf,
        "current_close": current_close,
        "analysis_date": str(date.today()),
    }

    # ---- SECTION 2 — Trend & Momentum ----
    stage_info = _safe("classify_stage", classify_stage, ticker) or {}
    ma = _safe("ma_snapshot", get_ma_snapshot, ticker) or {}
    rs = _safe("rs_profile", get_rs_profile, ticker) or {}
    vp = _safe("volume_profile", get_volume_profile, ticker) or {}
    es = _safe("entry_signals", get_entry_signals, ticker) or {}

    def _trig(key):
        return (es.get(key) or {}).get("triggered") if es else None

    section_2 = {
        "stage": stage_info.get("stage"),
        "stage_label": stage_info.get("stage_label"),
        "ma_snapshot": {
            "close": ma.get("close"), "sma_50": ma.get("sma_50"),
            "sma_100": ma.get("sma_100"), "sma_200": ma.get("sma_200"),
            "sma_30w": ma.get("sma_30w"),
            "pct_above_sma50": ma.get("pct_above_sma50"),
            "pct_above_sma200": ma.get("pct_above_sma200"),
        },
        "mas_stacked_bullish": _safe("mas_stacked", are_mas_stacked_bullish, ticker),
        "rs_vs_spy": {
            "3m": rs.get("rs_3m_vs_spy"), "6m": rs.get("rs_6m_vs_spy"),
            "12m": rs.get("rs_12m_vs_spy"), "composite": rs.get("composite_vs_spy"),
            "label": get_rs_rating(rs.get("composite_vs_spy")) if rs else "N/A",
        },
        "rs_vs_sector": {
            "3m": rs.get("rs_3m_vs_sector"), "6m": rs.get("rs_6m_vs_sector"),
            "12m": rs.get("rs_12m_vs_sector"), "composite": rs.get("composite_vs_sector"),
            "label": get_rs_rating(rs.get("composite_vs_sector")) if rs else "N/A",
            "sector_etf_used": rs.get("sector_etf_used"),
        },
        "volume_profile": {
            "up_down_ratio": vp.get("up_down_vol_ratio"),
            "obv_direction": vp.get("obv_direction"),
            "vol_trend": vp.get("vol_trend"),
            "label": vp.get("accumulation_label"),
        },
        "entry_signals": {
            "breakout": _trig("long_breakout"),
            "pullback": _trig("long_pullback"),
            "breakdown": _trig("short_breakdown"),
            "failed_rally": _trig("short_failed_rally"),
            "any_triggered": es.get("any_signal_triggered"),
        },
    }

    # ---- SECTION 3 — Fundamental Trajectory ----
    rev = _safe("revisions", analyze_revision_trend, ticker) or {}
    margins = _safe("margins", analyze_margin_trend, ticker) or {}
    beat = _safe("beat_raise", score_beat_raise, ticker) or {}
    section_3 = {
        "revision_direction": rev.get("revision_direction"),
        "revision_pct_change": rev.get("revision_pct_change"),
        "num_revision_snapshots": rev.get("num_snapshots"),
        "latest_forward_eps": rev.get("latest_forward_eps"),
        "gross_margin_latest": margins.get("gross_margin_latest"),
        "gross_margin_earliest": margins.get("gross_margin_earliest"),
        "gross_margin_change_pp": margins.get("gross_margin_change_pp"),
        "gross_margin_direction": _margin_descriptor(
            margins.get("gross_margin_direction"), margins.get("gross_margin_sequential")
        ),
        "operating_margin_latest": margins.get("operating_margin_latest"),
        "operating_margin_earliest": margins.get("operating_margin_earliest"),
        "operating_margin_change_pp": margins.get("operating_margin_change_pp"),
        "operating_margin_direction": _margin_descriptor(
            margins.get("operating_margin_direction"),
            margins.get("operating_margin_sequential"),
        ),
        "beat_rate": beat.get("beat_rate"),
        "avg_surprise_pct": beat.get("avg_surprise_pct"),
        "consecutive_beats": beat.get("consecutive_beats"),
        "beat_raise_pattern_label": beat.get("pattern_label"),
        "next_earnings_date": _safe("next_earnings", _next_earnings_date, ticker),
        "days_to_earnings": _safe("days_to_earnings", get_days_to_earnings, ticker),
        "earnings_flag": _safe("earnings_flag", get_earnings_flag, ticker),
    }

    # ---- SECTION 4 — Top-Down & Rotation ----
    sr = _safe("sector_rank", get_sector_rank, ticker) or {}
    tilt = _safe("top_down_tilt", get_top_down_tilt) or {}
    macro = _safe("macro_phase", lambda: classify_cycle_phase(get_macro_snapshot())) or {}
    cycle_phase = macro.get("phase", "Unknown")
    favored = macro.get("favored_sectors", [])
    if cycle_phase == "Unknown":
        macro_fit = "Unknown"
    elif sector_etf and any(str(f).startswith(sector_etf) for f in favored):
        macro_fit = True
    else:
        macro_fit = False
    section_4 = {
        "sector_rank": sr.get("rank"),
        "sector_rotation_label": sr.get("rotation_label"),
        "top_3_sectors": tilt.get("top_3_sectors"),
        "bottom_3_sectors": tilt.get("bottom_3_sectors"),
        "market_breadth_label": tilt.get("breadth_label"),
        "leading_count": tilt.get("leading_count"),
        "cycle_phase": cycle_phase,
        "cycle_favored_sectors": favored,
        "macro_fit": macro_fit,
    }

    # ---- SECTION 5 — Valuation & Target ----
    val = _safe("valuation", assess_valuation_room, ticker) or {}
    pt = _safe("price_target", calculate_price_target, ticker) or {}
    section_5 = {
        "forward_pe": val.get("forward_pe"),
        "peg_ratio": val.get("peg_ratio"),
        "fcf_yield": val.get("fcf_yield"),
        "ev_to_ebitda": val.get("ev_to_ebitda"),
        "multiple_vs_history": val.get("multiple_vs_history"),
        "multiple_vs_peers": val.get("multiple_vs_peers"),
        "valuation_room": val.get("room_to_expand"),
        "sector_median_pe": val.get("sector_median_pe"),
        "forward_pe_percentile": val.get("forward_pe_percentile"),
        "target_price": pt.get("target_price"),
        "target_multiple": pt.get("target_multiple"),
        "upside_pct": pt.get("upside_pct"),
        "target_rationale": pt.get("rationale"),
    }

    # ---- SECTION 6 — Confluence Score ----
    score = _safe("confluence_score", score_stock, ticker) or {}
    section_6 = {
        "total_score": score.get("total_score"),
        "confidence_label": score.get("confidence_label"),
        "direction": score.get("direction"),
        "engine_1_pts": score.get("engine_1_pts"),
        "engine_2_pts": score.get("engine_2_pts"),
        "engine_3_pts": score.get("engine_3_pts"),
        "engine_4_pts": score.get("engine_4_pts"),
        "engines_firing": score.get("engines_firing"),
    }

    return {
        "section_1_identity": section_1,
        "section_2_trend": section_2,
        "section_3_fundamental": section_3,
        "section_4_topdown": section_4,
        "section_5_valuation": section_5,
        "section_6_confluence": section_6,
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def _money(v):
    return f"${v:.2f}" if v is not None else "—"


def _x(v):
    return f"{v:.1f}x" if v is not None else "—"


def _pp(v):
    return f"{v:+.1f}pp" if v is not None else "n/a"


def _pct(v, decimals=1):
    return f"{v:+.{decimals}f}%" if v is not None else "n/a"


def format_analysis_report(packet: dict) -> str:
    """Render the analysis packet into a structured report string (printed + returned)."""
    s1 = packet["section_1_identity"]
    s2 = packet["section_2_trend"]
    s3 = packet["section_3_fundamental"]
    s4 = packet["section_4_topdown"]
    s5 = packet["section_5_valuation"]
    s6 = packet["section_6_confluence"]

    bar = "═" * 52
    lines = [bar]
    lines.append(f"STOCK GRADER REPORT  |  {s1['ticker']} — {s1.get('company_name') or 'Unknown'}")
    lines.append(
        f"{s1.get('sector') or '?'} ({s1.get('sector_etf') or '?'})  |  "
        f"{_money(s1.get('current_close'))}  |  {s1['analysis_date']}"
    )
    lines.append(bar)

    # --- TREND & MOMENTUM ---
    ma = s2["ma_snapshot"]
    stage = s2.get("stage")
    stage_line = (
        f"Stage {stage} — {s2.get('stage_label')}"
        if stage is not None else "Stage: n/a"
    )
    stacked = "Yes" if s2.get("mas_stacked_bullish") else "No"
    rss = s2["rs_vs_spy"]
    rsec = s2["rs_vs_sector"]
    vol = s2["volume_profile"]
    ent = s2["entry_signals"]
    triggered = [
        name for name, key in
        [("Breakout", "breakout"), ("Pullback", "pullback"),
         ("Breakdown", "breakdown"), ("Failed Rally", "failed_rally")]
        if ent.get(key)
    ]
    obv = vol.get("obv_direction")
    lines.append("")
    lines.append("TREND & MOMENTUM")
    lines.append(f"{stage_line}  |  MAs stacked bullish: {stacked}")
    lines.append(
        f"50d MA: {_money(ma.get('sma_50'))}  ({_pct(ma.get('pct_above_sma50'), 2)} above)  |  "
        f"200d MA: {_money(ma.get('sma_200'))}  ({_pct(ma.get('pct_above_sma200'))} above)"
    )
    lines.append(f"RS vs SPY:    {_pp(rss.get('composite'))} composite  →  {rss.get('label')}")
    lines.append(
        f"RS vs {rsec.get('sector_etf_used') or 'sector'}:   "
        f"{_pp(rsec.get('composite'))} composite  →  {rsec.get('label')}"
    )
    ud = vol.get("up_down_ratio")
    lines.append(
        f"Volume:  {vol.get('label') or 'n/a'}  |  "
        f"Up/Down ratio: {ud:.2f}" if ud is not None else
        f"Volume:  {vol.get('label') or 'n/a'}  |  Up/Down ratio: n/a"
    )
    lines[-1] += f"  |  OBV: {obv.capitalize() if obv else '—'}"
    lines.append(f"Entry signals: {', '.join(triggered) if triggered else 'None triggered'}")

    # --- FUNDAMENTAL TRAJECTORY ---
    lines.append("")
    lines.append("FUNDAMENTAL TRAJECTORY")
    rev_dir = s3.get("revision_direction")
    n_snap = s3.get("num_revision_snapshots")
    if rev_dir == "Insufficient history":
        rev_str = f"Insufficient history ({n_snap} snapshot{'s' if n_snap != 1 else ''})"
    elif rev_dir is not None:
        rev_str = f"{rev_dir} ({_pct(s3.get('revision_pct_change'))}, {n_snap} snapshots)"
    else:
        rev_str = "n/a"
    lines.append(f"Estimate revisions: {rev_str}")

    def _margin_line(label, earliest, latest, change, descriptor):
        if latest is None:
            return f"{label} n/a"
        return (
            f"{label} {earliest:.1f}% → {latest:.1f}%  "
            f"({_pp(change)}, {descriptor})"
        )

    lines.append(_margin_line(
        "Operating margin: ", s3.get("operating_margin_earliest"),
        s3.get("operating_margin_latest"), s3.get("operating_margin_change_pp"),
        s3.get("operating_margin_direction"),
    ))
    lines.append(_margin_line(
        "Gross margin:     ", s3.get("gross_margin_earliest"),
        s3.get("gross_margin_latest"), s3.get("gross_margin_change_pp"),
        s3.get("gross_margin_direction"),
    ))
    beat_pattern = s3.get("beat_raise_pattern_label")
    if beat_pattern and beat_pattern != "Insufficient Data":
        lines.append(
            f"Beat history:       {beat_pattern}  |  "
            f"{s3.get('consecutive_beats')} consecutive  |  "
            f"avg {_pct(s3.get('avg_surprise_pct'))}"
        )
    else:
        lines.append("Beat history:       Insufficient data")
    nd = s3.get("next_earnings_date")
    if nd is not None:
        lines.append(
            f"Next earnings:      {nd}  ({s3.get('days_to_earnings')} days)  →  "
            f"{s3.get('earnings_flag')}"
        )
    else:
        lines.append(f"Next earnings:      —  →  {s3.get('earnings_flag')}")

    # --- TOP-DOWN & ROTATION ---
    lines.append("")
    lines.append("TOP-DOWN & ROTATION")
    rank = s4.get("sector_rank")
    rank_str = f"#{rank} of 11  ({s4.get('sector_rotation_label')})" if rank else "—"
    lines.append(f"Sector rank:        {rank_str}")
    lines.append(
        f"Market breadth:     {s4.get('market_breadth_label') or 'n/a'}  "
        f"({s4.get('leading_count')} of 11 sectors leading)"
    )
    if s4.get("cycle_phase") == "Unknown":
        lines.append("Macro cycle:        Unknown  (FRED key not set)")
    else:
        lines.append(f"Macro cycle:        {s4.get('cycle_phase')}  (sector fit: {s4.get('macro_fit')})")

    # --- VALUATION & TARGET ---
    lines.append("")
    lines.append("VALUATION & TARGET")
    lines.append(
        f"Forward P/E: {_x(s5.get('forward_pe'))}  |  "
        f"PEG: {s5.get('peg_ratio') if s5.get('peg_ratio') is not None else '—'}  |  "
        f"FCF yield: {_pct(s5.get('fcf_yield')).lstrip('+') if s5.get('fcf_yield') is not None else '—'}"
    )
    hist = s5.get("multiple_vs_history")
    peers = s5.get("multiple_vs_peers")
    med = s5.get("sector_median_pe")
    peers_str = (
        f"{peers} (median {med:.1f}x)" if peers not in (None, "No peer data") and med is not None
        else (peers or "No peer data")
    )
    lines.append(f"vs history: {hist or 'n/a'}  |  vs peers: {peers_str}")
    tp = s5.get("target_price")
    if tp is not None:
        lines.append(f"Target:  {_money(tp)}  ({s5.get('target_rationale')})")
        lines.append(f"Upside:  {_pct(s5.get('upside_pct'))}")
    else:
        lines.append("Target:  —  (no target derivable)")
        lines.append("Upside:  —")

    # --- CONFLUENCE SCORE ---
    lines.append("")
    lines.append("CONFLUENCE SCORE")
    lines.append(
        f"{s6.get('total_score')} / 100  |  "
        f"{s6.get('confidence_label')} confidence  |  {s6.get('direction')}"
    )
    lines.append(
        f"E1: {s6.get('engine_1_pts')}/35  E2: {s6.get('engine_2_pts')}/25  "
        f"E3: {s6.get('engine_3_pts')}/25  E4: {s6.get('engine_4_pts')}/15"
    )
    lines.append(bar)

    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    packet = run_full_analysis("AAPL")
    print()
    format_analysis_report(packet)
    print(f"\nAnalysis packet: {_count_fields(packet)} fields collected")
