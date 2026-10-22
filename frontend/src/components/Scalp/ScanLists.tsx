"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { api } from "@/lib/api";

/** The stock lists a Scalp scan can read; the ticked ones are joined. */
export type ScanList = "armed" | "fno" | "nifty50" | "nifty100" | "niftybank";

export const SCAN_LISTS: { key: ScanList; label: string; hint: string }[] = [
  { key: "armed", label: "Armed stocks", hint: "Every bot's Trade list (quick)" },
  { key: "fno", label: "All F&O stocks", hint: "About 200 stocks with futures (slow)" },
  { key: "nifty50", label: "Nifty 50", hint: "NSE's Nifty 50 constituents" },
  { key: "nifty100", label: "Nifty 100", hint: "NSE's Nifty 100 constituents (includes the Nifty 50)" },
  { key: "niftybank", label: "Nifty Bank", hint: "NSE's Nifty Bank constituents" },
];
const KEYS = new Set<string>(SCAN_LISTS.map((l) => l.key));

export function cleanLists(raw: unknown, fallback: ScanList[]): ScanList[] {
  const picked = Array.isArray(raw) ? raw.filter((k): k is ScanList => KEYS.has(String(k))) : [];
  return picked.length ? Array.from(new Set(picked)) : fallback;
}

export function listsLabel(lists: ScanList[]): string {
  return SCAN_LISTS.filter((l) => lists.includes(l.key))
    .map((l) => l.label)
    .join(" + ");
}

/**
 * The stocks to scan for the ticked lists: armed stocks from the page, the rest from the desk
 * (`/api/scalp/universe?lists=`), joined without repeats. Throws with the reason when empty.
 */
export async function resolveScanSymbols(lists: ScanList[], armed: Iterable<string>): Promise<string[]> {
  const names: string[] = [];
  const notes: string[] = [];
  if (lists.includes("armed")) {
    const mine = Array.from(armed, (s) => s.toUpperCase());
    if (!mine.length) notes.push("no stocks are armed on any bot");
    names.push(...mine);
  }
  const fetched = lists.filter((k) => k !== "armed");
  if (fetched.length) {
    const u = await api.scalpUniverse(fetched);
    for (const s of u.symbols) if (!names.includes(s)) names.push(s);
    for (const [key, why] of Object.entries(u.errors ?? {})) notes.push(`${SCAN_LISTS.find((l) => l.key === key)?.label ?? key}: ${why}`);
    if (!u.symbols.length && u.error && !notes.length) notes.push(u.error);
  }
  if (!names.length) throw new Error(`Nothing to scan: ${notes.join("; ") || "tick at least one list"}.`);
  return names;
}

/** The ticked lists, remembered in this browser under `storeKey`. */
export function useScanLists(storeKey: string, initial: ScanList[]): [ScanList[], (next: ScanList[]) => void] {
  const [lists, setLists] = useState<ScanList[]>(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storeKey);
      if (raw) setLists(cleanLists(JSON.parse(raw), initial));
    } catch {
      /* private mode */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeKey]);
  const set = (next: ScanList[]) => {
    const clean = cleanLists(next, initial);
    setLists(clean);
    try {
      localStorage.setItem(storeKey, JSON.stringify(clean));
    } catch {
      /* private mode */
    }
  };
  return [lists, set];
}

/** "Scan: Nifty 50 + Nifty 100 ▾" with a tick box per list (at least one stays ticked). */
export function ScanListPicker({ value, onChange, disabled }: { value: ScanList[]; onChange: (next: ScanList[]) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
        title="Which stocks the scan reads. Tick one or more lists; they are joined without repeats."
        className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
      >
        <span className="text-slate-400">Scan:</span> <span className="font-semibold">{listsLabel(value)}</span>
        <ChevronDown size={13} aria-hidden className={clsx("transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div
          role="group"
          aria-label="Scan lists"
          className="absolute left-0 z-30 mt-1 w-72 rounded-lg border border-slate-700 bg-card p-2 shadow-xl"
        >
          {SCAN_LISTS.map((l) => {
            const on = value.includes(l.key);
            return (
              <label key={l.key} className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1.5 text-sm hover:bg-white/5">
                <input
                  type="checkbox"
                  checked={on}
                  disabled={on && value.length === 1}
                  onChange={() => onChange(on ? value.filter((k) => k !== l.key) : [...value, l.key])}
                  className="mt-0.5 accent-sky-500"
                />
                <span>
                  <span className="text-slate-100">{l.label}</span>
                  <span className="block text-xs text-slate-400">{l.hint}</span>
                </span>
              </label>
            );
          })}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="mt-1 w-full rounded-md py-1 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
          >
            Done
          </button>
        </div>
      ) : null}
    </div>
  );
}
