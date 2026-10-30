"use client";

import { usePinned } from "@/lib/usePinned";
import { PinToggle } from "@/components/ui/PinToggle";
import { useEffect, useState } from "react";
import clsx from "clsx";

export type SettingsGroup = { id: string; label: string; sections: [id: string, label: string][] };

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
      <div role="tablist" aria-label="Settings groups" className="flex items-center gap-1.5">
        {groups.map((g) => (
          <a
            key={g.id}
            role="tab"
            href={`#${g.id}`}
            aria-selected={active === g.id}
            onClick={(e) => {
              e.preventDefault();
              pick(g);
            }}
            className={clsx(
              "flex min-h-9 items-center whitespace-nowrap rounded-full px-3.5 font-semibold ring-1 ring-inset",
              active === g.id ? "bg-accentSky text-white ring-accentSky" : "text-slate-300 ring-border hover:bg-white/5"
            )}
          >
            {g.label}
          </a>
        ))}
      </div>
    </nav>
  );
}

/** Jump links to the sections of a tab that has several. */
export function OnThisTab({ sections }: { sections: [id: string, label: string][] }) {
  if (sections.length < 2) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
      <span>On this tab:</span>
      {sections.map(([id, label]) => (
        <a key={id} href={`#${id}`} className="text-sky-300 underline-offset-2 hover:underline">
          {label}
        </a>
      ))}
    </p>
  );
}
