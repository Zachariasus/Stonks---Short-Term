// src/components/StockRow.jsx
// ===========================
// One stock rendered as a TABLE ROW (<tr>) for the Stocks page table. A superset
// of the old FlagCard: it also shows the company name and a flag marker, and
// degrades gracefully for NON-flagged rows (no setup levels → em-dashes, an
// unscored name → "—" score in neutral gray instead of a red 0).
//
// NOTE: the <td> order MUST match the column order defined in StocksPage.

import { fmtSpan } from "../format";

// Format a dollar value, or an em-dash when missing.
function money(v) {
  return v == null ? "—" : `$${Number(v).toFixed(2)}`;
}

// Format a plain number (e.g. R:R), or an em-dash when missing.
function num(v) {
  return v == null ? "—" : Number(v).toFixed(2);
}

export default function StockRow({ stock }) {
  const isLong = stock.direction === "Long";
  const directionClass = isLong
    ? "bg-green-500/20 text-green-400"
    : stock.direction === "Short"
    ? "bg-red-500/20 text-red-400"
    : "bg-slate-700/40 text-slate-400";

  // Score color: green ≥70, yellow ≥50, red <50. Unscored (null) → neutral gray "—".
  const hasScore = stock.score != null;
  const score = stock.score ?? 0;
  const scoreClass = !hasScore
    ? "text-slate-500"
    : score >= 70
    ? "text-green-400"
    : score >= 50
    ? "text-yellow-400"
    : "text-red-400";

  // Earnings: "51d", orange if a report is within 30 days, gray otherwise.
  const days = stock.days_to_earnings;
  const earnings =
    days == null ? (
      <span className="text-slate-500">—</span>
    ) : (
      <span className={days < 30 ? "text-orange-400 font-medium" : "text-slate-400"}>
        {days}d
      </span>
    );

  return (
    <tr className="border-b border-slate-800 hover:bg-slate-800/40">
      <td className="px-3 py-2 font-bold text-white whitespace-nowrap">
        {stock.is_flagged && (
          <span className="text-green-400 mr-1" title="Flagged setup">
            ★
          </span>
        )}
        {stock.ticker}
      </td>
      <td className="px-3 py-2 text-slate-400 whitespace-nowrap max-w-[16rem] truncate">
        {stock.company_name ?? "—"}
      </td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${directionClass}`}>
          {stock.direction ?? "—"}
        </span>
      </td>
      <td className={`px-3 py-2 font-semibold ${scoreClass}`}>{hasScore ? score : "—"}</td>
      <td className="px-3 py-2 text-slate-300">{stock.confidence_label ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{stock.stage ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{stock.rs_label ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{stock.sector_etf ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{money(stock.entry_price)}</td>
      <td className="px-3 py-2 text-slate-300">{money(stock.target_price)}</td>
      <td className="px-3 py-2 text-slate-300">{money(stock.suggested_stop)}</td>
      <td className="px-3 py-2 text-slate-300">{num(stock.rr_ratio)}</td>
      <td className="px-3 py-2 whitespace-nowrap">{earnings}</td>
      <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
        {stock.is_flagged ? fmtSpan(stock.stage_start_date, stock.last_seen_date) : "—"}
      </td>
    </tr>
  );
}
