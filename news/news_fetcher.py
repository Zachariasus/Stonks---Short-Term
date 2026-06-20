"""
news/news_fetcher.py
====================
Financial news fetcher + storage layer (Phase 8, Step 1).

WHAT THIS DOES
    Pulls recent headlines for a ticker from NewsAPI.org, normalizes them, and
    stores them in SQLite — deduplicated by URL. The web app's news feed reads
    from this table. News is CONTEXT, not signal: the quantitative engines decide
    what to trade; the news feed helps explain WHY a trend is moving.

GRACEFUL DEGRADATION
    NEWS_API_KEY may still be a placeholder. Every public function degrades
    cleanly — fetch_news returns [] (never None) on a missing key, a bad key, a
    rate-limit, or a network error, and the storage/print helpers handle an empty
    list without complaint.

LAYER NOTE
    This is the ONLY news module that touches the network. Analysis/grader code
    reads stored articles via get_stored_news() — it never calls NewsAPI directly.
"""

from datetime import datetime, timedelta, timezone

import requests

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.config import NEWS_API_KEY
    from data.database import NewsArticle, TickerUniverse, get_session, init_db
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import NEWS_API_KEY
    from data.database import NewsArticle, TickerUniverse, get_session, init_db

NEWSAPI_BASE_URL = "https://newsapi.org/v2/everything"

# ---------------------------------------------------------------------------
# NEWSAPI_RATE_LIMIT — free-tier constraints (NOT code, just the contract):
#   • 100 requests / day
#   • articles up to 30 days old only
#   • max 100 results per request (pageSize)
#   • the free "Developer" plan is non-commercial and may delay recent articles
#
# Implication: we must be SELECTIVE. Only fetch news for FLAGGED tickers (a
# handful), never the full 500-name universe — one request per ticker would blow
# the 100/day budget almost immediately. The screener decides what's worth
# watching; news follows the flags.
# ---------------------------------------------------------------------------


def build_query(ticker: str) -> str:
    """Build the NewsAPI search query for a ticker.

    We OR the symbol with the company name because financial articles almost
    never write the ticker symbol — they say "Apple", not "AAPL". Searching on
    the symbol alone would miss the bulk of relevant coverage (and "AAPL" alone
    also pulls in unrelated noise). The company name comes from TickerUniverse;
    if we don't have it, we fall back to the bare symbol.
    """
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker)
            .first()
        )
        company_name = row.company_name if row else None
    finally:
        session.close()

    if company_name:
        return f"{ticker} OR {company_name}"
    return ticker


def _parse_published(iso_str):
    """Parse NewsAPI's ISO-8601 publishedAt ("2026-06-09T10:30:00Z") → naive UTC datetime."""
    if not iso_str:
        return None
    try:
        # Replace the trailing 'Z' (UTC) with an explicit offset, parse, then drop
        # tzinfo so SQLite stores a clean naive-UTC timestamp.
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _key_is_set() -> bool:
    """True only if a real NewsAPI key is configured (not missing/placeholder)."""
    return bool(NEWS_API_KEY) and NEWS_API_KEY != "your_key_here"


def _parse_epoch(ts):
    """Parse a Unix epoch seconds value → naive UTC datetime (or None)."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def fetch_news_yfinance(ticker: str, max_articles: int = 20) -> list:
    """Fetch recent headlines for a ticker from yfinance — FREE, no API key.

    yfinance exposes Yahoo Finance's per-ticker news feed (real headlines, real
    publishers, working links). Handles both the new nested shape
    ({'content': {...}}) and the older flat shape. Returns [] on any failure.
    """
    ticker = ticker.strip().upper()
    try:
        import yfinance as yf  # already a project dependency

        raw = yf.Ticker(ticker).news or []
    except Exception as err:  # noqa: BLE001 - network / yfinance internals
        print(f"⚠️  fetch_news_yfinance: yfinance news failed for {ticker} — {err}")
        return []

    articles = []
    for item in raw[:max_articles]:
        # New yfinance nests fields under 'content'; the old shape is flat.
        c = item.get("content") or item
        url = (
            (c.get("canonicalUrl") or {}).get("url")
            or (c.get("clickThroughUrl") or {}).get("url")
            or c.get("link")  # legacy flat shape
        )
        if not url:
            continue
        # Publisher name: new = provider.displayName, legacy = publisher.
        source = (c.get("provider") or {}).get("displayName") or c.get("publisher")
        # Date: new = ISO 'pubDate'; legacy = epoch 'providerPublishTime'.
        pub = c.get("pubDate") or c.get("displayTime")
        published_at = (
            _parse_published(pub) if isinstance(pub, str)
            else _parse_epoch(c.get("providerPublishTime"))
        )
        raw_text = c.get("summary") or c.get("description") or ""
        articles.append(
            {
                "ticker": ticker,
                "url": url,
                "title": c.get("title"),
                "source": source,
                "published_at": published_at,
                "content_snippet": raw_text[:200] if raw_text else None,
            }
        )
    return articles


def fetch_news(ticker: str, days_back: int = 7, max_articles: int = 20) -> list:
    """Fetch recent articles for a ticker. Returns [] on any failure (never None).

    DEFAULT is the FREE yfinance source — no key required. If a real NEWS_API_KEY
    is configured, use NewsAPI instead (broader/older coverage), falling back to
    yfinance if NewsAPI yields nothing. Mirrors the grader's keyless-by-default,
    API-as-an-upgrade pattern.
    """
    if not _key_is_set():
        return fetch_news_yfinance(ticker, max_articles=max_articles)
    articles = _fetch_newsapi(ticker, days_back=days_back, max_articles=max_articles)
    return articles if articles else fetch_news_yfinance(ticker, max_articles=max_articles)


def _fetch_newsapi(ticker: str, days_back: int = 7, max_articles: int = 20) -> list:
    """Fetch recent articles for a ticker from NewsAPI (the opt-in upgrade path).

    Never returns None and never raises — a missing/placeholder key, a NewsAPI
    error (bad key, rate limit), invalid JSON, or a network error all degrade to
    an empty list with a printed warning.
    """
    ticker = ticker.strip().upper()
    key = NEWS_API_KEY

    # Graceful no-op when the key isn't configured yet.
    if not _key_is_set():
        return []

    from_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    params = {
        "q": build_query(ticker),
        "from": from_date,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": max_articles,
        "apiKey": key,
    }

    # --- Network call, guarded ---
    try:
        resp = requests.get(NEWSAPI_BASE_URL, params=params, timeout=15)
    except requests.RequestException as err:
        print(f"⚠️  fetch_news: network error for {ticker} — {err}")
        return []

    # --- HTTP-level errors (NewsAPI puts a 'message' in the JSON body) ---
    if resp.status_code != 200:
        try:
            msg = resp.json().get("message", resp.text[:200])
        except ValueError:
            msg = resp.text[:200]
        print(f"⚠️  fetch_news: NewsAPI HTTP {resp.status_code} for {ticker} — {msg}")
        return []

    # --- Body parsing + API-level status ---
    try:
        payload = resp.json()
    except ValueError:
        print(f"⚠️  fetch_news: invalid JSON from NewsAPI for {ticker}.")
        return []

    if payload.get("status") != "ok":
        print(
            f"⚠️  fetch_news: NewsAPI status '{payload.get('status')}' for "
            f"{ticker} — {payload.get('message')}"
        )
        return []

    # --- Normalize into our flat dict shape ---
    articles = []
    for a in payload.get("articles", []):
        url = a.get("url")
        if not url:
            continue  # no URL = no dedup key = unusable
        # Prefer the truncated 'content', fall back to 'description'.
        raw = a.get("content") or a.get("description") or ""
        snippet = raw[:200] if raw else None
        articles.append(
            {
                "ticker": ticker,
                "url": url,
                "title": a.get("title"),
                "source": (a.get("source") or {}).get("name"),
                "published_at": _parse_published(a.get("publishedAt")),
                "content_snippet": snippet,
            }
        )
    return articles


def save_articles(articles: list) -> dict:
    """Insert new articles (by URL) into news_articles; skip duplicates.

    Returns {inserted, skipped_duplicates}. Dedup happens both against the DB
    (URL already stored) and within the batch (same URL twice in one response).
    """
    if not articles:
        return {"inserted": 0, "skipped_duplicates": 0}

    inserted = 0
    skipped = 0
    seen_in_batch = set()

    session = get_session()
    try:
        for art in articles:
            url = art.get("url")
            if not url:
                continue

            # Duplicate within this same batch?
            if url in seen_in_batch:
                skipped += 1
                continue
            seen_in_batch.add(url)

            # Already stored from a previous fetch?
            exists = session.query(NewsArticle).filter_by(url=url).first()
            if exists:
                skipped += 1
                continue

            session.add(
                NewsArticle(
                    ticker=art["ticker"],
                    url=url,
                    title=art.get("title"),
                    source=art.get("source"),
                    published_at=art.get("published_at"),
                    content_snippet=art.get("content_snippet"),
                )
            )
            inserted += 1

        session.commit()
    finally:
        session.close()

    return {"inserted": inserted, "skipped_duplicates": skipped}


def fetch_and_store_news(ticker: str, days_back: int = 7) -> dict:
    """Fetch then store news for a ticker; print a one-line summary."""
    ticker = ticker.strip().upper()
    articles = fetch_news(ticker, days_back=days_back)
    result = save_articles(articles)
    print(
        f"{ticker}: fetched {len(articles)} articles, "
        f"{result['inserted']} new, {result['skipped_duplicates']} duplicates skipped"
    )
    return result


def get_stored_news(ticker: str, limit: int = 10) -> list:
    """Return the most recent stored articles for a ticker (newest first)."""
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        rows = (
            session.query(NewsArticle)
            .filter(NewsArticle.ticker == ticker)
            .order_by(NewsArticle.published_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ticker": r.ticker,
                "url": r.url,
                "title": r.title,
                "source": r.source,
                "published_at": r.published_at,
                "content_snippet": r.content_snippet,
                "sentiment_label": r.sentiment_label,
                "relevance_score": r.relevance_score,
            }
            for r in rows
        ]
    finally:
        session.close()


if __name__ == "__main__":
    # Make sure the news_articles table exists (no-op if already created).
    init_db()

    key_set = bool(NEWS_API_KEY) and NEWS_API_KEY != "your_key_here"

    print("=== First fetch ===")
    fetch_and_store_news("AAPL", days_back=7)

    if not key_set:
        # Expected path right now — the key is still a placeholder.
        print("\nSet NEWS_API_KEY in .env to enable news fetching.")
    else:
        # Live path — show the three most recent stored articles.
        print("\nMost recent 3 stored AAPL articles:")
        for i, art in enumerate(get_stored_news("AAPL", limit=3), start=1):
            dt = art["published_at"]
            dt_str = dt.strftime("%Y-%m-%d") if dt else "n/a"
            print(f"[{i}] {art['title']} | {art['source']} | {dt_str}")
            if art.get("content_snippet"):
                print(f"    {art['content_snippet']}")

    # Second fetch — proves deduplication (with a live key this shows "0 new").
    print("\n=== Second fetch (dedup check) ===")
    fetch_and_store_news("AAPL")
