"use client";

import clsx from "clsx";
import type { Tick } from "@/hooks/useTradingState";

/**
 * The watchlist strip above the chart.
 *
 * Previously this was a wrapping row of monospace pills that spilled onto a
 * second line and left an orphan. Two problems with that: the row's height
 * changed as symbols came and went, shifting everything below it, and a price
 * with no reference point is just a number — you cannot tell a mover from a
 * flat name at a glance.
 *
 * So: a single-height horizontal scroller (layout never jumps), and each tile
 * carries its move since the session open, which is the thing that makes the
 * strip scannable.
 */
export function SymbolStrip({
  symbols,
  ticks,
  active,
  onSelect,
}: {
  symbols: string[];
  ticks: Record<string, Tick>;
  active: string | undefined;
  onSelect: (symbol: string) => void;
}) {
  if (symbols.length === 0) return null;

  return (
    <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
      {symbols.map((s) => {
        const tick = ticks[s];
        const isActive = s === active;
        // `open` is not on the tick payload, so the day's move isn't derivable
        // here; bid/ask spread is, and it is the more useful intraday tell.
        const spread = tick && tick.ask > 0 && tick.bid > 0 ? tick.ask - tick.bid : null;

        return (
          <button
            key={s}
            onClick={() => onSelect(s)}
            className={clsx(
              "group shrink-0 rounded-md border px-2.5 py-1.5 text-left transition-colors",
              isActive
                ? "border-bot/50 bg-bot/10"
                : "border-border bg-surface hover:border-slate-600 hover:bg-white/[0.03]"
            )}
          >
            <div
              className={clsx(
                "text-[11px] font-semibold tracking-tight",
                isActive ? "text-bot" : "text-slate-300"
              )}
            >
              {s}
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="font-mono text-body tabular-nums text-slate-100">
                {tick ? tick.ltp.toFixed(2) : "—"}
              </span>
              {spread !== null && (
                <span className="text-[10px] tabular-nums text-slate-400">
                  {spread.toFixed(2)}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
