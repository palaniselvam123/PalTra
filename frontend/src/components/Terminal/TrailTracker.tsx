"use client";

import clsx from "clsx";
import { inr, px, type SmaState } from "@/lib/smaApi";
import { SideBadge, pnlTone } from "./ui";

type Book = NonNullable<SmaState["books"]>[number];
type Held = Book & { direction: "LONG" | "SHORT"; entry_price: number };

export type TrailRead = {
  /** +1 for a long, -1 for a short: multiply a price move by it to read it as "your way". */
  sign: 1 | -1;
  last: number;
  best: number;
  stop: number | null;
  /** ₹ a share between the last price and the stop (how much room before it is hit). */
  room: number | null;
  roomPct: number | null;
  /** P&L (before charges) if the stop filled now; ≥ 0 means the stop has locked a profit. */
  locked: number | null;
  /** The price at which the trailing stop moves next, and where it moves to. */
  nextAt: number | null;
  nextStop: number | null;
  status: "NO_STOP" | "AT_RISK" | "BREAKEVEN" | "LOCKED";
};

/** Where a held trade stands against its stop (display only; the bot's own trailing stop does the work). */
export function trailRead(b: Held): TrailRead {
  const sign = b.direction === "LONG" ? 1 : -1;
  const entry = b.entry_price;
  const last = b.ltp ?? entry;
  const seen = b.best ?? last;
  const best = sign === 1 ? Math.max(seen, last, entry) : Math.min(seen, last, entry);
  const stop = b.stop_active === false ? null : b.sl_trigger ?? null;
  const room = stop == null ? null : (last - stop) * sign;
  const roomPct = room == null || !last ? null : (room / last) * 100;
  const locked = stop == null ? null : (stop - entry) * sign * b.qty;
  let nextAt: number | null = null;
  let nextStop: number | null = null;
  const step = b.tsl_step ?? 0;
  if (b.trailing && step > 0 && stop != null) {
    const steps = Math.floor(((best - entry) * sign) / step + 1e-9);
    nextAt = entry + sign * step * (Math.max(steps, 0) + 1);
    nextStop = stop + sign * step;
  }
  // Within half a tick-ish (0.01 % of the entry) of the entry counts as the entry.
  const gap = stop == null ? null : (stop - entry) * sign;
  const near = Math.abs(entry) * 0.0001;
  const status: TrailRead["status"] = gap == null ? "NO_STOP" : gap > near ? "LOCKED" : gap >= -near ? "BREAKEVEN" : "AT_RISK";
  return { sign: sign as 1 | -1, last, best, stop, room, roomPct, locked, nextAt, nextStop, status };
}

const CHIP: Record<TrailRead["status"], { text: string; cls: string; title: string }> = {
  NO_STOP: { text: "No stop", cls: "bg-rose-500/20 text-rose-200", title: "This trade has no stop: only the exits and the square-off close it" },
  AT_RISK: { text: "At risk", cls: "bg-amber-500/20 text-amber-200", title: "The stop is still on the losing side of the entry" },
  BREAKEVEN: { text: "No loss", cls: "bg-sky-500/20 text-sky-200", title: "The stop sits at the entry: a turn now costs only the charges" },
  LOCKED: { text: "Profit locked", cls: "bg-emerald-500/20 text-emerald-200", title: "The stop is past the entry: a turn now still closes in profit" },
};

/**
 * Every held stock against its stop: entry, last, best price since the entry, the stop now, the room left to it, what
 * the stop would lock if it filled now, and where the trailing stop moves next. Display only (the bot moves the stop).
 */
export function TrailTracker({ state }: { state: SmaState | null }) {
  const held = (state?.books ?? []).filter((b): b is Held => (b.direction === "LONG" || b.direction === "SHORT") && b.entry_price != null);
  const reads = held.map((b) => ({ b, r: trailRead(b) }));
  const lockedTotal = reads.reduce((sum, { r }) => sum + (r.locked ?? 0), 0);
  const counts = reads.reduce<Record<string, number>>((acc, { r }) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {});
  return (
    <section aria-label="Trail tracker" className="rounded-xl border border-white/10 bg-[#151921] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Trail tracker · {held.length} held
          {held.length
            ? ` · ${counts.LOCKED ?? 0} profit locked · ${counts.BREAKEVEN ?? 0} no loss · ${(counts.AT_RISK ?? 0) + (counts.NO_STOP ?? 0)} at risk`
            : ""}
        </h3>
        {held.length ? (
          <span className={clsx("font-mono text-sm font-semibold", pnlTone(lockedTotal))} title="P&L (before charges) if every stop filled now">
            If all stops hit {lockedTotal > 0 ? "+" : ""}
            {inr(lockedTotal)}
          </span>
        ) : null}
      </div>
      {held.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">Nothing held right now. Each trade the bot opens shows here with its stop.</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="pb-1 font-medium">Stock</th>
                <th className="pb-1 text-right font-medium">Entry</th>
                <th className="pb-1 text-right font-medium">Last</th>
                <th className="pb-1 text-right font-medium" title="Best price since the entry">Best</th>
                <th className="pb-1 text-right font-medium">Stop</th>
                <th className="pb-1 text-right font-medium" title="How far the price can still move against you before the stop is hit">Room</th>
                <th className="pb-1 text-right font-medium" title="P&L before charges if the stop filled now">If stop hits</th>
                <th className="pb-1 text-right font-medium" title="The price at which the trailing stop moves next, and where it moves to">Next trail</th>
                <th className="pb-1 text-right font-medium">Target</th>
                <th className="pb-1 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {reads.map(({ b, r }) => {
                const chip = CHIP[r.status];
                return (
                  <tr key={b.symbol}>
                    <td className="py-1.5 pr-2">
                      <span className="mr-1.5 font-semibold text-slate-200">{b.symbol}</span>
                      <SideBadge side={b.direction} />
                    </td>
                    <td className="py-1.5 pr-2 text-right font-mono text-slate-300">{px(b.entry_price)}</td>
                    <td className="py-1.5 pr-2 text-right font-mono text-slate-200">{px(r.last)}</td>
                    <td className="py-1.5 pr-2 text-right font-mono text-slate-300">{px(r.best)}</td>
                    <td className="py-1.5 pr-2 text-right font-mono text-rose-200">{r.stop == null ? "—" : px(r.stop)}</td>
                    <td className={clsx("py-1.5 pr-2 text-right font-mono", r.room != null && r.room <= 0 ? "text-rose-300" : "text-slate-300")}>
                      {r.room == null ? "—" : `${px(r.room)} (${r.roomPct?.toFixed(2)}%)`}
                    </td>
                    <td className={clsx("py-1.5 pr-2 text-right font-mono", pnlTone(r.locked ?? 0))}>
                      {r.locked == null ? "—" : `${r.locked > 0 ? "+" : ""}${inr(r.locked)}`}
                    </td>
                    <td className="py-1.5 pr-2 text-right font-mono text-amber-200">
                      {r.nextAt == null ? (b.trailing ? "moves with its line" : "fixed") : `at ${px(r.nextAt)} → ${px(r.nextStop)}`}
                    </td>
                    <td className="py-1.5 pr-2 text-right font-mono text-emerald-200">{b.target ? px(b.target) : "none"}</td>
                    <td className="py-1.5">
                      <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", chip.cls)} title={chip.title}>
                        {chip.text}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
