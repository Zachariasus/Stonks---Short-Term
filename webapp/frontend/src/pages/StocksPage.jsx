// src/pages/StocksPage.jsx
// ========================
// The Stocks page. Shows EVERY stock in the S&P 500 from the confluence screen,
// but defaults to the "Flagged" view — so by default it looks exactly like the
// old Flagged-Stocks page, and flipping the View filter to "All S&P 500" reveals
// the full universe. The flagging system is unchanged; it's just a filter now.
//
// Desktop (≥768px): the full sortable TanStack table. Mobile (<768px): a compact
// card list (the wide table is unusable at 375px). Layouts swap via Tailwind
// responsive prefixes — no JS branching.

import { useEffect, useMemo, useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
} from "@tanstack/react-table";

import { fetchStocks } from "../api";
import StockRow from "../components/StockRow";
import { fmtSpan } from "../format";

// Column model drives the sortable headers. accessorKey points at the stock field
// (numeric fields sort numerically, strings alphabetically). The cells themselves
// are rendered by StockRow, so the header order here MUST match StockRow's <td>s.
const COLUMNS = [
  { accessorKey: "ticker", header: "Ticker" },
  { accessorKey: "company_name", header: "Company" },
  { accessorKey: "direction", header: "Direction" },
  { accessorKey: "score", header: "Score" },
  { accessorKey: "confidence_label", header: "Confidence" },
  { accessorKey: "stage", header: "Stage" },
  { accessorKey: "rs_label", header: "RS Label" },
  { accessorKey: "sector_etf", header: "Sector" },
  { accessorKey: "entry_price", header: "Entry" },
  { accessorKey: "target_price", header: "Target" },
  { accessorKey: "suggested_stop", header: "Stop" },
  { accessorKey: "rr_ratio", header: "R:R" },
  { accessorKey: "days_to_earnings", header: "Earnings" },
  { accessorKey: "stage_start_date", header: "Flagged" },
];

function money(v) {
  return v == null ? "—" : `$${Number(v).toFixed(2)}`;
}

// Compact card used on mobile (<768px) in place of the wide table row.
function MobileStockCard({ stock }) {
  const isLong = stock.direction === "Long";
  const directionClass = isLong
    ? "bg-green-500/20 text-green-400"
    : stock.direction === "Short"
    ? "bg-red-500/20 text-red-400"
    : "bg-slate-700/40 text-slate-400";

  const hasScore = stock.score != null;
  const score = stock.score ?? 0;
  const scoreClass = !hasScore
    ? "bg-slate-700/40 text-slate-400"
    : score >= 70
    ? "bg-green-500/20 text-green-400"
    : score >= 50
    ? "bg-yellow-500/20 text-yellow-400"
    : "bg-red-500/20 text-red-400";

  const days = stock.days_to_earnings;

  return (
    <div className="border border-slate-800 rounded-lg p-4 bg-slate-800/30">
      {/* Row 1: ticker (+flag star) + direction + score */}
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold text-white">
          {stock.is_flagged && <span className="text-green-400 mr-1">★</span>}
          {stock.ticker}
        </span>
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${directionClass}`}>
          {stock.direction ?? "—"}
        </span>
        <span className={`ml-auto px-2 py-0.5 rounded text-xs font-semibold ${scoreClass}`}>
          {hasScore ? score : "—"}
        </span>
      </div>

      {/* Company name */}
      <div className="mt-1 text-xs text-slate-500 truncate">{stock.company_name ?? ""}</div>

      {/* Row 2: stage | rs | sector */}
      <div className="mt-2 text-xs text-slate-400">
        {(stock.stage ?? "—")} &nbsp;·&nbsp; {(stock.rs_label ?? "—")} &nbsp;·&nbsp;{" "}
        {(stock.sector_etf ?? "—")}
      </div>

      {/* Rows 3–4: setup levels + span, only meaningful for flagged names */}
      {stock.is_flagged && (
        <>
          <div className="mt-2 text-sm text-slate-300 flex flex-wrap gap-x-4 gap-y-1">
            <span>Entry {money(stock.entry_price)}</span>
            <span>Stop {money(stock.suggested_stop)}</span>
            {stock.rr_ratio != null && <span>R:R {stock.rr_ratio.toFixed(1)}x</span>}
          </div>
          <div className="mt-1 text-xs flex flex-wrap gap-x-4 gap-y-1">
            <span>
              <span className="text-slate-500">Earnings: </span>
              {days == null ? (
                <span className="text-slate-500">—</span>
              ) : (
                <span className={days < 30 ? "text-orange-400 font-medium" : "text-slate-400"}>
                  {days}d
                </span>
              )}
            </span>
            <span>
              <span className="text-slate-500">Flagged: </span>
              <span className="text-slate-400">
                {fmtSpan(stock.stage_start_date, stock.last_seen_date)}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export default function StocksPage() {
  const [stocks, setStocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sorting, setSorting] = useState([]);

  // Filter controls (client-side — no re-fetching).
  const [view, setView] = useState("Flagged"); // "Flagged" (default) | "All"
  const [directionFilter, setDirectionFilter] = useState("All");
  const [confidenceFilter, setConfidenceFilter] = useState("All");
  const [minScore, setMinScore] = useState(0);

  // Fetch the full universe once when the page mounts.
  useEffect(() => {
    let active = true;
    fetchStocks()
      .then((data) => {
        if (active) {
          setStocks(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || "Request failed");
          setLoading(false);
        }
      });
    return () => {
      active = false; // ignore the response if the component unmounts first
    };
  }, []);

  // Apply the filters in JS over the already-fetched data.
  const filtered = useMemo(() => {
    return stocks.filter((s) => {
      if (view === "Flagged" && !s.is_flagged) return false;
      if (directionFilter !== "All" && s.direction !== directionFilter) return false;
      if (confidenceFilter !== "All" && s.confidence_label !== confidenceFilter) return false;
      if (Number(minScore || 0) > 0 && (s.score ?? -1) < Number(minScore)) return false;
      return true;
    });
  }, [stocks, view, directionFilter, confidenceFilter, minScore]);

  const table = useReactTable({
    data: filtered,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return <div className="p-4 md:p-6 text-slate-300">Loading stocks...</div>;
  }
  if (error) {
    return (
      <div className="p-4 md:p-6 text-red-400">
        Error loading stocks: {error}.
        <div className="text-slate-500 text-sm mt-2">
          Is the backend running? (python webapp/run.py at the API base URL)
        </div>
      </div>
    );
  }

  const rows = table.getRowModel().rows;
  const flaggedCount = stocks.filter((s) => s.is_flagged).length;

  return (
    <div className="p-4 md:p-6">
      {/* --- Filter controls: horizontal scroll on mobile, wrap on desktop --- */}
      <div className="flex items-end gap-4 mb-4 overflow-x-auto md:flex-wrap pb-1">
        <label className="flex flex-col text-xs text-slate-400 shrink-0">
          View
          <select
            value={view}
            onChange={(e) => setView(e.target.value)}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white"
          >
            <option value="Flagged">Flagged only ({flaggedCount})</option>
            <option value="All">All S&P 500 ({stocks.length})</option>
          </select>
        </label>

        <label className="flex flex-col text-xs text-slate-400 shrink-0">
          Direction
          <select
            value={directionFilter}
            onChange={(e) => setDirectionFilter(e.target.value)}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white"
          >
            <option>All</option>
            <option>Long</option>
            <option>Short</option>
          </select>
        </label>

        <label className="flex flex-col text-xs text-slate-400 shrink-0">
          Confidence
          <select
            value={confidenceFilter}
            onChange={(e) => setConfidenceFilter(e.target.value)}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white"
          >
            <option>All</option>
            <option>High</option>
            <option>Medium</option>
            <option>Low</option>
          </select>
        </label>

        <label className="flex flex-col text-xs text-slate-400 shrink-0">
          Min Score
          <input
            type="number"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white w-24"
          />
        </label>

        <div className="shrink-0 md:ml-auto self-center text-xs text-slate-500">
          {filtered.length} of {view === "Flagged" ? flaggedCount : stocks.length} stocks
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-slate-800 rounded">
          {view === "Flagged" ? "No active flags" : "No stocks match these filters"}
        </div>
      ) : (
        <>
          {/* --- Desktop: full table (≥768px) --- */}
          <div className="hidden md:block overflow-x-auto border border-slate-800 rounded">
            <table className="w-full text-sm border-collapse">
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="border-b border-slate-700 text-left text-slate-400 bg-slate-800/40">
                    {hg.headers.map((header) => {
                      const sorted = header.column.getIsSorted();
                      return (
                        <th
                          key={header.id}
                          onClick={header.column.getToggleSortingHandler()}
                          className="px-3 py-2 font-medium cursor-pointer select-none hover:text-white whitespace-nowrap"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sorted === "asc" ? " ▲" : sorted === "desc" ? " ▼" : ""}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {rows.map((row) => (
                  <StockRow key={row.id} stock={row.original} />
                ))}
              </tbody>
            </table>
          </div>

          {/* --- Mobile: card list (<768px) --- */}
          <div className="flex flex-col gap-3 md:hidden">
            {rows.map((row) => (
              <MobileStockCard key={row.id} stock={row.original} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
