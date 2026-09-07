"use client";

import Link from "next/link";
import clsx from "clsx";
import { ArrowRight, FileBarChart } from "lucide-react";
import { money } from "@/lib/format";

export type ClosedTrade = {
  id: number;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss?: number | null;
  target?: number | null;
  amount?: number | null;
  pnl: number | null;
  closed_at: string | null;
};

export function TradeHistory({ trades }: { trades: ClosedTrade[] }) {
  return (
    <div className="overflow-hidden rounded-card border border-border bg-surface">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-section text-slate-100">Trade history</span>
          <span className="rounded-full bg-accentViolet/15 px-1.5 py-0.5 text-caption font-medium text-accentViolet">
            {trades.length}
          </span>
        </div>
        <Link
          href="/reports"
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-bot/15 text-bot text-[11px] font-medium hover:bg-bot/25 transition"
        >
          <FileBarChart size={12} /> Detailed Report
          <ArrowRight size={11} />
        </Link>
      </div>
      <div className="overflow-x-auto max-h-72 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface">
            <tr className="text-left text-xs text-slate-400 border-b border-border">
              <th className="px-4 py-2 font-medium">Time</th>
              <th className="px-4 py-2 font-medium">Symbol</th>
              <th className="px-4 py-2 font-medium">Side</th>
              <th className="px-4 py-2 font-medium">Qty</th>
              <th className="px-4 py-2 font-medium">Entry</th>
              <th className="px-4 py-2 font-medium">Amount</th>
              <th className="px-4 py-2 font-medium">Exit</th>
              <th className="px-4 py-2 font-medium">SL</th>
              <th className="px-4 py-2 font-medium">Target</th>
              <th className="px-4 py-2 font-medium text-right">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-6 text-center text-slate-500 text-sm">
                  No closed trades yet.
                </td>
              </tr>
            )}
            {trades.map((t) => (
              <tr key={t.id} className="border-b border-border/60 last:border-0">
                <td className="px-4 py-2 font-mono text-xs text-slate-500">
                  {t.closed_at ? (
                    <span className="whitespace-nowrap">
                      {new Date(t.closed_at + "Z").toLocaleDateString("en-IN", {
                        day: "2-digit",
                        month: "short",
                      })}{" "}
                      <span className="text-slate-400">
                        {new Date(t.closed_at + "Z").toLocaleTimeString("en-IN", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                          hour12: false,
                        })}
                      </span>
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-2 text-slate-100">{t.symbol}</td>
                <td className={clsx("px-4 py-2", t.side === "BUY" ? "text-profit" : "text-loss")}>{t.side}</td>
                <td className="px-4 py-2 font-mono">{t.quantity}</td>
                <td className="px-4 py-2 font-mono">{t.entry_price.toFixed(2)}</td>
                <td className="px-4 py-2 font-mono text-slate-300">
                  {money(t.amount ?? t.entry_price * t.quantity)}
                </td>
                <td className="px-4 py-2 font-mono">{t.exit_price?.toFixed(2) ?? "—"}</td>
                <td className="px-4 py-2 font-mono text-loss">{t.stop_loss ? t.stop_loss.toFixed(2) : "—"}</td>
                <td className="px-4 py-2 font-mono text-profit">{t.target ? t.target.toFixed(2) : "—"}</td>
                <td className={clsx("px-4 py-2 font-mono text-right", (t.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                  {(t.pnl ?? 0) >= 0 ? "+" : ""}
                  {(t.pnl ?? 0).toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
