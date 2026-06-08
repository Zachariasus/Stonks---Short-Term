"""
data/universe_etfs.py
=====================
Reference tickers (sector ETFs + broad-market benchmarks) — DATA ONLY.

WHAT THIS FILE IS
    A plain constants file (no functions, just data). It defines the ETF and
    index proxies that stand in for "the sector" and "the market" when we
    measure a stock's relative strength.

HOW OTHER MODULES SHOULD USE IT
    Import the constant(s) you need, e.g.:
        from data.universe_etfs import SECTOR_ETFS, BENCHMARKS, ALL_REFERENCE_TICKERS

    - SECTOR_ETFS:          {etf_ticker: sector_name}  — map a stock's sector to
                            the ETF that represents it.
    - BENCHMARKS:           {ticker: index_name}       — the broad-market gauges.
    - ALL_REFERENCE_TICKERS: flat list of every reference ticker, convenient for
                            bulk-fetching them all at once (see fetcher_etfs.py).
"""

# The 11 SPDR sector ETFs — one per stock-market sector. Comparing a stock to
# its OWN sector ETF answers: "is this name leading or lagging its peers?"
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials",
    "XLC": "Communication Services",
}

# Broad-market benchmarks — the "market" half of relative strength.
BENCHMARKS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
}

# A single flat list of every reference ticker (sectors first, then benchmarks).
# 11 sector ETFs + 3 benchmarks = 14 tickers.
ALL_REFERENCE_TICKERS = list(SECTOR_ETFS.keys()) + list(BENCHMARKS.keys())
