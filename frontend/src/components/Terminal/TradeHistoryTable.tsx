"use client";

import clsx from "clsx";
import { smaApi, inr, px, type SmaState, type TradeRow } from "@/lib/smaApi";

const REASON: Record<string, string> = {
  MA_CROSS: "MA CROSS",
  ATR_SL_HIT: "ATR SL HIT",
  EOD_SQUARE_OFF: "EOD SQUARE-OFF",
  KILL_SWITCH: "KILL SWITCH",
};

export function TradeHistoryTable({ trades, state }: { trades: TradeRow[]; state: SmaState | null }) {
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
                "Stock",
                "Side",
                "Executed",
                "Market",
                "P&L",
                "ATR",
                "SL",
                "Exit",
                "Trigger",
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
                <td colSpan={12} className="px-3 py-8 text-center text-slate-500">
                  No closed trades yet. Crossovers are judged on closed 1-minute candles only.
                </td>
              </tr>
            )}
            {trades.map((t) => {
              const market = marketPrice(t, state);
              const live = markPnl(t, market);
              const pnl = t.exit_price == null ? live?.pnl ?? null : t.gross_pnl;
              return (
              <tr key={t.id} className="border-b border-white/5 text-slate-200">
                <td className="px-3 py-2 font-mono text-slate-500">{t.id}</td>
                <td className="px-3 py-2">
                  <div className="font-medium text-slate-100">{t.symbol}</div>
                  <div className={clsx("font-mono", pnl == null ? "text-slate-500" : pnl >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
                    {pnl == null ? "P&L —" : inr(pnl)}
                  </div>
                </td>
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
                  {px(t.entry_price)}
                  <div className="text-slate-500">{fmtTime(t.entry_time)}</div>
                </td>
                <td className="px-3 py-2 font-mono">
                  {market == null ? "—" : px(market)}
                  <div className="text-slate-500">{t.exit_price == null ? "live" : "exit"}</div>
                </td>
                <td className={clsx("px-3 py-2 font-mono", pnl == null ? "text-slate-500" : pnl >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
                  {pnl == null ? "—" : inr(pnl)}
                  {live && (
                    <div className="text-slate-500">
                      {live.points >= 0 ? "+" : ""}
                      {live.points.toFixed(2)} pts
                    </div>
                  )}
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
                <td className="px-3 py-2 font-mono text-[#F59E0B]">
                  {t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)}
                </td>
                <td className={clsx("px-3 py-2 font-mono", (t.net_pnl ?? 0) >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
                  {t.net_pnl == null ? "—" : inr(t.net_pnl)}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function marketPrice(trade: TradeRow, state: SmaState | null): number | null {
  if (trade.exit_price != null && trade.exit_price > 0) return trade.exit_price;
  const symbol = trade.symbol?.toUpperCase();
  const book = state?.books?.find((row) => row.symbol.toUpperCase() === symbol);
  if (book?.ltp != null && book.ltp > 0) return book.ltp;
  if (state && state.symbol?.toUpperCase() === symbol && state.ltp > 0) return state.ltp;
  if (trade.market_price != null && trade.market_price > 0) return trade.market_price;
  return null;
}

/** Long P&L is market minus the fill. Short P&L is the fill minus market. */
export function markPnl(trade: TradeRow, market: number | null): { points: number; pnl: number } | null {
  if (market == null || !Number.isFinite(market) || !Number.isFinite(trade.entry_price)) return null;
  const points = trade.direction === "LONG" ? market - trade.entry_price : trade.entry_price - market;
  return { points, pnl: points * trade.qty };
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
