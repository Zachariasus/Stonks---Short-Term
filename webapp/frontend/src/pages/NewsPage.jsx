// src/pages/NewsPage.jsx
// ======================
// News page. Defaults to a "watchlist" home feed — headlines for the most
// recently flagged stocks first. A search box looks up headlines for a specific
// ticker. Each item is just the HEADLINE + outlet + bias tag + a button to the
// ORIGINAL article (opens in a new tab) — no article body, no summary yet.

import { useEffect, useState } from "react";
import { fetchWatchlistNews, fetchNews } from "../api";

// "2026-06-17T10:30:00" → "Jun 17, 2026"
function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// Bias tag color: US convention (left = blue, right = red), plus non-political tags.
const BIAS_CLASS = {
  Left: "bg-blue-600/30 text-blue-300",
  "Lean Left": "bg-blue-500/20 text-blue-300",
  Center: "bg-slate-600/50 text-slate-200",
  "Lean Right": "bg-red-500/20 text-red-300",
  Right: "bg-red-600/30 text-red-300",
  "Corporate / Promotional": "bg-amber-500/20 text-amber-300",
  "Primary / Official": "bg-emerald-500/20 text-emerald-300",
  "User-generated / Sentiment": "bg-purple-500/20 text-purple-300",
};

function BiasBadge({ bias }) {
  if (!bias) return null;
  const cls = BIAS_CLASS[bias] || "bg-slate-700/50 text-slate-400";
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{bias}</span>;
}

function ArticleCard({ article }) {
  return (
    <div className="border border-slate-800 rounded-lg p-4 bg-slate-800/30 flex flex-col gap-2">
      {/* Ticker + headline (the headline links out too) */}
      <div className="flex items-start gap-3">
        <span className="shrink-0 px-2 py-0.5 rounded text-xs font-bold bg-green-500/20 text-green-400">
          {article.ticker}
        </span>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-white font-medium leading-snug hover:text-green-400"
        >
          {article.title || "(untitled)"}
        </a>
      </div>

      {/* Outlet + bias + date */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
        <span className="text-slate-300">{article.outlet || article.source || "Unknown source"}</span>
        <BiasBadge bias={article.bias} />
        {formatDate(article.published_at) && (
          <>
            <span>·</span>
            <span>{formatDate(article.published_at)}</span>
          </>
        )}
      </div>

      {/* Button to the original story */}
      <div>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block rounded border border-green-500/40 px-3 py-1 text-xs font-medium text-green-400 hover:bg-green-500/10 hover:text-green-300"
        >
          Read article ↗
        </a>
      </div>
    </div>
  );
}

export default function NewsPage() {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [ticker, setTicker] = useState("");
  const [heading, setHeading] = useState("Watchlist news");
  const [isHome, setIsHome] = useState(true);

  async function loadHome() {
    setLoading(true);
    setError(null);
    setHeading("Watchlist news");
    setIsHome(true);
    try {
      setArticles(await fetchWatchlistNews(40));
    } catch (err) {
      setError(err.message || "Request failed");
      setArticles([]);
    } finally {
      setLoading(false);
    }
  }

  async function runSearch(symbol) {
    const t = (symbol || "").trim().toUpperCase();
    if (!t) return;
    setLoading(true);
    setError(null);
    setHeading(`News for ${t}`);
    setIsHome(false);
    try {
      setArticles(await fetchNews(t, 30));
    } catch (err) {
      setError(err.message || "Request failed");
      setArticles([]);
    } finally {
      setLoading(false);
    }
  }

  // Default to the watchlist home feed on mount.
  useEffect(() => {
    loadHome();
  }, []);

  return (
    <div className="p-4 md:p-6">
      {/* Search + back-to-watchlist */}
      <form
        className="flex items-center gap-2 mb-4"
        onSubmit={(e) => {
          e.preventDefault();
          runSearch(ticker);
        }}
      >
        <input
          type="text"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Search ticker (e.g. AAPL)"
          className="flex-1 md:flex-none md:w-56 bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white uppercase"
        />
        <button
          type="submit"
          className="bg-green-600 hover:bg-green-500 text-white text-sm font-medium px-4 py-2 rounded"
        >
          Search
        </button>
        {!isHome && (
          <button
            type="button"
            onClick={() => {
              setTicker("");
              loadHome();
            }}
            className="border border-slate-700 text-slate-300 hover:text-white text-sm px-3 py-2 rounded"
          >
            Watchlist
          </button>
        )}
      </form>

      <h2 className="text-sm font-semibold text-slate-400 mb-3">{heading}</h2>

      {loading && <div className="text-slate-300">Loading headlines...</div>}
      {error && (
        <div className="text-red-400">
          Error loading news: {error}.
          <div className="text-slate-500 text-sm mt-1">Is the backend running?</div>
        </div>
      )}

      {!loading && !error && articles.length === 0 && (
        <div className="p-8 text-center text-slate-400 border border-slate-800 rounded">
          {isHome
            ? "No stored headlines yet — add a NEWS_API_KEY and run the news scheduler to populate the watchlist feed."
            : `No news stored for ${heading.replace("News for ", "")} yet.`}
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
