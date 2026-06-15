// src/components/FlagCard.jsx
// ===========================
// One flag rendered as a TABLE ROW (<tr>) — designed to sit inside the TanStack
// Table body in FlagsPage. All the visual encoding (color-coded score, direction
// badge, earnings urgency) lives here; the table itself only handles sorting.
//
// NOTE: the <td> order MUST match the column order defined in FlagsPage.

// Format a dollar value, or an em-dash when missing.
function money(v) {
  return v == null ? "—" : `$${Number(v).toFixed(2)}`;
}

// Format a plain number (e.g. R:R), or an em-dash when missing.
function num(v) {
  return v == null ? "—" : Number(v).toFixed(2);
}

export default function FlagCard({ flag }) {
  // Direction badge: green for Long, red for Short.
  const isLong = flag.direction === "Long";
  const directionClass = isLong
    ? "bg-green-500/20 text-green-400"
    : "bg-red-500/20 text-red-400";

  // Score color: green ≥70, yellow ≥50, red <50.
  const score = flag.score ?? 0;
  const scoreClass =
    score >= 70 ? "text-green-400" : score >= 50 ? "text-yellow-400" : "text-red-400";

  // Earnings: "51d", orange if a report is within 30 days, gray otherwise.
  const days = flag.days_to_earnings;
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
      <td className="px-3 py-2 font-bold text-white">{flag.ticker}</td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${directionClass}`}>
          {flag.direction ?? "—"}
        </span>
      </td>
      <td className={`px-3 py-2 font-semibold ${scoreClass}`}>{score}</td>
      <td className="px-3 py-2 text-slate-300">{flag.confidence_label ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{flag.stage ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{flag.rs_label ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{flag.sector_etf ?? "—"}</td>
      <td className="px-3 py-2 text-slate-300">{money(flag.entry_price)}</td>
      <td className="px-3 py-2 text-slate-300">{money(flag.target_price)}</td>
      <td className="px-3 py-2 text-slate-300">{money(flag.suggested_stop)}</td>
      <td className="px-3 py-2 text-slate-300">{num(flag.rr_ratio)}</td>
      <td className="px-3 py-2 whitespace-nowrap">{earnings}</td>
      <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
        {flag.flagged_date ?? "—"}
      </td>
    </tr>
  );
}
