// src/format.js
// =============
// Small shared formatting helpers.

// "2026-06-16" → "06/16/26". Parses the ISO date manually (no Date object) so a
// negative-offset timezone can't shift it to the previous day.
export function fmtMDY(iso) {
  if (!iso) return "—";
  const parts = String(iso).slice(0, 10).split("-");
  if (parts.length !== 3) return "—";
  const [y, m, d] = parts;
  return `${m}/${d}/${y.slice(2)}`;
}

// The flagged-date SPAN. One date if start === end (a 1-day-old flag), else a
// "start – end" range, e.g. "01/25/26 – 02/25/26".
export function fmtSpan(start, end) {
  if (!start && !end) return "—";
  const s = fmtMDY(start);
  const e = fmtMDY(end);
  return s === e ? s : `${s} – ${e}`;
}
