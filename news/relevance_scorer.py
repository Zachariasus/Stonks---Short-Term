"""
news/relevance_scorer.py
========================
News relevance + sentiment scorer (Phase 8, Step 2).

WHAT THIS DOES
    The fetcher stores every article it finds; this module separates signal from
    noise. For each stored article it computes:
      • a 0–100 RELEVANCE score (is this article actually about the stock, or does
        it just mention it in passing?), via keyword proximity, and
      • a Bullish / Bearish / Neutral SENTIMENT label, via simple keyword counts.
    For the most relevant articles it can also call the Claude API to write a
    one-sentence summary. Results are written back to the NewsArticle row
    (relevance_score, sentiment_label, content_snippet).

DESIGN NOTES
    • Relevance matching uses WORD BOUNDARIES so short keywords (e.g. "AI") don't
      false-match inside other words ("r-AI-ses"). Sentiment matching uses
      SUBSTRINGS so a base word catches its variants ("beat" → "beats", "cut" →
      "cuts"). Both are deliberately simple — keyword proximity is a cheap, decent
      proxy for relevance without a real NLP model.
    • Claude summaries are best-effort: a missing key or any API error returns
      None and never crashes the scorer.
"""

import re

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.config import ANTHROPIC_API_KEY
    from data.database import NewsArticle, TickerUniverse, get_session
    from news.news_fetcher import get_stored_news
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import ANTHROPIC_API_KEY
    from data.database import NewsArticle, TickerUniverse, get_session
    from news.news_fetcher import get_stored_news

# Fast, cheap Claude model for the one-sentence summaries.
SUMMARY_MODEL = "claude-haiku-4-5-20251001"

# --- Sentiment keyword lists (deliberately simple; substring-matched) ---
BULLISH_WORDS = [
    "beat", "raised", "upgrade", "outperform", "record",
    "growth", "strong", "surge", "rally", "breakout",
    "expanded", "accelerating", "buyback", "dividend",
]
BEARISH_WORDS = [
    "miss", "cut", "downgrade", "underperform", "warning",
    "weak", "decline", "fell", "layoffs", "lawsuit",
    "investigation", "recall", "guidance cut", "slowing",
]

# --- Sector keyword lists, keyed by sector ETF (word-boundary matched) ---
# A sector-themed article (e.g. "chip demand") is mildly relevant to a stock in
# that sector even if the company isn't named — worth a small bump, not a lot.
SECTOR_KEYWORDS = {
    "XLK": ["tech", "technology", "semiconductor", "software", "AI", "chip", "cloud"],
    "XLF": ["bank", "financial", "interest rate", "Fed", "lending", "insurance"],
    "XLE": ["oil", "energy", "gas", "drilling", "refinery", "OPEC"],
    "XLV": ["pharma", "drug", "FDA", "biotech", "healthcare", "clinical"],
    "XLI": ["industrial", "manufacturing", "aerospace", "defense", "logistics"],
    "XLY": ["retail", "consumer", "e-commerce", "spending", "restaurant"],
    "XLP": ["staples", "grocery", "household", "beverage", "tobacco"],
    "XLU": ["utility", "electric", "power", "grid", "regulated"],
    "XLRE": ["real estate", "REIT", "property", "mortgage", "housing"],
    "XLB": ["materials", "mining", "chemical", "steel", "copper"],
    "XLC": ["telecom", "media", "streaming", "advertising", "social"],
}

# Generic corporate suffix tokens to strip so "Apple Inc." matches "Apple".
_CORP_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "holdings", "holding", "group", "the", "sa", "ag",
    "nv", "lp", "llc", "class",
}


def _clean_company_name(name):
    """Reduce a full company name to its core for matching ("Apple Inc." → "apple").

    Articles write "Apple", not "Apple Inc." — so we drop generic corporate
    suffixes and keep the distinctive part of the name.
    """
    if not name:
        return None
    tokens = re.sub(r"[^\w\s]", " ", name.lower()).split()
    kept = [t for t in tokens if t not in _CORP_SUFFIXES]
    cleaned = " ".join(kept).strip()
    return cleaned or name.lower()


def _contains_word(text, phrase):
    """True if `phrase` appears in `text` as a whole word/phrase (case-insensitive)."""
    if not text or not phrase:
        return False
    return re.search(r"\b" + re.escape(phrase) + r"\b", text, re.IGNORECASE) is not None


def _lookup_identity(ticker):
    """Return (company_name, sector_etf) for a ticker from TickerUniverse (or None, None)."""
    session = get_session()
    try:
        row = (
            session.query(TickerUniverse)
            .filter(TickerUniverse.ticker == ticker)
            .first()
        )
        if row is None:
            return (None, None)
        return (row.company_name, row.sector_etf)
    finally:
        session.close()


def score_relevance(article_dict, ticker) -> int:
    """Score how directly an article is about a stock, 0–100.

    Scoring (a headline mention counts for more than a buried one):
        +40  ticker symbol in title
        +20  ticker symbol in snippet only
        +20  company name in title
        +10  company name in snippet only
        +10  any sector keyword present
    A score >= 50 means the article is directly about the stock; < 30 means it's
    tangential (mentions it in passing or not at all).
    """
    ticker = ticker.strip().upper()
    title = article_dict.get("title") or ""
    snippet = article_dict.get("content_snippet") or ""

    company_name, sector_etf = _lookup_identity(ticker)
    company_core = _clean_company_name(company_name)

    score = 0

    # --- Ticker symbol (title weighted more than snippet) ---
    if _contains_word(title, ticker):
        score += 40
    elif _contains_word(snippet, ticker):
        score += 20

    # --- Company name (title weighted more than snippet) ---
    if company_core:
        if _contains_word(title, company_core):
            score += 20
        elif _contains_word(snippet, company_core):
            score += 10

    # --- Sector theme (small bump if any sector keyword appears anywhere) ---
    if sector_etf:
        combined = f"{title} {snippet}"
        keywords = SECTOR_KEYWORDS.get(sector_etf, [])
        if any(_contains_word(combined, kw) for kw in keywords):
            score += 10

    return min(score, 100)


def score_sentiment(article_dict) -> str:
    """Label an article Bullish / Bearish / Neutral by counting keyword hits.

    Substring matching so base words catch variants ("beat" → "beats").
    """
    title = article_dict.get("title") or ""
    snippet = article_dict.get("content_snippet") or ""
    text = f"{title} {snippet}".lower()

    bullish_count = sum(1 for word in BULLISH_WORDS if word.lower() in text)
    bearish_count = sum(1 for word in BEARISH_WORDS if word.lower() in text)

    if bullish_count > bearish_count:
        return "Bullish"
    if bearish_count > bullish_count:
        return "Bearish"
    return "Neutral"  # tie or no keywords at all


def get_claude_summary(title, snippet):
    """One-sentence Claude summary of an article. None on missing key or any error.

    Best-effort only: a summary failure must never crash the scorer, so the whole
    call is wrapped in try/except.
    """
    key = ANTHROPIC_API_KEY
    if not key or key == "your_key_here":
        return None  # silently skip — summaries are optional

    try:
        import anthropic  # lazy import — only when a key is configured

        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "In one sentence, summarize this financial news headline and its "
            f"likely impact on the stock: Title: {title}. Context: {snippet}"
        )
        message = client.messages.create(
            model=SUMMARY_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        ).strip()
        return text or None
    except Exception as err:  # noqa: BLE001 - never let a summary failure propagate
        print(f"⚠️  get_claude_summary: {err}")
        return None


def score_and_update_articles(ticker, min_relevance=30) -> dict:
    """Score every stored article for a ticker and write the results back to the DB.

    For each article: compute relevance + sentiment, and (only for relevant,
    not-yet-processed articles) attempt a Claude summary. We treat an article as
    "already processed" when its sentiment_label is already set — so summaries are
    attempted once, on first scoring, not regenerated on every run.
    """
    ticker = ticker.strip().upper()

    articles_scored = 0
    high_relevance = 0
    bullish = bearish = neutral = 0
    summaries_generated = 0

    session = get_session()
    try:
        rows = (
            session.query(NewsArticle)
            .filter(NewsArticle.ticker == ticker)
            .order_by(NewsArticle.published_at.desc())
            .limit(50)
            .all()
        )

        for r in rows:
            article = {"title": r.title, "content_snippet": r.content_snippet}
            relevance = score_relevance(article, ticker)
            sentiment = score_sentiment(article)

            # Summary only for relevant articles we haven't processed before.
            needs_summary = relevance >= min_relevance and r.sentiment_label is None
            if needs_summary:
                summary = get_claude_summary(r.title, r.content_snippet)
                if summary:
                    r.content_snippet = summary  # replace snippet with the summary
                    summaries_generated += 1

            # Write the scores back.
            r.relevance_score = relevance
            r.sentiment_label = sentiment

            # Tally.
            articles_scored += 1
            if relevance >= 50:
                high_relevance += 1
            if sentiment == "Bullish":
                bullish += 1
            elif sentiment == "Bearish":
                bearish += 1
            else:
                neutral += 1

        session.commit()
    finally:
        session.close()

    return {
        "ticker": ticker,
        "articles_scored": articles_scored,
        "high_relevance": high_relevance,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "summaries_generated": summaries_generated,
    }


def get_relevant_news(ticker, min_relevance=40, limit=10) -> list:
    """Return stored articles at/above a relevance threshold (newest first)."""
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        rows = (
            session.query(NewsArticle)
            .filter(
                NewsArticle.ticker == ticker,
                NewsArticle.relevance_score >= min_relevance,  # NULLs excluded by SQL
            )
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


def search_news(query, limit=30) -> list:
    """Broad news search: match by ticker, company NAME, or words in the headline.

    Resolves the query to tickers two ways — an exact ticker symbol, and any
    company whose name contains the query (so "apple" → AAPL via "Apple Inc.") —
    then returns stored articles for those tickers OR whose headline contains the
    query text. Newest first. Case-insensitive.
    """
    from sqlalchemy import func, or_

    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()

    session = get_session()
    try:
        # Tickers to include: exact symbol + any company name containing the query.
        tickers = {q.upper()}
        name_rows = (
            session.query(TickerUniverse.ticker)
            .filter(func.lower(TickerUniverse.company_name).like(f"%{ql}%"))
            .all()
        )
        tickers.update(t[0] for t in name_rows)

        rows = (
            session.query(NewsArticle)
            .filter(
                or_(
                    NewsArticle.ticker.in_(tickers),
                    func.lower(NewsArticle.title).like(f"%{ql}%"),
                )
            )
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
    from datetime import datetime

    # ── Insert 3 test articles for AAPL (same throwaway pattern as Step 1) ────
    # Snippets are chosen so the scorer lands where we expect:
    #   1) "Apple" in title (+20) + "AAPL" in snippet (+20) = 40 → relevant, Bullish
    #   2) "AAPL" in title (+40) + "Apple" in snippet (+10) = 50 → high-relevance, Bullish
    #   3) no AAPL/Apple/XLK terms → 0 → tangential; "rally" (bull) vs "weak" (bear) → Neutral
    TEST = [
        {
            "url": "https://test.local/aapl-earnings",
            "title": "Apple beats Q2 earnings estimates, raises guidance",
            "source": "Reuters",
            "published_at": datetime(2026, 6, 9, 10, 0),
            "content_snippet": (
                "Apple (AAPL) reported quarterly earnings that beat analyst "
                "estimates, and management raised its full-year guidance."
            ),
        },
        {
            "url": "https://test.local/aapl-iphone",
            "title": "AAPL shares surge on strong iPhone demand",
            "source": "Bloomberg",
            "published_at": datetime(2026, 6, 8, 14, 0),
            "content_snippet": (
                "Apple shares jumped after stronger-than-expected iPhone sales "
                "drove a record quarter."
            ),
        },
        {
            "url": "https://test.local/market-rally",
            "title": "Stock market rally continues amid Fed uncertainty",
            "source": "CNBC",
            "published_at": datetime(2026, 6, 7, 9, 0),
            "content_snippet": (
                "Major indexes rose as investors weighed weak manufacturing data "
                "against expectations for the Federal Reserve."
            ),
        },
    ]

    session = get_session()
    for t in TEST:
        session.add(NewsArticle(ticker="AAPL", **t))
    session.commit()
    session.close()

    try:
        # Score + update everything for AAPL.
        result = score_and_update_articles("AAPL")

        # Per-article scores (re-read so we see what was written to the DB).
        print("=== Scored articles (AAPL) ===")
        for art in get_stored_news("AAPL", limit=50):
            print(
                f"  rel={art['relevance_score']:>3.0f}  "
                f"{art['sentiment_label']:<8}  {art['title']}"
            )

        # Run summary.
        print("\n=== score_and_update_articles summary ===")
        for key, value in result.items():
            print(f"  {key}: {value}")

        # Relevance filter — only the 2 AAPL-focused articles should return.
        print("\n=== get_relevant_news('AAPL', min_relevance=40) ===")
        relevant = get_relevant_news("AAPL")
        print(f"  Returned {len(relevant)} article(s) (generic market article excluded):")
        for art in relevant:
            print(
                f"  - {art['title']}  "
                f"(relevance {art['relevance_score']:.0f}, {art['sentiment_label']})"
            )
    finally:
        # Clean up the test rows by URL.
        session = get_session()
        deleted = (
            session.query(NewsArticle)
            .filter(NewsArticle.url.in_([t["url"] for t in TEST]))
            .delete(synchronize_session=False)
        )
        session.commit()
        session.close()
        print(f"\nCleaned up {deleted} test articles.")
