// src/App.jsx
// ===========
// App shell + client-side routing (React Router). Three routes:
//   /        → Flagged Stocks (FlagsPage)
//   /news    → News Feed       (NewsPage)
//   /grader  → Stock Grader    (GraderPage)
// The nav uses <Link> (no full page reloads) and highlights the active route
// with a green underline. Dark slate theme, green accents.

import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";

import FlagsPage from "./pages/FlagsPage";
import NewsPage from "./pages/NewsPage";
import GraderPage from "./pages/GraderPage";

// A nav link that underlines itself when its route is active.
function NavLink({ to, children }) {
  const { pathname } = useLocation();
  const active = pathname === to;
  return (
    <Link
      to={to}
      className={
        active
          ? "text-white font-medium border-b-2 border-green-400 pb-1"
          : "text-slate-400 hover:text-slate-200 pb-1"
      }
    >
      {children}
    </Link>
  );
}

function Header() {
  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
      <Link to="/" className="text-xl font-bold tracking-tight">
        <span className="text-green-400">Stonks</span>
      </Link>
      <nav className="flex gap-6 text-sm">
        <NavLink to="/">Flagged Stocks</NavLink>
        <NavLink to="/news">News Feed</NavLink>
        <NavLink to="/grader">Stock Grader</NavLink>
      </nav>
    </header>
  );
}

// Per-route page heading.
function PageHeading({ title, subtitle }) {
  return (
    <div className="px-6 pt-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-slate-900 text-white">
        <Header />
        <main className="max-w-7xl mx-auto">
          <Routes>
            <Route
              path="/"
              element={
                <>
                  <PageHeading
                    title="Flagged Stocks"
                    subtitle="Active setup flags identified by the confluence screener."
                  />
                  <FlagsPage />
                </>
              }
            />
            <Route
              path="/news"
              element={
                <>
                  <PageHeading
                    title="News Feed"
                    subtitle="Relevance-ranked, sentiment-tagged headlines for a ticker."
                  />
                  <NewsPage />
                </>
              }
            />
            <Route
              path="/grader"
              element={
                <>
                  <PageHeading
                    title="Stock Grader"
                    subtitle="AI letter grade, confluence breakdown, and position sizing for one stock."
                  />
                  <GraderPage />
                </>
              }
            />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
