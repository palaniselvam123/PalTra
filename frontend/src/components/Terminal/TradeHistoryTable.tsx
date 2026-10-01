"use client";

import clsx from "clsx";
import { smaApi, inr, px, type TradeRow } from "@/lib/smaApi";

const REASON: Record<string, string> = {
  MA_CROSS: "MA CROSS",
  ATR_SL_HIT: "ATR SL HIT",
  EOD_SQUARE_OFF: "EOD SQUARE-OFF",
  KILL_SWITCH: "KILL SWITCH",
};

export function TradeHistoryTable({ trades }: { trades: TradeRow[] }) {
  return (
    <section className="rounded-xl border border-white/5 bg-[#151921]">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-medium text-slate-200">Trade blotter</h2>
        <a
          href={smaApi.csvUrl()}
          className="rounded-md border border-white/10 px-2 py-1 text-xs text-slate-300 hover:bg-white/5"
        >
          Download CSV
        </a>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500">
            <tr className="border-y border-white/5">
              {[
                "#",
                "Side",
                "Entry",
                "ATR",
                "SL",
                "Exit",
                "Trigger",
                "Points",
                "Gross",
                "Charges",
                "Net",
              ].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr>
                <td colSpan={11} className="px-3 py-8 text-center text-slate-500">
                  No closed trades yet. Crossovers are judged on closed 1-minute candles only.
                </td>
              </tr>
            )}
            {trades.map((t) => (
              <tr key={t.id} className="border-b border-white/5 text-slate-200">
                <td className="px-3 py-2 font-mono text-slate-500">{t.id}</td>
                <td className="px-3 py-2">
                  <span
                    className={clsx(
                      "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                      t.direction === "LONG" ? "bg-[#10B981]/15 text-[#10B981]" : "bg-[#F43F5E]/15 text-[#F43F5E]"
                    )}
                  >
                    {t.direction}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono">
                  {fmtTime(t.entry_time)}
                  <div className="text-slate-400">{px(t.entry_price)}</div>
                </td>
                <td className="px-3 py-2 font-mono">{px(t.atr_at_entry)}</td>
                <td className="px-3 py-2 font-mono">{px(t.sl_trigger_price)}</td>
                <td className="px-3 py-2 font-mono">
                  {fmtTime(t.exit_time)}
                  <div className="text-slate-400">{t.exit_price == null ? "open" : px(t.exit_price)}</div>
                </td>
                <td className="px-3 py-2">
                  {t.exit_reason ? (
                    <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300">
                      {REASON[t.exit_reason] ?? t.exit_reason}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className={clsx("px-3 py-2 font-mono", (t.points ?? 0) >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
                  {t.points == null ? "—" : t.points.toFixed(2)}
                </td>
                <td className="px-3 py-2 font-mono">{t.gross_pnl == null ? "—" : inr(t.gross_pnl)}</td>
                <td className="px-3 py-2 font-mono text-[#F59E0B]">
                  {t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)}
                </td>
                <td className={clsx("px-3 py-2 font-mono", (t.net_pnl ?? 0) >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
                  {t.net_pnl == null ? "—" : inr(t.net_pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
