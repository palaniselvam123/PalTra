"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import type { SecondCandleRow } from "@/lib/smaApi";
import { fmt, levelMin, mean, nearestLevel, outcome, reverseOutcome, signed } from "./secondCandleMath";

/**
 * The day's trades added up (the Second candle tab's "Per day" view; display only, from the filtered rows): with Top N
 * by volume picked above, each day's N trades together, so the day's consolidated result shows. Each way of exiting is
 * its own column (held to the square-off, the target and stop chosen above, the same plus the reverse after the stop, the
 * trailing stop picked); the chosen one drives the running total and the summary. Money: shares per trade, or ₹ per
 * trade (shares = ₹ ÷ the entry price, whole shares). Before charges.
 */
type Basis = "close" | "ts" | "rev" | "trail";
type Size = "SHARES" | "RUPEES";

const BASIS_LABEL: Record<Basis, string> = {
  close: "Held to the square-off",
  ts: "Target and stop",
  rev: "Target and stop + reverse",
  trail: "Trailing stop",
};

export function DayTotals({
  rows,
  target,
  stop,
  trailPct,
}: {
  rows: SecondCandleRow[];
  target: number;
  stop: number;
  trailPct: number | null;
}) {
  const [basis, setBasis] = useState<Basis>("ts");
  const [size, setSize] = useState<Size>("SHARES");
  const [amount, setAmount] = useState(1);
  const trailKey = trailPct == null ? null : Object.keys(rows[0]?.trail ?? {}).find((k) => Number(k) === trailPct) ?? null;

  const days = useMemo(() => {
    const a = Math.max(0, Number(amount) || 0);
    const qtyOf = (r: SecondCandleRow) => (size === "SHARES" ? a : r.entry > 0 ? Math.floor(a / r.entry) : 0);
    const by = new Map<string, SecondCandleRow[]>();
    for (const r of rows) by.set(r.day, [...(by.get(r.day) ?? []), r]);
    let running = 0;
    return Array.from(by.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, part]) => {
        let close = 0;
        let ts = 0;
        let rev = 0;
        let trail = 0;
        let capital = 0;
        let wins = 0;
        let reached = 0;
        let targetFirst = 0;
        for (const r of part) {
          const q = qtyOf(r);
          const o = outcome(r, target, stop);
          const t = o.pts;
          if (levelMin(r.fav_min, nearestLevel(target)) != null) reached += 1;
          if (o.kind === "target") targetFirst += 1;
          const rv = reverseOutcome(r, target, stop)?.pts ?? 0;
          const tr = trailKey ? r.trail?.[trailKey] ?? 0 : 0;
          close += r.close_pts * q;
          ts += t * q;
          rev += (t + rv) * q;
          trail += tr * q;
          capital += r.entry * q;
          const pick = basis === "close" ? r.close_pts : basis === "ts" ? t : basis === "rev" ? t + rv : tr;
          if (pick > 0) wins += 1;
        }
        const pick = basis === "close" ? close : basis === "ts" ? ts : basis === "rev" ? rev : trail;
        running += pick;
        return {
          day,
          trades: part.length,
          stocks: part.map((r) => r.symbol).join(", "),
          wins,
          reached,
          targetFirst,
          close,
          ts,
          rev,
          trail,
          capital,
          pick,
          running,
        };
      });
  }, [rows, target, stop, trailKey, basis, size, amount]);

  const summary = useMemo(() => {
    const picks = days.map((d) => d.pick);
    const green = picks.filter((v) => v > 0).length;
    const best = days.length ? days.reduce((a, b) => (b.pick > a.pick ? b : a)) : null;
    const worst = days.length ? days.reduce((a, b) => (b.pick < a.pick ? b : a)) : null;
    let peak = 0;
    let drop = 0;
    for (const d of days) {
      peak = Math.max(peak, d.running);
      drop = Math.max(drop, peak - d.running);
    }
    return {
      total: picks.reduce((a, b) => a + b, 0),
      avg: mean(picks),
      green,
      red: picks.filter((v) => v < 0).length,
      best,
      worst,
      drop,
      maxCapital: days.length ? Math.max(...days.map((d) => d.capital)) : 0,
    };
  }, [days]);

  const hitDays = useMemo(() => {
    const most = days.length ? Math.max(...days.map((d) => d.trades)) : 0;
    return Array.from({ length: most + 1 }, (_, k) => ({
      k,
      reached: days.filter((d) => d.reached === k).length,
      reachedAtLeast: days.filter((d) => d.reached >= k).length,
      first: days.filter((d) => d.targetFirst === k).length,
      firstAtLeast: days.filter((d) => d.targetFirst >= k).length,
    })).reverse();
  }, [days]);

  if (!rows.length) return <p className="p-3 text-xs text-slate-500">No trades match these filters.</p>;
  const money = !(size === "SHARES" && Number(amount) === 1);
  const unit = money ? "₹" : "₹ a share";
  const cell = "px-2 py-1.5";
  const head = "px-2 py-1.5 text-left font-semibold uppercase tracking-wide text-slate-500";
  const tone = (v: number) => (v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-400");
  const select = "min-h-8 rounded-md border border-white/15 bg-black/30 px-2 text-xs text-slate-100";

  return (
    <div className="space-y-3 rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs">
      <div className="flex flex-wrap items-end gap-3">
        <label className="block text-slate-400">
          Exit by
          <select value={basis} onChange={(e) => setBasis(e.target.value as Basis)} className={clsx(select, "mt-1 block")}>
            <option value="close">{BASIS_LABEL.close}</option>
            <option value="ts">
              {BASIS_LABEL.ts} (₹{target} / ₹{stop})
            </option>
            <option value="rev">{BASIS_LABEL.rev}</option>
            <option value="trail" disabled={!trailKey}>
              {BASIS_LABEL.trail}
              {trailPct != null ? ` (${trailPct}%)` : ""}
            </option>
          </select>
        </label>
        <label className="block text-slate-400">
          Size each trade by
          <select value={size} onChange={(e) => setSize(e.target.value as Size)} className={clsx(select, "mt-1 block")}>
            <option value="SHARES">Shares per trade</option>
            <option value="RUPEES">₹ per trade (shares = ₹ ÷ price)</option>
          </select>
        </label>
        <label className="block text-slate-400">
          {size === "SHARES" ? "Shares" : "₹ per trade"}
          <input
            type="number"
            min={0}
            step={size === "SHARES" ? 1 : 1000}
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            className={clsx(select, "mt-1 block w-32")}
          />
        </label>
        <p className="max-w-md text-slate-500">
          Each day&apos;s trades added up: with Top N picked above, the N stocks of that day together. 1 share = ₹ a share. Before
          charges.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <Tile label={`All days (${unit})`} value={signed(summary.total)} tone={summary.total} />
        <Tile label="Average day" value={signed(summary.avg)} tone={summary.avg ?? 0} />
        <Tile label="Green / red days" value={`${summary.green} / ${summary.red} of ${days.length}`} tone={summary.green - summary.red} />
        <Tile label="Best day" value={summary.best ? `${signed(summary.best.pick)} · ${summary.best.day}` : "—"} tone={1} />
        <Tile label="Worst day" value={summary.worst ? `${signed(summary.worst.pick)} · ${summary.worst.day}` : "—"} tone={-1} />
        <Tile label="Biggest drop" value={`−${fmt(summary.drop)}`} tone={summary.drop ? -1 : 0} />
        <Tile label="Most capital in a day" value={money ? `₹${fmt(summary.maxCapital, 0)}` : "—"} />
      </div>

      <div className="rounded-lg border border-slate-800 p-2">
        <div className="mb-1 font-semibold text-slate-200">
          Days by how many stocks reached the target (₹{target}, stop ₹{stop})
        </div>
        <p className="mb-2 text-slate-500">
          Of the {days.length} days: on how many days 0, 1, 2… of that day&apos;s stocks reached ₹{target} with the trade, and on how
          many they reached it before the ₹{stop} stop. &quot;At least&quot; adds the rows above.
        </p>
        <table className="text-xs">
          <thead>
            <tr>
              <th className={head}>Stocks that day</th>
              <th className={clsx(head, "text-right")}>Reached ₹{target}</th>
              <th className={clsx(head, "text-right")}>At least</th>
              <th className={clsx(head, "text-right")}>Before the stop</th>
              <th className={clsx(head, "text-right")}>At least</th>
            </tr>
          </thead>
          <tbody>
            {hitDays.map((h) => (
              <tr key={h.k} className="border-t border-white/5">
                <td className={cell}>{h.k}</td>
                <td className={clsx(cell, "text-right")}>
                  {h.reached} <span className="text-slate-500">({fmt(days.length ? (h.reached / days.length) * 100 : 0, 0)}%)</span>
                </td>
                <td className={clsx(cell, "text-right text-slate-300")}>
                  {h.reachedAtLeast} <span className="text-slate-500">({fmt(days.length ? (h.reachedAtLeast / days.length) * 100 : 0, 0)}%)</span>
                </td>
                <td className={clsx(cell, "text-right")}>
                  {h.first} <span className="text-slate-500">({fmt(days.length ? (h.first / days.length) * 100 : 0, 0)}%)</span>
                </td>
                <td className={clsx(cell, "text-right text-slate-300")}>
                  {h.firstAtLeast} <span className="text-slate-500">({fmt(days.length ? (h.firstAtLeast / days.length) * 100 : 0, 0)}%)</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="max-h-[32rem] overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-surface">
            <tr>
              <th className={head}>Day</th>
              <th className={clsx(head, "text-right")}>Trades</th>
              <th className={clsx(head, "text-right")} title={`Stocks that day whose price went at least ₹${target} with the trade at any time`}>
                Reached ₹{target}
              </th>
              <th className={clsx(head, "text-right")} title={`Stocks that day that reached ₹${target} before the ₹${stop} stop`}>
                Target before stop
              </th>
              <th className={clsx(head, "text-right")}>In profit</th>
              <th className={clsx(head, "text-right")}>Square-off</th>
              <th className={clsx(head, "text-right")}>
                T ₹{target} / S ₹{stop}
              </th>
              <th className={clsx(head, "text-right")}>T/S + reverse</th>
              <th className={clsx(head, "text-right")}>{trailPct != null ? `Trail ${trailPct}%` : "Trail"}</th>
              <th className={clsx(head, "text-right")}>Running total</th>
              <th className={head}>Stocks</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.day} className="border-t border-white/5">
                <td className={clsx(cell, "text-slate-300")}>{d.day}</td>
                <td className={clsx(cell, "text-right")}>{d.trades}</td>
                <td className={clsx(cell, "text-right", d.reached === d.trades ? "font-semibold text-emerald-300" : d.reached === 0 ? "text-rose-300" : "")}>
                  {d.reached} of {d.trades}
                </td>
                <td className={clsx(cell, "text-right", d.targetFirst === d.trades ? "font-semibold text-emerald-300" : d.targetFirst === 0 ? "text-rose-300" : "")}>
                  {d.targetFirst} of {d.trades}
                </td>
                <td className={clsx(cell, "text-right")}>
                  {d.wins} <span className="text-slate-500">({fmt((d.wins / d.trades) * 100, 0)}%)</span>
                </td>
                {(["close", "ts", "rev", "trail"] as const).map((k) => (
                  <td key={k} className={clsx(cell, "text-right", tone(d[k]), basis === k && "font-semibold")}>
                    {k === "trail" && !trailKey ? "—" : signed(d[k])}
                  </td>
                ))}
                <td className={clsx(cell, "text-right font-semibold", tone(d.running))}>{signed(d.running)}</td>
                <td className={clsx(cell, "max-w-[18rem] truncate text-slate-400")} title={d.stocks}>
                  {d.stocks}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Tile({ label, value, tone = 0 }: { label: string; value: string; tone?: number }) {
  return (
    <div className="rounded-md border border-white/10 bg-black/20 px-2 py-1.5">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={clsx("font-semibold tabular-nums", tone > 0 ? "text-emerald-300" : tone < 0 ? "text-rose-300" : "text-slate-200")}>{value}</div>
    </div>
  );
}
