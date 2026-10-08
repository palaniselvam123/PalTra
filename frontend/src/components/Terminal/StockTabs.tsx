"use client";

import clsx from "clsx";
import { ExternalLink } from "lucide-react";
import { inr } from "@/lib/smaApi";

export type StockTab = {
  symbol: string;
  /** Net P&L of this stock's closed trades (and open one, live). */
  net?: number | null;
  trades?: number;
  /** The open position, if any. */
  side?: "LONG" | "SHORT" | null;
};

/**
 * One tab per stock the bot is watching or traded. Each opens that stock's
 * chart in a new browser tab, so several stocks can be followed side by side.
 */
export function StockTabs({
  tabs,
  active,
  hrefFor,
  label,
}: {
  tabs: StockTab[];
  active: string | null;
  hrefFor: (symbol: string) => string;
  label: string;
}) {
  if (tabs.length < 2) return null;
  return (
    <nav aria-label={label} className="mb-2 flex min-w-0 items-stretch gap-1.5 overflow-x-auto pb-1">
      <span className="shrink-0 self-center pr-1 text-xs uppercase tracking-wider text-slate-400">{label}</span>
      {tabs.map((tab) => {
        const on = active != null && tab.symbol.toUpperCase() === active.toUpperCase();
        const net = tab.net ?? null;
        return (
          <a
            key={tab.symbol}
            href={hrefFor(tab.symbol)}
            target="_blank"
            rel="noopener noreferrer"
            title={`Open ${tab.symbol}'s chart in a new tab`}
            aria-current={on ? "page" : undefined}
            className={clsx(
              "flex min-h-10 shrink-0 items-center gap-2 rounded-lg px-3 text-sm ring-1 ring-inset",
              on ? "bg-sky-500/15 font-semibold text-sky-100 ring-sky-400/50" : "bg-white/[0.03] text-slate-200 ring-white/10 hover:bg-white/5"
            )}
          >
            <span className="font-semibold text-amber-300">{tab.symbol}</span>
            {tab.side ? (
              <span
                className={clsx(
                  "rounded px-1 text-xs font-bold",
                  tab.side === "LONG" ? "bg-sky-500/20 text-sky-300" : "bg-violet-500/20 text-violet-300"
                )}
              >
                {tab.side === "LONG" ? "B" : "S"}
              </span>
            ) : null}
            {tab.trades ? <span className="text-xs text-slate-400">{tab.trades} tr</span> : null}
            {net != null && (tab.trades || tab.side) ? (
              <span className={clsx("font-mono text-xs", net > 0 ? "text-emerald-300" : net < 0 ? "text-rose-300" : "text-slate-400")}>
                {net > 0 ? "+" : ""}
                {inr(net)}
              </span>
            ) : null}
            <ExternalLink size={12} aria-hidden className="text-slate-400" />
          </a>
        );
      })}
    </nav>
  );
}

/** Each stock's trade count and net from a list of trades (one replay run, say). */
export function tradeTotals(rows: { symbol: string; net_pnl?: number | null }[]): Map<string, { net: number; trades: number }> {
  const out = new Map<string, { net: number; trades: number }>();
  for (const row of rows) {
    const name = row.symbol.toUpperCase();
    const cur = out.get(name) ?? { net: 0, trades: 0 };
    cur.net += Number(row.net_pnl ?? 0);
    cur.trades += 1;
    out.set(name, cur);
  }
  return out;
}

/** The address of one stock's chart page. */
export function chartHref(symbol: string, opts: { date?: string | null; runId?: number | null; desk?: string } = {}): string {
  const q = new URLSearchParams({ symbol: symbol.toUpperCase() });
  if (opts.date) q.set("date", opts.date);
  if (opts.runId != null) q.set("run", String(opts.runId));
  if (opts.desk && opts.desk !== "live") q.set("desk", opts.desk);
  return `/terminal/chart/?${q.toString()}`;
}
