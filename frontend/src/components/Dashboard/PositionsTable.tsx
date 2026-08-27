"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import type { Position, Tick } from "@/hooks/useTradingState";

type Props = {
  positions: Position[];
  ticks: Record<string, Tick>;
  onClose: (symbol: string) => void;
};

export function PositionsTable({ positions, ticks, onClose }: Props) {
  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-border text-sm font-medium text-slate-200">Active Positions</div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400 border-b border-border">
              <th className="px-4 py-2 font-medium">Symbol</th>
              <th className="px-4 py-2 font-medium">Side</th>
              <th className="px-4 py-2 font-medium">Qty</th>
              <th className="px-4 py-2 font-medium">Entry</th>
              <th className="px-4 py-2 font-medium">LTP</th>
              <th className="px-4 py-2 font-medium">P&amp;L</th>
              <th className="px-4 py-2 font-medium">SL</th>
              <th className="px-4 py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-6 text-center text-slate-500 text-sm">
                  No open positions.
                </td>
              </tr>
            )}
            {positions.map((p) => {
              const ltp = ticks[p.symbol]?.ltp ?? p.entry_price;
              const pnl = p.side === "BUY" ? (ltp - p.entry_price) * p.quantity : (p.entry_price - ltp) * p.quantity;
              return (
                <tr key={p.symbol} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-2 font-medium text-slate-100">{p.symbol}</td>
                  <td className={clsx("px-4 py-2", p.side === "BUY" ? "text-profit" : "text-loss")}>{p.side}</td>
                  <td className="px-4 py-2 font-mono">{p.quantity}</td>
                  <td className="px-4 py-2 font-mono">{p.entry_price.toFixed(2)}</td>
                  <td className="px-4 py-2 font-mono">{ltp.toFixed(2)}</td>
                  <td className={clsx("px-4 py-2 font-mono", pnl >= 0 ? "text-profit" : "text-loss")}>
                    {pnl >= 0 ? "+" : ""}
                    {pnl.toFixed(2)}
                  </td>
                  <td className="px-4 py-2 font-mono text-loss">{p.stop_loss.toFixed(2)}</td>
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
