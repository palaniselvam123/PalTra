"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import type { SecondCandleRow } from "@/lib/smaApi";

/**
 * How far the trades went, as bar charts (the Second candle tab; display only, from the filtered rows):
 * - With the trade: % of trades whose best move reached at least each level ("≥ ₹5: 80%, ≥ ₹10: 70%").
 * - Against the trade: the same for the worst move.
 * - At the square-off: % that closed at least that much in profit (green) and in loss (red).
 * Levels in ₹ a share or in % of the entry price.
 */
const RS_LEVELS = [2, 3, 5, 8, 10, 15, 20, 30, 50];
const PCT_LEVELS = [0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3];

type Unit = "RS" | "PCT";

function share(values: number[], test: (v: number) => boolean) {
  const n = values.filter(test).length;
  return { n, pct: values.length ? (n / values.length) * 100 : 0 };
}

export function MoveBars({ rows }: { rows: SecondCandleRow[] }) {
  const [unit, setUnit] = useState<Unit>("RS");
  const levels = unit === "RS" ? RS_LEVELS : PCT_LEVELS;
  const label = (lv: number) => (unit === "RS" ? `₹${lv}` : `${lv}%`);
  const data = useMemo(() => {
    const best = rows.map((r) => (unit === "RS" ? r.best_pts : r.best_pct));
    const worst = rows.map((r) => (unit === "RS" ? r.worst_pts : r.worst_pct));
    const close = rows.map((r) => (unit === "RS" ? r.close_pts : r.close_pct));
    return levels.map((lv) => ({
      lv,
      best: share(best, (v) => v >= lv),
      worst: share(worst, (v) => v >= lv),
      profit: share(close, (v) => v >= lv),
      loss: share(close, (v) => v <= -lv),
    }));
  }, [rows, unit, levels]);

  if (!rows.length) return null;
  return (
    <div className="rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-200">How far the trades went</span>
        <span className="text-slate-500">{rows.length} trades · each bar = % of trades that reached at least that level</span>
        <div className="ml-auto flex gap-1" role="group" aria-label="Unit">
          {(["RS", "PCT"] as const).map((u) => (
            <button
              key={u}
              type="button"
              aria-pressed={unit === u}
              onClick={() => setUnit(u)}
              className={clsx("min-h-8 rounded-md px-2 ring-1 ring-inset", unit === u ? "bg-sky-500/20 text-sky-100 ring-sky-400/50" : "text-slate-400 ring-slate-700 hover:bg-white/5")}
            >
              {u === "RS" ? "₹ a share" : "% of entry"}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Chart title="With the trade (best move)" rows={data.map((d) => ({ label: `≥ ${label(d.lv)}`, bars: [{ ...d.best, tone: "green" as const }] }))} />
        <Chart title="Against the trade (worst move)" rows={data.map((d) => ({ label: `≥ ${label(d.lv)}`, bars: [{ ...d.worst, tone: "red" as const }] }))} />
        <Chart
          title="At the square-off (profit / loss)"
          rows={data.map((d) => ({
            label: `≥ ${label(d.lv)}`,
            bars: [
              { ...d.profit, tone: "green" as const, word: "profit" },
              { ...d.loss, tone: "red" as const, word: "loss" },
            ],
          }))}
        />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Read the first chart as: &quot;{data[2] ? `${Math.round(data[2].best.pct)}% of trades went at least ${label(data[2].lv)} with the trade` : ""}&quot;.
        Before charges. The filters above change every bar.
      </p>
    </div>
  );
}

type Bar = { n: number; pct: number; tone: "green" | "red"; word?: string };

function Chart({ title, rows }: { title: string; rows: { label: string; bars: Bar[] }[] }) {
  return (
    <div>
      <div className="mb-1 text-slate-300">{title}</div>
      <div className="space-y-1">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-2">
            <span className="w-14 shrink-0 text-right tabular-nums text-slate-400">{r.label}</span>
            <div className="flex-1 space-y-0.5">
              {r.bars.map((b, i) => (
                <div key={i} className="flex items-center gap-1.5" title={`${b.n} trades${b.word ? ` closed in ${b.word}` : ""} (${b.pct.toFixed(1)}%)`}>
                  <div className="h-3 flex-1 rounded-sm bg-white/5">
                    <div
                      className={clsx("h-3 rounded-sm", b.tone === "green" ? "bg-emerald-500/70" : "bg-rose-500/70")}
                      style={{ width: `${Math.min(100, b.pct)}%` }}
                    />
                  </div>
                  <span className={clsx("w-20 shrink-0 tabular-nums", b.tone === "green" ? "text-emerald-300" : "text-rose-300")}>
                    {b.pct.toFixed(0)}% <span className="text-slate-500">({b.n})</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
