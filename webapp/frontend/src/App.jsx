// src/App.jsx
// ===========
// Single-page shell for now (no router yet): a minimal header + the Flagged
// Stocks page. The nav links are placeholders until routing is added in a later
// step. Dark slate theme, green accents.

import FlagsPage from "./pages/FlagsPage";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-900 text-white">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
        <div className="text-xl font-bold tracking-tight">
          <span className="text-green-400">Stonks</span>
        </div>
        <nav className="flex gap-6 text-sm text-slate-400">
          {/* Active page */}
          <span className="text-white font-medium border-b-2 border-green-400 pb-1">
            Flagged Stocks
          </span>
          {/* Placeholders — wired up in later steps */}
          <span className="cursor-not-allowed hover:text-slate-300">Grader</span>
          <span className="cursor-not-allowed hover:text-slate-300">News</span>
        </nav>
      </header>

      {/* Page body */}
      <main className="max-w-7xl mx-auto">
        <div className="px-6 pt-6">
          <h1 className="text-2xl font-semibold">Flagged Stocks</h1>
          <p className="mt-1 text-sm text-slate-400">
            Active setup flags identified by the confluence screener.
          </p>
        </div>
        <FlagsPage />
      </main>
    </div>
  );
}
