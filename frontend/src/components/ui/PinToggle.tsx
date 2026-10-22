"use client";

import clsx from "clsx";
import { Pin, PinOff } from "lucide-react";

/** The freeze / unfreeze button on a bar that sticks to the top of the screen. */
export function PinToggle({
  pinned,
  onChange,
  what,
  className,
}: {
  pinned: boolean;
  onChange: (next: boolean) => void;
  /** What is pinned, for the tooltip ("this header", "the blotter bar"). */
  what: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!pinned)}
      aria-pressed={pinned}
      aria-label={pinned ? `Unfreeze ${what}` : `Freeze ${what}`}
      title={pinned ? `Frozen at the top. Press to unfreeze ${what} so it scrolls away with the page.` : `Freeze ${what} at the top of the screen.`}
      className={clsx(
        "flex min-h-8 min-w-8 shrink-0 items-center justify-center rounded-md ring-1 ring-inset",
        pinned ? "text-sky-300 ring-sky-400/40 hover:bg-sky-500/10" : "text-slate-400 ring-white/15 hover:bg-white/5",
        className
      )}
    >
      {pinned ? <Pin size={14} aria-hidden /> : <PinOff size={14} aria-hidden />}
    </button>
  );
}
