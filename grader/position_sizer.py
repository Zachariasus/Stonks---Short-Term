"""
grader/position_sizer.py
========================
Position sizing & risk calculator — the last practical step before a trade.

WHAT THIS DOES
    The grader tells you WHETHER to trade (letter grade + narrative). This module
    tells you HOW to trade it: given your account size and risk tolerance, exactly
    how many shares to buy, where the ATR-based stop goes, and what the
    reward:risk looks like. The core formula is deliberately simple —

        shares = dollar_risk_budget / (entry − stop)

    — but the value is in getting the inputs right: a volatility-aware (ATR) stop
    and a realistic target from the valuation engine.

DIVISION SAFETY
    Same house rule as the rest of the system: every division is guarded. A
    zero/negative risk-per-share, a missing ATR, or a zero account never produces
    a nonsense number — it returns None (with a warning) instead.
"""

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from analysis.moving_averages import calculate_atr
    from analysis.price_target import calculate_price_target, calculate_reward_risk
    from data.db_reader import get_price_bars
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis.moving_averages import calculate_atr
    from analysis.price_target import calculate_price_target, calculate_reward_risk
    from data.db_reader import get_price_bars


def derive_atr_stop(ticker, entry_price, atr_multiple=2.5, direction="Long"):
    """Place a stop a fixed number of ATRs away from the entry.

    Why ATR and not a flat percentage? A stop should sit outside the stock's
    normal day-to-day noise. A volatile name needs a wider stop than a calm one;
    anchoring the distance to ATR (Average True Range) sizes the stop to each
    stock's own volatility automatically.

    Long  → stop BELOW entry  (entry − multiple × ATR)
    Short → stop ABOVE entry  (entry + multiple × ATR)

    Returns {stop_price, atr, atr_multiple, direction}, or None if ATR is
    unavailable (never guess a stop on missing data).
    """
    atr, _weekly_atr = calculate_atr(ticker)
    if atr is None:
        print(f"⚠️  derive_atr_stop: no ATR available for '{ticker}' — cannot place a stop.")
        return None

    distance = atr_multiple * atr
    if direction == "Short":
        stop_price = round(entry_price + distance, 2)
    else:  # default Long
        stop_price = round(entry_price - distance, 2)

    return {
        "stop_price": stop_price,
        "atr": atr,
        "atr_multiple": atr_multiple,
        "direction": direction,
    }


def calculate_position_size(entry_price, stop_price, account_size,
                            risk_pct=0.01, direction="Long"):
    """Size the position so a stop-out loses exactly `risk_pct` of the account.

    This is the heart of risk-based sizing. You decide up front how many dollars
    you're willing to lose if the stop is hit (the dollar-risk budget). The
    distance from entry to stop is the loss PER SHARE. Dividing the budget by the
    per-share loss gives the share count — so every trade risks the same dollars
    regardless of price or volatility.

    Returns the full sizing dict, or None (with a warning) if the risk-per-share
    is zero or negative (which would mean the stop is at/through the entry).
    """
    dollar_risk_budget = account_size * risk_pct

    # Loss per share if stopped out. abs() so the same formula works for longs
    # (stop below) and shorts (stop above).
    risk_per_share = abs(entry_price - stop_price)

    # Guard: a stop at or on the wrong side of the entry → no valid risk distance.
    if risk_per_share <= 0:
        print(
            f"⚠️  calculate_position_size: risk-per-share is {risk_per_share} "
            f"(stop ${stop_price} vs entry ${entry_price}) — invalid, cannot size."
        )
        return None

    # Round DOWN: never buy a fraction of a share, and never round up into
    # slightly more risk than budgeted.
    shares = int(dollar_risk_budget / risk_per_share)

    position_value = shares * entry_price

    # Guard the "% of account" division against a zero/negative account.
    if account_size > 0:
        position_pct_of_account = (position_value / account_size) * 100
    else:
        position_pct_of_account = None

    return {
        "shares": shares,
        "position_value": round(position_value, 2),
        "position_pct_of_account": (
            round(position_pct_of_account, 2)
            if position_pct_of_account is not None else None
        ),
        "dollar_risk_budget": round(dollar_risk_budget, 2),
        "risk_per_share": round(risk_per_share, 2),
        "entry_price": entry_price,
        "stop_price": stop_price,
        "account_size": account_size,
        "risk_pct": risk_pct,
    }


def calculate_full_risk_profile(ticker, account_size, risk_pct=0.01,
                                entry_price=None, target_price=None,
                                direction="Long", atr_multiple=2.5):
    """Assemble everything needed for the risk card: entry, stop, target, size, R:R.

    Pulls the entry from the latest close (if not supplied), derives an ATR stop,
    sizes the position, and computes reward:risk against the valuation target.
    Returns the combined dict, or None (with a warning) on missing data.
    """
    ticker = ticker.strip().upper()

    # --- Entry price: use the supplied value, else the latest stored close ---
    if entry_price is None:
        bars = get_price_bars(ticker, days=5)
        if bars is None or bars.empty:
            print(f"⚠️  calculate_full_risk_profile: no price data for '{ticker}'.")
            return None
        entry_price = round(float(bars["Close"].iloc[-1]), 2)

    # --- Stop: ATR-based ---
    atr_stop = derive_atr_stop(ticker, entry_price, atr_multiple, direction)
    if atr_stop is None:
        print(f"⚠️  calculate_full_risk_profile: cannot size '{ticker}' without an ATR stop.")
        return None
    stop_price = atr_stop["stop_price"]
    atr = atr_stop["atr"]

    # --- Position size ---
    sizing = calculate_position_size(entry_price, stop_price, account_size,
                                     risk_pct, direction)
    if sizing is None:
        return None

    # --- Target: supplied, else from the valuation engine ---
    if target_price is None:
        target = calculate_price_target(ticker)
        target_price = target["target_price"] if target else None

    # --- Reward:risk (price_target.calculate_reward_risk anchors to the latest
    # close as the entry and is written for the long side). For the default
    # entry=None case, entry == latest close so it lines up; it returns None for
    # a stop on the wrong side of price, which we surface as n/a. ---
    rr = calculate_reward_risk(ticker, stop_price)

    # ATR stop distance as a percent of entry (positive = the ATR gap). Guarded.
    if entry_price:
        atr_stop_distance_pct = round((entry_price - stop_price) / entry_price * 100, 2)
    else:
        atr_stop_distance_pct = None

    return {
        "ticker": ticker,
        "direction": direction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "atr": atr,
        "atr_multiple": atr_multiple,
        "atr_stop_distance_pct": atr_stop_distance_pct,
        "shares": sizing["shares"],
        "position_value": sizing["position_value"],
        "position_pct_of_account": sizing["position_pct_of_account"],
        "dollar_risk_budget": sizing["dollar_risk_budget"],
        "risk_per_share": sizing["risk_per_share"],
        "risk_pct": risk_pct,
        "account_size": account_size,
        "rr_ratio": rr["rr_ratio"] if rr else None,
        "rr_label": rr["rr_label"] if rr else None,
        "reward_dollars": rr["reward_dollars"] if rr else None,
        "risk_dollars": rr["risk_dollars"] if rr else None,
    }


def print_risk_card(risk_dict) -> dict:
    """Print a clean position-sizing card and return the dict."""
    g = risk_dict
    if g is None:
        print("Risk card — insufficient data.")
        return g

    entry = g["entry_price"]
    stop = g["stop_price"]
    target = g.get("target_price")
    direction = g.get("direction", "Long")
    account = g["account_size"]
    budget = g["dollar_risk_budget"]

    # Header
    print(f"── POSITION SIZING  |  {g['ticker']}  ({direction}) ──────────────────")
    print(
        f"Account: ${account:,.0f}  |  Risk per trade: {g['risk_pct'] * 100:.1f}%  "
        f"(${budget:,.0f})\n"
    )

    # Entry
    print(f"Entry:   ${entry:,.2f}")

    # Stop — show the ATR gap in dollars and the signed % move from entry.
    atr_gap = abs(entry - stop)
    side = "above entry" if direction == "Short" else "below entry"
    stop_move_pct = ((stop - entry) / entry * 100) if entry else 0.0
    print(
        f"Stop:    ${stop:,.2f}   ({g['atr_multiple']}× ATR = ${atr_gap:,.2f} "
        f"{side}, {stop_move_pct:+.1f}%)"
    )

    # Target — flag clearly if it's below the entry (negative upside).
    if target is None:
        print("Target:  n/a   (no valuation target available)")
    else:
        target_move_pct = ((target - entry) / entry * 100) if entry else 0.0
        if target < entry:
            print(
                f"Target:  ${target:,.2f}   ← note: below entry = negative upside "
                f"({target_move_pct:+.1f}%) flagged"
            )
        else:
            print(f"Target:  ${target:,.2f}   (+{target_move_pct:.1f}% upside)")

    # Sizing
    print()
    print(f"Shares:      {g['shares']}")
    pct = g.get("position_pct_of_account")
    pct_str = f"{pct:.1f}% of account" if pct is not None else "n/a"
    print(f"Position:    ${g['position_value']:,.2f}  ({pct_str})")
    # Actual dollars at risk = the rounded-down share count × the per-share risk
    # (slightly under the budget because we round shares down).
    actual_risk = g["shares"] * g["risk_per_share"]
    print(f"Dollar risk: ${actual_risk:,.2f}")

    # Reward:risk
    print()
    if g.get("rr_ratio") is None:
        print("R:R:   n/a   (no valid target/stop for reward:risk)")
    else:
        print(f"R:R:   {g['rr_ratio']:.1f}x  ({g['rr_label']})")

    # --- Warning block: negative upside, poor R:R, or oversized position ---
    warnings = []
    if target is not None and target < entry:
        warnings.append(
            "Target is below entry — this setup has negative upside. The valuation\n"
            "    engine is flagging downside risk despite the trend. Consider waiting\n"
            "    for a better entry or a thesis change before acting."
        )
    if g.get("rr_ratio") is not None and g["rr_ratio"] < 1.5:
        warnings.append(
            f"Reward:risk is {g['rr_ratio']:.1f}x (below 1.5) — the potential reward does\n"
            "    not justify the risk on this entry."
        )
    if pct is not None and pct > 20:
        warnings.append(
            f"Position is {pct:.1f}% of the account (>20%) — oversized. Use a wider\n"
            "    stop or a smaller risk % to bring concentration down."
        )

    bar = "─" * 58
    if warnings:
        print(bar)
        for w in warnings:
            print(f"⚠️  {w}")
        print(bar)
    else:
        print(bar)

    return g


if __name__ == "__main__":
    # AAPL: target ($276) sits BELOW the current price → negative upside and a
    # poor (negative) R:R. The warning block firing here is EXPECTED and CORRECT —
    # the system is refusing to dress up a bad entry as a good trade.
    aapl = calculate_full_risk_profile("AAPL", account_size=50000)
    print_risk_card(aapl)

    print()

    # MSFT: a discount re-rating gave a target well above price (+30.6%), so this
    # should show positive upside and a healthy R:R — no warnings.
    msft = calculate_full_risk_profile("MSFT", account_size=50000)
    print_risk_card(msft)
