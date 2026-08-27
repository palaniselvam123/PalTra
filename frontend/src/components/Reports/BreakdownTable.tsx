"use client";

import clsx from "clsx";
import type { Breakdown } from "@/lib/api";
import { money, num, pct, pnlClass } from "@/lib/format";

/** One P&L breakdown (by symbol, side, source, exit reason, …). The bar is
 *  scaled to the largest absolute P&L in the group so the biggest contributor
 *  is obvious without reading every number. */
export function BreakdownTable({
  title,
  rows,
  keyLabel,
  empty = "No closed trades in this filter.",
}: {
  title: string;
  rows: Breakdown[];
  keyLabel: string;
  empty?: string;
}) {
  const peak = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl)));

  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border text-sm font-medium text-slate-200">{title}</div>
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-xs text-slate-500">{empty}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] text-slate-500 border-b border-border">
                <th className="px-3 py-2 font-medium">{keyLabel}</th>
                <th className="px-3 py-2 font-medium text-right">Trades</th>
                <th className="px-3 py-2 font-medium text-right">Win %</th>
                <th className="px-3 py-2 font-medium text-right">Avg R</th>
                <th className="px-3 py-2 font-medium text-right">Avg P&amp;L</th>
                <th className="px-3 py-2 font-medium text-right">Net P&amp;L</th>
                <th className="px-3 py-2 font-medium w-24" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-b border-border/50 last:border-0">
                  <td className="px-3 py-1.5 text-slate-200">{r.key}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-right text-slate-400">
                    {r.trades}
                    <span className="text-slate-600">
                      {" "}
                      ({r.wins}/{r.losses})
                    </span>
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs text-right text-slate-300">{pct(r.win_rate_pct, 0)}</td>
                  <td className={clsx("px-3 py-1.5 font-mono text-xs text-right", pnlClass(r.avg_r))}>
                    {r.avg_r !== null ? `${num(r.avg_r)}R` : "—"}
                  </td>
                  <td className={clsx("px-3 py-1.5 font-mono text-xs text-right", pnlClass(r.avg_pnl))}>
                    {money(r.avg_pnl, true)}
                  </td>
                  <td className={clsx("px-3 py-1.5 font-mono text-right", pnlClass(r.net_pnl))}>
                    {money(r.net_pnl, true)}
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="h-1.5 w-full bg-border/60 rounded-full overflow-hidden flex">
                      <div className="w-1/2 flex justify-end">
                        {r.net_pnl < 0 && (
                          <div
                            className="h-full bg-loss rounded-l-full"
                            style={{ width: `${(Math.abs(r.net_pnl) / peak) * 100}%` }}
                          />
                        )}
                      </div>
                      <div className="w-1/2">
                        {r.net_pnl > 0 && (
                          <div
                            className="h-full bg-profit rounded-r-full"
                            style={{ width: `${(r.net_pnl / peak) * 100}%` }}
                          />
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
