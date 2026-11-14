"use client";

import { useEffect, useState } from "react";
import { DEFAULT_NEW_DAYS, FEATURES, NEW_DAY_CHOICES, isNew, istToday, type Feature } from "@/lib/features";

/**
 * What's new, per browser: how many days a feature counts as New (`whatsnew.days`), and the new features already seen
 * on the What's new page (`whatsnew.seen`, ids), for the unseen count on the menu. Display only.
 */
const DAYS_KEY = "whatsnew.days";
const SEEN_KEY = "whatsnew.seen";
const EVENT = "paltra:whatsnew";

function readDays(): number {
  try {
    const n = Number(localStorage.getItem(DAYS_KEY));
    return (NEW_DAY_CHOICES as readonly number[]).includes(n) ? n : DEFAULT_NEW_DAYS;
  } catch {
    return DEFAULT_NEW_DAYS;
  }
}

function readSeen(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

/** Re-read on a change in this tab (EVENT) or another tab (storage). */
function useStored<T>(read: () => T, initial: T): T {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    const update = () => setValue(read());
    update();
    window.addEventListener(EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(EVENT, update);
      window.removeEventListener("storage", update);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return value;
}

/** How many days a feature counts as New, and a setter (kept in this browser). */
export function useNewDays(): [number, (days: number) => void] {
  const days = useStored(readDays, DEFAULT_NEW_DAYS);
  const set = (next: number) => {
    try {
      localStorage.setItem(DAYS_KEY, String(next));
    } catch {
      /* private window: lasts for this page only */
    }
    window.dispatchEvent(new Event(EVENT));
  };
  return [days, set];
}

/** Today in IST, known only in the browser (null while the page is being built), so a static page never goes stale. */
export function useToday(): string | null {
  const [today, setToday] = useState<string | null>(null);
  useEffect(() => setToday(istToday()), []);
  return today;
}

/** The features that are New now (empty until the browser knows the date). */
export function useNewFeatures(): Feature[] {
  const [days] = useNewDays();
  const today = useToday();
  return today ? FEATURES.filter((f) => isNew(f, days, today)) : [];
}

/** New features not yet seen on the What's new page (the menu's count). */
export function useUnseenCount(): number {
  const fresh = useNewFeatures();
  const seen = useStored(readSeen, new Set<string>());
  return fresh.filter((f) => !seen.has(f.id)).length;
}

/** Mark these features as seen (the What's new page does it for the new ones on screen). */
export function markSeen(ids: string[]): void {
  try {
    const known = new Set(FEATURES.map((f) => f.id));
    const seen = readSeen();
    ids.forEach((id) => seen.add(id));
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seen].filter((id) => known.has(id))));
  } catch {
    /* private window */
  }
  window.dispatchEvent(new Event(EVENT));
}

/** How many New features point at a place: their link contains `match` (e.g. "#trend", "view=history", "#sma-stop"). */
export function useNewCount(match: (f: Feature) => boolean): number {
  return useNewFeatures().filter(match).length;
}
