"use client";

import clsx from "clsx";
import { TrendingUp, Target, BarChart3, TrendingDown, Wallet } from "lucide-react";

type Props = {
  totalPnl: number;
  winRatePct: number;
  profitFactor: number;
  maxDrawdown: number;
  capitalDeployed: number;
};

export function MetricCards({ totalPnl, winRatePct, profitFactor, maxDrawdown, capitalDeployed }: Props) {
  const cards = [
    {
      label: "Today's P&L",
      value: `${totalPnl >= 0 ? "+" : ""}₹${totalPnl.toFixed(2)}`,
      icon: TrendingUp,
      tone: totalPnl >= 0 ? "profit" : "loss",
    },
    { label: "Win Rate", value: `${winRatePct.toFixed(1)}%`, icon: Target, tone: "bot" },
    { label: "Profit Factor", value: profitFactor.toFixed(2), icon: BarChart3, tone: "bot" },
    { label: "Max Drawdown", value: `₹${maxDrawdown.toFixed(2)}`, icon: TrendingDown, tone: "loss" },
    { label: "Capital Deployed", value: `₹${capitalDeployed.toFixed(0)}`, icon: Wallet, tone: "bot" },
  ] as const;

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-slate-400">{c.label}</span>
            <c.icon
              size={15}
              className={clsx(c.tone === "profit" && "text-profit", c.tone === "loss" && "text-loss", c.tone === "bot" && "text-bot")}
            />
          </div>
          <div
            className={clsx(
              "text-lg font-mono font-semibold",
              c.tone === "profit" && "text-profit",
              c.tone === "loss" && "text-loss",
              c.tone === "bot" && "text-slate-100"
            )}
          >
            {c.value}
          </div>
        </div>
      ))}
    </div>
  );
}
