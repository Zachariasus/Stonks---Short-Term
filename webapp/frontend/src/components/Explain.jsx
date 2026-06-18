// src/components/Explain.jsx
// ==========================
// Shared "click-to-explain" UI used by the Stock Profile and Sector Profile
// pages: a small info dot, a click-to-toggle popover, and a Stat box that wires
// the two together. Click a stat to open a floating box explaining it; click it
// again, click outside, or press Escape to close. One concern, one file — both
// profiles import from here instead of each redefining it.

import { useEffect, useRef, useState } from "react";

// A tiny "i" affordance so it's obvious a stat is tappable for an explanation.
export function InfoDot() {
  return (
    <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-slate-500 text-[9px] font-bold leading-none text-slate-400">
      i
    </span>
  );
}

// Click-to-toggle explanation box. Flips to the right edge when the trigger sits
// in the right half of the screen, so the box never runs off-screen on a phone.
export function InfoPopover({ info, children, buttonClassName }) {
  const [open, setOpen] = useState(false);
  const [alignRight, setAlignRight] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (!open && ref.current) {
      const r = ref.current.getBoundingClientRect();
      setAlignRight(r.left > window.innerWidth / 2);
    }
    setOpen((o) => !o);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className={`${buttonClassName} ${open ? "ring-1 ring-green-500/50" : ""}`}
      >
        {children}
      </button>
      {open && (
        <div
          role="tooltip"
          className={`absolute z-30 top-full mt-1 ${
            alignRight ? "right-0" : "left-0"
          } w-64 max-w-[calc(100vw-1.5rem)] rounded-lg border border-slate-600 bg-slate-900 p-3 text-xs leading-relaxed text-slate-200 shadow-xl`}
        >
          {info}
        </div>
      )}
    </div>
  );
}

// A labeled stat box. With `info`, the whole box becomes a click target that
// toggles its explanation popover (and shows the info dot); without it, a plain
// read-only box.
export function Stat({ label, value, valueClass = "text-white", info }) {
  const body = (
    <>
      <div className="text-xs text-slate-400 flex items-center gap-1">
        {label}
        {info && <InfoDot />}
      </div>
      <div className={`text-sm font-semibold ${valueClass}`}>{value ?? "—"}</div>
    </>
  );

  if (!info) {
    return <div className="border border-slate-700 rounded-lg px-3 py-2 bg-slate-800/40">{body}</div>;
  }

  return (
    <InfoPopover
      info={info}
      buttonClassName="w-full text-left border border-slate-700 rounded-lg px-3 py-2 bg-slate-800/40 hover:border-slate-500 transition-colors"
    >
      {body}
    </InfoPopover>
  );
}

// Small inline loading spinner with a label.
export function Spinner({ label }) {
  return (
    <div className="flex items-center gap-3 text-slate-400 text-sm">
      <div className="h-4 w-4 rounded-full border-2 border-slate-600 border-t-green-400 animate-spin" />
      {label}
    </div>
  );
}
