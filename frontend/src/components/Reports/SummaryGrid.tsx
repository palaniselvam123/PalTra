"use client";

import clsx from "clsx";
import { Info } from "lucide-react";
import type { ReportSummary } from "@/lib/api";
import { duration, money, num, pct, pnlClass } from "@/lib/format";

function Tile({
  label,
  value,
  sub,
  tone,
  hint,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-card border border-border bg-surface px-3 py-2.5" title={hint}>
      <div className="text-[11px] text-slate-500 flex items-center gap-1">
        {label}
        {hint && <Info size={10} className="text-slate-600" />}
      </div>
      <div className={clsx("font-mono text-base mt-0.5", tone ?? "text-slate-100")}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export function SummaryGrid({ summary }: { summary: ReportSummary }) {
  const chargesMissing = summary.charges_coverage.startsWith("0/") && summary.trades_closed > 0;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
        <Tile
          label="Net P&L"
          value={money(summary.net_pnl, true)}
          tone={pnlClass(summary.net_pnl)}
          sub={`${summary.trades_closed} closed · ${summary.trades_open} open`}
        />
        <Tile
          label="Win Rate"
          value={pct(summary.win_rate_pct)}
          sub={`${summary.wins}W / ${summary.losses}L / ${summary.breakeven}BE`}
        />
        <Tile
          label="Profit Factor"
          value={num(summary.profit_factor)}
          tone={summary.profit_factor >= 1 ? "text-profit" : "text-loss"}
          hint="Gross profit ÷ gross loss. Below 1.0 means the strategy loses money."
        />
        <Tile
          label="Expectancy"
          value={money(summary.expectancy, true)}
          tone={pnlClass(summary.expectancy)}
          sub={summary.expectancy_r !== null ? `${num(summary.expectancy_r)}R per trade` : undefined}
          hint="Average rupees expected per trade at this win rate and payoff."
        />
        <Tile
          label="Max Drawdown"
          value={money(-summary.max_drawdown)}
          tone={summary.max_drawdown > 0 ? "text-loss" : "text-slate-400"}
          sub={`${pct(summary.max_drawdown_pct, 2)} of capital`}
          hint="Largest peak-to-trough fall of the cumulative P&L curve within this filter."
        />
        <Tile
          label="Payoff Ratio"
          value={summary.payoff_ratio !== null ? num(summary.payoff_ratio) : "—"}
          sub={`avg win ${money(summary.avg_win)} / avg loss ${money(summary.avg_loss)}`}
          hint="Average win ÷ average loss."
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
        <Tile label="Gross Profit" value={money(summary.gross_profit)} tone="text-profit" />
        <Tile label="Gross Loss" value={money(-summary.gross_loss)} tone="text-loss" />
        <Tile label="Largest Win" value={money(summary.largest_win, true)} tone="text-profit" />
        <Tile label="Largest Loss" value={money(summary.largest_loss, true)} tone="text-loss" />
        <Tile
          label="Streaks"
          value={`${summary.max_win_streak}W · ${summary.max_loss_streak}L`}
          sub="longest consecutive"
        />
        <Tile label="Avg Hold" value={duration(summary.avg_holding_sec)} sub={`turnover ${money(summary.total_turnover)}`} />
      </div>

      {chargesMissing ? (
        <div className="text-[11px] text-amber-400/90 bg-amber-500/10 border border-amber-500/25 rounded-md px-3 py-2">
          Charges were not recorded for any of these {summary.trades_closed} trades — they closed before per-leg
          costs were stored. Their net P&amp;L is still after costs; only the cost breakdown is unavailable, so
          the total below reads ₹0 rather than a measured figure.
        </div>
      ) : (
        <div className="text-[11px] text-slate-500">
          Total charges {money(summary.total_charges)} across {summary.charges_coverage} closed trades with per-leg
          costs recorded.
          {summary.unrealised_open !== 0 && (
            <>
              {" "}
              Open positions carry{" "}
              <span className={pnlClass(summary.unrealised_open)}>{money(summary.unrealised_open, true)}</span>{" "}
              unrealised.
            </>
          )}
        </div>
      )}
    </div>
  );
}
