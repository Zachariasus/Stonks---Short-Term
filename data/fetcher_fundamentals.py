"""
data/fetcher_fundamentals.py
============================
Fundamentals fetcher — company financial health & valuation data.

WHAT THIS MODULE DOES
    Pulls *fundamental* data (valuation ratios, margins, growth, balance-sheet
    health, and earnings history/estimates) for a ticker from Yahoo Finance via
    the `yfinance` library.

HOW IT FITS INTO THE PROJECT
    This is the second fetcher in the Phase 2 data layer, alongside
    `fetcher_price.py` (OHLCV). Where price data tells us how a stock has been
    *trading*, fundamentals tell us about the underlying *business*. The Phase 3+
    analysis engines combine the two:
        - valuation engine  → uses P/E, EV/EBITDA, P/FCF from here
        - fundamentals eng. → uses margins, growth, ROE, leverage from here
        - grader            → blends price action + fundamentals into a grade

DESIGN NOTES
    - yfinance's `.info` is notoriously patchy: any given field may simply be
      absent for a given company. Every function here is built to expect that —
      missing fields become `None` and nothing crashes.
    - Each fetcher is independent and self-contained so analysis code can call
      exactly the piece it needs.
"""

from typing import Optional

import pandas as pd
import yfinance as yf


def get_fundamentals(ticker: str) -> dict:
    """Pull a flat dictionary of key valuation / quality / growth metrics.

    Args:
        ticker: Stock symbol, e.g. "AAPL" (case-insensitive).

    Returns:
        A flat dict {metric_name: value}. Any metric yfinance doesn't provide
        for this company is set to None (this is common and expected).
    """
    ticker = ticker.strip().upper()
    t = yf.Ticker(ticker)

    # `.info` is a single dict of dozens of fields. It can occasionally raise
    # (network/parse issues); if so we fall back to an empty dict so every
    # metric below simply resolves to None instead of crashing.
    try:
        info = t.info or {}
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  Warning: could not fetch .info for '{ticker}': {err}")
        info = {}

    if not info:
        print(f"⚠️  Warning: no fundamental data returned for '{ticker}'.")

    # ---- Price-to-Free-Cash-Flow -------------------------------------------
    # yfinance has no direct P/FCF field, so we derive it: market cap divided
    # by free cash flow. Guard against a missing or zero FCF (no divide-by-zero).
    market_cap = info.get("marketCap")
    free_cash_flow = info.get("freeCashflow")
    if market_cap and free_cash_flow:  # both present and FCF != 0
        price_to_fcf = round(market_cap / free_cash_flow, 2)
    else:
        price_to_fcf = None

    # Each metric uses info.get(), which returns None when the field is absent.
    fundamentals = {
        # --- Valuation: is the stock cheap or expensive? ---
        "forward_pe": info.get("forwardPE"),            # price vs NEXT year's expected earnings — forward-looking valuation
        "trailing_pe": info.get("trailingPE"),          # price vs LAST 12 months' earnings — backward-looking valuation
        "ev_to_ebitda": info.get("enterpriseToEbitda"), # enterprise value vs operating earnings — valuation that ignores capital structure
        "price_to_fcf": price_to_fcf,                   # price vs cash the business actually generates — a hard-to-fake valuation check

        # --- Earnings per share ---
        "forward_eps": info.get("forwardEps"),          # analysts' expected EPS for the coming year
        "trailing_eps": info.get("trailingEps"),        # actual EPS over the last 12 months

        # --- Growth: is the business getting bigger? ---
        "revenue_growth_yoy": info.get("revenueGrowth"),    # year-over-year sales growth (as a fraction, e.g. 0.08 = 8%)
        "earnings_growth_yoy": info.get("earningsGrowth"),  # year-over-year earnings growth (fraction)

        # --- Profitability: how much of revenue becomes profit? ---
        "gross_margins": info.get("grossMargins"),          # profit after cost of goods — pricing power / product economics
        "operating_margins": info.get("operatingMargins"),  # profit after running the business — operational efficiency
        "profit_margins": info.get("profitMargins"),        # bottom-line profit after everything incl. taxes & interest
        "return_on_equity": info.get("returnOnEquity"),     # profit generated per dollar of shareholder equity — capital efficiency

        # --- Balance-sheet health: can it survive a downturn? ---
        "current_ratio": info.get("currentRatio"),          # short-term assets vs short-term liabilities (>1 = can cover near-term bills)
        "debt_to_equity": info.get("debtToEquity"),         # leverage: total debt relative to equity (higher = riskier)
    }

    return fundamentals


def get_earnings_history(ticker: str) -> Optional[pd.DataFrame]:
    """Return the last 4–8 quarters of reported EPS vs estimated EPS.

    Args:
        ticker: Stock symbol, e.g. "AAPL".

    Returns:
        DataFrame with columns [Date, EPS_Estimate, EPS_Actual, Surprise_Pct],
        most recent quarter first, or None if no data is available.
        Surprise_Pct = ((Actual - Estimate) / abs(Estimate)) * 100, 2 dp.
    """
    ticker = ticker.strip().upper()
    t = yf.Ticker(ticker)

    # `.earnings_dates` returns a DataFrame indexed by earnings date, covering
    # both upcoming (no reported EPS yet) and past quarters. Try the property,
    # then the method form, then give up gracefully.
    raw = None
    try:
        raw = t.earnings_dates
    except Exception:
        raw = None
    if raw is None or len(raw) == 0:
        try:
            raw = t.get_earnings_dates(limit=12)
        except Exception:
            raw = None

    if raw is None or len(raw) == 0:
        print(f"⚠️  Warning: no earnings history available for '{ticker}'.")
        return None

    df = raw.copy()

    # Column names vary slightly across yfinance versions, so detect them by
    # keyword instead of hard-coding ("EPS Estimate", "Reported EPS", etc.).
    est_col = next((c for c in df.columns if "Estimate" in str(c)), None)
    act_col = next((c for c in df.columns if "Reported" in str(c) or "Actual" in str(c)), None)
    if est_col is None or act_col is None:
        print(f"⚠️  Warning: unexpected earnings columns for '{ticker}': {list(df.columns)}")
        return None

    # Move the date index into a column.
    df = df.reset_index()
    date_col = df.columns[0]

    out = pd.DataFrame({
        "Date": df[date_col],
        "EPS_Estimate": df[est_col],
        "EPS_Actual": df[act_col],
    })

    # Keep only quarters that have actually been reported (Actual is present),
    # then take the most recent 8, newest first.
    out = out[out["EPS_Actual"].notna()].copy()
    if out.empty:
        print(f"⚠️  Warning: no *reported* earnings yet for '{ticker}'.")
        return None
    out = out.sort_values("Date", ascending=False).head(8).reset_index(drop=True)

    # Earnings "surprise": how far actual EPS beat (or missed) the estimate.
    def _surprise(row):
        est, act = row["EPS_Estimate"], row["EPS_Actual"]
        if pd.isna(est) or pd.isna(act) or est == 0:
            return None
        return round(((act - est) / abs(est)) * 100, 2)

    out["Surprise_Pct"] = out.apply(_surprise, axis=1)
    return out


def get_earnings_calendar(ticker: str) -> Optional[dict]:
    """Return the next expected earnings date and any available estimates.

    Args:
        ticker: Stock symbol, e.g. "AAPL".

    Returns:
        A dict with the next earnings date plus EPS/revenue estimates where
        available, or None if nothing is available.
    """
    ticker = ticker.strip().upper()
    t = yf.Ticker(ticker)

    try:
        cal = t.calendar
    except Exception as err:  # noqa: BLE001
        print(f"⚠️  Warning: could not fetch calendar for '{ticker}': {err}")
        return None

    if cal is None or (hasattr(cal, "__len__") and len(cal) == 0):
        print(f"⚠️  Warning: no earnings calendar available for '{ticker}'.")
        return None

    # Modern yfinance returns `.calendar` as a dict. (Older versions returned a
    # DataFrame — we handle that too, just in case.)
    if isinstance(cal, dict):
        earnings_dates = cal.get("Earnings Date")
        # "Earnings Date" is usually a list of upcoming dates; the first is next.
        if isinstance(earnings_dates, (list, tuple)) and earnings_dates:
            next_date = earnings_dates[0]
        else:
            next_date = earnings_dates  # could be a single date or None

        result = {
            "next_earnings_date": next_date,
            "eps_estimate_avg": cal.get("Earnings Average"),
            "eps_estimate_high": cal.get("Earnings High"),
            "eps_estimate_low": cal.get("Earnings Low"),
            "revenue_estimate_avg": cal.get("Revenue Average"),
            "revenue_estimate_high": cal.get("Revenue High"),
            "revenue_estimate_low": cal.get("Revenue Low"),
        }
    else:
        # DataFrame fallback: try to read the earnings date row.
        try:
            result = {"next_earnings_date": cal.loc["Earnings Date"].iloc[0]}
        except Exception:
            result = None

    if result is None or result.get("next_earnings_date") is None:
        print(f"⚠️  Warning: no upcoming earnings date found for '{ticker}'.")
        return None

    return result


if __name__ == "__main__":
    # Make pandas show the full earnings-history table in the console.
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    ticker = "AAPL"

    print(f"=== Fundamentals for {ticker} ===")
    fundamentals = get_fundamentals(ticker)
    for key, value in fundamentals.items():
        print(f"  {key}: {value}")

    print(f"\n=== Earnings history for {ticker} (most recent first) ===")
    history = get_earnings_history(ticker)
    if history is not None:
        print(history.to_string(index=False))
    else:
        print("  No earnings history available.")

    print(f"\n=== Next earnings for {ticker} ===")
    calendar = get_earnings_calendar(ticker)
    if calendar is not None:
        for key, value in calendar.items():
            print(f"  {key}: {value}")
    else:
        print("  No upcoming earnings info available.")
