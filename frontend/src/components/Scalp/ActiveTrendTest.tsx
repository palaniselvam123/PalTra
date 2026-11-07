"use client";

import clsx from "clsx";
import { FlaskConical, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { api, type ActiveStudy, type ActiveStudyParam } from "@/lib/api";

const DAYS = [5, 10, 20, 30, 60];
const TIMES = ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"];
const TOPS = [5, 10, 15, 20];
const SIDES = [
  { value: "BOTH", label: "Bullish and bearish" },
  { value: "LONG", label: "Bullish only" },
  { value: "SHORT", label: "Bearish only" },
] as const;
type Side = (typeof SIDES)[number]["value"];
type Key = "label" | "verdict" | "close_hit" | "edge_close" | "bottom_close" | "spread_close" | "hour_hit" | "edge_hour" | "close_avg" | "n";

const VERDICT: Record<ActiveStudyParam["verdict"], { text: string; cls: string; order: number }> = {
  strong: { text: "Strong", cls: "bg-emerald-500/15 text-emerald-300", order: 4 },
  some: { text: "Some", cls: "bg-sky-500/15 text-sky-300", order: 3 },
  weak: { text: "Weak", cls: "bg-slate-500/15 text-slate-300", order: 2 },
  none: { text: "No help", cls: "bg-rose-500/15 text-rose-300", order: 1 },
  few: { text: "Too few", cls: "bg-amber-500/15 text-amber-300", order: 0 },
};

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(0)}%`);
const pts = (v: number | null | undefined) => {
  if (v == null) return "—";
  const r = Math.round(v);
  return r === 0 ? "0" : `${r > 0 ? "+" : "−"}${Math.abs(r)}`;
};
const move = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-emerald-400" : "text-rose-400");

/**
 * Which Most active column finds the trend (backend active_study.py): from the saved Most active scans of past days, at
 * each check time, each column's strongest bullish / bearish stocks against all of them, an hour later and at the close.
 * Read-only: saved scans only.
 */
export function ActiveTrendTest() {
  const [days, setDays] = useState(20);
  const [times, setTimes] = useState<string[]>(["10:00", "11:00", "12:00", "13:00", "14:00"]);
  const [top, setTop] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [study, setStudy] = useState<ActiveStudy | null>(null);
  const [at, setAt] = useState("ALL");
  const [side, setSide] = useState<Side>("BOTH");

  const toggle = (t: string) => setTimes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t].sort()));

  const run = async () => {
    if (!times.length) {
      setError("Tick at least one time.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setStudy(await api.scalpActiveStudy({ days, times, top }));
      setAt("ALL");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not run the test");
    } finally {
      setBusy(false);
    }
  };

  const table = study?.tables.find((t) => t.time === at && t.side === side) ?? null;
  const { sorted, sort, onSort } = useSort<ActiveStudyParam, Key>(table?.params ?? [], (p, k) => {
    switch (k) {
      case "label":
        return p.label;
      case "verdict":
        return VERDICT[p.verdict].order;
      case "close_hit":
        return p.top.close_hit;
      case "edge_close":
        return p.edge_close;
      case "bottom_close":
        return p.bottom.close_hit;
      case "spread_close":
        return p.spread_close;
      case "hour_hit":
        return p.top.hour_hit;
      case "edge_hour":
        return p.edge_hour;
      case "close_avg":
        return p.top.close_avg;
      case "n":
        return p.top.close_n;
    }
  });

  // The best column at each time and side, for the summary grid.
  const grid = useMemo(() => {
    if (!study) return [];
    return ["ALL", ...study.times].map((t) => ({
      time: t,
      cells: SIDES.map((s) => {
        const tab = study.tables.find((x) => x.time === t && x.side === s.value);
        const best = tab?.params.find((p) => p.verdict !== "few" && p.edge_close != null && p.edge_close > 0) ?? null;
        return { side: s.value, best, base: tab?.base.close_hit ?? null };
      }),
    }));
  }, [study]);

  const select = "min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100";
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-surface2/60 p-3 text-xs text-slate-400">
        <p className="mb-2 max-w-4xl">
          Which column on this page finds a stock that keeps trending? For each past day with saved scans, the scan nearest
          each time you tick is read: stocks up on the day count as bullish, down as bearish. Within each side the stocks are
          ranked by every column, and the top ones are followed to see if they kept moving their way an hour later and at
          the close. The more a column&apos;s top beats &quot;all stocks&quot;, the better it finds the trend. Saved scans only:
          nothing is downloaded and no order is placed.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            Last
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
              {DAYS.map((d) => (
                <option key={d} value={d}>
                  {d} days with saved scans
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Top of each column
            <select value={top} onChange={(e) => setTop(Number(e.target.value))} className={select}>
              {TOPS.map((n) => (
                <option key={n} value={n}>
                  Top {n} bullish + top {n} bearish
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-col gap-1">
            Times
            <div className="flex flex-wrap gap-1">
              {TIMES.map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={times.includes(t)}
                  onClick={() => toggle(t)}
                  className={clsx(
                    "min-h-9 rounded-md px-2 font-mono ring-1 ring-inset",
                    times.includes(t) ? "bg-accentSky/20 text-accentSky ring-accentSky/50" : "text-slate-400 ring-slate-700 hover:bg-white/5"
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button"
            onClick={() => void run()}
            disabled={busy}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky/15 px-3 font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/25 disabled:opacity-40"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Run the test
          </button>
        </div>
        {error && <p className="mt-2 text-amber-300">{error}</p>}
      </div>

      {study && (
        <>
          <p className="text-xs text-slate-500">
            {study.days.length} day{study.days.length === 1 ? "" : "s"} read
            {study.days.length ? ` (${study.days[0].day} → ${study.days[study.days.length - 1].day})` : ""}
            {study.days.some((d) => !d.closed) ? `, ${study.days.filter((d) => !d.closed).length} without a scan near the close (no close result)` : ""}
            {" · "}
            {study.samples.toLocaleString("en-IN")} stock readings · a scan within {study.tolerance_min} min of a time stands for it.
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-200">
            {study.answer.map((line) => (
              <li key={line} className={clsx(/too few|No column|No saved/.test(line) && "text-amber-300")}>
                {line}
              </li>
            ))}
          </ul>

          <details className="rounded border border-slate-800 p-2 text-xs" open>
            <summary className="cursor-pointer font-semibold text-slate-300">How to read the table</summary>
            <p className="mt-1 max-w-4xl text-slate-400">
              Example: &quot;Volume vs usual — top 68%, all stocks 52%, +16&quot; means the {study.top} bullish and {study.top} bearish
              stocks with the most volume against their usual kept moving their way to the close on 68 of every 100
              readings, while all the stocks did on 52. +16 points is the column&apos;s edge: Strong from +10, Some from +5.
              &quot;Weakest&quot; is the bottom {study.top} of the same column: the wider the gap between top and weakest, the more the
              column tells the stocks apart. &quot;Too few&quot; means under 30 readings: run more days.
            </p>
          </details>

          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-2 py-1 text-left">Time</th>
                  {SIDES.map((s) => (
                    <th key={s.value} className="px-2 py-1 text-left">
                      Best for {s.label.toLowerCase()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grid.map((g) => (
                  <tr key={g.time} className="border-t border-slate-800">
                    <td className="px-2 py-1 font-mono text-slate-300">
                      <button type="button" onClick={() => setAt(g.time)} className="underline-offset-2 hover:underline">
                        {g.time === "ALL" ? "All times" : g.time}
                      </button>
                    </td>
                    {g.cells.map((c) => (
                      <td key={c.side} className="px-2 py-1 text-slate-300">
                        {c.best ? (
                          <>
                            <span className="font-semibold text-slate-100">{c.best.label}</span>{" "}
                            <span className="text-emerald-400">{pct(c.best.top.close_hit)}</span>
                            <span className="text-slate-500"> vs {pct(c.base)}</span>
                          </>
                        ) : (
                          <span className="text-slate-500">none better than all stocks</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <label className="flex items-center gap-2">
              Time
              <select value={at} onChange={(e) => setAt(e.target.value)} className={select}>
                <option value="ALL">All times</option>
                {study.times.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              Stocks
              <select value={side} onChange={(e) => setSide(e.target.value as Side)} className={select}>
                {SIDES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            {table && (
              <span>
                All stocks: kept going to the close {pct(table.base.close_hit)} ({table.base.close_n.toLocaleString("en-IN")}), an hour later{" "}
                {pct(table.base.hour_hit)}
              </span>
            )}
          </div>

          {table && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-xs">
                <thead className="text-[10px] text-slate-400">
                  <tr>
                    <SortTh label="Column" k="label" sort={sort} onSort={onSort} text />
                    <SortTh label="Finds the trend?" k="verdict" sort={sort} onSort={onSort} />
                    <SortTh label="Top kept going (close)" k="close_hit" sort={sort} onSort={onSort} num />
                    <SortTh label="Edge vs all" k="edge_close" sort={sort} onSort={onSort} num title="Points above all stocks" />
                    <SortTh label="Weakest kept going" k="bottom_close" sort={sort} onSort={onSort} num />
                    <SortTh label="Top − weakest" k="spread_close" sort={sort} onSort={onSort} num />
                    <SortTh label="Top, 1 hour later" k="hour_hit" sort={sort} onSort={onSort} num />
                    <SortTh label="Edge, 1 hour" k="edge_hour" sort={sort} onSort={onSort} num />
                    <SortTh label="Top avg move to close" k="close_avg" sort={sort} onSort={onSort} num title="Signed the stock's way" />
                    <SortTh label="Readings" k="n" sort={sort} onSort={onSort} num />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((p) => (
                    <tr key={p.key} className="border-t border-slate-800">
                      <td className="px-2 py-1" title={p.help}>
                        <span className="font-semibold text-slate-100">{p.label}</span>
                        <span className="block text-[10px] text-slate-500">{p.help}</span>
                      </td>
                      <td className="px-2 py-1">
                        <span className={clsx("rounded px-1.5 py-0.5 text-[11px] font-semibold", VERDICT[p.verdict].cls)}>{VERDICT[p.verdict].text}</span>
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-slate-100">{pct(p.top.close_hit)}</td>
                      <td className={clsx("px-2 py-1 text-right font-mono", tone(p.edge_close))}>{pts(p.edge_close)}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-300">{pct(p.bottom.close_hit)}</td>
                      <td className={clsx("px-2 py-1 text-right font-mono", tone(p.spread_close))}>{pts(p.spread_close)}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-100">{pct(p.top.hour_hit)}</td>
                      <td className={clsx("px-2 py-1 text-right font-mono", tone(p.edge_hour))}>{pts(p.edge_hour)}</td>
                      <td className={clsx("px-2 py-1 text-right font-mono", tone(p.top.close_avg))}>{move(p.top.close_avg)}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-400">{p.top.close_n.toLocaleString("en-IN")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
