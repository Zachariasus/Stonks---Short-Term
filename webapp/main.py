"""
webapp/main.py
==============
FastAPI backend for the Stonks trading system (Phase 9, Step 1).

WHAT THIS IS
    A THIN API wrapper. All the intelligence — screening, grading, sizing, news —
    already lives in data/, analysis/, screener/, grader/, and news/. This file
    adds NO business logic: it only routes HTTP requests to those existing
    functions, serializes the results to JSON (via Pydantic models), and handles
    errors. If you find yourself computing something here, it belongs in a lower
    layer instead.

RUN IT
    From the project root, with the venv active:
        python webapp/run.py
    Interactive docs are then auto-generated at http://localhost:8000/docs
"""

import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

# Make project imports resolve no matter how the app is launched (uvicorn from
# the project root, or a bare run). webapp/main.py → parent.parent = project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

# --- Existing backend functions (the API just exposes these) ---
from analysis.earnings_calendar import get_earnings_summary  # noqa: E402
from analysis.sector_ranker import rank_sectors  # noqa: E402
from data.database import init_db  # noqa: E402
from data.db_reader import get_price_bars  # noqa: E402
from grader.ai_grader import grade_stock  # noqa: E402
from grader.position_sizer import calculate_full_risk_profile  # noqa: E402
from news.relevance_scorer import get_relevant_news, search_news  # noqa: E402
from news.source_bias import lookup as bias_lookup  # noqa: E402
from screener.flag_generator import get_active_flags  # noqa: E402

API_VERSION = "0.1.0"

# The built React frontend (webapp/frontend/dist). Resolved from this file's
# location so it works regardless of the current working directory.
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run once on startup: make sure the database/tables exist before serving."""
    init_db()
    yield
    # (nothing to tear down)


app = FastAPI(title="Stonks Trading System", version=API_VERSION, lifespan=lifespan)

# CORS: the production app serves the frontend on the SAME origin (:8000), so it
# needs no CORS. We also allow the Vite dev server (:5173/:5174) so `npm run dev`
# can call this backend cross-origin during development. Localhost-only — we'll
# reopen it for the deployed origin later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Response models
#
# Why response models matter: they make the API self-documenting (FastAPI turns
# them into the OpenAPI schema at /docs) AND they validate/serialize output —
# FastAPI coerces whatever we return into exactly these shapes, so the frontend
# always gets a predictable JSON contract. Most fields are Optional because the
# underlying functions legitimately return None when data is missing.
# ---------------------------------------------------------------------------
class FlagResponse(BaseModel):
    # from_attributes lets us validate straight from a Flag ORM object.
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    flagged_date: Optional[date] = None        # stable original entry date
    stage_start_date: Optional[date] = None     # start of the current stage run (span start)
    last_seen_date: Optional[date] = None       # most recent qualifying scan (span end)
    score: Optional[int] = None
    confidence_label: Optional[str] = None
    direction: Optional[str] = None
    stage: Optional[str] = None
    rs_label: Optional[str] = None
    sector_etf: Optional[str] = None
    sector_rotation_label: Optional[str] = None
    entry_price: Optional[float] = None
    target_price: Optional[float] = None
    suggested_stop: Optional[float] = None
    rr_ratio: Optional[float] = None
    earnings_flag: Optional[str] = None
    days_to_earnings: Optional[int] = None
    status: Optional[str] = None


class NewsArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    title: Optional[str] = None
    source: Optional[str] = None
    published_at: Optional[datetime] = None
    url: Optional[str] = None
    relevance_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    content_snippet: Optional[str] = None
    # Outlet + bias, looked up from the curated source list at read time.
    outlet: Optional[str] = None
    bias: Optional[str] = None
    reliability: Optional[str] = None
    homepage: Optional[str] = None  # outlet homepage (click the outlet name)


class GradeRequest(BaseModel):
    ticker: str
    account_size: float = 50000.0
    risk_pct: float = 0.01


class GradeResponse(BaseModel):
    ticker: str
    grade: Optional[str] = None
    one_line_verdict: Optional[str] = None
    bull_case: Optional[str] = None
    bear_case: Optional[str] = None
    key_risks: List[str] = []
    suggested_action: Optional[str] = None
    confluence_score: Optional[int] = None
    confidence_label: Optional[str] = None
    direction: Optional[str] = None
    # Per-engine breakdown (maxes are fixed by the scorer: 35/25/25/15).
    engine_1_pts: Optional[int] = None
    engine_2_pts: Optional[int] = None
    engine_3_pts: Optional[int] = None
    engine_4_pts: Optional[int] = None
    engines_firing: Optional[int] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    shares: Optional[int] = None
    position_value: Optional[float] = None
    rr_ratio: Optional[float] = None
    rr_label: Optional[str] = None
    next_earnings_date: Optional[date] = None
    days_to_earnings: Optional[int] = None
    earnings_flag: Optional[str] = None
    stub: bool = False


# ---------------------------------------------------------------------------
# Simple 1-hour in-memory cache for the sector rankings (expensive: 11 RS
# profiles per recompute; the picture changes slowly). Not a cache library —
# just a dict with a timestamp, as intended for this small need.
# ---------------------------------------------------------------------------
SECTOR_CACHE_TTL = 3600  # seconds
_sector_cache = {"data": None, "ts": 0.0}


def _get_sector_rankings():
    now = time.time()
    if _sector_cache["data"] is None or (now - _sector_cache["ts"]) > SECTOR_CACHE_TTL:
        # use_cache=False forces a fresh recompute (bypasses rank_sectors' own
        # process-lifetime memo so our 1-hour TTL is the source of truth).
        _sector_cache["data"] = rank_sectors(use_cache=False)
        _sector_cache["ts"] = now
    return _sector_cache["data"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness check — no DB call, just confirms the server is up."""
    return {"status": "ok", "version": API_VERSION}


@app.get("/flags", response_model=List[FlagResponse])
def list_flags(direction: Optional[str] = None, min_score: int = 0):
    """All active flags, optionally filtered by direction / minimum score."""
    flags = get_active_flags(direction=direction, min_score=min_score)
    return [FlagResponse.model_validate(f) for f in flags]  # [] if none — never 404


@app.get("/flags/{ticker}", response_model=FlagResponse)
def get_flag(ticker: str):
    """The most recent active flag for a single ticker (404 if there is none)."""
    ticker = ticker.strip().upper()
    matches = [f for f in get_active_flags() if f.ticker == ticker]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No active flag for {ticker}")
    # Most recent by flagged_date.
    latest = max(matches, key=lambda f: f.flagged_date)
    return FlagResponse.model_validate(latest)


def _enrich_news(article: dict) -> dict:
    """Add outlet/bias/reliability to a stored-article dict (read-time tagging)."""
    tag = bias_lookup(url=article.get("url"), source_name=article.get("source"))
    return {
        **article,
        "outlet": tag["outlet"],
        "bias": tag["bias"],
        "reliability": tag["reliability"],
        "homepage": tag.get("homepage"),
    }


@app.get("/watchlist-news", response_model=List[NewsArticleResponse])
def watchlist_news(limit: int = 40, per_ticker: int = 8, min_relevance: int = 30):
    """Home feed: news for the active flagged stocks, most-recently-flagged first.

    Iterates flagged tickers by flag recency (stage_start_date, then flagged_date)
    and collects each one's relevant news, deduped by URL — so the freshest flags
    surface at the top of the feed.
    """
    flags = get_active_flags()
    flags_sorted = sorted(
        flags,
        key=lambda f: (f.stage_start_date or f.flagged_date or date.min, f.flagged_date or date.min),
        reverse=True,
    )

    out = []
    seen_urls = set()
    seen_tickers = set()
    for f in flags_sorted:
        if f.ticker in seen_tickers:
            continue
        seen_tickers.add(f.ticker)
        for a in get_relevant_news(f.ticker, min_relevance=min_relevance, limit=per_ticker):
            if a.get("url") in seen_urls:
                continue
            seen_urls.add(a["url"])
            out.append(_enrich_news(a))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return [NewsArticleResponse.model_validate(a) for a in out]


@app.get("/news-search", response_model=List[NewsArticleResponse])
def news_search(q: str = "", limit: int = 30):
    """Broad headline search by company name, ticker, or keyword ([] if none)."""
    articles = search_news(q, limit=limit)
    return [NewsArticleResponse.model_validate(_enrich_news(a)) for a in articles]


@app.get("/news/{ticker}", response_model=List[NewsArticleResponse])
def get_news(ticker: str, limit: int = 20, min_relevance: int = 40):
    """Relevance-filtered news for a ticker, newest first ([] if none — never 404)."""
    ticker = ticker.strip().upper()
    articles = get_relevant_news(ticker, min_relevance=min_relevance, limit=limit)
    return [NewsArticleResponse.model_validate(_enrich_news(a)) for a in articles]


@app.get("/sector-rankings")
def sector_rankings():
    """The 11 sector ETFs ranked by relative strength (1-hour cached)."""
    return _get_sector_rankings()


@app.post("/grade", response_model=GradeResponse)
def grade(req: GradeRequest):
    """Full single-stock grade: AI letter grade + position sizing + earnings.

    The most expensive endpoint (it runs the whole analysis pipeline). Guards:
    a ticker with no stored price data → 422 with a clear message; any other
    failure → 500 with the error string (never a bare crash).
    """
    ticker = req.ticker.strip().upper()

    # Guard: no price data → can't analyze. 422 (unprocessable) with guidance.
    bars = get_price_bars(ticker, days=5)
    if bars is None or bars.empty:
        raise HTTPException(
            status_code=422,
            detail=f"No price data for {ticker} — run the data scheduler first",
        )

    try:
        grade_result = grade_stock(ticker)
        risk = calculate_full_risk_profile(
            ticker, account_size=req.account_size, risk_pct=req.risk_pct
        ) or {}
        earnings = get_earnings_summary(ticker)
    except HTTPException:
        raise  # let deliberate HTTP errors through unchanged
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Grade failed for {ticker}: {err}")

    return GradeResponse(
        ticker=ticker,
        grade=grade_result.get("grade"),
        one_line_verdict=grade_result.get("one_line_verdict"),
        bull_case=grade_result.get("bull_case"),
        bear_case=grade_result.get("bear_case"),
        key_risks=grade_result.get("key_risks") or [],
        suggested_action=grade_result.get("suggested_action"),
        confluence_score=grade_result.get("confluence_score"),
        confidence_label=grade_result.get("confidence_label"),
        direction=grade_result.get("direction"),
        engine_1_pts=grade_result.get("engine_1_pts"),
        engine_2_pts=grade_result.get("engine_2_pts"),
        engine_3_pts=grade_result.get("engine_3_pts"),
        engine_4_pts=grade_result.get("engine_4_pts"),
        engines_firing=grade_result.get("engines_firing"),
        entry_price=risk.get("entry_price"),
        stop_price=risk.get("stop_price"),
        target_price=risk.get("target_price"),
        shares=risk.get("shares"),
        position_value=risk.get("position_value"),
        rr_ratio=risk.get("rr_ratio"),
        rr_label=risk.get("rr_label"),
        next_earnings_date=earnings.get("next_earnings_date"),
        days_to_earnings=earnings.get("days_to_earnings"),
        earnings_flag=earnings.get("flag"),
        stub=grade_result.get("stub", False),
    )


# Global safety net: any unhandled exception → clean 500 JSON instead of a crash.
# (FastAPI routes HTTPException — 404/422 above — to its own handler first, so
# this only catches genuinely unexpected errors.)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal error", "error": str(exc)},
    )


# ---------------------------------------------------------------------------
# Serve the built React frontend (single-process production-style local run).
# Registered AFTER all API routes so /health, /flags, /grade, etc. take
# precedence. The hashed JS/CSS bundles live under /assets; the catch-all below
# returns index.html for every other path so React Router's client-side routes
# (/, /news, /grader, /sectors) resolve on a hard refresh or a direct visit.
# ---------------------------------------------------------------------------
app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    return FileResponse(str(FRONTEND_DIST / "index.html"))


if __name__ == "__main__":
    # Don't run this module directly — start the server via the launcher so
    # reload + host/port are configured consistently:
    #     python webapp/run.py
    print("Start the API with:  python webapp/run.py   (from the project root)")
