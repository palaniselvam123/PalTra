"use client";

import type { FastLogRow } from "@/lib/smaApi";

/**
 * Bot 5's tab while it follows a Bot 1-4 replay (replay.bot5_view): the stocks that bot handed over as fast
 * movers, when and why. The positions, trades and P&L above are Bot 5's part of that run only.
 */
export function FastFollow({ source, log }: { source: number; log: FastLogRow[] }) {
  return (
    <div role="note" className="space-y-2 rounded-xl border border-violet-400/40 bg-violet-500/[0.08] px-3 py-2 text-sm text-violet-100">
      <p>
        <span className="font-semibold">Following Bot {source}&apos;s replay.</span> Bot 5 shows only the stocks Bot {source} handed
        over as fast movers (entry reason FAST_MOVE), with Bot 5&apos;s quantity, stop and exits, and their P&amp;L. Pause, speed and
        Stop here control Bot {source}&apos;s run. Practice money; nothing is ordered.
      </p>
      {log.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1 pr-3 font-medium">Time</th>
                <th className="py-1 pr-3 font-medium">Stock</th>
                <th className="py-1 pr-3 font-medium">Side</th>
                <th className="py-1 pr-3 font-medium">Why fast</th>
                <th className="py-1 font-medium">What happened</th>
              </tr>
            </thead>
            <tbody>
              {[...log].reverse().map((row, i) => (
                <tr key={`${row.at}-${row.symbol}-${i}`} className="border-t border-white/5">
                  <td className="py-1 pr-3 font-mono">{row.at}</td>
                  <td className="py-1 pr-3 font-semibold">{row.symbol}</td>
                  <td className={row.side === "LONG" ? "py-1 pr-3 text-emerald-300" : "py-1 pr-3 text-rose-300"}>{row.side}</td>
                  <td className="py-1 pr-3 text-slate-300">{row.why}</td>
                  <td className={row.moved ? "py-1 text-emerald-200" : "py-1 text-amber-200"}>
                    {row.moved ? `Bot ${row.from_bot} → Bot 5: ` : "Not moved: "}
                    {row.result}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-slate-400">No fast move yet on this replayed day.</p>
      )}
    </div>
  );
}
