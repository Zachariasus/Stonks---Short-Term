"""
analysis/price_target.py
========================
Price target generator — final module of the Top-Down & Valuation engine.

WHAT THIS DOES
    Converts the valuation re-rating read into an actionable price target and a
    reward:risk ratio. The framework is deliberately simple:
        target price = target multiple × forward EPS
    The judgment is the TARGET MULTIPLE — anchored to where the multiple can
    realistically go (history / peers / contraction risk), not wishful thinking.

DIVISION SAFETY
    Same rule as the valuation module: every division is guarded and returns None
    rather than a nonsense number when a denominator is missing / zero / invalid.
"""

import pandas as pd

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.moving_averages import calculate_atr
    from analysis.valuation import assess_valuation_room, get_valuation_snapshot
    from data.db_reader import get_latest_fundamentals, get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.moving_averages import calculate_atr
    from analysis.valuation import assess_valuation_room, get_valuation_snapshot
    from data.db_reader import get_latest_fundamentals, get_price_bars


def _clean_num(value):
    """Return a float, or None for missing/NaN values."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def derive_target_multiple(valuation_dict):
    """Derive a DEFENSIBLE target multiple from the re-rating assessment.

    Returns (target_multiple, rationale_string). The multiple is anchored to
    something real for each case (see comments) rather than wished-for.
    """
    forward_pe = valuation_dict.get("forward_pe")

    # No current multiple at all → nothing to anchor to.
    if forward_pe is None:
        return (forward_pe, "Fallback — no data")

    room = valuation_dict.get("room_to_expand")
    pe_avg = valuation_dict.get("forward_pe_avg")
    sector_median = valuation_dict.get("sector_median_pe")

    # Compressed on BOTH dimensions → assume it reverts to its own historical
    # average multiple (the most defensible "normal" level for this stock).
    if room == "Yes — compressed vs history and peers":
        if pe_avg is not None:
            return (round(pe_avg, 2), "Mean reversion to historical average")
        anchor = sector_median if sector_median is not None else forward_pe
        return (round(anchor, 2), "Mean reversion (sector-median fallback)")

    # Compressed on ONE dimension → assume a PARTIAL re-rating: split the
    # difference between today's multiple and the historical average (or, if no
    # history yet, lean on the sector median).
    if room == "Partial — compressed on one dimension":
        if pe_avg is not None:
            return (round((forward_pe + pe_avg) / 2, 2), "Partial re-rating toward average")
        if sector_median is not None:
            return (round(sector_median, 2), "Partial re-rating toward sector median")
        return (round(forward_pe, 2), "Partial re-rating (no anchor — held current)")

    # Already extended → don't assume expansion; price in a modest contraction.
    if room == "Limited — already extended":
        return (round(forward_pe * 0.95, 2), "Modest multiple contraction risk")

    # Unknown / history missing → no re-rating assumed; hold the current multiple.
    return (round(forward_pe, 2), "No re-rating assumed — insufficient history")


def calculate_price_target(ticker: str):
    """Compute target price = target multiple × forward EPS, plus upside %."""
    ticker = ticker.strip().upper()

    snapshot = get_valuation_snapshot(ticker)
    if snapshot is None:
        print(f"⚠️  calculate_price_target: no valuation data for '{ticker}'.")
        return None

    valuation = assess_valuation_room(ticker)

    # forward EPS isn't in the valuation snapshot, so read it from fundamentals.
    fundamentals = get_latest_fundamentals(ticker)
    forward_eps = _clean_num(fundamentals.get("forward_eps")) if fundamentals else None
    forward_pe = snapshot.get("forward_pe")

    # Guard: target = multiple × EPS only makes sense with positive forward EPS
    # (zero/negative EPS → a meaningless or negative target).
    if forward_eps is None or forward_eps <= 0:
        print(f"⚠️  calculate_price_target: no positive forward EPS for '{ticker}'.")
        return None

    target_multiple, rationale = derive_target_multiple(valuation)
    if target_multiple is None:
        print(f"⚠️  calculate_price_target: could not derive a target multiple for '{ticker}'.")
        return None

    target_price = round(target_multiple * forward_eps, 2)

    # Current close = latest stored daily close.
    bars = get_price_bars(ticker, days=5)
    if bars is None or bars.empty:
        print(f"⚠️  calculate_price_target: no price data for '{ticker}'.")
        return None
    current_close = round(float(bars["Close"].iloc[-1]), 2)

    # Guard the upside division against a non-positive price.
    if current_close <= 0:
        upside_pct = None
    else:
        upside_pct = round((target_price - current_close) / current_close * 100, 2)

    return {
        "ticker": ticker,
        "current_close": current_close,
        "forward_eps": round(forward_eps, 2),
        "current_pe": round(forward_pe, 2) if forward_pe is not None else None,
        "target_multiple": target_multiple,
        "target_price": target_price,
        "upside_pct": upside_pct,
        "rationale": rationale,
        "time_horizon": "4–6 months",
    }


def calculate_reward_risk(ticker: str, stop_price: float):
    """Reward:risk from the price target and a stop price.

    reward = target − current ; risk = current − stop. Guarded: a stop at or
    above the current price gives risk ≤ 0 (an invalid trade), so we return None.
    """
    target = calculate_price_target(ticker)
    if target is None:
        return None

    target_price = target["target_price"]
    current_close = target["current_close"]

    reward = target_price - current_close
    risk = current_close - stop_price

    # Guard: stop must sit BELOW the entry, else risk ≤ 0 and R:R is undefined.
    if risk <= 0:
        print(
            f"⚠️  calculate_reward_risk: stop ${stop_price:.2f} is not below the "
            f"current price ${current_close:.2f} — invalid risk."
        )
        return None

    rr_ratio = round(reward / risk, 2)
    if rr_ratio >= 3.0:
        rr_label = "Excellent"
    elif rr_ratio >= 2.0:
        rr_label = "Good"
    elif rr_ratio >= 1.5:
        rr_label = "Marginal"
    else:
        rr_label = "Poor"

    return {
        "rr_ratio": rr_ratio,
        "rr_label": rr_label,
        "reward_dollars": round(reward, 2),
        "risk_dollars": round(risk, 2),
        "target_price": target_price,
        "stop_price": stop_price,
        "current_close": current_close,
    }


# ---------------------------------------------------------------------------
# SHORT side — the mirror of the long target/R:R.
#
# The long target is a fundamental anchor (target multiple × forward EPS — where
# the multiple can RE-RATE UP). The SHORT playbook is explicit: do not short on
# valuation alone, and cover into a "measured-move or prior-support target." So
# the short target is TECHNICAL: the prior support a decline tends to reach,
# with a measured-move fallback for names already pinned at their lows.
# ---------------------------------------------------------------------------
SHORT_TARGET_LOOKBACK = 252   # ~52 weeks of trading days for the prior-support low
MEASURED_MOVE_ATR = 3.0       # fallback leg = 3× daily ATR when already near lows


def calculate_short_target(ticker: str):
    """Downside objective for a short: prior support, else a measured move.

    target = lowest LOW over ~52 weeks (the prior support a Stage 4 decline
    revisits) when that sits a useful distance below price; otherwise — the name
    is already at/near its lows — a measured-move leg of 3× ATR below price.

    Returns {current_close, target_price, downside_pct (negative), rationale,
    time_horizon}, or None on missing data. Mirrors calculate_price_target's shape
    on the short side.
    """
    ticker = ticker.strip().upper()

    bars = get_price_bars(ticker, days=SHORT_TARGET_LOOKBACK + 30)
    if bars is None or bars.empty:
        print(f"⚠️  calculate_short_target: no price data for '{ticker}'.")
        return None

    bars = bars.sort_values("Date").reset_index(drop=True)
    current_close = round(float(bars["Close"].iloc[-1]), 2)
    if current_close <= 0:
        return None

    # Prior support = lowest low over the lookback, EXCLUDING the last 5 sessions
    # (so a fresh breakdown low doesn't define its own target).
    window = bars.iloc[-SHORT_TARGET_LOOKBACK:] if len(bars) >= SHORT_TARGET_LOOKBACK else bars
    lows = window["Low"].iloc[:-5]
    support = round(float(lows.min()), 2) if len(lows) else None

    # Use prior support only if it offers a meaningful (>3%) move down; otherwise
    # the stock is already pinned at its lows → project a measured move instead.
    if support is not None and support < current_close * 0.97:
        target_price = support
        rationale = "Prior support (~52-week low)"
    else:
        atr, _weekly = calculate_atr(ticker)
        leg = (MEASURED_MOVE_ATR * atr) if atr else current_close * 0.12
        target_price = round(max(current_close - leg, 0.01), 2)
        rationale = "Measured move (already near prior lows)"

    downside_pct = round((target_price - current_close) / current_close * 100, 2)
    return {
        "ticker": ticker,
        "current_close": current_close,
        "target_price": target_price,
        "downside_pct": downside_pct,   # negative = how far below price the target sits
        "rationale": rationale,
        "time_horizon": "4–6 months",
    }


def calculate_short_reward_risk(ticker: str, stop_price: float):
    """Reward:risk for a SHORT — the inverse of the long version.

    reward = current − target (target sits BELOW price) ; risk = stop − current
    (stop sits ABOVE price). Guarded: a stop at or below the current price gives
    risk ≤ 0 (an invalid short) → None.
    """
    target = calculate_short_target(ticker)
    if target is None:
        return None

    target_price = target["target_price"]
    current_close = target["current_close"]

    reward = current_close - target_price   # downside captured
    risk = stop_price - current_close        # loss if the stop (above) is hit

    if risk <= 0:
        print(
            f"⚠️  calculate_short_reward_risk: stop ${stop_price:.2f} is not above the "
            f"current price ${current_close:.2f} — invalid risk for a short."
        )
        return None

    rr_ratio = round(reward / risk, 2)
    if rr_ratio >= 3.0:
        rr_label = "Excellent"
    elif rr_ratio >= 2.0:
        rr_label = "Good"
    elif rr_ratio >= 1.5:
        rr_label = "Marginal"
    else:
        rr_label = "Poor"

    return {
        "rr_ratio": rr_ratio,
        "rr_label": rr_label,
        "reward_dollars": round(reward, 2),
        "risk_dollars": round(risk, 2),
        "target_price": target_price,
        "stop_price": stop_price,
        "current_close": current_close,
    }


def get_price_target_summary(ticker: str, stop_price=None):
    """Print a clean price-target block (and R:R if a stop is given); return the dict."""
    ticker = ticker.strip().upper()
    target = calculate_price_target(ticker)
    if target is None:
        print(f"{ticker} | Price Target — insufficient data")
        return None

    upside = target["upside_pct"]
    if upside is None:
        move_str = ""
    elif upside >= 0:
        move_str = f"(+{upside:.1f}% upside)"
    else:
        move_str = f"({upside:.1f}% downside)"

    print(f"{target['ticker']} | Price Target ({target['time_horizon']} horizon)")
    print(f"Current:        ${target['current_close']:.2f}")
    print(
        f"Forward EPS:    ${target['forward_eps']:.2f}   ×   "
        f"Target multiple: {target['target_multiple']:.1f}x"
    )
    print(f"Target price:   ${target['target_price']:.2f}   {move_str}")
    print(f"Rationale:      {target['rationale']}")

    combined = dict(target)
    if stop_price is not None:
        print("─" * 40)
        rr = calculate_reward_risk(ticker, stop_price)
        if rr is None:
            print(
                f"Stop: ${stop_price:.2f}  →  invalid "
                f"(stop must be below current ${target['current_close']:.2f})"
            )
        else:
            print(
                f"Stop: ${rr['stop_price']:.2f}  →  "
                f"R:R = {rr['rr_ratio']:.1f}x  ({rr['rr_label']})"
            )
            combined["reward_risk"] = rr

    return combined


if __name__ == "__main__":
    get_price_target_summary("AAPL")
    print()
    get_price_target_summary("AAPL", stop_price=278.00)
    print()
    get_price_target_summary("MSFT", stop_price=390.00)
