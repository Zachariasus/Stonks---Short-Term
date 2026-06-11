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
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

# --- Existing backend functions (the API just exposes these) ---
from analysis.earnings_calendar import get_earnings_summary  # noqa: E402
from analysis.sector_ranker import rank_sectors  # noqa: E402
from data.database import init_db  # noqa: E402
from data.db_reader import get_price_bars  # noqa: E402
from grader.ai_grader import grade_stock  # noqa: E402
from grader.position_sizer import calculate_full_risk_profile  # noqa: E402
from news.relevance_scorer import get_relevant_news  # noqa: E402
from screener.flag_generator import get_active_flags  # noqa: E402

API_VERSION = "0.1.0"


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

# CORS: the React dev server runs on a different origin (e.g. localhost:5173),
# so the browser needs permission to call this API. Wildcard origins are fine for
# local dev; credentials stay off (a "*" origin with credentials is invalid CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    flagged_date: Optional[date] = None
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


@app.get("/news/{ticker}", response_model=List[NewsArticleResponse])
def get_news(ticker: str, limit: int = 10, min_relevance: int = 40):
    """Relevance-filtered news for a ticker, newest first ([] if none — never 404)."""
    ticker = ticker.strip().upper()
    articles = get_relevant_news(ticker, min_relevance=min_relevance, limit=limit)
    return [NewsArticleResponse.model_validate(a) for a in articles]


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


if __name__ == "__main__":
    # Don't run this module directly — start the server via the launcher so
    # reload + host/port are configured consistently:
    #     python webapp/run.py
    print("Start the API with:  python webapp/run.py   (from the project root)")
