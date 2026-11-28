"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { NewTag } from "@/components/ui/NewTag";
import { SKIP_FIRST_CHOICES, SKIP_FIRST_HELP, skipFirstLabel } from "@/lib/skipFirst";
import { PICK_TIMES } from "@/lib/pickWindow";
import { smaApi, type LabAutoArmBody } from "@/lib/smaApi";

const SIZES = [1, 3, 5, 10, 15] as const;
const EVERY = [1, 2, 3, 5, 10, 15, 30] as const;
const MAX_DAYS = [1, 2, 3, 5, 8, 10, 15, 20, 30, 50];

export const DEFAULT_LAB_ARM: LabAutoArmBody = { first: "09:30", last: "14:30", every_min: 5, minutes: 5, candles: "NORMAL", skip_candles: 0 };

/**
 * The Strategy lab's "Auto-arm: Trend quality picks": the rule the live auto-arm uses (strong trend on the Trend quality
 * read every N minutes), so each past day only the stocks it would have armed count, and only after their arm minute.
 * Starts on the live auto-arm's own settings (Scalp alerts) and the Trend quality scan's candle. Form only.
 */
export function LabAutoArmPick({ value, onChange, disabled }: { value: LabAutoArmBody; onChange: (v: LabAutoArmBody) => void; disabled?: boolean }) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (loaded) return;
    let base = { ...value };
    Promise.allSettled([smaApi.scalpAlerts(), smaApi.chopScan()]).then(([a, c]) => {
      if (a.status === "fulfilled") {
        const al = a.value;
        base = {
          ...base,
          min_checks: al.strong_min_checks,
          side: al.strong_side,
          max_day: al.arm_max_day,
          price_min: al.arm_price_min || 0,
          price_max: al.arm_price_max || 0,
          stack: Boolean(al.arm_strong_stack) || (al.arm_strong_widen ?? 0) > 0,
          widen: al.arm_strong_widen ?? 0,
          third: al.arm_strong_third ?? 50,
        };
      }
      if (c.status === "fulfilled") {
        const m = Number(c.value.auto?.minutes ?? 0);
        if ((SIZES as readonly number[]).includes(m)) base = { ...base, minutes: m as LabAutoArmBody["minutes"], candles: c.value.auto?.candles === "HA" ? "HA" : "NORMAL" };
      }
      onChange(base);
      setLoaded(true);
    });
    // Only once: later edits are the person's own.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (patch: Partial<LabAutoArmBody>) => onChange({ ...value, ...patch });
  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <div className={clsx("rounded-lg border border-sky-500/30 bg-sky-500/[0.04] p-3 text-xs text-slate-400", disabled && "opacity-60")}>
      <p className="mb-2 text-slate-300">
        Each past day, only the stocks the Trend quality auto-arm would have armed are traded, from their arm minute on (a
        cross before it is not taken, as live). The stock lists above are the stocks the rule reads. It starts on the live
        auto-arm&apos;s settings (Scalp page → Alerts).
        {value.stack ? ` Also needs the three SMAs stacked${value.widen ? ` with both gaps widening ${value.widen} candle(s)` : ""} (third SMA ${value.third ?? 50}), as the auto-arm.` : ""}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          Arm from
          <select value={value.first} onChange={(e) => set({ first: e.target.value })} className={select} disabled={disabled}>
            {PICK_TIMES.filter((t) => t < value.last).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Arm until
          <select value={value.last} onChange={(e) => set({ last: e.target.value })} className={select} disabled={disabled}>
            {PICK_TIMES.filter((t) => t > value.first).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Read every
          <select value={value.every_min} onChange={(e) => set({ every_min: Number(e.target.value) as LabAutoArmBody["every_min"] })} className={select} disabled={disabled}>
            {EVERY.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Trend candle
          <select value={value.minutes} onChange={(e) => set({ minutes: Number(e.target.value) as LabAutoArmBody["minutes"] })} className={select} disabled={disabled}>
            {SIZES.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Candles
          <select value={value.candles} onChange={(e) => set({ candles: e.target.value === "HA" ? "HA" : "NORMAL" })} className={select} disabled={disabled}>
            <option value="NORMAL">Normal</option>
            <option value="HA">Heikin Ashi</option>
          </select>
        </label>
        <label className="flex flex-col gap-1" title={SKIP_FIRST_HELP}>
          Skip today&apos;s first candles
          <select value={value.skip_candles} onChange={(e) => set({ skip_candles: Number(e.target.value) })} className={select} disabled={disabled}>
            {SKIP_FIRST_CHOICES.map((n) => (
              <option key={n} value={n}>
                {skipFirstLabel(n, value.minutes)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Checks passed
          <select value={value.min_checks ?? 6} onChange={(e) => set({ min_checks: Number(e.target.value) })} className={select} disabled={disabled}>
            {[5, 6, 7].map((n) => (
              <option key={n} value={n}>
                {n} of 7
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Side
          <select value={value.side ?? "BOTH"} onChange={(e) => set({ side: e.target.value as LabAutoArmBody["side"] })} className={select} disabled={disabled}>
            <option value="BOTH">Up and down</option>
            <option value="UP">Up only</option>
            <option value="DOWN">Down only</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          At most a day
          <select value={value.max_day ?? 10} onChange={(e) => set({ max_day: Number(e.target.value) })} className={select} disabled={disabled}>
            {Array.from(new Set([...MAX_DAYS, value.max_day ?? 10])).sort((a, b) => a - b).map((n) => (
              <option key={n} value={n}>
                {n} stock{n === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Price from ₹
          <input type="number" min={0} value={value.price_min || ""} placeholder="any" onChange={(e) => set({ price_min: Number(e.target.value) || 0 })} className={clsx(select, "w-20")} disabled={disabled} />
        </label>
        <label className="flex flex-col gap-1">
          to ₹
          <input type="number" min={0} value={value.price_max || ""} placeholder="any" onChange={(e) => set({ price_max: Number(e.target.value) || 0 })} className={clsx(select, "w-20")} disabled={disabled} />
        </label>
      </div>
    </div>
  );
}

/** The day-by-day list of the stocks the auto-arm picked in a lab run (display only). */
export function LabArmedList({ picks }: { picks: Record<string, { symbol: string; at: string; side?: string; why?: string }[]> }) {
  const days = Object.keys(picks).sort();
  const total = days.reduce((n, d) => n + (picks[d]?.length ?? 0), 0);
  return (
    <details className="rounded-lg border border-slate-800 p-3 text-xs">
      <summary className="cursor-pointer text-slate-300">
        Stocks auto-armed each day: {total} over {days.length} day(s) <NewTag id="lab-auto-arm" link={false} />
      </summary>
      <div className="mt-2 max-h-72 overflow-auto">
        <table className="w-full text-left">
          <thead className="text-slate-500">
            <tr>
              <th className="px-2 py-1 font-normal">Day</th>
              <th className="px-2 py-1 font-normal">Armed</th>
              <th className="px-2 py-1 font-normal">Stock</th>
              <th className="px-2 py-1 font-normal">Side</th>
              <th className="px-2 py-1 font-normal">Why</th>
            </tr>
          </thead>
          <tbody>
            {days.flatMap((d) =>
              (picks[d]?.length ? picks[d] : [null]).map((p, i) => (
                <tr key={`${d}-${i}`} className="border-t border-slate-800">
                  <td className="px-2 py-1 font-mono text-slate-400">{i === 0 ? d : ""}</td>
                  {p ? (
                    <>
                      <td className="px-2 py-1 font-mono">{p.at}</td>
                      <td className="px-2 py-1 font-semibold text-slate-200">{p.symbol}</td>
                      <td className={clsx("px-2 py-1", p.side === "SHORT" ? "text-loss" : "text-profit")}>{p.side === "SHORT" ? "Down" : "Up"}</td>
                      <td className="px-2 py-1 text-slate-400">{p.why ?? ""}</td>
                    </>
                  ) : (
                    <td colSpan={4} className="px-2 py-1 text-slate-500">
                      No stock armed that day
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </details>
  );
}
