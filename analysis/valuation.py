"""
analysis/valuation.py
=====================
Valuation calculator — third module of the Top-Down & Valuation engine.

WHAT THIS DOES
    Assesses a stock's RE-RATING ROOM — not "is it cheap?" but "does the multiple
    have space to expand as the earnings cycle plays out?". Compares the current
    multiple to the stock's own history and to its sector peers.

DIVISION SAFETY
    We've been burned twice by dividing by values that can be ~0 or negative
    (the RS ratio and the OBV slope). EVERY division here is guarded: if a
    denominator is None / zero / negative where that's meaningless, we return
    None rather than emit a nonsense number. Each guard is commented.

HOW IT FITS IN
    Reads stored fundamentals (db_reader / Fundamentals) and sector peers
    (TickerUniverse). Feeds the Phase 6 confidence scorer's valuation component.
"""

import statistics

import pandas as pd

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.database import Fundamentals, TickerUniverse, get_session
    from data.db_reader import get_latest_fundamentals
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.database import Fundamentals, TickerUniverse, get_session
    from data.db_reader import get_latest_fundamentals


def _clean_num(value):
    """Return a float, or None for missing/NaN values."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Individual ratios (each division guarded)
# ---------------------------------------------------------------------------
def calculate_peg(forward_pe, earnings_growth_yoy):
    """PEG = forward P/E ÷ (earnings growth %).

    Guarded because PEG is only meaningful for POSITIVE growth: zero growth would
    be a divide-by-zero, and NEGATIVE growth produces a negative PEG that's
    economically nonsense (you can't "pay a multiple for growth" that isn't
    there). In both cases we return None instead of a misleading number.
    """
    if forward_pe is None or earnings_growth_yoy is None:
        return None
    if earnings_growth_yoy <= 0:  # zero → div-by-zero; negative → nonsense PEG
        return None
    growth_pct = earnings_growth_yoy * 100  # 0.218 → 21.8
    return round(forward_pe / growth_pct, 2)


def calculate_fcf_yield(price_to_fcf):
    """FCF yield (%) = 1 / (price-to-FCF) × 100.

    Guarded: a None or non-positive P/FCF means no positive free cash flow, so
    the yield is undefined / meaningless (and zero would divide-by-zero).
    """
    if price_to_fcf is None or price_to_fcf <= 0:
        return None
    return round(1 / price_to_fcf * 100, 2)


# ---------------------------------------------------------------------------
# Snapshot of current multiples
# ---------------------------------------------------------------------------
def get_valuation_snapshot(ticker: str):
    """Current valuation multiples + derived PEG / FCF-yield, from stored fundamentals."""
    ticker = ticker.strip().upper()
    fundamentals = get_latest_fundamentals(ticker)
    if fundamentals is None:
        print(f"⚠️  get_valuation_snapshot: no stored fundamentals for '{ticker}'.")
        return None

    forward_pe = _clean_num(fundamentals.get("forward_pe"))
    trailing_pe = _clean_num(fundamentals.get("trailing_pe"))
    ev_to_ebitda = _clean_num(fundamentals.get("ev_to_ebitda"))
    price_to_fcf = _clean_num(fundamentals.get("price_to_fcf"))
    earnings_growth_yoy = _clean_num(fundamentals.get("earnings_growth_yoy"))
    revenue_growth_yoy = _clean_num(fundamentals.get("revenue_growth_yoy"))

    return {
        "forward_pe": forward_pe,
        "trailing_pe": trailing_pe,
        "ev_to_ebitda": ev_to_ebitda,
        "price_to_fcf": price_to_fcf,
        "fcf_yield": calculate_fcf_yield(price_to_fcf),
        "peg_ratio": calculate_peg(forward_pe, earnings_growth_yoy),
        "earnings_growth_yoy": earnings_growth_yoy,
        "revenue_growth_yoy": revenue_growth_yoy,
    }


# ---------------------------------------------------------------------------
# Historical range (where does today's multiple sit vs the stock's own history?)
# ---------------------------------------------------------------------------
def _range_stats(values, current):
    """min/max/avg/current/percentile for a metric's history.

    percentile: 0% = at the historic low, 100% = at the historic high. Computed
    as (count of values strictly below current) / (n − 1) × 100 — the (n−1)
    denominator is safe because we require ≥3 values upstream.
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 3 or current is None:
        return None
    below = sum(1 for v in vals if v < current)
    percentile = round(below / (len(vals) - 1) * 100)  # n−1 ≥ 2, safe
    return {
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
        "avg": round(sum(vals) / len(vals), 1),
        "current": round(current, 1),
        "percentile": percentile,
    }


def get_historical_valuation_range(ticker: str):
    """Forward-P/E and EV/EBITDA ranges across ALL stored fundamentals snapshots.

    Returns None if fewer than 3 snapshots exist. This improves over time as the
    scheduler accumulates a daily fundamentals snapshot per ticker — early on,
    with just a few rows, treat it as directional only.
    """
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        rows = (
            session.query(Fundamentals)
            .filter(Fundamentals.ticker == ticker)
            .order_by(Fundamentals.fetched_date.asc())
            .all()
        )
        pe_values = [r.forward_pe for r in rows if r.forward_pe is not None]
        ev_values = [r.ev_to_ebitda for r in rows if r.ev_to_ebitda is not None]
        num = len(rows)
    finally:
        session.close()

    if num < 3:
        return None

    pe_stats = _range_stats(pe_values, pe_values[-1] if pe_values else None)
    ev_stats = _range_stats(ev_values, ev_values[-1] if ev_values else None)

    return {
        "forward_pe_avg": pe_stats["avg"] if pe_stats else None,
        "forward_pe_min": pe_stats["min"] if pe_stats else None,
        "forward_pe_max": pe_stats["max"] if pe_stats else None,
        "forward_pe_percentile": pe_stats["percentile"] if pe_stats else None,
        "ev_ebitda_avg": ev_stats["avg"] if ev_stats else None,
        "ev_ebitda_min": ev_stats["min"] if ev_stats else None,
        "ev_ebitda_max": ev_stats["max"] if ev_stats else None,
        "ev_ebitda_percentile": ev_stats["percentile"] if ev_stats else None,
        "num_snapshots": num,
    }


# ---------------------------------------------------------------------------
# Peer comparison
# ---------------------------------------------------------------------------
def get_peer_valuation(ticker: str, max_peers: int = 10):
    """Median forward-P/E and EV/EBITDA across same-sector peers.

    We use the MEDIAN, not the mean: a single mega-multiple peer (or a negative/
    tiny-denominator outlier) would badly distort an average of multiples, while
    the median is robust to those. Returns None if <2 peers have usable data.
    """
    ticker = ticker.strip().upper()

    session = get_session()
    try:
        target = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker)
            .first()
        )
        sector_etf = target.sector_etf if target else None
        if not sector_etf:
            print(f"note: '{ticker}' not in TickerUniverse — no peer set.")
            return None

        peer_rows = (
            session.query(TickerUniverse.ticker)
            .filter(
                TickerUniverse.sector_etf == sector_etf,
                TickerUniverse.ticker != ticker,
                TickerUniverse.active.is_(True),
            )
            .limit(max_peers)
            .all()
        )
        peer_tickers = [r[0] for r in peer_rows]
    finally:
        session.close()

    pe_list = []
    ev_list = []
    for peer in peer_tickers:
        fundamentals = get_latest_fundamentals(peer)
        if fundamentals is None:
            continue
        pe = _clean_num(fundamentals.get("forward_pe"))
        ev = _clean_num(fundamentals.get("ev_to_ebitda"))
        if pe is not None and pe > 0:
            pe_list.append(pe)
        if ev is not None and ev > 0:
            ev_list.append(ev)

    # Need at least 2 peers with a usable forward P/E to form a meaningful median.
    if len(pe_list) < 2:
        return None

    return {
        "sector_median_pe": round(statistics.median(pe_list), 2),
        "sector_median_ev_ebitda": (
            round(statistics.median(ev_list), 2) if len(ev_list) >= 2 else None
        ),
        "peers_used": len(pe_list),
        "sector_etf": sector_etf,
    }


# ---------------------------------------------------------------------------
# Re-rating assessment
# ---------------------------------------------------------------------------
def assess_valuation_room(ticker: str) -> dict:
    """Combine current / historical / peer valuation into a re-rating read."""
    ticker = ticker.strip().upper()
    snapshot = get_valuation_snapshot(ticker)
    hist = get_historical_valuation_range(ticker)
    peers = get_peer_valuation(ticker)

    # No current fundamentals → nothing to assess.
    if snapshot is None:
        return {
            "forward_pe": None, "peg_ratio": None, "fcf_yield": None,
            "ev_to_ebitda": None,
            "multiple_vs_history": "Insufficient history",
            "multiple_vs_peers": "No peer data",
            "room_to_expand": "Unknown",
            "sector_median_pe": None, "forward_pe_percentile": None,
            "forward_pe_avg": None,
        }

    forward_pe = snapshot["forward_pe"]

    # --- vs own history ---
    pe_percentile = hist["forward_pe_percentile"] if hist else None
    pe_avg = hist["forward_pe_avg"] if hist else None
    if pe_percentile is None:
        multiple_vs_history = "Insufficient history"
    elif pe_percentile > 75:
        multiple_vs_history = "Extended"
    elif pe_percentile < 25:
        multiple_vs_history = "Compressed"
    else:
        multiple_vs_history = "Fair"

    # --- vs peers ---
    sector_median_pe = peers["sector_median_pe"] if peers else None
    if sector_median_pe is None or forward_pe is None:
        multiple_vs_peers = "No peer data"
    elif forward_pe > sector_median_pe * 1.15:
        multiple_vs_peers = "Premium"
    elif forward_pe < sector_median_pe * 0.85:
        multiple_vs_peers = "Discount"
    else:
        multiple_vs_peers = "In Line"

    # --- room to expand ---
    compressed = multiple_vs_history == "Compressed"
    discount = multiple_vs_peers == "Discount"
    extended = multiple_vs_history == "Extended"
    premium = multiple_vs_peers == "Premium"

    if multiple_vs_history == "Insufficient history" and multiple_vs_peers == "No peer data":
        room_to_expand = "Unknown"
    elif compressed and discount:
        room_to_expand = "Yes — compressed vs history and peers"
    elif compressed or discount:
        room_to_expand = "Partial — compressed on one dimension"
    elif extended or premium:
        room_to_expand = "Limited — already extended"
    else:
        room_to_expand = "Unknown"

    return {
        "forward_pe": forward_pe,
        "peg_ratio": snapshot["peg_ratio"],
        "fcf_yield": snapshot["fcf_yield"],
        "ev_to_ebitda": snapshot["ev_to_ebitda"],
        "multiple_vs_history": multiple_vs_history,
        "multiple_vs_peers": multiple_vs_peers,
        "room_to_expand": room_to_expand,
        "sector_median_pe": sector_median_pe,
        "forward_pe_percentile": pe_percentile,
        "forward_pe_avg": pe_avg,
    }


def _ordinal(n):
    """1 -> '1st', 2 -> '2nd', 62 -> '62nd' ..."""
    if n is None:
        return "n/a"
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def get_valuation_summary(ticker: str) -> dict:
    """Print a clean valuation block for a ticker and return the assessment dict."""
    ticker = ticker.strip().upper()
    a = assess_valuation_room(ticker)

    if a["forward_pe"] is None:
        print(f"{ticker} | Valuation — no fundamentals stored")
        return a

    def _x(v):
        return f"{v:.1f}x" if v is not None else "n/a"

    peg = f"{a['peg_ratio']:.2f}" if a["peg_ratio"] is not None else "n/a"
    fcf = f"{a['fcf_yield']:.1f}%" if a["fcf_yield"] is not None else "n/a"

    print(f"{ticker} | Valuation")
    print(
        f"Forward P/E:  {_x(a['forward_pe'])}  |  PEG: {peg}  |  "
        f"FCF yield: {fcf}  |  EV/EBITDA: {_x(a['ev_to_ebitda'])}"
    )

    # vs own history
    if a["multiple_vs_history"] == "Insufficient history":
        print("vs own history:  Insufficient history")
    else:
        avg_str = f", avg {a['forward_pe_avg']:.1f}x" if a["forward_pe_avg"] is not None else ""
        print(
            f"vs own history:  {a['multiple_vs_history']}  "
            f"({_ordinal(a['forward_pe_percentile'])} percentile{avg_str})"
        )

    # vs peers
    if a["multiple_vs_peers"] == "No peer data":
        print("vs sector peers: No peer data")
    else:
        med = f"{a['sector_median_pe']:.1f}x" if a["sector_median_pe"] is not None else "n/a"
        print(f"vs sector peers: {a['multiple_vs_peers']}  (sector median {med})")

    print(f"Re-rating room:  {a['room_to_expand']}")

    return a


if __name__ == "__main__":
    # Historical percentile will read "Insufficient history" until the scheduler
    # has accumulated several daily fundamentals snapshots — expected for now.
    get_valuation_summary("AAPL")
    print()
    get_valuation_summary("MSFT")
