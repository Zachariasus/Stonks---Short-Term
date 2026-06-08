# Stonks — Short-Term Stock Trading Analysis

Stonks is a web application for analyzing US equities to support short-term trading
decisions. It pulls market data, fundamentals, and news, runs them through four
analysis engines (trend, fundamentals, top-down, and valuation), screens the broad
universe of stocks to flag promising setups, and produces a deep single-stock report
with an AI-generated letter grade. The whole system is exposed through a FastAPI
backend and a React frontend.

## Project structure

- `data/` — fetching and storing price, fundamentals, and news data
- `analysis/` — the four analysis engines: trend, fundamentals, top-down, valuation
- `screener/` — scans the universe of stocks and flags setups
- `grader/` — single-stock deep analysis + AI-generated letter grade
- `news/` — news aggregation and relevance scoring
- `webapp/` — FastAPI backend + React frontend
