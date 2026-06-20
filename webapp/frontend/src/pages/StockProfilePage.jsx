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
import { InfoDot, InfoPopover, Stat, Spinner } from "../components/Explain";

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

// --- stat explanations -----------------------------------------------------
// Brief, plain-language overview of what each stat represents. Shown in the
// click-to-toggle info box. Keep these short — a sentence or two each.
const STAT_INFO = {
  score:
    "The system's overall 0–100 conviction score for the setup, blending all four engines below. Higher is stronger — roughly ≥70 is high conviction, 50–69 moderate, and under 50 weak.",
  confidence:
    "A plain-language band for the confluence score (High / Medium / Low) that reflects both the total score and how many engines agree.",
  direction:
    "Which side the setup favors: Long (positioned to rise — typically a healthy Stage 2 uptrend) or Short (positioned to fall — typically a Stage 4 downtrend).",
  stage:
    "Where the stock sits in the Weinstein market cycle: Stage 1 Basing (flat, after a decline), Stage 2 Advancing (uptrend — best for longs), Stage 3 Topping (stalling after a run), Stage 4 Declining (downtrend — short candidate).",
  rs:
    "Relative strength versus the broad market (S&P 500): a Leader is outperforming, a Laggard is underperforming. Leaders are preferred for longs, laggards for shorts.",
  sector:
    "The sector this stock belongs to, shown by its sector ETF (e.g. XLK = Technology, XLE = Energy, XLB = Materials). Used to judge whether its sector is in or out of favor.",
  rotation:
    "Whether money is rotating into or out of this stock's sector right now, based on the sector's relative strength — e.g. Leading (in favor) vs. Lagging (out of favor).",
  flagged:
    "The span of dates the stock has continuously met the flag criteria in its current stage. It resets when the stage changes. \"Not flagged\" means it doesn't currently clear the threshold.",
};

// The four confluence engines (and their point maxes).
const ENGINE_INFO = {
  e1:
    "Engine 1 — Trend & Momentum (up to 35 pts, the largest engine). Scores the price trend itself: the Weinstein stage, moving-average alignment, and relative strength. This is the technical backbone of the setup.",
  e2:
    "Engine 2 — Fundamental Trajectory (up to 25 pts). Scores the direction of the business: earnings and revenue growth, margin trends, and estimate revisions — i.e. whether the fundamentals are improving or deteriorating.",
  e3:
    "Engine 3 — Top-Down / Rotation (up to 25 pts). Scores the backdrop: the market's macro cycle phase and whether this stock's sector is being rotated into or out of. A strong stock in a strong sector scores higher.",
  e4:
    "Engine 4 — Valuation (up to 15 pts, the smallest engine). Scores how cheap or expensive the stock is on multiples like P/E and EV/EBITDA versus its own history. A tie-breaker, not the main driver.",
};

// --- page-local pieces -----------------------------------------------------
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
      <div className="flex items-baseline gap-2 mb-4">
        <h2 className="text-lg font-semibold">Screen Snapshot</h2>
        <span className="text-xs text-slate-500">tap a stat to learn what it means</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Confluence score" value={score == null ? "—" : `${score}/100`} valueClass={scoreClass} info={STAT_INFO.score} />
        <Stat label="Confidence" value={stock.confidence_label} info={STAT_INFO.confidence} />
        <Stat label="Direction" value={stock.direction} valueClass={dirClass} info={STAT_INFO.direction} />
        <Stat label="Stage" value={stock.stage} info={STAT_INFO.stage} />
        <Stat label="Relative strength" value={stock.rs_label} info={STAT_INFO.rs} />
        <Stat label="Sector ETF" value={stock.sector_etf} info={STAT_INFO.sector} />
        <Stat label="Sector rotation" value={stock.sector_rotation_label} info={STAT_INFO.rotation} />
        <Stat
          label="Flagged"
          value={stock.is_flagged ? fmtSpan(stock.stage_start_date, stock.last_seen_date) : "Not flagged"}
          valueClass={stock.is_flagged ? "text-green-400" : "text-slate-400"}
          info={STAT_INFO.flagged}
        />
      </div>
    </div>
  );
}

// =========================================================================
// Grade — letter grade + confluence engines + position sizing (from /grade).
// The letter grade + narrative come from the local rules-based grader by default
// (no API key); with a key configured the backend serves the AI version instead.
// =========================================================================
function GradeSection({ grade, loading, error }) {
  if (loading) {
    return (
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <Spinner label="Running the analysis pipeline & grade…" />
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
      {grade.grade_source === "rules" && (
        <div className="border border-slate-600/60 bg-slate-700/30 rounded-lg p-3 text-slate-300 text-xs">
          Rules-based grade — computed locally from the signals, no API key needed. Add
          ANTHROPIC_API_KEY to <code className="text-slate-400">.env</code> for an AI-written narrative.
        </div>
      )}

      {/* Letter grade + verdict */}
      <div className="flex items-center gap-4">
        <div className={`text-6xl font-bold leading-none ${gradeColor(grade.grade)}`}>
          {grade.grade || "?"}
        </div>
        <div>
          <div className="text-slate-300">{grade.one_line_verdict || "—"}</div>
          {grade.market_regime && (
            <div className="mt-1 text-xs text-slate-400">
              Market regime:{" "}
              <span
                className={
                  grade.market_regime === "Risk-On"
                    ? "text-green-400"
                    : grade.market_regime === "Risk-Off"
                    ? "text-red-400"
                    : "text-yellow-400"
                }
              >
                {grade.market_regime}
              </span>
              {grade.direction === "Short" && grade.market_regime === "Risk-On" && (
                <span className="text-slate-500"> — shorting is an uphill fight in a strong bull</span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Bull / bear — the case for vs. against the setup (in its direction). */}
      {(grade.bull_case || grade.bear_case) && (
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

      {grade.key_risks && grade.key_risks.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-400 mb-1">KEY RISKS</div>
          <ul className="list-disc list-inside text-sm text-slate-300">
            {grade.key_risks.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {grade.suggested_action && grade.suggested_action !== "—" && (
        <div className="border border-slate-600 rounded-lg p-3 bg-slate-700/30">
          <div className="text-xs font-semibold text-slate-400 mb-1">SUGGESTED ACTION</div>
          <div className="text-sm text-white">{grade.suggested_action}</div>
        </div>
      )}

      {/* Confluence engines */}
      <div>
        <div className="flex items-baseline justify-between gap-2 mb-3">
          <div className="flex items-baseline gap-2">
            <div className="text-sm font-semibold text-slate-300">Confluence engines</div>
            <span className="text-xs text-slate-500">tap to learn what each scores</span>
          </div>
          <div>
            <span className="text-xl font-bold text-white">{grade.confluence_score ?? "—"}</span>
            <span className="text-slate-500"> / 100</span>
          </div>
        </div>
        <div className="flex flex-col gap-1">
          {[
            { label: "E1 Trend & Momentum", pts: grade.engine_1_pts, max: 35, info: ENGINE_INFO.e1 },
            { label: "E2 Fundamental Traj.", pts: grade.engine_2_pts, max: 25, info: ENGINE_INFO.e2 },
            { label: "E3 Top-Down / Rotation", pts: grade.engine_3_pts, max: 25, info: ENGINE_INFO.e3 },
            { label: "E4 Valuation", pts: grade.engine_4_pts, max: 15, info: ENGINE_INFO.e4 },
          ].map((e) => (
            <InfoPopover
              key={e.label}
              info={e.info}
              buttonClassName="block w-full text-left rounded px-2 py-1.5 hover:bg-slate-800/60 transition-colors"
            >
              <EngineBar
                label={
                  <span className="inline-flex items-center gap-1">
                    {e.label}
                    <InfoDot />
                  </span>
                }
                pts={e.pts}
                max={e.max}
              />
            </InfoPopover>
          ))}
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
