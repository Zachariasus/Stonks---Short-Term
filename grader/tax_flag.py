"""
grader/tax_flag.py
==================
Tax threshold flag — the "stretch to long-term" decision window (final Phase 7 step).

WHAT THIS DOES
    A 4–6 month hold normally produces a SHORT-TERM capital gain, taxed as
    ordinary income. But a winner that's approaching the 1-year mark with an
    intact trend presents a genuine choice: hold a few more weeks to qualify for
    the much lower LONG-TERM rate, or exit now. This module detects that decision
    window automatically (so it's never missed), quantifies the tax savings, and
    — critically — checks whether the trend is still intact, because the cardinal
    rule is: never let the tax tail wag the dog.

DIVISION SAFETY
    Same house rule as the rest of the system: tax-savings math returns None on a
    non-positive gain rather than producing a meaningless number.
"""

from datetime import date, timedelta

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.stage_classifier import classify_stage
    from data.db_reader import get_price_bars
    from grader.position_sizer import derive_atr_stop
    from screener.exit_monitor import check_50day_break
    from screener.flag_generator import get_active_flags
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.stage_classifier import classify_stage
    from data.db_reader import get_price_bars
    from grader.position_sizer import derive_atr_stop
    from screener.exit_monitor import check_50day_break
    from screener.flag_generator import get_active_flags


# 2026 TOP MARGINAL FEDERAL RATES. The actual rate depends on the user's bracket
# and state; these illustrate the MAXIMUM savings from stretching to long-term.
#   short_term_rate: top ordinary-income rate (short-term gains are taxed as income)
#   long_term_rate:  top long-term capital-gains rate (20%) + 3.8% NIIT = 23.8%
#   rate_difference: the spread you save per dollar of gain by waiting (37% − 23.8%)
TAX_RATES = {
    "short_term_rate": 0.37,
    "long_term_rate": 0.238,
    "rate_difference": 0.132,
}

# How close to the 1-year mark counts as the "decision window" (≈ 8 weeks out).
DECISION_WINDOW_DAYS = 56


def check_tax_threshold(flag) -> dict:
    """How far is this position from the 1-year (long-term) mark?

    Takes a Flag ORM object and computes the holding period from its flagged_date.
    The "Tax Decision Window" is the ~8 weeks before the 1-year mark — close enough
    that stretching for long-term treatment is a real, near-term choice.
    """
    days_held = (date.today() - flag.flagged_date).days
    days_to_one_year = 365 - days_held

    if days_to_one_year > DECISION_WINDOW_DAYS:
        holding_period_label = "Short-Term (<1yr)"
    elif days_to_one_year > 0:  # 0 < days_to_one_year <= 56
        holding_period_label = "Tax Decision Window"
    else:  # days_to_one_year <= 0
        holding_period_label = "Long-Term (>1yr)"

    in_decision_window = 0 < days_to_one_year <= DECISION_WINDOW_DAYS

    return {
        "ticker": flag.ticker,
        "flagged_date": flag.flagged_date,
        "days_held": days_held,
        "days_to_one_year": days_to_one_year,
        "holding_period_label": holding_period_label,
        "in_decision_window": in_decision_window,
    }


def calculate_tax_savings(gain_dollars, short_term_rate=None, long_term_rate=None):
    """Dollar tax saved by qualifying a gain for long-term instead of short-term.

    Uses the TAX_RATES defaults unless explicit rates are passed (so a user can
    plug in their own bracket). Returns None on a non-positive gain — there's no
    tax to save on a loss, and dividing by it for the % figure would be invalid.
    """
    if short_term_rate is None:
        short_term_rate = TAX_RATES["short_term_rate"]
    if long_term_rate is None:
        long_term_rate = TAX_RATES["long_term_rate"]

    # Guard: no tax savings on a break-even or losing position.
    if gain_dollars <= 0:
        return None

    tax_if_short = gain_dollars * short_term_rate
    tax_if_long = gain_dollars * long_term_rate
    savings = tax_if_short - tax_if_long

    return {
        "gain_dollars": round(gain_dollars, 2),
        "tax_if_short": round(tax_if_short, 2),
        "tax_if_long": round(tax_if_long, 2),
        "savings": round(savings, 2),
        # Always equals the rate spread (≈13.2%) — a nice built-in sanity check.
        "savings_pct_of_gain": round(savings / gain_dollars * 100, 2),
    }


def evaluate_stretch_decision(flag, account_size=None) -> dict:
    """Full stretch-to-long-term assessment for one flag.

    Returns early (in_window=False) when the position isn't near the 1-year mark.
    Inside the window, it estimates the current gain, the tax savings, and — most
    importantly — whether the trend is STILL intact, then issues a recommendation.
    """
    threshold = check_tax_threshold(flag)

    # Not near the 1-year mark → nothing to decide.
    if not threshold["in_decision_window"]:
        return {
            "in_window": False,
            "ticker": flag.ticker,
            "direction": flag.direction,
            "flagged_date": flag.flagged_date,
            "days_held": threshold["days_held"],
            "days_to_one_year": threshold["days_to_one_year"],
            "message": (
                f"No tax decision — {threshold['days_to_one_year']} days "
                f"remaining until the 1-year window"
            ),
        }

    ticker = flag.ticker
    direction = flag.direction or "Long"

    # --- Estimate the current gain ---
    gain_pct = None
    current_close = None
    estimated_gain_dollars = None
    tax_savings = None

    if flag.entry_price is not None:
        bars = get_price_bars(ticker, days=5)
        if bars is not None and not bars.empty:
            current_close = round(float(bars["Close"].iloc[-1]), 2)
            if flag.entry_price:  # guard the division against a zero entry
                gain_pct = round(
                    (current_close - flag.entry_price) / flag.entry_price * 100, 2
                )

            # If we know the account size, estimate the DOLLAR gain from a standard
            # 1%-risk position with the ATR stop. This is approximate: it assumes
            # the position was sized at flagging time the way position_sizer would.
            #   shares ≈ (1% of account) / risk_per_share
            #   dollar gain ≈ shares × (current − entry)
            # (Equivalent to the spec's account×0.01/|entry−atr_stop| × entry ×
            #  gain_pct, with gain_pct expressed as a fraction.)
            if account_size is not None and gain_pct is not None:
                atr_stop = derive_atr_stop(ticker, flag.entry_price, direction=direction)
                if atr_stop is not None:
                    risk_per_share = abs(flag.entry_price - atr_stop["stop_price"])
                    if risk_per_share > 0:
                        shares_est = int((account_size * 0.01) / risk_per_share)
                        estimated_gain_dollars = round(
                            shares_est * (current_close - flag.entry_price), 2
                        )

    # Tax savings only make sense on a real positive dollar gain.
    if estimated_gain_dollars is not None and estimated_gain_dollars > 0:
        tax_savings = calculate_tax_savings(estimated_gain_dollars)

    # --- Is the trend still intact RIGHT NOW? ---
    # This is the discipline check. We don't hold a deteriorating position just for
    # a tax break — the trend has to still be there to justify waiting.
    stage_info = classify_stage(ticker)
    stage_num = stage_info["stage"] if stage_info else None
    stage_label = stage_info["stage_label"] if stage_info else None

    ma_break = check_50day_break(ticker)        # True / False / None
    ma_break_detected = ma_break is True

    if direction == "Short":
        # For a short, "intact" means still a Stage 4 downtrend (the 50-day-break
        # check is a long-side signal, so it doesn't apply cleanly here).
        trend_still_intact = stage_num == 4
    else:  # Long
        trend_still_intact = (not ma_break_detected) and (stage_num == 2)

    # --- Recommendation ---
    # Cardinal rule first: if the trend is broken, EXIT — don't let the tax tail
    # wag the dog. Only if it's intact do we weigh the tax benefit of waiting.
    if not trend_still_intact:
        recommendation = "EXIT — trend broken, don't let tax tail wag the dog"
    elif gain_pct is not None and gain_pct > 0:
        recommendation = "HOLD — trend intact, tax savings worth waiting for"
    else:
        recommendation = "MONITOR — in window, gain is small, watch closely"

    return {
        "in_window": True,
        "ticker": ticker,
        "direction": direction,
        "days_held": threshold["days_held"],
        "days_to_one_year": threshold["days_to_one_year"],
        "current_close": current_close,
        "entry_price": flag.entry_price,
        "gain_pct": gain_pct,
        "estimated_gain_dollars": estimated_gain_dollars,
        "tax_savings": tax_savings,
        "trend_still_intact": trend_still_intact,
        "stage": stage_num,
        "stage_label": stage_label,
        "ma_break_detected": ma_break_detected,
        "stretch_recommendation": recommendation,
    }


def scan_flags_for_tax_window() -> list:
    """Check every active flag for the tax decision window; print a summary."""
    flags = get_active_flags()
    results = [evaluate_stretch_decision(f) for f in flags]

    in_window_count = sum(1 for r in results if r.get("in_window"))

    print("=== Tax Threshold Scanner ===")
    print(f"Active flags scanned: {len(flags)}")
    window_note = "   (expected — all flags are days old)" if in_window_count == 0 else ""
    print(f"In decision window:   {in_window_count}{window_note}\n")

    for r in results:
        line = (
            f"{r['ticker']:<4} ({r['direction']}, flagged {r['flagged_date']}): "
            f"{r['days_to_one_year']} days until 1-year window"
        )
        if r.get("in_window"):
            line += f"  ← IN WINDOW: {r['stretch_recommendation']}"
        print(line)

    return results


def print_tax_decision_card(decision: dict) -> dict:
    """Print the full tax decision card for a flag that's in the decision window."""
    d = decision
    if not d.get("in_window"):
        # Nothing to decide — just echo the calm status.
        print(d.get("message", "Not in the tax decision window."))
        return d

    inner_width = 46
    header = f"  TAX DECISION WINDOW  |  {d['ticker']}"
    header_line = header[:inner_width].ljust(inner_width)
    print("╔" + "═" * inner_width + "╗")
    print("║" + header_line + "║")
    print("╚" + "═" * inner_width + "╝")

    print(
        f"Held: {d['days_held']} days  |  {d['days_to_one_year']} days until "
        f"long-term status\n"
    )

    # Gain
    entry = d.get("entry_price")
    close = d.get("current_close")
    if entry is not None and close is not None:
        per_share = close - entry
        sign = "+" if per_share >= 0 else "−"
        pct = d.get("gain_pct")
        pct_str = f"{pct:+.1f}%" if pct is not None else "n/a"
        print(f"Gain: {sign}${abs(per_share):,.2f}/share ({pct_str})")
    else:
        print("Gain: n/a (no entry price on flag)")

    # Tax math
    ts = d.get("tax_savings")
    if ts is not None:
        st_pct = TAX_RATES["short_term_rate"] * 100
        lt_pct = TAX_RATES["long_term_rate"] * 100
        print(f"Tax if sold now (short-term, {st_pct:.0f}%): ${ts['tax_if_short']:,.2f}")
        print(f"Tax if held to 1yr  (long-term, {lt_pct:.1f}%): ${ts['tax_if_long']:,.2f}")
        print(
            f"Estimated savings: ${ts['savings']:,.2f} "
            f"({ts['savings_pct_of_gain']:.1f}% of gain)"
        )
    else:
        print("Tax savings: n/a (no positive dollar gain to compute)")

    # Trend check
    if d.get("stage") is not None:
        intact_mark = "intact ✓" if d["trend_still_intact"] else "✗ (deteriorating)"
        stage_str = f"Stage {d['stage']} {intact_mark}"
    else:
        stage_str = "Stage n/a"
    ma_str = "Break detected" if d.get("ma_break_detected") else "No break"
    print(f"\nTrend check: {stage_str} | 50-day MA: {ma_str}")

    print(f"\nRECOMMENDATION: {d['stretch_recommendation']}")
    return d


if __name__ == "__main__":
    # ── A) Real flags ───────────────────────────────────────────────────────
    # Our flags are days old, so NONE will be near the 1-year mark. The scanner
    # should report 0 in the decision window — graceful, expected output.
    scan_flags_for_tax_window()

    print()

    # ── B) Simulated near-1-year flag ───────────────────────────────────────
    # To prove the decision logic actually fires, insert a TEMPORARY flag with a
    # backdated flagged_date (330 days ago → 35 days from the 1-year mark) and a
    # low entry ($265, below the current ~$301) so there's a real gain. We use a
    # direct SQLAlchemy insert here — NOT flag_generator — and delete it after.
    from data.database import Flag, get_session

    session = get_session()
    sim_flag = Flag(
        ticker="AAPL",
        flagged_date=date.today() - timedelta(days=330),
        direction="Long",
        status="Active",
        entry_price=265.00,
    )
    session.add(sim_flag)
    session.commit()  # expire_on_commit=False → sim_flag stays usable after commit

    try:
        decision = evaluate_stretch_decision(sim_flag, account_size=50000)
        print_tax_decision_card(decision)
    finally:
        # Always clean up the simulated test row, even if evaluation raised.
        session.delete(sim_flag)
        session.commit()
        session.close()
