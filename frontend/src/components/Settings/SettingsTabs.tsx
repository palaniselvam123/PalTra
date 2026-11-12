"use client";

import { usePinned } from "@/lib/usePinned";
import { PinToggle } from "@/components/ui/PinToggle";
import { useEffect, useState, type ReactNode } from "react";
import clsx from "clsx";

export type SettingsGroup = {
  id: string;
  label: string;
  /** One plain line under the label: what is on the tab. */
  hint?: string;
  icon?: ReactNode;
  sections: [id: string, label: string][];
};

const KEY = "settings.tab.v1";

/** The group a `#hash` points at: a group id, one of its sections, or a part of the SMA form (`#sma-stop`). */
export function groupForHash(groups: SettingsGroup[], hash: string): SettingsGroup | null {
  const id = hash.replace(/^#/, "");
  if (!id) return null;
  for (const g of groups) {
    if (g.id === id || g.sections.some(([s]) => s === id)) return g;
    if (id.startsWith("sma-") && g.sections.some(([s]) => s === "sma-strategy")) return g;
  }
  return null;
}

export function rememberedGroup(groups: SettingsGroup[]): SettingsGroup | null {
  try {
    const id = localStorage.getItem(KEY);
    return groups.find((g) => g.id === id) ?? null;
  } catch {
    return null;
  }
}

/**
 * The Settings page's tabs, one group of settings at a time, pinned under the app header
 * while the group scrolls. The chosen tab goes in the address (`#bots`) and is remembered.
 */
export function SettingsTabs({ groups, active, onPick }: { groups: SettingsGroup[]; active: string; onPick: (group: SettingsGroup) => void }) {
  const [top, setTop] = useState(0);
  const [pinned, setPinned] = usePinned("settings-sections");
  // Under the menu bar only while that bar is frozen too.
  const [navPinned] = usePinned("navbar");

  // Sit just under the sticky header, whatever its height (the simulated-prices banner changes it).
  useEffect(() => {
    const header = document.querySelector("header");
    if (!header) return;
    const measure = () => setTop(navPinned ? header.getBoundingClientRect().height : 0);
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(header);
    return () => watcher.disconnect();
  }, [navPinned]);

  const pick = (g: SettingsGroup) => {
    try {
      localStorage.setItem(KEY, g.id);
    } catch {
      /* private window */
    }
    onPick(g);
  };

  return (
    <nav
      aria-label="Settings sections"
      style={{ top }}
      className={clsx(
        pinned ? "sticky" : "relative",
        "z-10 -mx-4 flex items-center gap-1.5 overflow-x-auto border-b border-border bg-base/90 px-4 py-2 text-xs backdrop-blur lg:-mx-6 lg:px-6"
      )}
    >
      <PinToggle pinned={pinned} onChange={setPinned} what="these tabs" />
      <div
        role="tablist"
        aria-label="Settings groups"
        className="flex items-center gap-1.5"
        onKeyDown={(e) => {
          // Arrow keys move between the tabs, Home / End jump to the first / last (the usual tab pattern).
          const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (!step && e.key !== "Home" && e.key !== "End") return;
          e.preventDefault();
          const at = Math.max(0, groups.findIndex((g) => g.id === active));
          const next = e.key === "Home" ? 0 : e.key === "End" ? groups.length - 1 : (at + step + groups.length) % groups.length;
          pick(groups[next]);
          requestAnimationFrame(() => document.getElementById(`settings-tab-${groups[next].id}`)?.focus());
        }}
      >
        {groups.map((g) => (
          <a
            key={g.id}
            id={`settings-tab-${g.id}`}
            role="tab"
            href={`#${g.id}`}
            aria-selected={active === g.id}
            aria-controls={g.id}
            tabIndex={active === g.id ? 0 : -1}
            onClick={(e) => {
              e.preventDefault();
              pick(g);
            }}
            title={g.hint}
            className={clsx(
              "flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl px-3.5 ring-1 ring-inset",
              active === g.id ? "bg-accentSky font-semibold text-on-accent shadow-sm ring-accentSky" : "text-slate-300 ring-border hover:bg-white/5 hover:text-slate-100"
            )}
          >
            {g.icon ? <span aria-hidden className="shrink-0 opacity-90">{g.icon}</span> : null}
            <span className="flex flex-col items-start leading-tight">
              <span className="font-semibold">{g.label}</span>
              {g.hint ? (
                <span className={clsx("hidden text-[10px] font-normal md:block", active === g.id ? "opacity-80" : "text-slate-400")}>
                  {g.hint}
                </span>
              ) : null}
            </span>
          </a>
        ))}
      </div>
    </nav>
  );
}

/** Jump links to the sections of a tab that has several, as small pills. */
export function OnThisTab({ sections }: { sections: [id: string, label: string][] }) {
  if (sections.length < 2) return null;
  return (
    <nav aria-label="On this tab" className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-slate-400">
      <span>On this tab:</span>
      {sections.map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          className="inline-flex min-h-8 items-center rounded-full px-2.5 font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/30 hover:bg-sky-500/10"
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
