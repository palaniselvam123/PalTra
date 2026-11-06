"use client";

import clsx from "clsx";
import { Layers, Loader2, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Candle } from "@/lib/smaApi";

/**
 * Drill-down candles (display only, nothing is traded or saved): click a candle on the chart, pick a smaller size, and that
 * candle (and the one before it, when ticked) is redrawn in place as the smaller candles that made it, built from the
 * same 1-minute tape. The rest of the chart stays as it was. The panel lists each smaller candle with the slope of its own
 * SMA 9 (in % of the price per candle and in ATR per candle) and how steep that is.
 */

/** Sizes a `bar`-minute candle can be split into: smaller ones that divide it exactly. */
export const DRILL_CHOICES = [1, 2, 3, 5, 10, 15, 30, 60] as const;

export function drillSizes(bar: number): number[] {
  return DRILL_CHOICES.filter((s) => s < bar && bar % s === 0);
}

/** % per candle under which the SMA 9 counts as flat, and at or over which it counts as steep (the review's defaults). */
export const FLAT_PCT = 0.01;
export const STEEP_PCT = 0.03;

export type DrillRow = Candle & {
  /** SMA 9 slope on this candle size: % of the price per candle (signed), and ATR per candle. */
  slopePct: number | null;
  slopeAtr: number | null;
};

export type Drill = {
  /** The bot-size candles drilled (their start times). */
  starts: number[];
  size: number;
  bar: number;
  rows: DrillRow[];
};

export type DrillAskAt = { time: number; x: number; y: number };

/** Slope of SMA 9 between each candle and the one before, on the smaller candles. */
export function withSlopes(rows: Candle[]): DrillRow[] {
  return rows.map((c, i) => {
    const prev = i > 0 ? rows[i - 1].sma9 : null;
    const now = c.sma9;
    const slopePct = now != null && prev != null && prev !== 0 ? ((now - prev) / prev) * 100 : null;
    const slopeAtr = now != null && prev != null && c.atr14 ? (now - prev) / c.atr14 : null;
    return { ...c, slopePct, slopeAtr };
  });
}

export function steepWord(slope: number | null): { word: string; cls: string } {
  if (slope == null) return { word: "—", cls: "text-slate-500" };
  const a = Math.abs(slope);
  const way = slope > 0 ? "up" : "down";
  if (a < FLAT_PCT) return { word: "flat", cls: "text-slate-300" };
  if (a < STEEP_PCT) return { word: `gentle ${way}`, cls: slope > 0 ? "text-emerald-300" : "text-rose-300" };
  return { word: `steep ${way}`, cls: slope > 0 ? "font-semibold text-emerald-300" : "font-semibold text-rose-300" };
}

function clock(sec: number): string {
  return new Date(sec * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

function sign(v: number | null, digits: number): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

/** The small "drill this candle into…" menu shown where the chart was clicked. */
export function DrillAsk({
  at,
  bar,
  before,
  onBefore,
  onPick,
  onClose,
}: {
  at: DrillAskAt;
  bar: number;
  before: boolean;
  onBefore: (on: boolean) => void;
  onPick: (size: number) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const sizes = drillSizes(bar);
  const prevStart = at.time - bar * 60;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={`Drill down the ${clock(at.time)} candle`}
      style={{ left: Math.max(4, at.x - 110), top: Math.max(4, at.y + 12) }}
      className="absolute z-20 w-[230px] rounded-lg border border-white/15 bg-[#0B0E14]/95 p-2.5 text-xs text-slate-200 shadow-xl"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-slate-100">
          Drill down the {clock(at.time)} {bar}-min candle into
        </p>
        <button type="button" onClick={onClose} aria-label="Close" className="-m-1 grid h-7 w-7 place-items-center rounded text-slate-400 hover:bg-white/10">
          <X size={14} aria-hidden />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {sizes.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="min-h-9 min-w-12 rounded-md bg-sky-500/15 px-2.5 font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/25"
          >
            {s} min
          </button>
        ))}
      </div>
      <label className="mt-2 flex items-center gap-2 text-slate-300">
        <input type="checkbox" checked={before} onChange={(e) => onBefore(e.target.checked)} className="accent-sky-400" />
        Also the candle before ({clock(prevStart)})
      </label>
      <p className="mt-1 text-[11px] text-slate-500">Only these candles split; the rest of the chart stays.</p>
    </div>
  );
}

/** The drilled candles as a table: OHLC, own SMA 9, its slope and how steep, plus a one-line summary. */
export function DrillPanel({
  drill,
  loading,
  error,
  onClear,
}: {
  drill: Drill | null;
  loading: boolean;
  error: string | null;
  onClear: () => void;
}) {
  if (!drill && !loading && !error) return null;
  const rows = drill?.rows ?? [];
  const slopes = rows.map((r) => r.slopePct).filter((v): v is number => v != null);
  const steepest = slopes.length ? slopes.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a)) : null;
  const avg = slopes.length ? slopes.reduce((a, b) => a + b, 0) / slopes.length : null;
  const turned = slopes.length >= 2 && Math.sign(slopes[0]) !== Math.sign(slopes[slopes.length - 1]) && slopes[0] !== 0;
  const startTimes = drill ? drill.starts.map(clock).join(" + ") : "";
  return (
    <div className="mt-2 rounded-lg border border-sky-400/30 bg-sky-500/[0.05] p-2.5 text-xs text-slate-300">
      <div className="flex flex-wrap items-center gap-2">
        <Layers size={14} className="text-sky-300" aria-hidden />
        <span className="font-semibold text-slate-100">
          {drill ? `Drill-down: ${startTimes} ${drill.bar}-min candle${drill.starts.length > 1 ? "s" : ""} as ${drill.size}-min candles` : "Drill-down"}
        </span>
        {loading ? <Loader2 size={13} className="animate-spin text-sky-300" aria-label="Loading" /> : null}
        <button
          type="button"
          onClick={onClear}
          className="ml-auto inline-flex min-h-8 items-center gap-1 rounded-md px-2.5 font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5"
        >
          <X size={13} aria-hidden /> Back to {drill ? `${drill.bar}-min` : "normal"} candles
        </button>
      </div>
      {error ? <p className="mt-1 text-amber-300">{error}</p> : null}
      {drill && rows.length ? (
        <>
          <p className="mt-1">
            SMA 9 on {drill.size}-min candles: average slope <b className={steepWord(avg).cls}>{sign(avg, 3)}%</b>/candle (
            {steepWord(avg).word}), steepest <b className={steepWord(steepest).cls}>{sign(steepest, 3)}%</b>/candle
            {turned ? <span className="ml-1 font-semibold text-amber-300">· it turned inside these candles</span> : null}.
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left">
              <thead>
                <tr className="text-slate-500">
                  <th className="py-1 font-normal">Candle</th>
                  <th className="py-1 text-right font-normal">Open</th>
                  <th className="py-1 text-right font-normal">High</th>
                  <th className="py-1 text-right font-normal">Low</th>
                  <th className="py-1 text-right font-normal">Close</th>
                  <th className="py-1 text-right font-normal">SMA 9</th>
                  <th className="py-1 text-right font-normal" title="How much SMA 9 moved from the candle before, in % of the price">
                    Slope %/candle
                  </th>
                  <th className="py-1 text-right font-normal" title="The same move in ATRs of this candle size: one number for any price">
                    ATR/candle
                  </th>
                  <th className="py-1 pl-3 font-normal">Steep?</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const up = r.close >= r.open;
                  const w = steepWord(r.slopePct);
                  return (
                    <tr key={r.time} className="border-t border-white/5">
                      <td className="py-1 font-mono tabular-nums">
                        <span className={clsx("mr-1 inline-block h-2 w-2 rounded-sm", up ? "bg-emerald-400" : "bg-rose-400")} aria-hidden />
                        {clock(r.time)}
                      </td>
                      <td className="py-1 text-right font-mono tabular-nums">{r.open.toFixed(2)}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{r.high.toFixed(2)}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{r.low.toFixed(2)}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{r.close.toFixed(2)}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{r.sma9 != null ? r.sma9.toFixed(2) : "—"}</td>
                      <td className={clsx("py-1 text-right font-mono tabular-nums", w.cls)}>{sign(r.slopePct, 3)}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{sign(r.slopeAtr, 2)}</td>
                      <td className={clsx("py-1 pl-3", w.cls)}>{w.word}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-[11px] text-slate-500">
            Flat = under {FLAT_PCT}%/candle, steep = {STEEP_PCT}%/candle or more (the 1-minute review&apos;s defaults). The dotted
            line on the chart is SMA 9 of these {drill.size}-min candles. Display only.
          </p>
        </>
      ) : drill && !loading ? (
        <p className="mt-1 text-slate-400">No {drill.size}-minute candles found for that time.</p>
      ) : null}
    </div>
  );
}
