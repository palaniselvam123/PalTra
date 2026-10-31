"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { Calculator, Loader2 } from "lucide-react";
import { ApiError, smaApi, type SmaConfig, type TslCalcResult } from "@/lib/smaApi";

const SIZES = [1, 2, 3, 5, 10, 15] as const;
const input = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

function inr(v: number | null | undefined): string {
  return v == null ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

/**
 * Trailing stop and target sized from the candle (tsl_calc.py): the stock's own ATR on each candle size, times the
 * multiples chosen here, as a stop distance, a trail step and a target in ₹ and in % of the price. "Use" copies a row
 * into the form's TSL fields (points or percent); the usual Save then applies it to the bot, or to the stock when a
 * stock is picked above. Read-only until Save; never an order.
 */
export function TslCalculator({
  form,
  set,
  symbol,
  armed,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  /** The stock the Settings page is scoped to, if any. */
  symbol?: string | null;
  armed: string[];
}) {
  const [stock, setStock] = useState<string>(symbol || armed[0] || String(form.symbol ?? ""));
  const [stopMult, setStopMult] = useState(1.5);
  const [trailMult, setTrailMult] = useState(0.5);
  const [targetMult, setTargetMult] = useState(3);
  const [atrPeriod, setAtrPeriod] = useState(Number(form.atr_period ?? 14) || 14);
  const [sizes, setSizes] = useState<number[]>([1, 3, 5, 15]);
  const [out, setOut] = useState<TslCalcResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [withCandle, setWithCandle] = useState(false);

  useEffect(() => {
    if (symbol) setStock(symbol);
  }, [symbol]);

  const run = useCallback(async () => {
    const name = stock.trim().toUpperCase();
    if (!name) {
      setNote("Type a stock first.");
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      setOut(await smaApi.tslCalc(name, sizes, atrPeriod, stopMult, trailMult, targetMult));
    } catch (err: unknown) {
      setOut(null);
      setNote(err instanceof ApiError || err instanceof Error ? err.message : "Could not read the candles");
    } finally {
      setBusy(false);
    }
  }, [stock, sizes, atrPeriod, stopMult, trailMult, targetMult]);

  const use = (row: NonNullable<TslCalcResult["sizes"]>[number], unit: "POINTS" | "PERCENT") => {
    if (row.stop == null) return;
    set("use_stop", true);
    set("stop_type", "TSL");
    set("tsl_mode", unit);
    if (unit === "POINTS") {
      set("tsl_sl_points", String(row.stop));
      set("tsl_trail_points", String(row.trail ?? 0));
      set("tsl_target_points", String(row.target ?? 0));
    } else {
      set("tsl_sl_pct", String(row.stop_pct));
      set("tsl_trail_pct", String(row.trail_pct ?? 0));
      set("tsl_target_pct", String(row.target_pct ?? 0));
    }
    if (withCandle) set("candle_minutes", String(row.minutes));
    setNote(
      `Filled the trailing stop with the ${row.minutes}-min numbers (${unit === "POINTS" ? "₹" : "%"})${withCandle ? ` and the ${row.minutes}-min candle` : ""}. Press Save to keep them${symbol ? ` for ${symbol}` : ""}.`
    );
  };

  const toggleSize = (n: number) => setSizes((cur) => (cur.includes(n) ? cur.filter((x) => x !== n) : [...cur, n].sort((a, b) => a - b)));

  return (
    <section aria-label="Trailing stop calculator" className="mt-3 rounded-lg border border-emerald-400/25 bg-emerald-500/[0.04] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
          <Calculator size={14} aria-hidden /> Size the trailing stop and target from the candle
        </h4>
        <span className="text-[11px] text-slate-500">ATR = how far one candle usually travels; a bigger candle needs a wider stop</span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Reads the stock&apos;s own candles on each size, takes the ATR({atrPeriod}) of that size and turns the multiples below into a stop
        distance, a trail step and a target, in ₹ (rounded to the tick) and in % of the price. A stop under 1 × ATR is hit by
        noise; 1.5 × leaves one wild candle; the trail at ½ ATR follows in half-candle steps; a target of 3 × is two-to-one on the
        stop. Use the row of the candle size the bot trades on{symbol ? ` for ${symbol}` : ""}; Save keeps it.
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Stock
          <input
            list="tsl-calc-armed"
            value={stock}
            onChange={(e) => setStock(e.target.value.toUpperCase())}
            disabled={Boolean(symbol)}
            className={clsx(input, "w-36 font-mono uppercase disabled:opacity-70")}
            placeholder="SBIN"
          />
          <datalist id="tsl-calc-armed">
            {armed.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Stop (× ATR)
          <input inputMode="decimal" value={String(stopMult)} onChange={(e) => setStopMult(Math.max(0, Math.min(20, Number(e.target.value) || 0)))} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Trail every (× ATR)
          <input inputMode="decimal" value={String(trailMult)} onChange={(e) => setTrailMult(Math.max(0, Math.min(20, Number(e.target.value) || 0)))} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Target (× ATR, 0 = none)
          <input inputMode="decimal" value={String(targetMult)} onChange={(e) => setTargetMult(Math.max(0, Math.min(20, Number(e.target.value) || 0)))} className={clsx(input, "w-20")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          ATR period
          <input inputMode="numeric" value={String(atrPeriod)} onChange={(e) => setAtrPeriod(Math.max(2, Math.min(100, Math.round(Number(e.target.value) || 14))))} className={clsx(input, "w-20")} />
        </label>
        <fieldset className="flex flex-col gap-1 text-xs text-slate-400">
          <legend className="mb-1">Candle sizes</legend>
          <div className="flex gap-2">
            {SIZES.map((n) => (
              <label key={n} className="flex min-h-9 items-center gap-1 text-sm text-slate-300">
                <input type="checkbox" checked={sizes.includes(n)} onChange={() => toggleSize(n)} className="accent-[#10B981]" />
                {n}m
              </label>
            ))}
          </div>
        </fieldset>
        <button
          type="button"
          disabled={busy || !sizes.length}
          onClick={() => void run()}
          className="flex min-h-9 items-center gap-1 rounded-md bg-emerald-600 px-3 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <Calculator size={13} aria-hidden />} Calculate
        </button>
      </div>
      {out ? (
        <div className="mt-3 overflow-x-auto">
          <div className="mb-1 text-xs text-slate-400">
            {out.symbol} at {inr(out.price)} ({out.source === "bot tape" ? "the bot's own candles" : "Groww candles"}
            {out.as_of ? `, as of ${new Date(out.as_of).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""})
          </div>
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="whitespace-nowrap py-1 pr-3 font-medium">Candle</th>
                <th className="py-1 pr-2 text-right font-medium">ATR</th>
                <th className="py-1 pr-2 text-right font-medium">Avg range</th>
                <th className="py-1 pr-2 text-right font-medium">Stop</th>
                <th className="py-1 pr-2 text-right font-medium">Trail every</th>
                <th className="py-1 pr-2 text-right font-medium">Target</th>
                <th className="py-1 pr-2 text-right font-medium">In %</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {(out.sizes ?? []).map((row) => (
                <tr key={row.minutes} className={clsx(Number(form.candle_minutes ?? 1) === row.minutes && "bg-emerald-400/[0.06]")}>
                  <td className="whitespace-nowrap py-1.5 pr-3 font-semibold text-slate-200">
                    {row.minutes} min{Number(form.candle_minutes ?? 1) === row.minutes ? <span className="ml-1 text-[10px] font-normal text-emerald-300">bot&apos;s candle</span> : null}
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono text-slate-300">
                    {row.atr == null ? <span className="text-slate-500">only {row.candles} candles</span> : `${inr(row.atr)} (${row.atr_pct}%)`}
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono text-slate-400">{inr(row.avg_range)}</td>
                  <td className="py-1.5 pr-2 text-right font-mono text-rose-200">{inr(row.stop)}</td>
                  <td className="py-1.5 pr-2 text-right font-mono text-amber-200">{inr(row.trail)}</td>
                  <td className="py-1.5 pr-2 text-right font-mono text-emerald-200">{row.target ? inr(row.target) : "none"}</td>
                  <td className="py-1.5 pr-2 text-right font-mono text-slate-400">
                    {row.stop_pct != null ? `${row.stop_pct} / ${row.trail_pct} / ${row.target_pct || 0}` : "—"}
                  </td>
                  <td className="py-1.5 text-right">
                    {row.stop != null ? (
                      <span className="inline-flex gap-1">
                        <button type="button" onClick={() => use(row, "POINTS")} className="rounded px-2 py-0.5 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10" title="Fill the trailing stop fields with these rupee numbers">
                          Use ₹
                        </button>
                        <button type="button" onClick={() => use(row, "PERCENT")} className="rounded px-2 py-0.5 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10" title="Fill the trailing stop fields with these percent numbers (they scale with the price)">
                          Use %
                        </button>
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <label className="mt-2 flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={withCandle} onChange={(e) => setWithCandle(e.target.checked)} className="accent-[#10B981]" />
            Use also sets the bot&apos;s candle size to that row
          </label>
        </div>
      ) : null}
      {note ? <p className="mt-2 text-xs text-amber-200">{note}</p> : null}
    </section>
  );
}
