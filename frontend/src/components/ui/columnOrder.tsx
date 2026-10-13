"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { RotateCcw } from "lucide-react";

/** Props to spread on a header cell so it can be dragged onto another one. */
export type DragThProps = {
  draggable: true;
  onDragStart: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
  onDragEnd: () => void;
  className: string;
  "aria-roledescription": string;
};

function readOrder<K extends string>(storeKey: string, defaults: readonly K[]): K[] {
  try {
    const raw = localStorage.getItem(storeKey);
    if (!raw) return [...defaults];
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return [...defaults];
    // Keep the saved order of columns that still exist, then add any new ones where they belong.
    const kept = saved.filter((k): k is K => defaults.includes(k as K));
    const missing = defaults.filter((k) => !kept.includes(k));
    return [...kept, ...missing];
  } catch {
    return [...defaults];
  }
}

/**
 * The order of a table's columns, changed by dragging one header onto another,
 * and remembered in this browser under `storeKey`. Display only.
 */
export function useColumnOrder<K extends string>(storeKey: string, defaults: readonly K[]) {
  const [order, setOrder] = useState<K[]>([...defaults]);
  const [dragging, setDragging] = useState<K | null>(null);
  const [over, setOver] = useState<K | null>(null);

  useEffect(() => {
    setOrder(readOrder(storeKey, defaults));
    // `defaults` is a fixed list per table.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeKey]);

  const save = useCallback(
    (next: K[]) => {
      setOrder(next);
      try {
        localStorage.setItem(storeKey, JSON.stringify(next));
      } catch {
        /* private mode: the order lasts until the page reloads */
      }
    },
    [storeKey]
  );

  const move = (from: K, to: K) => {
    if (from === to) return;
    const next = order.filter((k) => k !== from);
    const at = next.indexOf(to);
    // Dropped on a column to the right: land after it; to the left: before it.
    next.splice(order.indexOf(from) < order.indexOf(to) ? at + 1 : at, 0, from);
    save(next);
  };

  const dragProps = (k: K): DragThProps => ({
    draggable: true,
    "aria-roledescription": "draggable column",
    onDragStart: (e) => {
      setDragging(k);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", k);
    },
    onDragOver: (e) => {
      if (dragging == null) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (over !== k) setOver(k);
    },
    onDragLeave: () => setOver((cur) => (cur === k ? null : cur)),
    onDrop: (e) => {
      e.preventDefault();
      if (dragging != null) move(dragging, k);
      setDragging(null);
      setOver(null);
    },
    onDragEnd: () => {
      setDragging(null);
      setOver(null);
    },
    className: clsx(
      "cursor-grab select-none active:cursor-grabbing",
      dragging === k && "opacity-40",
      over === k && dragging != null && dragging !== k && "bg-accentSky/10 shadow-[inset_2px_0_0_rgb(var(--c-sky))]"
    ),
  });

  const moved = order.join("|") !== defaults.join("|");
  const reset = () => save([...defaults]);
  return { order, dragProps, moved, reset };
}

/** Render a row's cells (or a header's cells) in the chosen column order. */
export function inOrder<K extends string>(order: readonly K[], cells: Record<K, React.ReactNode>) {
  return order.map((k) => <Fragment key={k}>{cells[k]}</Fragment>);
}

/** "Drag a header to move a column" with a Reset button once a column was moved. */
export function ColumnHint({ moved, onReset }: { moved: boolean; onReset: () => void }) {
  return (
    <p className="mb-1 flex items-center gap-2 text-[11px] text-slate-500">
      Drag a column header to move it.
      {moved ? (
        <button
          type="button"
          onClick={onReset}
          className="inline-flex min-h-7 items-center gap-1 rounded px-1.5 text-accentSky hover:bg-white/5"
        >
          <RotateCcw size={11} aria-hidden /> Reset columns
        </button>
      ) : null}
    </p>
  );
}
