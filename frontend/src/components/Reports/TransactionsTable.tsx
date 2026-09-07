"use client";

import { Fragment, useEffect, useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { api, type Transaction } from "@/lib/api";
import { duration, money, num, pct, pnlClass, timestamp } from "@/lib/format";

const OUTCOME_STYLES: Record<string, string> = {
  WIN: "bg-profit/15 text-profit",
  LOSS: "bg-loss/15 text-loss",
  BREAKEVEN: "bg-slate-700/40 text-slate-400",
  OPEN: "bg-bot/15 text-bot",
  VOID: "bg-amber-500/15 text-amber-400",
};

function Field({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("font-mono text-xs mt-0.5", tone ?? "text-slate-200")}>{value}</div>
    </div>
  );
}

function ExpandedDetail({ trade }: { trade: Transaction }) {
  const [analyses, setAnalyses] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getTransaction(trade.id)
      .then((d) => !cancelled && setAnalyses(d.ai_analyses))
      .catch(() => !cancelled && setAnalyses([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [trade.id]);

  return (
    <div className="bg-base/60 px-4 py-3 space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-x-4 gap-y-3">
        <Field label="Opened" value={timestamp(trade.opened_at)} />
        <Field label="Closed" value={timestamp(trade.closed_at)} />
        <Field label="Holding" value={duration(trade.holding_sec)} />
        <Field label="Turnover" value={money(trade.turnover)} />
        <Field label="Stop Loss" value={money(trade.stop_loss)} />
        <Field label="Target" value={money(trade.target)} />

        <Field label="Risk / Share" value={money(trade.risk_per_share)} />
        <Field label="Planned Risk" value={money(trade.planned_risk)} tone="text-loss" />
        <Field label="Planned Reward" value={money(trade.planned_reward)} tone="text-profit" />
        <Field label="Planned R:R" value={trade.planned_rr !== null ? `1:${num(trade.planned_rr)}` : "—"} />
        <Field
          label="R Multiple"
          value={trade.r_multiple !== null ? `${num(trade.r_multiple)}R` : "—"}
          tone={pnlClass(trade.r_multiple)}
        />
        <Field
          label="Return on Turnover"
          value={pct(trade.return_on_turnover_pct, 3)}
          tone={pnlClass(trade.return_on_turnover_pct)}
        />

        <Field label="Gross P&L" value={money(trade.gross_pnl, true)} tone={pnlClass(trade.gross_pnl)} />
        <Field
          label="Entry Charges"
          value={trade.charges_recorded ? money(trade.entry_charges) : "not recorded"}
          tone={trade.charges_recorded ? undefined : "text-slate-500"}
        />
        <Field
          label="Exit Charges"
          value={trade.charges_recorded ? money(trade.exit_charges) : "not recorded"}
          tone={trade.charges_recorded ? undefined : "text-slate-500"}
        />
        <Field
          label="Cost Drag"
          value={trade.charges_drag_pct !== null ? `${pct(trade.charges_drag_pct)} of gross` : "—"}
          tone={trade.charges_drag_pct !== null && trade.charges_drag_pct > 25 ? "text-amber-400" : undefined}
        />
        <Field label="Net P&L" value={money(trade.net_pnl, true)} tone={pnlClass(trade.net_pnl)} />
        <Field label="Exit Reason" value={trade.exit_reason ?? "—"} />
      </div>

      {trade.status === "OPEN" && (
        <div className="text-xs text-bot">
          Still open at {money(trade.ltp)} — unrealised{" "}
          <span className={pnlClass(trade.unrealised_pnl)}>{money(trade.unrealised_pnl, true)}</span>
        </div>
      )}

      <div className="border-t border-border pt-2.5">
        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1.5">AI expert view on this trade</div>
        {loading ? (
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Loader2 size={12} className="animate-spin" /> loading…
          </div>
        ) : analyses && analyses.length > 0 ? (
          <div className="space-y-2">
            {analyses.map((a) => (
              <div key={a.id} className="rounded-md border border-border bg-surface p-2.5 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap text-[11px]">
                  <span
                    className={clsx(
                      "px-1.5 py-0.5 rounded",
                      a.stance === "BULLISH"
                        ? "bg-profit/15 text-profit"
                        : a.stance === "BEARISH"
                        ? "bg-loss/15 text-loss"
                        : "bg-slate-700/40 text-slate-400"
                    )}
                  >
                    {a.stance}
                  </span>
                  <span className="text-slate-400">conviction {a.conviction}</span>
                  <span className="text-slate-400">sentiment {a.sentiment_label}</span>
                  <span className="text-slate-600">{a.requested_by}</span>
                  {a.gate_passed !== null && (
                    <span className={a.gate_passed ? "text-profit" : "text-loss"}>
                      gate {a.gate_passed ? "passed" : "blocked"}
                    </span>
                  )}
                </div>
                {a.gate_reason && <div className="text-[11px] text-slate-500">{a.gate_reason}</div>}
                <div className="text-xs text-slate-300 leading-relaxed">{a.thesis}</div>
                {a.sources?.length > 0 && (
                  <div className="flex flex-wrap gap-2 text-[10px]">
                    {a.sources.slice(0, 5).map((s: any) => (
                      <a
                        key={s.url}
                        href={s.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-bot hover:underline flex items-center gap-0.5"
                      >
                        {s.title.slice(0, 48)} <ExternalLink size={9} />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-500">
            No expert view was recorded for this trade — it was taken without the AI gate.
          </div>
        )}
      </div>
    </div>
  );
}

export function TransactionsTable({ transactions }: { transactions: Transaction[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div className="rounded-card border border-border bg-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2 flex-wrap">
        <div className="text-sm font-medium text-slate-200">
          Transactions <span className="text-xs text-slate-500">({transactions.length})</span>
        </div>
        <div className="text-[11px] text-slate-500">Click any row for the full breakdown</div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] text-slate-500 border-b border-border">
              <th className="px-2 py-2 w-6" />
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Closed (IST)</th>
              <th className="px-3 py-2 font-medium">Symbol</th>
              <th className="px-3 py-2 font-medium">Side</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2 font-medium text-right">Qty</th>
              <th className="px-3 py-2 font-medium text-right">Entry</th>
              <th className="px-3 py-2 font-medium text-right">Amount</th>
              <th className="px-3 py-2 font-medium text-right">Exit</th>
              <th className="px-3 py-2 font-medium text-right">SL</th>
              <th className="px-3 py-2 font-medium text-right">Target</th>
              <th className="px-3 py-2 font-medium text-right">Charges</th>
              <th className="px-3 py-2 font-medium text-right">R</th>
              <th className="px-3 py-2 font-medium text-right">Hold</th>
              <th className="px-3 py-2 font-medium text-right">Net P&amp;L</th>
              <th className="px-3 py-2 font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {transactions.length === 0 && (
              <tr>
                <td colSpan={17} className="px-4 py-8 text-center text-sm text-slate-500">
                  No transactions match these filters.
                </td>
              </tr>
            )}
            {transactions.map((t) => {
              const open = expanded === t.id;
              return (
                // Two sibling rows in ONE table, rather than a nested table per
                // row — nesting would let every row size its own columns and
                // nothing would line up with the header.
                <Fragment key={t.id}>
                  <tr
                    onClick={() => setExpanded(open ? null : t.id)}
                    className={clsx(
                      "border-b border-border/50 cursor-pointer hover:bg-white/[0.03] transition",
                      open && "bg-white/[0.03] border-b-0"
                    )}
                  >
                    <td className="px-2 py-2 w-6 text-slate-500">
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-600">{t.id}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">
                      {timestamp(t.closed_at ?? t.opened_at)}
                    </td>
                    <td className="px-3 py-2 text-slate-100">{t.symbol}</td>
                    <td className={clsx("px-3 py-2 text-xs", t.side === "BUY" ? "text-profit" : "text-loss")}>
                      {t.side}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-slate-400">{t.source}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right">{t.quantity}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right">{num(t.entry_price)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-slate-400">{money(t.turnover)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-right">
                      {t.exit_price !== null ? num(t.exit_price) : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-loss">
                      {t.stop_loss ? num(t.stop_loss) : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-profit">
                      {t.target ? num(t.target) : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-slate-400">
                      {t.charges_recorded ? money(t.charges) : "—"}
                    </td>
                    <td className={clsx("px-3 py-2 font-mono text-xs text-right", pnlClass(t.r_multiple))}>
                      {t.r_multiple !== null ? `${num(t.r_multiple)}R` : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-right text-slate-400">
                      {duration(t.holding_sec)}
                    </td>
                    <td className={clsx("px-3 py-2 font-mono text-right", pnlClass(t.net_pnl ?? t.unrealised_pnl))}>
                      {t.status === "OPEN" ? money(t.unrealised_pnl, true) : money(t.net_pnl, true)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={clsx(
                          "text-[10px] px-1.5 py-0.5 rounded",
                          OUTCOME_STYLES[t.outcome] ?? "bg-slate-700/40 text-slate-400"
                        )}
                      >
                        {t.outcome}
                      </span>
                    </td>
                  </tr>
                  {open && (
                    <tr className="border-b border-border/50">
                      <td colSpan={17} className="p-0">
                        <ExpandedDetail trade={t} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
