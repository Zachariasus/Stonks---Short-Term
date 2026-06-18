// src/App.jsx
// ===========
// App shell + client-side routing (React Router). Routes:
//   /              → Stocks          (StocksPage — every S&P 500 name, flag is a filter)
//   /stock         → Stock Profile   (search landing)
//   /stock/:ticker → Stock Profile   (grade + snapshot + news for one name)
//   /news          → News Feed       (NewsPage)
//   /grader        → Stock Grader    (GraderPage)
//   /sectors       → Sector Rankings (SectorPage)
// Desktop: horizontal nav on the right. Mobile (<768px): a hamburger (☰) that
// toggles a dropdown (closes on link click). Dark slate theme, green accents.

import { useState } from "react";
import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";

import StocksPage from "./pages/StocksPage";
import StockProfilePage from "./pages/StockProfilePage";
import NewsPage from "./pages/NewsPage";
import GraderPage from "./pages/GraderPage";
import SectorPage from "./pages/SectorPage";

const NAV = [
  { to: "/", label: "Stocks" },
  { to: "/stock", label: "Profile" },
  { to: "/news", label: "News Feed" },
  { to: "/grader", label: "Stock Grader" },
  { to: "/sectors", label: "Sectors" },
];

// A nav link that underlines itself when its route is active. For nested routes
// (e.g. /stock/:ticker under the "Profile" tab) a non-root link also matches its
// sub-paths, so the tab stays highlighted on a profile page.
function NavLink({ to, children, onClick }) {
  const { pathname } = useLocation();
  const active = pathname === to || (to !== "/" && pathname.startsWith(to + "/"));
  return (
    <Link
      to={to}
      onClick={onClick}
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
  const [open, setOpen] = useState(false);
  return (
    <header className="border-b border-slate-800">
      <div className="flex items-center justify-between px-4 md:px-6 py-4">
        <Link to="/" className="text-xl font-bold tracking-tight" onClick={() => setOpen(false)}>
          <span className="text-green-400">Stonks</span>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden md:flex gap-6 text-sm">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to}>
              {n.label}
            </NavLink>
          ))}
        </nav>

        {/* Mobile hamburger */}
        <button
          className="md:hidden text-2xl text-slate-300 leading-none"
          onClick={() => setOpen((o) => !o)}
          aria-label="Toggle menu"
          aria-expanded={open}
        >
          ☰
        </button>
      </div>

      {/* Mobile dropdown (closes when a link is clicked) */}
      {open && (
        <nav className="md:hidden flex flex-col gap-3 px-4 pb-4 text-sm border-t border-slate-800 pt-3">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} onClick={() => setOpen(false)}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      )}
    </header>
  );
}

// Per-route page heading.
function PageHeading({ title, subtitle }) {
  return (
    <div className="px-4 md:px-6 pt-6">
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
                    title="Stocks"
                    subtitle="Every S&P 500 stock from the confluence screen — defaulted to the flagged setups."
                  />
                  <StocksPage />
                </>
              }
            />
            {/* Stock Profile renders its own header (back-link + ticker), so it
                skips the generic PageHeading. Both the search landing (/stock)
                and a specific name (/stock/:ticker) use the same component. */}
            <Route path="/stock" element={<StockProfilePage />} />
            <Route path="/stock/:ticker" element={<StockProfilePage />} />
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
            <Route
              path="/sectors"
              element={
                <>
                  <PageHeading
                    title="Sector Rankings"
                    subtitle="The 11 sectors ranked by relative strength, with the top-down tilt."
                  />
                  <SectorPage />
                </>
              }
            />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
