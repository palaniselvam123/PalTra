"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

/** Rows per page; 0 = all on one page. */
export const PAGE_SIZES = [25, 50, 100, 0] as const;

export type Paged<T> = {
  shown: T[];
  /** Index of the first shown row in the full list (for ranks). */
  start: number;
  page: number;
  pages: number;
  size: number;
  total: number;
  setPage: (page: number) => void;
  setSize: (size: number) => void;
  /** Put this on the element above the table: a page change scrolls back to it. */
  anchor: React.RefObject<HTMLDivElement | null>;
};

function readSize(storeKey: string, fallback: number): number {
  try {
    const n = Number(localStorage.getItem(storeKey));
    return (PAGE_SIZES as readonly number[]).includes(n) && localStorage.getItem(storeKey) !== null ? n : fallback;
  } catch {
    return fallback;
  }
}

/**
 * One page of a table's rows. The page size is remembered in this browser under
 * `storeKey`; the page itself stays put while the rows refresh, and moves back
 * when the list gets shorter than the page.
 */
export function usePaged<T>(rows: T[], storeKey: string, fallback = 25): Paged<T> {
  const [size, setSizeState] = useState(fallback);
  const [page, setPageState] = useState(0);
  const anchor = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSizeState(readSize(storeKey, fallback));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeKey]);

  const total = rows.length;
  const pages = size > 0 ? Math.max(1, Math.ceil(total / size)) : 1;
  const current = Math.min(page, pages - 1);
  useEffect(() => {
    if (page !== current) setPageState(current);
  }, [page, current]);

  const setPage = useCallback(
    (next: number) => {
      setPageState(Math.max(0, Math.min(next, pages - 1)));
      // Back to the top of the table, clear of the sticky menu bar, when it is above the screen.
      const el = anchor.current;
      if (el) {
        const top = el.getBoundingClientRect().top;
        if (top < 0) window.scrollTo({ top: window.scrollY + top - 96, behavior: "smooth" });
      }
    },
    [pages]
  );
  const setSize = useCallback(
    (next: number) => {
      setSizeState(next);
      setPageState(0);
      try {
        localStorage.setItem(storeKey, String(next));
      } catch {
        /* private mode: the size lasts until the page reloads */
      }
    },
    [storeKey]
  );

  const start = size > 0 ? current * size : 0;
  const shown = size > 0 ? rows.slice(start, start + size) : rows;
  return { shown, start, page: current, pages, size, total, setPage, setSize, anchor };
}

const btn =
  "inline-flex h-8 min-w-8 items-center justify-center rounded-md border border-border bg-surface px-1.5 text-slate-300 hover:border-accentSky/50 hover:text-slate-100 disabled:pointer-events-none disabled:opacity-40";

/** "1–25 of 213 · Rows [25] · « ‹ Page 1 of 9 › »". Hidden when everything fits on the smallest page. */
export function Pager<T>({ p, noun = "stocks", className }: { p: Paged<T>; noun?: string; className?: string }) {
  if (p.total <= PAGE_SIZES[0]) return null;
  const first = p.total === 0 ? 0 : p.start + 1;
  const last = p.start + p.shown.length;
  return (
    <nav aria-label="Table pages" className={clsx("flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-slate-400", className)}>
      <span className="tabular-nums">
        {first}–{last} of {p.total} {noun}
      </span>
      <label className="flex items-center gap-1.5">
        Rows
        <select
          value={p.size}
          onChange={(e) => p.setSize(Number(e.target.value))}
          className="h-8 rounded-md border border-border bg-surface px-1.5 text-xs text-slate-200 outline-none"
        >
          {PAGE_SIZES.map((s) => (
            <option key={s} value={s}>
              {s === 0 ? "All" : s}
            </option>
          ))}
        </select>
      </label>
      {p.pages > 1 && (
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className={btn} onClick={() => p.setPage(0)} disabled={p.page === 0} aria-label="First page">
            <ChevronsLeft size={14} />
          </button>
          <button type="button" className={btn} onClick={() => p.setPage(p.page - 1)} disabled={p.page === 0} aria-label="Previous page">
            <ChevronLeft size={14} />
          </button>
          <span className="px-1.5 tabular-nums">
            Page {p.page + 1} of {p.pages}
          </span>
          <button type="button" className={btn} onClick={() => p.setPage(p.page + 1)} disabled={p.page >= p.pages - 1} aria-label="Next page">
            <ChevronRight size={14} />
          </button>
          <button type="button" className={btn} onClick={() => p.setPage(p.pages - 1)} disabled={p.page >= p.pages - 1} aria-label="Last page">
            <ChevronsRight size={14} />
          </button>
        </div>
      )}
    </nav>
  );
}
