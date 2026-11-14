"use client";

import { BacktestCompare } from "@/components/Terminal/BacktestCompare";

/**
 * Compare backtest runs, opened from the terminal's Backtests tab (`?runs=12,15,18&base=12&basis=gross`). Read-only.
 */
export default function ComparePage() {
  return <BacktestCompare />;
}
