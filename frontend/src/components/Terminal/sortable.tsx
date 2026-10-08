"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

export type SortDir = "asc" | "desc";
export type SortState<K extends string> = { key: K; dir: SortDir } | null;

/**
 * Rows sorted by a clicked column. The first click sorts high to low (text A to Z),
 * the second reverses it, the third goes back to the original order.
 */
export function useSort<T, K extends string>(
  rows: T[],
  value: (row: T, key: K) => number | string | null | undefined
): { sorted: T[]; sort: SortState<K>; onSort: (key: K, text?: boolean) => void } {
  const [sort, setSort] = useState<SortState<K>>(null);
  const onSort = (key: K, text = false) =>
    setSort((cur) => {
      const first: SortDir = text ? "asc" : "desc";
      if (!cur || cur.key !== key) return { key, dir: first };
      if (cur.dir === first) return { key, dir: first === "asc" ? "desc" : "asc" };
      return null;
    });
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = value(a, sort.key);
      const y = value(b, sort.key);
      if (x == null && y == null) return 0;
      if (x == null) return 1; // blanks last either way
      if (y == null) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
      return String(x).localeCompare(String(y)) * sign;
    });
    // `value` is a plain reader; the rows and the sort decide the order.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);
  return { sorted, sort, onSort };
}

/** A header cell that sorts its column when clicked. */
export function SortTh<K extends string>({
  label,
  k,
  sort,
  onSort,
  num = false,
  text = false,
  title,
  className,
}: {
  label: React.ReactNode;
  k: K;
  sort: SortState<K>;
  onSort: (key: K, text?: boolean) => void;
  num?: boolean;
  text?: boolean;
  title?: string;
  className?: string;
}) {
  const on = sort?.key === k;
  const Icon = !on ? ArrowUpDown : sort!.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={on ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
      className={clsx("px-1 py-1", num && "text-right", className)}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onSort(k, text);
        }}
        title={title ?? "Sort by this column"}
        className={clsx(
          "inline-flex min-h-7 items-center gap-1 rounded px-1 uppercase tracking-wider hover:bg-white/5 hover:text-white",
          num && "flex-row-reverse",
          on ? "text-white" : "text-slate-400"
        )}
      >
        {label}
        <Icon size={11} aria-hidden className={on ? "text-sky-300" : "text-slate-600"} />
      </button>
    </th>
  );
}
