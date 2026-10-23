"use client";

import clsx from "clsx";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { api, type OptionBook as Book, type OptionTrade } from "@/lib/api";
import { istDateTime, money, num, pnlClass } from "@/lib/format";

function contract(t: OptionTrade): string {
  return `${t.underlying} ${t.strike.toLocaleString("en-IN")} ${t.option_type} · ${t.expiry}`;
}

/** The practice options book: open positions marked to their own feed, and closed trades. */
export function OptionBook({ book, onChanged }: { book: Book | null; onChanged: () => void }) {
  const [closing, setClosing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  const exit = async (id: number) => {
    setClosing(id);
    setError(null);
    try {
      await api.optionClose(id);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClosing(null);
    }
  };

  const t = book?.totals;
  const cell = "px-3 py-2";
  return (
    <section aria-label="Practice options book" className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-100">Practice book</h2>
        <div className="flex flex-wrap gap-4 text-xs text-slate-400">
          <span>
            Open P&amp;L <b className={clsx("font-mono", pnlClass(t?.open_pnl))}>{money(t?.open_pnl ?? null, true)}</b>
          </span>
          <span>
            Closed today <b className={clsx("font-mono", pnlClass(t?.closed_today_pnl))}>{money(t?.closed_today_pnl ?? null, true)}</b>
            {t?.closed_today ? ` (${t.closed_today}, charges about ${money(t.closed_today_charges)})` : ""}
          </span>
        </div>
      </div>
      {error && (
        <p role="alert" className="mx-4 mt-3 rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
          {error}
        </p>
      )}
      {!book ? (
        <p className="px-4 py-6 text-sm text-slate-500">Loading…</p>
      ) : book.positions.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">
          No open practice positions. Click a price in the chain to buy or sell it.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className={cell}>Contract</th>
                <th className={cell}>Side</th>
                <th className={clsx(cell, "text-right")}>Qty</th>
                <th className={clsx(cell, "text-right")}>Entry</th>
                <th className={clsx(cell, "text-right")}>LTP</th>
                <th className={clsx(cell, "text-right")}>P&amp;L</th>
                <th className={cell}>Prices</th>
                <th className={cell} />
              </tr>
            </thead>
            <tbody className="font-mono tabular-nums text-slate-200">
              {book.positions.map((p) => (
                <tr key={p.id} className="border-t border-white/5">
                  <td className={clsx(cell, "font-sans")}>{contract(p)}</td>
                  <td className={clsx(cell, p.side === "BUY" ? "text-profit" : "text-loss")}>{p.side}</td>
                  <td className={clsx(cell, "text-right")}>
                    {p.quantity.toLocaleString("en-IN")}
                    <span className="block text-[10px] text-slate-500">
                      {p.lots} × {p.lot_size}
                    </span>
                  </td>
                  <td className={clsx(cell, "text-right")}>{num(p.entry_price)}</td>
                  <td className={clsx(cell, "text-right")}>{num(p.ltp ?? null)}</td>
                  <td className={clsx(cell, "text-right", pnlClass(p.pnl))}>{money(p.pnl, true)}</td>
                  <td className={clsx(cell, "font-sans text-slate-400")}>{p.feed === "groww" ? "Groww" : "Simulated"}</td>
                  <td className={clsx(cell, "text-right")}>
                    <button
                      type="button"
                      onClick={() => exit(p.id)}
                      disabled={closing === p.id}
                      className="inline-flex min-h-8 items-center gap-1 rounded-md px-3 font-sans text-xs font-semibold text-slate-100 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
                    >
                      {closing === p.id ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
                      Exit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {t?.open_unpriced ? (
            <p className="px-4 pb-3 text-xs text-amber-300">
              {t.open_unpriced} position(s) have no price right now (their feed is not available); P&amp;L leaves them out.
            </p>
          ) : null}
        </div>
      )}
      {book && book.closed.length > 0 && (
        <div className="border-t border-border">
          <button
            type="button"
            onClick={() => setShowClosed((v) => !v)}
            aria-expanded={showClosed}
            className="min-h-10 w-full px-4 text-left text-xs font-semibold text-slate-300 hover:bg-white/[0.03]"
          >
            {showClosed ? "Hide" : "Show"} closed trades ({book.closed.length})
          </button>
          {showClosed && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-xs">
                <thead className="text-left text-slate-400">
                  <tr>
                    <th className={cell}>Closed</th>
                    <th className={cell}>Contract</th>
                    <th className={cell}>Side</th>
                    <th className={clsx(cell, "text-right")}>Qty</th>
                    <th className={clsx(cell, "text-right")}>Entry</th>
                    <th className={clsx(cell, "text-right")}>Exit</th>
                    <th className={clsx(cell, "text-right")}>P&amp;L</th>
                    <th className={clsx(cell, "text-right")}>Charges</th>
                    <th className={cell}>Why</th>
                  </tr>
                </thead>
                <tbody className="font-mono tabular-nums text-slate-300">
                  {book.closed.map((c) => (
                    <tr key={c.id} className="border-t border-white/5">
                      <td className={clsx(cell, "font-sans")}>{istDateTime(c.closed_at)}</td>
                      <td className={clsx(cell, "font-sans")}>{contract(c)}</td>
                      <td className={clsx(cell, c.side === "BUY" ? "text-profit" : "text-loss")}>{c.side}</td>
                      <td className={clsx(cell, "text-right")}>{c.quantity.toLocaleString("en-IN")}</td>
                      <td className={clsx(cell, "text-right")}>{num(c.entry_price)}</td>
                      <td className={clsx(cell, "text-right")}>{num(c.exit_price)}</td>
                      <td className={clsx(cell, "text-right", pnlClass(c.pnl))}>{money(c.pnl, true)}</td>
                      <td className={clsx(cell, "text-right")}>{money(c.charges)}</td>
                      <td className={clsx(cell, "font-sans text-slate-400")}>
                        {c.exit_reason === "EXPIRED" ? "Expired" : "Exit"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
