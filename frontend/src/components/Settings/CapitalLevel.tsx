"use client";

import clsx from "clsx";
import type { ReactNode } from "react";
import { inr } from "@/lib/smaApi";

/** ₹ amount or % of capital (the practice wallet's money loaded), for the all-bots level boxes. */
export type LevelUnit = "RS" | "PCT";

const fmtPct = (v: number) => `${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })} %`;

/** The switch between ₹ and % of capital, with the capital it reads. */
export function UnitSwitch({
  unit,
  onChange,
  capital,
  badge,
}: {
  unit: LevelUnit;
  onChange: (u: LevelUnit) => void;
  capital: number;
  badge?: ReactNode;
}) {
  const pill = (u: LevelUnit, label: string) => (
    <button
      type="button"
      aria-pressed={unit === u}
      onClick={() => onChange(u)}
      className={clsx("min-h-8 px-2.5 text-xs", unit === u ? "bg-accentSky/20 font-semibold text-accentSky" : "text-slate-300 hover:bg-white/5")}
    >
      {label}
    </button>
  );
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      Enter the levels as
      <span role="group" aria-label="₹ or % of capital" className="inline-flex overflow-hidden rounded-md ring-1 ring-inset ring-white/15">
        {pill("RS", "₹ amount")}
        {pill("PCT", "% of capital")}
      </span>
      {badge}
      <span>
        Capital (practice wallet&apos;s money loaded): <span className="font-mono text-slate-200">{capital > 0 ? inr(capital) : "none loaded"}</span>
      </span>
      {unit === "PCT" && capital <= 0 ? <span className="text-amber-300">Load money in the wallet first: with no capital the % levels are off.</span> : null}
    </div>
  );
}

/** One level box: typed in the chosen unit, the other unit shown beside it (₹ ↔ % of capital). */
export function LevelBox({
  label,
  unit,
  rs,
  pct,
  onRs,
  onPct,
  capital,
  disabled,
}: {
  label: string;
  unit: LevelUnit;
  rs: string;
  pct: string;
  onRs: (v: string) => void;
  onPct: (v: string) => void;
  capital: number;
  disabled?: boolean;
}) {
  const rsNum = Math.max(0, Number(rs) || 0);
  const pctNum = Math.max(0, Number(pct) || 0);
  const other =
    unit === "PCT"
      ? capital > 0 && pctNum > 0
        ? `= ${inr((pctNum * capital) / 100)}`
        : pctNum > 0
          ? "no capital"
          : "off"
      : capital > 0 && rsNum > 0
        ? `= ${fmtPct((rsNum / capital) * 100)} of capital`
        : rsNum > 0
          ? ""
          : "off";
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label} ({unit === "PCT" ? "% of capital" : "₹"})
      <input
        inputMode="decimal"
        value={unit === "PCT" ? pct : rs}
        onChange={(e) => (unit === "PCT" ? onPct(e.target.value) : onRs(e.target.value))}
        disabled={disabled}
        className="min-h-9 w-32 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 disabled:opacity-50"
      />
      <span className="font-mono text-[11px] text-slate-500">{other}</span>
    </label>
  );
}
