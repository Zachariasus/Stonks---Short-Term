// src/pages/NewsPage.jsx
// ======================
// News feed: search a ticker, see its relevance-ranked, sentiment-tagged
// articles (newest first). Reads from the backend's /news/{ticker}, which is
// populated by the news scheduler.

import { useEffect, useState } from "react";
import { fetchNews } from "../api";

// "2026-06-09T10:30:00" → "Jun 9, 2026"
function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function SentimentBadge({ sentiment }) {
  const cls =
    sentiment === "Bullish"
      ? "bg-green-500/20 text-green-400"
      : sentiment === "Bearish"
      ? "bg-red-500/20 text-red-400"
      : "bg-slate-600/40 text-slate-300";
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {sentiment || "Neutral"}
    </span>
  );
}

function ArticleCard({ article }) {
  const score = article.relevance_score ?? 0;
  const snippet = article.content_snippet || "";
  const shown = snippet.length > 120 ? `${snippet.slice(0, 120)}…` : snippet;

  return (
    <div className="border border-slate-800 rounded-lg p-4 bg-slate-800/30">
      <div className="flex items-start justify-between gap-4">
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-white font-medium hover:text-green-400"
        >
          {article.title || "(untitled)"}
        </a>
        <SentimentBadge sentiment={article.sentiment_label} />
      </div>

      <div className="mt-1 text-xs text-slate-400">
        {article.source || "Unknown source"} &nbsp;|&nbsp; {formatDate(article.published_at)}
      </div>

      {/* Relevance bar: green fill proportional to score / 100 */}
      <div className="mt-3 flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded bg-slate-700 overflow-hidden">
          <div
            className="h-full bg-green-500"
            style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
          />
        </div>
        <span className="text-xs text-slate-500 w-14 text-right">rel {Math.round(score)}</span>
      </div>

      {shown && <p className="mt-2 text-sm text-slate-300">{shown}</p>}
    </div>
  );
}

export default function NewsPage() {
  const [ticker, setTicker] = useState("AAPL");
  const [searched, setSearched] = useState("AAPL");
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function runSearch(symbol) {
    const t = (symbol || "").trim().toUpperCase();
    if (!t) return;
    setLoading(true);
    setError(null);
    setSearched(t);
    try {
      const data = await fetchNews(t, 20);
      // Newest first.
      data.sort((a, b) => new Date(b.published_at) - new Date(a.published_at));
      setArticles(data);
    } catch (err) {
      setError(err.message || "Request failed");
      setArticles([]);
    } finally {
      setLoading(false);
    }
  }

  // Fire an initial search on mount so the page isn't empty.
  useEffect(() => {
    runSearch("AAPL");
  }, []);

  return (
    <div className="p-4 md:p-6">
      {/* Search bar (full width on mobile) */}
      <form
        className="flex items-center gap-2 mb-6"
        onSubmit={(e) => {
          e.preventDefault();
          runSearch(ticker);
        }}
      >
        <input
          type="text"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Ticker (e.g. AAPL)"
          className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white flex-1 md:flex-none md:w-48 uppercase"
        />
        <button
          type="submit"
          className="bg-green-600 hover:bg-green-500 text-white text-sm font-medium px-4 py-2 rounded"
        >
          Search
        </button>
      </form>

      {loading && <div className="text-slate-300">Loading news for {searched}...</div>}
      {error && (
        <div className="text-red-400">
          Error loading news: {error}.
          <div className="text-slate-500 text-sm mt-1">Is the backend running?</div>
        </div>
      )}

      {!loading && !error && articles.length === 0 && (
        <div className="p-8 text-center text-slate-400 border border-slate-800 rounded">
          No news stored for {searched} — run the news scheduler to fetch articles
        </div>
      )}

      {!loading && !error && articles.length > 0 && (
        <div className="flex flex-col gap-3">
          {articles.map((a) => (
            <ArticleCard key={a.url} article={a} />
          ))}
        </div>
      )}
    </div>
  );
}
