"use client";

import { useState, type ReactNode } from "react";
import clsx from "clsx";

/**
 * A short lead line with the long explanation one tap away. Page intros were
 * four-line paragraphs on every visit; the detail is still there for whoever
 * wants it, without pushing the controls down the screen.
 */
export function Explain({ lead, children, className }: { lead: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={clsx("max-w-3xl text-sm text-slate-400", className)}>
      <span>{lead}</span>{" "}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="whitespace-nowrap text-xs font-medium text-accentSky underline-offset-2 hover:underline"
      >
        {open ? "Less" : "More"}
      </button>
      {open ? <p className="mt-1 leading-relaxed">{children}</p> : null}
    </div>
  );
}
