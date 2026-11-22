"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { backtestBasis, onBacktestBasis, setBacktestBasis, type BacktestBasis } from "@/lib/pnlBasis";

/** The backtest screens' basis, following the one switch every backtest page shares (kept per browser). */
export function useBacktestBasis(): BacktestBasis {
  const [basis, setBasis] = useState<BacktestBasis>("gross");
  useEffect(() => {
    setBasis(backtestBasis());
    return onBacktestBasis(setBasis);
  }, []);
  return basis;
}

/** "Before charges | After charges": one click turns every backtest result (runs, days, stocks, PDF, compare,
 * P&L dashboard) to that basis. Display only. */
export function BacktestBasisSwitch({ className }: { className?: string }) {
  const basis = useBacktestBasis();
  const pill = (value: BacktestBasis, label: string) => (
    <button
      type="button"
      aria-pressed={basis === value}
      onClick={() => setBacktestBasis(value)}
      className={clsx(
        "min-h-8 px-2.5 text-xs",
        basis === value ? "bg-accentSky/20 font-semibold text-accentSky" : "text-slate-300 hover:bg-white/5",
      )}
    >
      {label}
    </button>
  );
  return (
    <span
      role="group"
      aria-label="P&L before or after charges"
      title="Every backtest result on this browser: P&L before charges, or after Groww's estimated charges"
      className={clsx("inline-flex overflow-hidden rounded-md ring-1 ring-inset ring-white/15", className)}
    >
      {pill("gross", "Before charges")}
      {pill("net", "After charges")}
    </span>
  );
}
