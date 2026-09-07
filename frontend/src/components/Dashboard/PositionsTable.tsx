"use client";

import clsx from "clsx";
import { Layers, X } from "lucide-react";
import type { Position, Tick } from "@/hooks/useTradingState";
import { EmptyState } from "@/components/ui";
import { money } from "@/lib/format";

type Props = {
  positions: Position[];
  ticks: Record<string, Tick>;
  onClose: (symbol: string) => void;
};

export function PositionsTable({ positions, ticks, onClose }: Props) {
  return (
    <div className="overflow-hidden rounded-card border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <span className="text-section text-slate-100">Active positions</span>
        {positions.length > 0 && (
          <span className="rounded-full bg-accentTeal/15 px-1.5 py-0.5 text-caption font-medium text-accentTeal">
            {positions.length}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-white/[0.03] text-left text-caption uppercase text-slate-500">
              <th className="px-4 py-2 font-medium">Symbol</th>
              <th className="px-4 py-2 font-medium">Side</th>
              <th className="px-4 py-2 font-medium">Qty</th>
              <th className="px-4 py-2 font-medium">Entry</th>
              <th className="px-4 py-2 font-medium">Amount</th>
              <th className="px-4 py-2 font-medium">LTP</th>
              <th className="px-4 py-2 font-medium">P&amp;L</th>
              <th className="px-4 py-2 font-medium">SL</th>
              <th className="px-4 py-2 font-medium">Target</th>
              <th className="px-4 py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 && (
              <tr>
                <td colSpan={10}>
                  <EmptyState
                    icon={<Layers size={20} />}
                    title="No open positions"
                    hint="Entries opened by the bot or the manual desk appear here with live P&L and their bracket levels."
                  />
                </td>
              </tr>
            )}
            {positions.map((p) => {
              const ltp = ticks[p.symbol]?.ltp ?? p.entry_price;
              const pnl = p.side === "BUY" ? (ltp - p.entry_price) * p.quantity : (p.entry_price - ltp) * p.quantity;
              const amount = p.entry_price * p.quantity;
              return (
                <tr key={p.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-white/[0.03]">
                  <td className="px-4 py-2 font-medium text-slate-100">{p.symbol}</td>
                  <td className="px-4 py-2">
                    <span
                      className={clsx(
                        "rounded px-1.5 py-0.5 text-caption font-semibold",
                        p.side === "BUY" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
                      )}
                    >
                      {p.side}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono">{p.quantity}</td>
                  <td className="px-4 py-2 font-mono">{p.entry_price.toFixed(2)}</td>
                  <td className="px-4 py-2 font-mono text-slate-300">{money(amount)}</td>
                  <td className="px-4 py-2 font-mono">{ltp.toFixed(2)}</td>
                  <td className={clsx("px-4 py-2 font-mono", pnl >= 0 ? "text-profit" : "text-loss")}>
                    {pnl >= 0 ? "+" : ""}
                    {pnl.toFixed(2)}
                  </td>
                  <td className="px-4 py-2 font-mono text-loss">{p.stop_loss ? p.stop_loss.toFixed(2) : "—"}</td>
                  <td className="px-4 py-2 font-mono text-profit">{p.target ? p.target.toFixed(2) : "—"}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => onClose(p.symbol)}
                      className="p-1.5 rounded-md hover:bg-loss/10 text-slate-400 hover:text-loss transition"
                      title="Close position"
                    >
                      <X size={14} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
