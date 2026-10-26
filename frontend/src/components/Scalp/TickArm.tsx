"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { CheckSquare, Newspaper } from "lucide-react";
import { StockNewsPanel } from "@/components/Terminal/StockNewsPanel";
import { WatchNewsDialog } from "@/components/News/WatchNewsDialog";

/** Ticked stocks in a scan table, for "Arm ticked" (several stocks on one bot at once). */
export function useTicks() {
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const toggle = (symbol: string, on: boolean) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(symbol);
      else next.delete(symbol);
      return next;
    });
  return { ticked, setTicked, toggle };
}

type Ticks = ReturnType<typeof useTicks>;

/** The bar above a scan table: how many are ticked, Clear, Watch news and Arm ticked. */
export function TickBar({ ticks, onArm }: { ticks: Ticks; onArm: (symbols: string[]) => void }) {
  const n = ticks.ticked.size;
  const [watch, setWatch] = useState<string[] | null>(null);
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-profit/25 bg-profit/[0.05] px-3 py-1.5 text-xs text-slate-300">
      <CheckSquare size={13} className="text-profit" />
      <span>{n ? `${n} ticked` : "Tick stocks to arm several on one bot with the same quantity, stop and target (then backtest them)"}</span>
      {n > 0 && (
        <button type="button" onClick={() => ticks.setTicked(new Set())} className="text-slate-400 hover:text-slate-200">
          Clear
        </button>
      )}
      <button
        type="button"
        disabled={n === 0}
        onClick={() => setWatch(Array.from(ticks.ticked))}
        title="Add the ticked stocks to a news watch list and open their news"
        className="ml-auto inline-flex min-h-8 items-center gap-1.5 rounded-md bg-amber-500/10 px-3 font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/20 disabled:opacity-40"
      >
        <Newspaper size={13} /> Watch news{n ? ` (${n})` : ""}
      </button>
      {watch ? <WatchNewsDialog symbols={watch} onClose={() => setWatch(null)} /> : null}
      <button
        type="button"
        disabled={n === 0}
        onClick={() => onArm(Array.from(ticks.ticked))}
        className="inline-flex min-h-8 items-center gap-1.5 rounded-md bg-profit/15 px-3 font-semibold text-profit ring-1 ring-inset ring-profit/40 hover:bg-profit/25 disabled:opacity-40"
      >
        Arm ticked{n ? ` (${n})` : ""}
      </button>
    </div>
  );
}

/** Header checkbox: ticks or clears every stock in `symbols` (the rows the filters show). */
export function TickHead({ ticks, symbols }: { ticks: Ticks; symbols: string[] }) {
  const all = symbols.length > 0 && symbols.every((s) => ticks.ticked.has(s));
  return (
    <th className="w-7 pb-2">
      <input
        type="checkbox"
        aria-label="Tick every stock shown"
        className="accent-emerald-400"
        checked={all}
        onChange={(e) =>
          ticks.setTicked((prev) => {
            const next = new Set(prev);
            for (const s of symbols) {
              if (e.target.checked) next.add(s);
              else next.delete(s);
            }
            return next;
          })
        }
      />
    </th>
  );
}

export function TickCell({ ticks, symbol }: { ticks: Ticks; symbol: string }) {
  const [news, setNews] = useState(false);
  return (
    <td className="whitespace-nowrap py-1.5">
      <input
        type="checkbox"
        aria-label={`Tick ${symbol}`}
        className="accent-emerald-400"
        checked={ticks.ticked.has(symbol)}
        onChange={(e) => ticks.toggle(symbol, e.target.checked)}
      />
      <button
        type="button"
        onClick={() => setNews(true)}
        aria-label={`${symbol} news`}
        title={`${symbol} news`}
        className="ml-1 rounded p-0.5 align-middle text-slate-500 hover:bg-white/5 hover:text-amber-300"
      >
        <Newspaper size={12} />
      </button>
      {news ? <NewsModal symbol={symbol} onClose={() => setNews(false)} /> : null}
    </td>
  );
}

/** One stock's news (the chart's News panel) over the page. */
export function NewsModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={`${symbol} news`} className="mt-10 w-full max-w-3xl overflow-hidden rounded-xl border border-border shadow-xl">
        <StockNewsPanel symbol={symbol} onClose={onClose} />
      </div>
    </div>,
    document.body
  );
}
