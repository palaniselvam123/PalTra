"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2, RefreshCw } from "lucide-react";
import { api, type PressureAfter, type PressureTest } from "@/lib/api";
import { PressureDayView } from "@/components/Scalp/PressureDay";

/**
 * Buyers / sellers test on the Most active card (backend/pressure_study.py): each time buyers or sellers reached the
 * chosen share of the waiting orders in a saved scan, what the real price did afterwards, against what any stock does
 * anyway. Read-only: saved scans only, no Groww call, no order.
 */
const VERDICT: Record<PressureAfter["verdict"], { text: string; tone: string }> = {
  strong: { text: "Strong sign", tone: "bg-emerald-500/15 text-emerald-300" },
  some: { text: "Useful sign", tone: "bg-emerald-500/10 text-emerald-300" },
  weak: { text: "Barely better than chance", tone: "bg-amber-500/15 text-amber-300" },
  none: { text: "No better than chance", tone: "bg-rose-500/15 text-rose-300" },
  few: { text: "Too few signals", tone: "bg-slate-500/20 text-slate-300" },
};

const pct = (v: number | null | undefined, sign = false) => (v == null ? "—" : `${sign && v > 0 ? "+" : ""}${v.toFixed(sign ? 2 : 0)}%`);
const after = (key: string) => (key === "close" ? "At the close" : `${key} min later`);

export function PressureTestView() {
  const [days, setDays] = useState(20);
  const [share, setShare] = useState(75);
  const [gap, setGap] = useState(30);
  const [data, setData] = useState<PressureTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.scalpPressureTest({ days, pct: share, gap_min: gap }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not run the test");
    } finally {
      setBusy(false);
    }
  }, [days, share, gap]);

  useEffect(() => {
    void load();
    // Run once on open; afterwards only when Run the test is pressed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = "h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-200";
  const th = "px-2 py-1.5 text-right font-medium";
  const td = "px-2 py-1.5 text-right tabular-nums";

  return (
    <div className="space-y-3">
      <PressureDayView />
      <div className="rounded-lg border border-sky-400/25 bg-sky-400/[0.05] p-3 text-xs leading-relaxed text-slate-300">
        <p className="text-sm font-semibold text-slate-100">Many days at once: does the price follow the buyers or sellers?</p>
        <p className="mt-1">
          Your idea: when buyers hold {share}% or more of the waiting orders the price goes up, and when sellers do it goes down. This
          checks it on the saved Most active scans. Each time a stock <b>newly</b> reached that share, the real traded price is read
          again 5, 15, 30 and 60 minutes later and at the close.
        </p>
        <p className="mt-1">
          <b>How to read it:</b> &quot;Right&quot; = the price went the way the idea says. &quot;Any stock&quot; = how often prices went that
          way anyway. Only the gap between the two (the <b>edge</b>) is worth trading on: 55% right is useless if any stock does 52%.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Buyers / sellers at least
          <span className="flex items-center gap-1">
            <input type="number" min={55} max={95} step={1} value={share} onChange={(e) => setShare(Math.min(95, Math.max(55, Number(e.target.value) || 75)))} className={clsx(select, "w-20")} />%
          </span>
        </label>
        <label className="flex flex-col gap-1">
          Days of saved scans
          <select className={select} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[5, 10, 20, 40, 60].map((v) => (
              <option key={v} value={v}>
                Last {v}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1" title="The same stock and side counts again only after this many minutes, so one long one-sided stretch is one signal.">
          Same stock again after
          <select className={select} value={gap} onChange={(e) => setGap(Number(e.target.value))}>
            {[15, 30, 60, 120].map((v) => (
              <option key={v} value={v}>
                {v} min
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/20 px-3 text-xs font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/50 hover:bg-sky-500/30 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Run the test
        </button>
        {data ? (
          <span className="text-slate-500">
            {data.days.length} day{data.days.length === 1 ? "" : "s"} · {data.days.reduce((a, d) => a + d.scans, 0)} saved scans · {data.total_signals} signals
          </span>
        ) : null}
      </div>
      {error ? <p className="text-xs font-semibold text-amber-300">{error}</p> : null}
      {!data ? (
        <p className="text-xs text-slate-500">{busy ? "Reading the saved scans…" : ""}</p>
      ) : data.days.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-400">
          No saved Most active scans yet. Keep the card&apos;s &quot;Auto scan&quot; on during market hours; after a day or two there is
          something to test.
        </p>
      ) : (
        <>
          <div className="rounded-lg border border-emerald-400/30 bg-emerald-400/[0.04] p-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-300">The answer</p>
            <ul className="mt-1 space-y-1 text-sm leading-relaxed text-slate-100">
              {data.answer.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {data.sides.map((s) => (
              <div key={s.side} className="rounded-lg border border-slate-800 p-3">
                <p className="text-sm font-semibold text-slate-100">
                  {s.side === "BUY" ? "🟢 Buyers" : "🔴 Sellers"} at {data.pct}% or more
                  <span className="ml-2 text-xs font-normal text-slate-400">
                    {s.signals} signals · expected: price {s.side === "BUY" ? "up" : "down"}
                  </span>
                </p>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[420px] text-xs">
                    <thead className="text-slate-400">
                      <tr>
                        <th className="px-2 py-1.5 text-left font-medium">When</th>
                        <th className={th}>Signals</th>
                        <th className={th} title="How often the price went the way the idea says">Right</th>
                        <th className={th} title="How often any stock went that way over the same time">Any stock</th>
                        <th className={th} title="Right minus Any stock, in points">Edge</th>
                        <th className={th} title="Average move the idea's way">Avg move</th>
                        <th className="px-2 py-1.5 text-left font-medium">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.after.map((r) => (
                        <tr key={r.after} className="border-t border-slate-800/70 text-slate-200">
                          <td className="px-2 py-1.5 text-left">{after(r.after)}</td>
                          <td className={td}>{r.signals}</td>
                          <td className={td}>{pct(r.right_pct)}</td>
                          <td className={clsx(td, "text-slate-400")}>{pct(r.base_right_pct)}</td>
                          <td className={clsx(td, (r.edge ?? 0) > 0 ? "text-emerald-300" : (r.edge ?? 0) < 0 ? "text-rose-300" : "")}>
                            {r.edge == null ? "—" : `${r.edge > 0 ? "+" : ""}${r.edge.toFixed(1)}`}
                          </td>
                          <td className={clsx(td, (r.avg_pct ?? 0) > 0 ? "text-emerald-300" : (r.avg_pct ?? 0) < 0 ? "text-rose-300" : "")}>{pct(r.avg_pct, true)}</td>
                          <td className="px-2 py-1.5 text-left">
                            <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", VERDICT[r.verdict].tone)}>{VERDICT[r.verdict].text}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs text-slate-400">
                  Within the next hour, on average: best {pct(s.avg_best_pct, true)} its way, worst {pct(s.avg_worst_pct, true)} (the dip a
                  stop would have to survive).
                </p>
              </div>
            ))}
          </div>
          <div className="rounded-lg border border-slate-800 p-3">
            <p className="text-sm font-semibold text-slate-100">Which share works best? (30 minutes later)</p>
            <p className="text-xs text-slate-400">The same test at every level, so you can see whether 75% is the right number.</p>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[520px] text-xs">
                <thead className="text-slate-400">
                  <tr>
                    <th className="px-2 py-1.5 text-left font-medium">Share at least</th>
                    {(["BUY", "SELL"] as const).map((side) => (
                      <th key={side} colSpan={3} className="px-2 py-1.5 text-center font-medium">
                        {side === "BUY" ? "Buyers → up" : "Sellers → down"}
                      </th>
                    ))}
                  </tr>
                  <tr>
                    <th />
                    {[0, 1].map((i) => (
                      <FragmentCols key={i} th={th} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from(new Set(data.by_threshold.map((r) => r.pct))).map((level) => (
                    <tr key={level} className={clsx("border-t border-slate-800/70 text-slate-200", level === data.pct && "bg-sky-400/[0.06]")}>
                      <td className="px-2 py-1.5 text-left font-semibold">{level}%</td>
                      {(["BUY", "SELL"] as const).map((side) => {
                        const r = data.by_threshold.find((x) => x.pct === level && x.side === side);
                        return (
                          <FragmentCells key={side} td={td} signals={r?.signals ?? 0} right={r?.right_pct ?? null} edge={r?.edge ?? null} />
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {data.signals.length ? (
            <details className="rounded-lg border border-slate-800 p-3">
              <summary className="cursor-pointer text-sm font-semibold text-slate-100">
                The signals themselves ({data.signals.length}
                {data.total_signals > data.signals.length ? ` of ${data.total_signals}, newest first` : ""})
              </summary>
              <div className="mt-2 max-h-96 overflow-auto">
                <table className="w-full min-w-[640px] text-xs">
                  <thead className="sticky top-0 bg-card text-slate-400">
                    <tr>
                      <th className="px-2 py-1.5 text-left font-medium">Day</th>
                      <th className="px-2 py-1.5 text-left font-medium">Time</th>
                      <th className="px-2 py-1.5 text-left font-medium">Stock</th>
                      <th className="px-2 py-1.5 text-left font-medium">Who</th>
                      <th className={th}>Price then</th>
                      {data.horizons.map((h) => (
                        <th key={h} className={th}>
                          {h} min
                        </th>
                      ))}
                      <th className={th}>Close</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.signals.map((s, i) => (
                      <tr key={`${s.day}-${s.time}-${s.symbol}-${s.side}-${i}`} className="border-t border-slate-800/70 text-slate-200">
                        <td className="px-2 py-1.5">{s.day}</td>
                        <td className="px-2 py-1.5 tabular-nums">{s.time}</td>
                        <td className="px-2 py-1.5 font-semibold">{s.symbol}</td>
                        <td className="px-2 py-1.5">
                          {s.side === "BUY" ? "Buyers" : "Sellers"} {s.share_pct.toFixed(0)}%
                        </td>
                        <td className={td}>₹{s.price.toFixed(2)}</td>
                        {[...data.horizons.map(String), "close"].map((k) => {
                          const v = s[k] as number | null;
                          return (
                            <td key={k} className={clsx(td, v == null ? "text-slate-500" : v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "")}>
                              {pct(v, true)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-slate-500">Moves are signed the idea&apos;s way: green = buyers were followed by a rise, or sellers by a fall.</p>
            </details>
          ) : null}
          <p className="text-xs leading-relaxed text-slate-500">
            What this is not: the share is of orders <b>waiting</b> in the order book at the moment of each scan, not of shares traded, and a
            scan is taken every few minutes, so a signal can be a few minutes old when it is seen. Prices are the real last traded prices in
            the saved scans, before charges. A scan within {data.tolerance_min} minutes of each time stands for it.
          </p>
        </>
      )}
    </div>
  );
}

function FragmentCols({ th }: { th: string }) {
  return (
    <>
      <th className={th}>Signals</th>
      <th className={th}>Right</th>
      <th className={th}>Edge</th>
    </>
  );
}

function FragmentCells({ td, signals, right, edge }: { td: string; signals: number; right: number | null; edge: number | null }) {
  return (
    <>
      <td className={td}>{signals}</td>
      <td className={td}>{pct(right)}</td>
      <td className={clsx(td, (edge ?? 0) > 0 ? "text-emerald-300" : (edge ?? 0) < 0 ? "text-rose-300" : "")}>
        {edge == null ? "—" : `${edge > 0 ? "+" : ""}${edge.toFixed(1)}`}
      </td>
    </>
  );
}
