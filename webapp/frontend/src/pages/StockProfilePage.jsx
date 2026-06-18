// src/pages/StockProfilePage.jsx
// ==============================
// The Stock Profile — a full per-stock page at /stock/:ticker. Reached by clicking
// any row on the Stocks page (or by typing a ticker into the search landing at
// /stock). It stitches together three existing backends:
//   • /stocks/{ticker}  → the screen snapshot (all the Stocks-page info)
//   • /grade            → the AI letter grade + confluence engines + position sizing
//   • /news/{ticker}    → recent headlines for the company
// plus a direct link out to the stock's Yahoo Finance page.
//
// Each section loads independently: the fast snapshot paints immediately, while
// the slow /grade pipeline and the news fetch fill in with their own spinners.

import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";

import { fetchStock, gradeStock, fetchNews } from "../api";
import { fmtSpan } from "../format";

// --- tiny formatters ------------------------------------------------------
function money(v) {
  return v == null ? "—" : `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}
function gradeColor(grade) {
  return (
    { A: "text-green-400", B: "text-blue-400", C: "text-yellow-400", D: "text-red-400" }[grade] ||
    "text-slate-400"
  );
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// Bias tag colors (matches the News page convention: left=blue, right=red).
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

// --- shared little pieces --------------------------------------------------
function Stat({ label, value, valueClass = "text-white" }) {
  return (
    <div className="border border-slate-700 rounded-lg px-3 py-2 bg-slate-800/40">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`text-sm font-semibold ${valueClass}`}>{value ?? "—"}</div>
    </div>
  );
}

function EngineBar({ label, pts, max }) {
  const value = pts ?? 0;
  const pct = max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="text-sm md:flex md:items-center md:gap-3">
      <div className="text-slate-300 md:w-44">{label}</div>
      <div className="mt-1 flex items-center gap-3 md:mt-0 md:flex-1">
        <div className="w-14 text-right text-slate-400 tabular-nums">
          {value}/{max}
        </div>
        <div className="h-2 flex-1 rounded bg-slate-700 overflow-hidden">
          <div className="h-full bg-green-500" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

function Spinner({ label }) {
  return (
    <div className="flex items-center gap-3 text-slate-400 text-sm">
      <div className="h-4 w-4 rounded-full border-2 border-slate-600 border-t-green-400 animate-spin" />
      {label}
    </div>
  );
}

// =========================================================================
// Landing: /stock with no ticker → a search box to pull up any profile.
// =========================================================================
function ProfileSearch() {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  return (
    <div className="p-4 md:p-6 max-w-md">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const t = q.trim().toUpperCase();
          if (t) navigate(`/stock/${t}`);
        }}
        className="flex items-end gap-2"
      >
        <label className="flex flex-col text-xs text-slate-400 flex-1">
          Ticker
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value.toUpperCase())}
            placeholder="e.g. AAPL"
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white uppercase"
          />
        </label>
        <button
          type="submit"
          className="bg-green-600 hover:bg-green-500 text-white text-sm font-medium px-4 py-2 rounded"
        >
          View profile
        </button>
      </form>
      <p className="mt-3 text-sm text-slate-500">
        Or click any stock on the <Link to="/" className="text-green-400 hover:underline">Stocks</Link> page.
      </p>
    </div>
  );
}

// =========================================================================
// Snapshot — every field from the Stocks page, for this one name.
// =========================================================================
function Snapshot({ stock }) {
  const dirClass =
    stock.direction === "Long"
      ? "text-green-400"
      : stock.direction === "Short"
      ? "text-red-400"
      : "text-slate-400";
  const score = stock.score;
  const scoreClass =
    score == null
      ? "text-slate-400"
      : score >= 70
      ? "text-green-400"
      : score >= 50
      ? "text-yellow-400"
      : "text-red-400";

  return (
    <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
      <h2 className="text-lg font-semibold mb-4">Screen Snapshot</h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Confluence score" value={score == null ? "—" : `${score}/100`} valueClass={scoreClass} />
        <Stat label="Confidence" value={stock.confidence_label} />
        <Stat label="Direction" value={stock.direction} valueClass={dirClass} />
        <Stat label="Stage" value={stock.stage} />
        <Stat label="Relative strength" value={stock.rs_label} />
        <Stat label="Sector ETF" value={stock.sector_etf} />
        <Stat label="Sector rotation" value={stock.sector_rotation_label} />
        <Stat
          label="Flagged"
          value={stock.is_flagged ? fmtSpan(stock.stage_start_date, stock.last_seen_date) : "Not flagged"}
          valueClass={stock.is_flagged ? "text-green-400" : "text-slate-400"}
        />
      </div>
    </div>
  );
}

// =========================================================================
// Grade — AI letter grade + confluence engines + position sizing (from /grade).
// =========================================================================
function GradeSection({ grade, loading, error }) {
  if (loading) {
    return (
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <Spinner label="Running the analysis pipeline & AI grade…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="border border-red-500/40 bg-red-500/10 rounded-lg p-4 text-red-300 text-sm">
        Grade unavailable: {error}
      </div>
    );
  }
  if (!grade) return null;

  return (
    <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30 flex flex-col gap-5">
      {grade.stub && (
        <div className="border border-blue-500/50 bg-blue-500/10 rounded-lg p-3 text-blue-300 text-sm">
          AI letter grade is a stub — add ANTHROPIC_API_KEY to .env to enable it. The quantitative
          breakdown below is live.
        </div>
      )}

      {/* Letter grade + verdict */}
      <div className="flex items-center gap-4">
        <div className={`text-6xl font-bold leading-none ${gradeColor(grade.grade)}`}>
          {grade.grade || "?"}
        </div>
        <div className="text-slate-300">{grade.one_line_verdict || "—"}</div>
      </div>

      {/* Bull / bear (only meaningful with a live AI grade) */}
      {!grade.stub && (grade.bull_case || grade.bear_case) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="border border-green-500/30 rounded-lg p-3 bg-green-500/5">
            <div className="text-xs font-semibold text-green-400 mb-1">BULL CASE</div>
            <div className="text-sm text-slate-300">{grade.bull_case || "—"}</div>
          </div>
          <div className="border border-red-500/30 rounded-lg p-3 bg-red-500/5">
            <div className="text-xs font-semibold text-red-400 mb-1">BEAR CASE</div>
            <div className="text-sm text-slate-300">{grade.bear_case || "—"}</div>
          </div>
        </div>
      )}

      {!grade.stub && grade.key_risks && grade.key_risks.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-400 mb-1">KEY RISKS</div>
          <ul className="list-disc list-inside text-sm text-slate-300">
            {grade.key_risks.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {!grade.stub && grade.suggested_action && grade.suggested_action !== "—" && (
        <div className="border border-slate-600 rounded-lg p-3 bg-slate-700/30">
          <div className="text-xs font-semibold text-slate-400 mb-1">SUGGESTED ACTION</div>
          <div className="text-sm text-white">{grade.suggested_action}</div>
        </div>
      )}

      {/* Confluence engines */}
      <div>
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-sm font-semibold text-slate-300">Confluence engines</div>
          <div>
            <span className="text-xl font-bold text-white">{grade.confluence_score ?? "—"}</span>
            <span className="text-slate-500"> / 100</span>
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <EngineBar label="E1 Trend & Momentum" pts={grade.engine_1_pts} max={35} />
          <EngineBar label="E2 Fundamental Traj." pts={grade.engine_2_pts} max={25} />
          <EngineBar label="E3 Top-Down / Rotation" pts={grade.engine_3_pts} max={25} />
          <EngineBar label="E4 Valuation" pts={grade.engine_4_pts} max={15} />
        </div>
      </div>

      {/* Position sizing */}
      <div>
        <div className="text-sm font-semibold text-slate-300 mb-3">Position sizing</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat label="Entry" value={money(grade.entry_price)} />
          <Stat label="Stop" value={money(grade.stop_price)} />
          <Stat label="Target" value={money(grade.target_price)} />
          <Stat
            label="R:R"
            value={grade.rr_ratio == null ? "—" : `${Number(grade.rr_ratio).toFixed(1)}x`}
          />
          <Stat label="Shares" value={grade.shares ?? "—"} />
          <Stat label="Position" value={money(grade.position_value)} />
          <Stat
            label="Next earnings"
            value={grade.next_earnings_date || "Unknown"}
          />
          <Stat
            label="Earnings timing"
            value={
              grade.earnings_flag
                ? grade.days_to_earnings != null
                  ? `${grade.earnings_flag} (${grade.days_to_earnings}d)`
                  : grade.earnings_flag
                : "—"
            }
          />
        </div>
      </div>
    </div>
  );
}

// =========================================================================
// News — recent headlines for the company (from /news/{ticker}).
// =========================================================================
function NewsSection({ news, loading, ticker }) {
  return (
    <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
      <h2 className="text-lg font-semibold mb-4">Recent News</h2>
      {loading ? (
        <Spinner label="Loading headlines…" />
      ) : news.length === 0 ? (
        <div className="text-sm text-slate-500">
          No recent headlines stored for {ticker}. (Add a NEWS_API_KEY and run the news scheduler to
          populate this.)
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {news.map((a) => (
            <div key={a.url} className="border border-slate-800 rounded-lg p-3 bg-slate-800/30">
              <a
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-white font-medium leading-snug hover:text-green-400"
              >
                {a.title || "(untitled)"}
              </a>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
                {a.homepage ? (
                  <a
                    href={a.homepage}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-300 hover:text-white hover:underline"
                  >
                    {a.outlet || a.source || "Unknown source"}
                  </a>
                ) : (
                  <span className="text-slate-300">{a.outlet || a.source || "Unknown source"}</span>
                )}
                {a.bias && (
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-medium ${
                      BIAS_CLASS[a.bias] || "bg-slate-700/50 text-slate-400"
                    }`}
                  >
                    {a.bias}
                  </span>
                )}
                {fmtDate(a.published_at) && (
                  <>
                    <span>·</span>
                    <span>{fmtDate(a.published_at)}</span>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// =========================================================================
// Page
// =========================================================================
export default function StockProfilePage() {
  const { ticker: rawTicker } = useParams();
  const ticker = (rawTicker || "").toUpperCase();

  const [stock, setStock] = useState(null);
  const [stockErr, setStockErr] = useState(null);
  const [stockLoading, setStockLoading] = useState(true);

  const [grade, setGrade] = useState(null);
  const [gradeErr, setGradeErr] = useState(null);
  const [gradeLoading, setGradeLoading] = useState(true);

  const [news, setNews] = useState([]);
  const [newsLoading, setNewsLoading] = useState(true);

  useEffect(() => {
    if (!ticker) return;
    let active = true;

    // Reset state when the ticker changes.
    setStock(null);
    setStockErr(null);
    setStockLoading(true);
    setGrade(null);
    setGradeErr(null);
    setGradeLoading(true);
    setNews([]);
    setNewsLoading(true);

    // 1) Fast snapshot.
    fetchStock(ticker)
      .then((d) => active && setStock(d))
      .catch((err) => active && setStockErr(err.response?.data?.detail || err.message || "Not found"))
      .finally(() => active && setStockLoading(false));

    // 2) Slow grade (full pipeline + AI).
    gradeStock(ticker)
      .then((d) => active && setGrade(d))
      .catch((err) => active && setGradeErr(err.response?.data?.detail || err.message || "Failed"))
      .finally(() => active && setGradeLoading(false));

    // 3) News.
    fetchNews(ticker, 15)
      .then((d) => active && setNews(d))
      .catch(() => active && setNews([]))
      .finally(() => active && setNewsLoading(false));

    return () => {
      active = false;
    };
  }, [ticker]);

  // No ticker in the URL → show the search landing.
  if (!ticker) return <ProfileSearch />;

  const yahooUrl = `https://finance.yahoo.com/quote/${ticker}`;
  const companyName = stock?.company_name;
  const sector = stock?.sector;

  return (
    <div className="p-4 md:p-6 max-w-4xl flex flex-col gap-6">
      {/* Back link */}
      <Link to="/" className="text-sm text-slate-400 hover:text-white w-fit">
        ← Back to Stocks
      </Link>

      {/* Header */}
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold text-white">
                {stock?.is_flagged && <span className="text-green-400 mr-1" title="Flagged setup">★</span>}
                {ticker}
              </h1>
              {stock?.direction && (
                <span
                  className={`px-2 py-0.5 rounded text-xs font-semibold ${
                    stock.direction === "Long"
                      ? "bg-green-500/20 text-green-400"
                      : "bg-red-500/20 text-red-400"
                  }`}
                >
                  {stock.direction}
                </span>
              )}
            </div>
            <div className="mt-1 text-sm text-slate-400">
              {companyName || (stockLoading ? "…" : "")}
              {sector ? ` · ${sector}` : ""}
            </div>
          </div>

          <a
            href={yahooUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded border border-violet-500/40 px-3 py-1.5 text-sm font-medium text-violet-300 hover:bg-violet-500/10"
          >
            Yahoo Finance ↗
          </a>
        </div>

        {stockErr && (
          <div className="mt-3 text-sm text-red-400">
            {stockErr}
          </div>
        )}
      </div>

      {/* Snapshot (fast) */}
      {stock && !stockErr && <Snapshot stock={stock} />}

      {/* Grade (slow) */}
      {!stockErr && <GradeSection grade={grade} loading={gradeLoading} error={gradeErr} />}

      {/* News */}
      {!stockErr && <NewsSection news={news} loading={newsLoading} ticker={ticker} />}
    </div>
  );
}
