"use client";

import clsx from "clsx";
import { inr, px, type SmaState } from "@/lib/smaApi";
import { SideBadge, pnlTone } from "./ui";

type Book = NonNullable<SmaState["books"]>[number];

function openOf(b: Book): number | null {
  return b.open_gross ?? b.open_net ?? null;
}

/** Every stock the desk holds now, with its side, size, entry, last price and open P&L (before charges). */
export function OpenPositionsCard({
  state,
  closing,
  onClose,
}: {
  state: SmaState | null;
  /** The stock whose close is in flight, if any. */
  closing?: string | null;
  onClose?: (symbol: string, direction: "LONG" | "SHORT", qty: number) => void;
}) {
  const held = (state?.books ?? []).filter((b): b is Book & { direction: "LONG" | "SHORT" } => b.direction === "LONG" || b.direction === "SHORT");
  const total = state?.open_gross_total ?? state?.open_net_total ?? held.reduce((sum, b) => sum + (openOf(b) ?? 0), 0);
  const value = held.reduce((sum, b) => sum + (b.ltp ?? b.entry_price ?? 0) * b.qty, 0);
  return (
    <section aria-label="Open positions" className="rounded-xl border border-white/10 bg-[#151921] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Open positions · {held.length} held{held.length ? ` · ${inr(value)} in the market` : ""}
        </h3>
        <span className={clsx("font-mono text-sm font-semibold", pnlTone(total))} title="Open P&L of every held stock, before charges">
          Open {total > 0 ? "+" : ""}
          {inr(total)}
        </span>
      </div>
      {held.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">Nothing held right now.</p>
      ) : (
        <table className="mt-2 w-full text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="pb-1 font-medium">Stock</th>
              <th className="pb-1 font-medium">Side</th>
              <th className="pb-1 text-right font-medium">Qty</th>
              <th className="pb-1 text-right font-medium">Entry</th>
              <th className="pb-1 text-right font-medium">Last</th>
              <th className="pb-1 text-right font-medium">Open P&amp;L</th>
              {onClose ? <th className="pb-1" /> : null}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {held.map((b) => {
              const open = openOf(b);
              return (
                <tr key={b.symbol}>
                  <td className="py-1.5 font-semibold text-amber-300">{b.symbol}</td>
                  <td className="py-1.5">
                    <SideBadge side={b.direction} />
                  </td>
                  <td className="py-1.5 text-right font-mono text-slate-200">{b.qty}</td>
                  <td className="py-1.5 text-right font-mono text-slate-300">{b.entry_price != null ? px(b.entry_price) : "—"}</td>
                  <td className="py-1.5 text-right font-mono text-slate-300">{b.ltp != null ? px(b.ltp) : "—"}</td>
                  <td className={clsx("py-1.5 text-right font-mono font-semibold", open != null ? pnlTone(open) : "text-slate-400")}>
                    {open != null ? `${open > 0 ? "+" : ""}${inr(open)}` : "—"}
                  </td>
                  {onClose ? (
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        disabled={closing === b.symbol}
                        onClick={() => onClose(b.symbol, b.direction, b.qty)}
                        className="rounded px-2 py-0.5 text-[11px] font-semibold text-rose-300 ring-1 ring-inset ring-rose-500/50 hover:bg-rose-500/15 disabled:opacity-50"
                      >
                        {closing === b.symbol ? "Closing…" : "Close"}
                      </button>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
