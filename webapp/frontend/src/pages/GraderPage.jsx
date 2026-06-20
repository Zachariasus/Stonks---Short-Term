// src/pages/GraderPage.jsx
// ========================
// The Stock Grader: a form that POSTs to /grade and renders a full report card —
// AI letter grade + narrative (A), confluence breakdown (B), position sizing (C),
// and earnings timing (D). The /grade call runs the whole pipeline, so it can
// take several seconds — hence the spinner.

import { useState } from "react";
import { gradeStock } from "../api";

// --- small helpers ---------------------------------------------------------
function money(v) {
  return v == null ? "—" : `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}
function gradeColor(grade) {
  return (
    { A: "text-green-400", B: "text-blue-400", C: "text-yellow-400", D: "text-red-400" }[grade] ||
    "text-slate-400" // "?" / stub
  );
}
function earningsBadge(flag) {
  const cls =
    {
      Imminent: "bg-red-500/20 text-red-400",
      "Near-Term": "bg-orange-500/20 text-orange-400",
      Upcoming: "bg-yellow-500/20 text-yellow-400",
    }[flag] || "bg-slate-600/40 text-slate-300"; // Not Imminent / Unknown / Stale
  return <span className={`px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>{flag || "—"}</span>;
}

// One labeled engine progress bar (Section B).
function EngineBar({ label, pts, max }) {
  const value = pts ?? 0;
  const pct = max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    // Desktop: label | pts | bar on one row. Mobile: label above, pts+bar below.
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

// One info box (Section C).
function InfoBox({ label, value, valueClass = "text-white" }) {
  return (
    <div className="flex-1 border border-slate-700 rounded-lg px-3 py-2 bg-slate-800/40">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`text-lg font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

export default function GraderPage() {
  const [ticker, setTicker] = useState("AAPL");
  const [accountSize, setAccountSize] = useState(50000);
  const [riskPct, setRiskPct] = useState(1.0); // shown as %, sent as a fraction

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleGrade(e) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      // riskPct is a percent in the UI (1.0) but a fraction in the API (0.01).
      const data = await gradeStock(t, Number(accountSize), Number(riskPct) / 100);
      setResult(data);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(detail || err.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  // Derived position-sizing values (computed client-side from the response).
  let positionPct = null;
  let dollarRisk = null;
  let negativeUpside = false;
  let poorRR = false;
  let oversized = false;
  if (result) {
    if (result.position_value != null && accountSize > 0) {
      positionPct = (result.position_value / Number(accountSize)) * 100;
    }
    if (result.shares != null && result.entry_price != null && result.stop_price != null) {
      dollarRisk = result.shares * Math.abs(result.entry_price - result.stop_price);
    }
    negativeUpside =
      result.target_price != null && result.entry_price != null && result.target_price < result.entry_price;
    poorRR = result.rr_ratio != null && result.rr_ratio < 1.5;
    oversized = positionPct != null && positionPct > 20;
  }

  return (
    <div className="p-4 md:p-6 max-w-4xl">
      {/* --- Form (stacks full-width on mobile, row on desktop) --- */}
      <form className="flex flex-col md:flex-row md:flex-wrap md:items-end gap-4 mb-6" onSubmit={handleGrade}>
        <label className="flex flex-col text-xs text-slate-400 w-full md:w-auto">
          Ticker
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="mt-1 bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white w-full md:w-32 uppercase"
          />
        </label>

        <label className="flex flex-col text-xs text-slate-400 w-full md:w-auto">
          Account size
          <div className="mt-1 flex items-center bg-slate-800 border border-slate-700 rounded px-2 w-full md:w-auto">
            <span className="text-slate-500 text-sm">$</span>
            <input
              type="number"
              value={accountSize}
              onChange={(e) => setAccountSize(e.target.value)}
              className="bg-transparent px-1 py-2 text-sm text-white w-full md:w-28 outline-none"
            />
          </div>
        </label>

        <label className="flex flex-col text-xs text-slate-400 w-full md:w-auto">
          Risk per trade
          <div className="mt-1 flex items-center bg-slate-800 border border-slate-700 rounded px-2 w-full md:w-auto">
            <input
              type="number"
              step="0.1"
              value={riskPct}
              onChange={(e) => setRiskPct(e.target.value)}
              className="bg-transparent px-1 py-2 text-sm text-white w-full md:w-16 outline-none"
            />
            <span className="text-slate-500 text-sm">%</span>
          </div>
        </label>

        <button
          type="submit"
          disabled={loading}
          className="w-full md:w-auto bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded"
        >
          {loading ? "Grading..." : "Grade This Stock"}
        </button>
      </form>

      {/* --- Loading spinner --- */}
      {loading && (
        <div className="flex items-center gap-3 text-slate-300">
          <div className="h-5 w-5 rounded-full border-2 border-slate-600 border-t-green-400 animate-spin" />
          Running the full analysis pipeline for {ticker}...
        </div>
      )}

      {/* --- Error card --- */}
      {error && !loading && (
        <div className="border border-red-500/50 bg-red-500/10 rounded-lg p-4 text-red-300">
          <div className="font-semibold text-red-400">Grade failed</div>
          <div className="text-sm mt-1">{error}</div>
        </div>
      )}

      {/* --- Report card --- */}
      {result && !loading && (
        <div className="flex flex-col gap-6">
          {/* Grade source note — rules-based by default (no key needed) */}
          {result.grade_source === "rules" && (
            <div className="border border-slate-600/60 bg-slate-700/30 rounded-lg p-3 text-slate-300 text-sm">
              Rules-based grade — computed locally from the signals, no API key needed. Add
              ANTHROPIC_API_KEY to <code className="text-slate-400">.env</code> for an AI-written narrative.
            </div>
          )}

          {/* SECTION A — Grade card */}
          <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
            <div className="text-center">
              <div className={`text-7xl font-bold ${gradeColor(result.grade)}`}>
                {result.grade || "?"}
              </div>
              <div className="mt-2 text-slate-300">{result.one_line_verdict || "—"}</div>
              <div className="mt-1 text-xs text-slate-500">
                {result.ticker} · {result.direction || "—"}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
              <div className="border border-green-500/30 rounded-lg p-3 bg-green-500/5">
                <div className="text-xs font-semibold text-green-400 mb-1">BULL CASE</div>
                <div className="text-sm text-slate-300">{result.bull_case || "—"}</div>
              </div>
              <div className="border border-red-500/30 rounded-lg p-3 bg-red-500/5">
                <div className="text-xs font-semibold text-red-400 mb-1">BEAR CASE</div>
                <div className="text-sm text-slate-300">{result.bear_case || "—"}</div>
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs font-semibold text-slate-400 mb-1">KEY RISKS</div>
              {result.key_risks && result.key_risks.length > 0 ? (
                <ul className="list-disc list-inside text-sm text-slate-300">
                  {result.key_risks.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-slate-500">—</div>
              )}
            </div>

            <div className="mt-4 border border-slate-600 rounded-lg p-3 bg-slate-700/30">
              <div className="text-xs font-semibold text-slate-400 mb-1">SUGGESTED ACTION</div>
              <div className="text-sm text-white">{result.suggested_action || "—"}</div>
            </div>
          </div>

          {/* SECTION B — Confluence score */}
          <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
            <div className="flex items-baseline justify-between mb-4">
              <h2 className="text-lg font-semibold">Confluence Score</h2>
              <div className="text-slate-300">
                <span className="text-2xl font-bold text-white">
                  {result.confluence_score ?? "—"}
                </span>
                <span className="text-slate-500"> / 100</span>
                <span className="ml-2 text-sm text-slate-400">{result.confidence_label || ""}</span>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <EngineBar label="E1 Trend & Momentum" pts={result.engine_1_pts} max={35} />
              <EngineBar label="E2 Fundamental Traj." pts={result.engine_2_pts} max={25} />
              <EngineBar label="E3 Top-Down / Rotation" pts={result.engine_3_pts} max={25} />
              <EngineBar label="E4 Valuation" pts={result.engine_4_pts} max={15} />
            </div>
          </div>

          {/* SECTION C — Position sizing */}
          <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
            <h2 className="text-lg font-semibold mb-4">Position Sizing</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <InfoBox label="Entry" value={money(result.entry_price)} />
              <InfoBox label="Stop" value={money(result.stop_price)} />
              <InfoBox label="Target" value={money(result.target_price)} valueClass={negativeUpside ? "text-red-400" : "text-white"} />
              <InfoBox
                label="R:R"
                value={result.rr_ratio == null ? "—" : `${result.rr_ratio.toFixed(1)}x`}
                valueClass={poorRR ? "text-red-400" : "text-white"}
              />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3">
              <InfoBox label="Shares" value={result.shares ?? "—"} />
              <InfoBox
                label="Position"
                value={
                  result.position_value == null
                    ? "—"
                    : `${money(result.position_value)}${positionPct != null ? ` (${positionPct.toFixed(1)}%)` : ""}`
                }
                valueClass={oversized ? "text-red-400" : "text-white"}
              />
              <InfoBox label="Dollar risk" value={dollarRisk == null ? "—" : money(dollarRisk)} />
            </div>

            {/* Warnings */}
            {(negativeUpside || poorRR || oversized) && (
              <div className="flex flex-col gap-2 mt-4">
                {negativeUpside && (
                  <div className="border border-red-500/50 bg-red-500/10 rounded px-3 py-2 text-sm text-red-300">
                    ⚠️ Target is below entry — negative upside on this setup.
                  </div>
                )}
                {poorRR && (
                  <div className="border border-red-500/50 bg-red-500/10 rounded px-3 py-2 text-sm text-red-300">
                    ⚠️ Reward:risk is below 1.5 — the reward doesn’t justify the risk.
                  </div>
                )}
                {oversized && (
                  <div className="border border-red-500/50 bg-red-500/10 rounded px-3 py-2 text-sm text-red-300">
                    ⚠️ Position is over 20% of the account — oversized.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* SECTION D — Earnings */}
          <div className="border border-slate-800 rounded-lg p-4 md:p-6 bg-slate-800/30">
            <h2 className="text-lg font-semibold mb-3">Earnings</h2>
            <div className="flex items-center gap-3 text-slate-300">
              <span>Next earnings:</span>
              <span className="text-white">{result.next_earnings_date || "Unknown"}</span>
              {result.days_to_earnings != null && (
                <span className="text-slate-400">({result.days_to_earnings} days)</span>
              )}
              <span>→</span>
              {earningsBadge(result.earnings_flag)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
