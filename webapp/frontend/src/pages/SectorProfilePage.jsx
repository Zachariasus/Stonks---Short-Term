// src/pages/SectorProfilePage.jsx
// ===============================
// The Sector Profile — a full per-sector page at /sector/:etf. Reached by
// clicking a row on the Sectors page. It shows the sector's relative-strength
// snapshot (rank, composite vs SPY, 3m/6m/12m RS, rotation) and lists the
// stocks in that sector, each clickable through to its own stock profile.
//
// Built from two existing backends, no new endpoint:
//   • /sector-rankings → the sector's RS row (found by ETF ticker)
//   • /stocks          → every S&P 500 name; filtered to this sector's ETF
// plus a direct Yahoo Finance link to the sector ETF.

import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";

import { fetchSectorRankings, fetchStocks } from "../api";
import { Stat, Spinner } from "../components/Explain";

// Signed percentage-point value, or em-dash.
function fmtPP(v) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}`;
}
function ppClass(v) {
  if (v == null) return "text-slate-400";
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-slate-300";
}
function rotationClass(label) {
  if (label === "Leading") return "bg-green-500/20 text-green-400";
  if (label === "Lagging") return "bg-red-500/20 text-red-400";
  return "bg-yellow-500/20 text-yellow-400"; // Neutral
}

// Brief, plain-language overview of each sector stat (shown in the info popover).
const SECTOR_INFO = {
  rank:
    "Where this sector ranks among the 11 SPDR sectors by relative strength vs. the S&P 500 (1 = strongest). The top sectors are where money is rotating in.",
  composite:
    "A blended relative-strength score vs. the S&P 500 across the 3-, 6-, and 12-month horizons, in percentage points. Positive means the sector is outperforming the market.",
  rs3:
    "The sector ETF's price performance vs. the S&P 500 over the last 3 months, in percentage points. The shortest, most recent momentum window.",
  rs6:
    "The sector ETF's price performance vs. the S&P 500 over the last 6 months, in percentage points — the medium-term trend.",
  rs12:
    "The sector ETF's price performance vs. the S&P 500 over the last 12 months, in percentage points — the long-term trend.",
  rotation:
    "The sector's rotation state from its composite RS: Leading (money flowing in, outperforming), Lagging (flowing out, underperforming), or Neutral (in line with the market).",
};

// One constituent stock — a clickable row through to its stock profile.
function ConstituentRow({ stock }) {
  const navigate = useNavigate();
  const score = stock.score;
  const scoreClass =
    score == null
      ? "bg-slate-700/40 text-slate-400"
      : score >= 70
      ? "bg-green-500/20 text-green-400"
      : score >= 50
      ? "bg-yellow-500/20 text-yellow-400"
      : "bg-red-500/20 text-red-400";
  const dirClass =
    stock.direction === "Long"
      ? "text-green-400"
      : stock.direction === "Short"
      ? "text-red-400"
      : "text-slate-500";

  return (
    <button
      type="button"
      onClick={() => navigate(`/stock/${stock.ticker}`)}
      className="w-full text-left flex items-center gap-3 px-3 py-2 rounded hover:bg-slate-800/60 transition-colors"
      title={`View ${stock.ticker} profile`}
    >
      <span className="w-20 shrink-0 font-bold text-white">
        {stock.is_flagged && <span className="text-green-400 mr-1">★</span>}
        {stock.ticker}
      </span>
      <span className="flex-1 truncate text-sm text-slate-400">{stock.company_name ?? "—"}</span>
      <span className={`shrink-0 text-xs ${dirClass}`}>{stock.direction ?? ""}</span>
      <span className="hidden sm:block shrink-0 w-40 truncate text-xs text-slate-400">
        {stock.stage ?? "—"}
      </span>
      <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-semibold ${scoreClass}`}>
        {score == null ? "—" : score}
      </span>
    </button>
  );
}

export default function SectorProfilePage() {
  const { etf: rawEtf } = useParams();
  const etf = (rawEtf || "").toUpperCase();

  const [sector, setSector] = useState(null);
  const [constituents, setConstituents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!etf) return undefined;
    let active = true;
    setLoading(true);
    setError(null);
    setSector(null);
    setConstituents([]);

    Promise.all([fetchSectorRankings(), fetchStocks()])
      .then(([rankings, stocks]) => {
        if (!active) return;
        const row = rankings.find((r) => (r.etf_ticker || "").toUpperCase() === etf);
        if (!row) {
          setError(`${etf} is not a tracked sector ETF.`);
          return;
        }
        setSector({ ...row, total: rankings.length });
        setConstituents(stocks.filter((s) => (s.sector_etf || "").toUpperCase() === etf));
      })
      .catch((err) => active && setError(err.message || "Request failed"))
      .finally(() => active && setLoading(false));

    return () => {
      active = false;
    };
  }, [etf]);

  if (loading) {
    return (
      <div className="p-4 md:p-6">
        <Spinner label={`Loading the ${etf} sector profile…`} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-4 md:p-6 flex flex-col gap-4 max-w-4xl">
        <Link to="/sectors" className="text-sm text-slate-400 hover:text-white w-fit">
          ← Back to Sectors
        </Link>
        <div className="text-red-400">{error}</div>
      </div>
    );
  }

  const flaggedCount = constituents.filter((s) => s.is_flagged).length;
  const yahooUrl = `https://finance.yahoo.com/quote/${etf}`;

  return (
    <div className="p-4 md:p-6 max-w-4xl flex flex-col gap-6">
      <Link to="/sectors" className="text-sm text-slate-400 hover:text-white w-fit">
        ← Back to Sectors
      </Link>

      {/* Header */}
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold text-white">{sector.sector_name}</h1>
              <span className="px-2 py-0.5 rounded text-sm font-semibold bg-slate-700/60 text-slate-200">
                {etf}
              </span>
              <span
                className={`px-2 py-0.5 rounded text-xs font-semibold ${rotationClass(
                  sector.rotation_label
                )}`}
              >
                {sector.rotation_label}
              </span>
            </div>
            <div className="mt-1 text-sm text-slate-400">
              Rank {sector.rank} of {sector.total} sectors · {constituents.length} stocks
              {flaggedCount > 0 ? ` · ${flaggedCount} flagged` : ""}
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
      </div>

      {/* RS snapshot */}
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <div className="flex items-baseline gap-2 mb-4">
          <h2 className="text-lg font-semibold">Relative Strength</h2>
          <span className="text-xs text-slate-500">tap a stat to learn what it means</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <Stat label="Rank" value={`#${sector.rank} of ${sector.total}`} info={SECTOR_INFO.rank} />
          <Stat
            label="Composite vs SPY"
            value={fmtPP(sector.composite_vs_spy)}
            valueClass={ppClass(sector.composite_vs_spy)}
            info={SECTOR_INFO.composite}
          />
          <Stat label="Rotation" value={sector.rotation_label} info={SECTOR_INFO.rotation} />
          <Stat label="RS 3-month" value={fmtPP(sector.rs_3m)} valueClass={ppClass(sector.rs_3m)} info={SECTOR_INFO.rs3} />
          <Stat label="RS 6-month" value={fmtPP(sector.rs_6m)} valueClass={ppClass(sector.rs_6m)} info={SECTOR_INFO.rs6} />
          <Stat label="RS 12-month" value={fmtPP(sector.rs_12m)} valueClass={ppClass(sector.rs_12m)} info={SECTOR_INFO.rs12} />
        </div>
      </div>

      {/* Constituents */}
      <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
        <div className="flex items-baseline gap-2 mb-3">
          <h2 className="text-lg font-semibold">Stocks in this sector</h2>
          <span className="text-xs text-slate-500">flagged first, then by score</span>
        </div>
        {constituents.length === 0 ? (
          <div className="text-sm text-slate-500">No tracked stocks mapped to this sector.</div>
        ) : (
          <div className="flex flex-col">
            {constituents.map((s) => (
              <ConstituentRow key={s.ticker} stock={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
