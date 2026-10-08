"use client";

import clsx from "clsx";
import type { ReactNode } from "react";

/** Shared pieces for the SMA terminal. One palette, one type scale.

Colours are picked for WCAG AA (4.5:1) on the terminal surfaces #0B0E14 and
#151921. Green and red mean profit/loss only; sky is long and violet is short.
*/
export const tone = {
  profit: "text-emerald-400",
  loss: "text-rose-400",
  muted: "text-slate-400",
  warn: "text-amber-300",
} as const;

export function pnlTone(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value === 0) return "text-slate-200";
  return value > 0 ? tone.profit : tone.loss;
}

type BadgeColor = "green" | "red" | "blue" | "amber" | "slate" | "violet" | "sky";

const BADGE: Record<BadgeColor, string> = {
  green: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  red: "bg-rose-500/15 text-rose-300 ring-rose-400/35",
  blue: "bg-blue-500/15 text-blue-300 ring-blue-400/35",
  amber: "bg-amber-500/15 text-amber-200 ring-amber-400/35",
  slate: "bg-white/5 text-slate-300 ring-white/15",
  violet: "bg-violet-500/15 text-violet-200 ring-violet-400/35",
  sky: "bg-sky-500/15 text-sky-200 ring-sky-400/35",
};

export function Badge({
  color,
  children,
  className,
  title,
}: {
  color: BadgeColor;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase leading-4 tracking-wide ring-1 ring-inset",
        BADGE[color],
        className
      )}
    >
      {children}
    </span>
  );
}

export function SideBadge({ side }: { side: "LONG" | "SHORT" | "FLAT" | string | null | undefined }) {
  if (side === "LONG") return <Badge color="sky">Long</Badge>;
  if (side === "SHORT") return <Badge color="violet">Short</Badge>;
  return <Badge color="slate">Flat</Badge>;
}

/** Grey placeholder while data loads. Never shown as a zero. */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden className={clsx("block animate-pulse rounded bg-white/10", className)} />;
}
