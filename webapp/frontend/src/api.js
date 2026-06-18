// src/api.js
// ==========
// Thin API client — the single place every component talks to the backend.
//
// WHY CENTRALIZE: if the base URL, auth headers, timeouts, or error handling ever
// change, we edit them HERE once instead of hunting through every component. Pages
// import these named functions and never touch axios directly.

import axios from "axios";

// Vite inlines VITE_* env vars at build time. In DEV (npm run dev), .env sets
// VITE_API_BASE_URL=http://localhost:8000 so the Vite server reaches the separate
// backend. In the PRODUCTION build it's empty (.env.production), so we fall back to
// a RELATIVE base — FastAPI serves this app on the same origin, so "/flags" etc.
// resolve to the same host automatically (no CORS needed).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000, // /grade is slow (runs the whole pipeline) — give it room
});

// GET /flags — all active flags, optionally filtered server-side.
export async function fetchFlags(direction = null, minScore = 0) {
  try {
    const params = {};
    if (direction) params.direction = direction;
    if (minScore) params.min_score = minScore;
    const { data } = await client.get("/flags", { params });
    return data;
  } catch (err) {
    console.error("fetchFlags failed:", err);
    throw err;
  }
}

// GET /stocks — every S&P 500 stock with its latest screen read (flagged enriched).
// The page filters client-side, so we fetch the full set by default.
export async function fetchStocks(flaggedOnly = false) {
  try {
    const params = flaggedOnly ? { flagged_only: true } : {};
    const { data } = await client.get("/stocks", { params });
    return data;
  } catch (err) {
    console.error("fetchStocks failed:", err);
    throw err;
  }
}

// GET /watchlist-news — the home feed: news across the flagged stocks.
export async function fetchWatchlistNews(limit = 40) {
  try {
    const { data } = await client.get("/watchlist-news", { params: { limit } });
    return data;
  } catch (err) {
    console.error("fetchWatchlistNews failed:", err);
    throw err;
  }
}

// GET /news/{ticker} — relevance-filtered news for one ticker.
export async function fetchNews(ticker, limit = 20) {
  try {
    const { data } = await client.get(`/news/${ticker}`, { params: { limit } });
    return data;
  } catch (err) {
    console.error(`fetchNews(${ticker}) failed:`, err);
    throw err;
  }
}

// GET /news-search — broad search by company name, ticker, or keyword.
export async function searchNews(q, limit = 30) {
  try {
    const { data } = await client.get("/news-search", { params: { q, limit } });
    return data;
  } catch (err) {
    console.error(`searchNews(${q}) failed:`, err);
    throw err;
  }
}

// POST /grade — full AI grade + position sizing for one ticker.
export async function gradeStock(ticker, accountSize = 50000, riskPct = 0.01) {
  try {
    const { data } = await client.post("/grade", {
      ticker,
      account_size: accountSize,
      risk_pct: riskPct,
    });
    return data;
  } catch (err) {
    console.error(`gradeStock(${ticker}) failed:`, err);
    throw err;
  }
}

// GET /sector-rankings — the 11 sector ETFs ranked by relative strength.
export async function fetchSectorRankings() {
  try {
    const { data } = await client.get("/sector-rankings");
    return data;
  } catch (err) {
    console.error("fetchSectorRankings failed:", err);
    throw err;
  }
}
