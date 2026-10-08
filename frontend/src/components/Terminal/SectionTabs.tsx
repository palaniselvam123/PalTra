"use client";

import clsx from "clsx";

export const TERMINAL_TABS = [
  ["all", "All"],
  ["watchlist", "Watchlist"],
  ["live", "Live"],
  ["strategy", "Strategy"],
  ["blotter", "Blotter"],
  ["alerts", "Alerts"],
] as const;
export type TerminalTab = (typeof TERMINAL_TABS)[number][0];
export const TAB_KEY = "terminal.tab";

/** Shows one section of the terminal at a time. Hidden sections stay mounted (CSS only). */
export function SectionTabs({
  tab,
  onTab,
  alerts = true,
}: {
  tab: TerminalTab;
  onTab: (tab: TerminalTab) => void;
  /** The research desk has no alerts. */
  alerts?: boolean;
}) {
  return (
    <div
      role="tablist"
      aria-label="Terminal sections"
      className="-mx-1 flex gap-1 overflow-x-auto px-1 [scrollbar-width:none]"
    >
      {TERMINAL_TABS.filter(([id]) => alerts || id !== "alerts").map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={tab === id}
          onClick={() => onTab(id)}
          className={clsx(
            "min-h-11 shrink-0 rounded-lg px-4 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400",
            tab === id
              ? "bg-sky-500/20 text-slate-100 ring-1 ring-inset ring-sky-400/60"
              : "text-slate-400 ring-1 ring-inset ring-white/10 hover:bg-white/5 hover:text-slate-100"
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Is this part of the page in view on `tab`? */
export function inTab(tab: TerminalTab, section: Exclude<TerminalTab, "all">): boolean {
  return tab === "all" || tab === section;
}
