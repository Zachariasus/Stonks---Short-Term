// src/pages/SectorPage.jsx
// ========================
// Sector Rankings: the 11 SPDR sectors ranked by relative strength vs SPY, plus
// a top-down tilt summary (top 3 / bottom 3 / breadth) derived from the ranked
// list. Mobile hides the 3m/6m columns, keeping 12m + Composite.

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchSectorRankings } from "../api";

// Signed percentage-point value, or em-dash.
function fmtPP(v) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}`;
}

function labelClass(label) {
  if (label === "Leading") return "bg-green-500/20 text-green-400";
  if (label === "Lagging") return "bg-red-500/20 text-red-400";
  return "bg-yellow-500/20 text-yellow-400"; // Neutral
}

function compositeClass(v) {
  if (v == null) return "text-slate-400";
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-slate-300";
}

export default function SectorPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    fetchSectorRankings()
      .then((data) => {
        if (active) {
          setRows(data);
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
      active = false;
    };
  }, []);

  if (loading) return <div className="p-4 md:p-6 text-slate-300">Loading sector rankings...</div>;
  if (error) {
    return (
      <div className="p-4 md:p-6 text-red-400">
        Error loading sectors: {error}.
        <div className="text-slate-500 text-sm mt-1">Is the backend running?</div>
      </div>
    );
  }

  // Derive the top-down tilt from the ranked list.
  const top3 = rows.slice(0, 3);
  const bottom3 = rows.slice(-3);
  const leadingCount = rows.filter((r) => r.rotation_label === "Leading").length;
  const breadth =
    leadingCount >= 7 ? "Broad Advance" : leadingCount >= 4 ? "Mixed" : "Narrow/Weak";

  return (
    <div className="p-4 md:p-6">
      <div className="overflow-x-auto border border-slate-800 rounded">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-700 text-left text-slate-400 bg-slate-800/40">
              <th className="px-3 py-2 font-medium">Rank</th>
              <th className="px-3 py-2 font-medium">Sector</th>
              <th className="px-3 py-2 font-medium">ETF</th>
              <th className="px-3 py-2 font-medium text-right hidden md:table-cell">3m</th>
              <th className="px-3 py-2 font-medium text-right hidden md:table-cell">6m</th>
              <th className="px-3 py-2 font-medium text-right">12m</th>
              <th className="px-3 py-2 font-medium text-right">Composite</th>
              <th className="px-3 py-2 font-medium">Label</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.etf_ticker}
                onClick={() => navigate(`/sectors/${r.etf_ticker}`)}
                className="border-b border-slate-800 hover:bg-slate-800/40 cursor-pointer"
                title={`View ${r.sector_name} sector profile`}
              >
                <td className="px-3 py-2 text-slate-400">{r.rank}</td>
                <td className="px-3 py-2 text-white hover:text-green-400">{r.sector_name}</td>
                <td className="px-3 py-2 text-slate-300">{r.etf_ticker}</td>
                <td className="px-3 py-2 text-right text-slate-300 hidden md:table-cell">{fmtPP(r.rs_3m)}</td>
                <td className="px-3 py-2 text-right text-slate-300 hidden md:table-cell">{fmtPP(r.rs_6m)}</td>
                <td className="px-3 py-2 text-right text-slate-300">{fmtPP(r.rs_12m)}</td>
                <td className={`px-3 py-2 text-right font-semibold ${compositeClass(r.composite_vs_spy)}`}>
                  {fmtPP(r.composite_vs_spy)}
                </td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-semibold ${labelClass(r.rotation_label)}`}>
                    {r.rotation_label}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* --- Top-down tilt summary --- */}
      <div className="mt-6 border border-slate-800 rounded-lg p-4 bg-slate-800/30 text-sm">
        <div className="text-slate-300">
          <span className="text-slate-500">Top 3: </span>
          {top3.map((r) => `${r.etf_ticker} (${r.sector_name})`).join(", ") || "—"}
        </div>
        <div className="text-slate-300 mt-1">
          <span className="text-slate-500">Bottom 3: </span>
          {bottom3.map((r) => r.etf_ticker).join(", ") || "—"}
        </div>
        <div className="text-slate-300 mt-1">
          <span className="text-slate-500">Breadth: </span>
          <span
            className={
              breadth === "Broad Advance"
                ? "text-green-400"
                : breadth === "Mixed"
                ? "text-yellow-400"
                : "text-red-400"
            }
          >
            {breadth}
          </span>
          <span className="text-slate-500"> ({leadingCount} of {rows.length} leading)</span>
        </div>
      </div>
    </div>
  );
}
