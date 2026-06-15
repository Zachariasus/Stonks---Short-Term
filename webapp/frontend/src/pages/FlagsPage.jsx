// src/pages/FlagsPage.jsx
// =======================
// The Flagged Stocks page: a sortable, filterable table of the system's active
// setup flags. Fetches once on mount, filters client-side, and uses TanStack
// Table (headless) for column model + sorting. Each row is a <FlagCard>.

import { useEffect, useMemo, useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
} from "@tanstack/react-table";

import { fetchFlags } from "../api";
import FlagCard from "../components/FlagCard";

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
  { accessorKey: "flagged_date", header: "Flagged Date" },
];

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
    return <div className="p-8 text-slate-300">Loading flags...</div>;
  }
  if (error) {
    return (
      <div className="p-8 text-red-400">
        Error loading flags: {error}.
        <div className="text-slate-500 text-sm mt-2">
          Is the backend running? (python webapp/run.py at the API base URL)
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      {/* --- Filter controls --- */}
      <div className="flex flex-wrap items-end gap-4 mb-4">
        <label className="flex flex-col text-xs text-slate-400">
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

        <label className="flex flex-col text-xs text-slate-400">
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

        <label className="flex flex-col text-xs text-slate-400">
          Min Score
          <input
            type="number"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white w-24"
          />
        </label>

        <div className="ml-auto self-center text-xs text-slate-500">
          {filtered.length} of {flags.length} flags
        </div>
      </div>

      {/* --- Table (or empty state) --- */}
      {filtered.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-slate-800 rounded">
          No active flags
        </div>
      ) : (
        <div className="overflow-x-auto border border-slate-800 rounded">
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
              {table.getRowModel().rows.map((row) => (
                <FlagCard key={row.id} flag={row.original} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
