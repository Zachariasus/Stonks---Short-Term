// src/pages/FlagsPage.jsx
// =======================
// The Flagged Stocks page. Desktop (≥768px): the full 13-column TanStack table.
// Mobile (<768px): a compact card list (the 13-column table is unusable at
// 375px). The two layouts are swapped purely with Tailwind responsive prefixes
// (hidden md:block / block md:hidden) — no JS branching.

import { useEffect, useMemo, useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
} from "@tanstack/react-table";

import { fetchFlags } from "../api";
import FlagCard from "../components/FlagCard";
import { fmtSpan } from "../format";

// Column model drives the sortable headers. accessorKey points at the Flag field
// (numeric fields sort numerically, strings alphabetically). The cells themselves
// are rendered by FlagCard, so the header order here MUST match FlagCard's <td>s.
const COLUMNS = [
  { accessorKey: "ticker", header: "Ticker" },
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
function MobileFlagCard({ flag }) {
  const isLong = flag.direction === "Long";
  const directionClass = isLong
    ? "bg-green-500/20 text-green-400"
    : "bg-red-500/20 text-red-400";

  const score = flag.score ?? 0;
  const scoreClass =
    score >= 70
      ? "bg-green-500/20 text-green-400"
      : score >= 50
      ? "bg-yellow-500/20 text-yellow-400"
      : "bg-red-500/20 text-red-400";

  const days = flag.days_to_earnings;

  return (
    <div className="border border-slate-800 rounded-lg p-4 bg-slate-800/30">
      {/* Row 1: ticker + direction + score */}
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold text-white">{flag.ticker}</span>
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${directionClass}`}>
          {flag.direction ?? "—"}
        </span>
        <span className={`ml-auto px-2 py-0.5 rounded text-xs font-semibold ${scoreClass}`}>
          {score}
        </span>
      </div>

      {/* Row 2: stage | rs | sector */}
      <div className="mt-2 text-xs text-slate-400">
        {(flag.stage ?? "—")} &nbsp;·&nbsp; {(flag.rs_label ?? "—")} &nbsp;·&nbsp;{" "}
        {(flag.sector_etf ?? "—")}
      </div>

      {/* Row 3: entry | stop | R:R */}
      <div className="mt-2 text-sm text-slate-300 flex flex-wrap gap-x-4 gap-y-1">
        <span>Entry {money(flag.entry_price)}</span>
        <span>Stop {money(flag.suggested_stop)}</span>
        {flag.rr_ratio != null && <span>R:R {flag.rr_ratio.toFixed(1)}x</span>}
      </div>

      {/* Row 4: earnings + flagged span */}
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
            {fmtSpan(flag.stage_start_date, flag.last_seen_date)}
          </span>
        </span>
      </div>
    </div>
  );
}

export default function FlagsPage() {
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sorting, setSorting] = useState([]);

  // Filter controls (client-side — no re-fetching).
  const [directionFilter, setDirectionFilter] = useState("All");
  const [confidenceFilter, setConfidenceFilter] = useState("All");
  const [minScore, setMinScore] = useState(0);

  // Fetch flags once when the page mounts.
  useEffect(() => {
    let active = true;
    fetchFlags()
      .then((data) => {
        if (active) {
          setFlags(data);
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
    return flags.filter((f) => {
      if (directionFilter !== "All" && f.direction !== directionFilter) return false;
      if (confidenceFilter !== "All" && f.confidence_label !== confidenceFilter) return false;
      if ((f.score ?? 0) < Number(minScore || 0)) return false;
      return true;
    });
  }, [flags, directionFilter, confidenceFilter, minScore]);

  const table = useReactTable({
    data: filtered,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return <div className="p-4 md:p-6 text-slate-300">Loading flags...</div>;
  }
  if (error) {
    return (
      <div className="p-4 md:p-6 text-red-400">
        Error loading flags: {error}.
        <div className="text-slate-500 text-sm mt-2">
          Is the backend running? (python webapp/run.py at the API base URL)
        </div>
      </div>
    );
  }

  const rows = table.getRowModel().rows;

  return (
    <div className="p-4 md:p-6">
      {/* --- Filter controls: horizontal scroll on mobile, wrap on desktop --- */}
      <div className="flex items-end gap-4 mb-4 overflow-x-auto md:flex-wrap pb-1">
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
          {filtered.length} of {flags.length} flags
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-slate-800 rounded">
          No active flags
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
                  <FlagCard key={row.id} flag={row.original} />
                ))}
              </tbody>
            </table>
          </div>

          {/* --- Mobile: card list (<768px) --- */}
          <div className="flex flex-col gap-3 md:hidden">
            {rows.map((row) => (
              <MobileFlagCard key={row.id} flag={row.original} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
