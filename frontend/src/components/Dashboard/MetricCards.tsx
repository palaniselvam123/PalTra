"use client";

import clsx from "clsx";
import { Layers, Scale, Target, TrendingDown } from "lucide-react";
import type { ReactNode } from "react";
import { Label } from "@/components/ui";
import { AreaSpark, Meter, MiniBars, Ring, SplitBar } from "@/components/ui/viz";
import type { ClosedTrade } from "@/components/Dashboard/TradeHistory";

type Props = {
  totalPnl: number;
  winRatePct: number;
  profitFactor: number;
  maxDrawdown: number;
  capitalDeployed: number;
  /** Drives the equity curve and the per-trade bars. */
  history?: ClosedTrade[];
  accountCapital?: number;
  byFeed?: Record<string, { pnl: number; trades: number; win_rate_pct: number }>;
};

const inr = (n: number, dp = 2) =>
  `₹${n.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;

/**
 * The dashboard's headline row.
 *
 * The previous version was five identical grey boxes, which gave the page no
 * focal point and left a lot of colour-carrying information undrawn. Now:
 *
 *  - P&L is a hero panel with the actual cumulative equity curve behind it,
 *    so the number has a shape as well as a value.
 *  - Each supporting metric owns a categorical accent and a micro-visual: a
 *    win/loss split bar, a profit-factor meter, a drawdown series, an
 *    exposure ring. The colour is the data, not decoration.
 *  - Green and red still mean exactly one thing — direction of money.
 *    The accents (violet, sky, amber, indigo) are identity only.
 */
export function MetricCards({
  totalPnl,
  winRatePct,
  profitFactor,
  maxDrawdown,
  capitalDeployed,
  history = [],
  accountCapital = 100_000,
  byFeed,
}: Props) {
  // Oldest → newest, so the curve reads left to right like every other chart.
  const closed = [...history].filter((t) => t.pnl !== null).reverse();
  const pnls = closed.map((t) => t.pnl as number);

  const equity: number[] = [];
  pnls.reduce((acc, p) => {
    const next = acc + p;
    equity.push(next);
    return next;
  }, 0);

  // Real drawdown: distance below the running peak, not the equity curve
  // tinted red. Drawing equity here would have shown a rising line on a tile
  // labelled "max drawdown", which is worse than drawing nothing.
  const drawdown: number[] = [];
  equity.reduce((peak, v) => {
    const nextPeak = Math.max(peak, v);
    drawdown.push(v - nextPeak); // <= 0
    return nextPeak;
  }, 0);

  const wins = pnls.filter((p) => p > 0).length;
  const losses = pnls.filter((p) => p < 0).length;
  const up = totalPnl >= 0;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* ---- Hero: the number this page exists for ---------------------- */}
      <div
        className={clsx(
          "relative overflow-hidden rounded-card border p-5",
          up ? "border-profit/25" : "border-loss/25"
        )}
      >
        {/* Tinted wash + curve sit behind the content, not on it, so the
            figures keep full contrast in both themes. */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.55]"
          style={{
            background: up
              ? "linear-gradient(135deg, rgb(var(--c-profit)/0.18), rgb(var(--c-teal)/0.10) 48%, rgb(var(--c-bot)/0.12))"
              : "linear-gradient(135deg, rgb(var(--c-loss)/0.18), rgb(var(--c-pink)/0.10) 48%, rgb(var(--c-violet)/0.12))",
          }}
        />
        <div className="absolute inset-x-0 bottom-0 opacity-70">
          {equity.length > 1 && <AreaSpark values={equity} height={72} showDot={false} />}
        </div>

        <div className="relative">
          <div className="flex items-center justify-between">
            <Label>Today&apos;s P&amp;L</Label>
            <span
              className={clsx(
                "rounded-full px-2 py-0.5 text-caption font-medium",
                up ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
              )}
            >
              {up ? "▲" : "▼"} {accountCapital ? ((totalPnl / accountCapital) * 100).toFixed(2) : "0.00"}%
            </span>
          </div>

          <div
            className={clsx(
              "mt-1.5 font-mono text-display tabular-nums",
              up ? "text-profit" : "text-loss"
            )}
          >
            {up ? "+" : ""}
            {inr(totalPnl)}
          </div>

          <div className="mt-3 flex items-center gap-4">
            <span className="text-caption tracking-normal text-slate-500">
              {closed.length > 0
                ? `curve: cumulative closed P&L · ${wins}W / ${losses}L`
                : "no closed trades yet"}
            </span>
            {pnls.length > 0 && (
              <div className="ml-auto w-24">
                <MiniBars values={pnls.slice(-20)} height={26} />
              </div>
            )}
          </div>

          {/* Simulated and live P&L are different currencies of information:
              one is a random walk, the other is the market. A blended figure
              can show an edge that does not exist, so they are never summed
              into a single headline without also being shown apart. */}
          {byFeed && (byFeed.simulated?.trades || byFeed.live?.trades) ? (
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-border/60 pt-2">
              {(["live", "simulated"] as const).map((k) => {
                const f = byFeed[k];
                if (!f || !f.trades) return null;
                return (
                  <span key={k} className="text-caption tracking-normal text-slate-500">
                    {k === "live" ? "NSE" : "Sim"}{" "}
                    <span
                      className={clsx(
                        "font-mono tabular-nums",
                        f.pnl > 0 ? "text-profit" : f.pnl < 0 ? "text-loss" : "text-slate-400"
                      )}
                    >
                      {f.pnl >= 0 ? "+" : ""}
                      {inr(f.pnl)}
                    </span>{" "}
                    <span className="text-slate-500">
                      · {f.trades} trade{f.trades > 1 ? "s" : ""}
                    </span>
                  </span>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      {/* ---- Supporting metrics, each with its own accent + micro-visual -- */}
      <div className="grid grid-cols-2 gap-4 lg:col-span-2 xl:grid-cols-4">
        <MetricTile
          icon={<Target size={14} />}
          accent="rgb(var(--c-violet))"
          label="Win rate"
          value={`${winRatePct.toFixed(1)}%`}
          footer={`${wins}W · ${losses}L`}
          visual={<SplitBar wins={wins} losses={losses} />}
        />

        <MetricTile
          icon={<Scale size={14} />}
          accent="rgb(var(--c-sky))"
          label="Profit factor"
          value={profitFactor.toFixed(2)}
          footer={profitFactor >= 1 ? "gross win ÷ loss" : "below 1.0 — losing"}
          // 3.0 is a generous ceiling; anything above it is exceptional and
          // pinning the meter there is honest rather than flattering.
          visual={<Meter value={Math.min(profitFactor, 3)} max={3} tone="rgb(var(--c-sky))" />}
        />

        <MetricTile
          icon={<TrendingDown size={14} />}
          accent="rgb(var(--c-loss))"
          label="Max drawdown"
          value={inr(maxDrawdown, 0)}
          footer="peak to trough"
          visual={
            drawdown.length > 1 ? (
              <AreaSpark values={drawdown} height={22} stroke="rgb(var(--c-loss))" showDot={false} />
            ) : (
              <Meter value={0} tone="rgb(var(--c-loss))" />
            )
          }
        />

        <MetricTile
          icon={<Layers size={14} />}
          accent="rgb(var(--c-indigo))"
          label="Deployed"
          value={inr(capitalDeployed, 0)}
          footer={`${((capitalDeployed / (accountCapital || 1)) * 100).toFixed(1)}% of capital`}
          right={
            <Ring
              value={capitalDeployed}
              max={accountCapital || 1}
              tone="rgb(var(--c-indigo))"
              size={38}
              label={`${Math.round((capitalDeployed / (accountCapital || 1)) * 100)}`}
            />
          }
        />
      </div>
    </div>
  );
}

function MetricTile({
  icon,
  accent,
  label,
  value,
  footer,
  visual,
  right,
}: {
  icon: ReactNode;
  accent: string;
  label: string;
  value: string;
  footer: string;
  visual?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="group relative overflow-hidden rounded-card border border-border bg-surface p-4 transition-colors hover:border-slate-500/40">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span
              className="grid h-5 w-5 shrink-0 place-items-center rounded"
              style={{ background: `color-mix(in srgb, ${accent} 16%, transparent)`, color: accent }}
            >
              {icon}
            </span>
            <Label>{label}</Label>
          </div>
          <div className="mt-1.5 truncate font-mono text-metric tabular-nums text-slate-100">{value}</div>
        </div>
        {right}
      </div>

      {visual && <div className="mt-2.5">{visual}</div>}
      <div className="mt-1.5 text-caption tracking-normal text-slate-400">{footer}</div>
    </div>
  );
}
