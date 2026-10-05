"use client";

import clsx from "clsx";
import { inr, px } from "@/lib/smaApi";
import { Badge, SideBadge } from "./ui";

export type StockCardData = {
  symbol: string;
  ltp: number | null;
  /** Only the chart stock has a day change in the API. */
  changePct: number | null;
  side: "LONG" | "SHORT" | "FLAT";
  qty: number;
  note: string;
  stopOff: boolean;
  armed: boolean;
  onChart: boolean;
  /** Today's P&L of this stock, sent for every stock, not only the chart's. */
  openNet?: number | null;
  closedNet?: number;
  closedTrades?: number;
  dayNet?: number;
  /** Groww's last refusal today, e.g. "Add ₹20092.46 to your Groww Balance". */
  reject?: string | null;
  rejectAt?: string | null;
};

function Money({ value }: { value: number }) {
  return <span className={value >= 0 ? "text-emerald-400" : "text-rose-400"}>{inr(value)}</span>;
}

type Props = {
  stock: StockCardData;
  busy: boolean;
  armLimitReached: boolean;
  onToggleArmed: () => void;
  onShowOnChart: () => void;
};

export function StockCard({ stock, busy, armLimitReached, onToggleArmed, onShowOnChart }: Props) {
  const { symbol, ltp, changePct, side, qty, note, stopOff, armed, onChart, openNet, closedNet, closedTrades, dayNet, reject, rejectAt } =
    stock;
  const traded = openNet != null || (closedTrades ?? 0) > 0;
  const cannotArm = !armed && armLimitReached;
  return (
    <li
      className={clsx(
        "flex min-w-0 flex-col gap-2 rounded-lg bg-[#151921] p-3 ring-inset",
        onChart ? "ring-2 ring-sky-400/60" : armed ? "ring-1 ring-emerald-400/35" : "ring-1 ring-white/10"
      )}
    >
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[15px] font-semibold tracking-wide text-amber-300" title={symbol}>
          {symbol}
        </span>
        <span className="flex shrink-0 items-baseline gap-2 font-mono">
          <span className="text-sm text-slate-100">{px(ltp)}</span>
          <span
            className={clsx(
              "text-xs",
              changePct == null ? "text-slate-400" : changePct >= 0 ? "text-emerald-400" : "text-rose-400"
            )}
            title={changePct == null ? "Day change is sent only for the stock on the chart" : "Change since the previous close"}
          >
            {changePct == null ? "—" : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
          </span>
        </span>
      </div>

      <div className="flex min-w-0 items-center gap-2">
        <SideBadge side={side} />
        {side !== "FLAT" && qty > 0 ? <span className="shrink-0 font-mono text-xs text-slate-300">{qty}</span> : null}
        {side !== "FLAT" && stopOff ? <Badge color="amber">No stop</Badge> : null}
        <span className="min-w-0 truncate text-xs text-slate-400" title={note || undefined}>
          {note || "No note yet"}
        </span>
      </div>

      {reject && (
        <div
          role="alert"
          className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-xs text-rose-200"
          title={reject}
        >
          <span className="font-semibold">Order refused{rejectAt ? ` ${rejectAt}` : ""}:</span> {reject}
        </div>
      )}

      {traded && (
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-xs text-slate-400">
          {openNet != null && (
            <span>
              Open <Money value={openNet} />
            </span>
          )}
          {(closedTrades ?? 0) > 0 && (
            <span>
              Closed <Money value={closedNet ?? 0} /> ({closedTrades})
            </span>
          )}
          <span className="font-semibold">
            Today <Money value={dayNet ?? (closedNet ?? 0) + (openNet ?? 0)} />
          </span>
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={armed}
          disabled={busy || cannotArm}
          onClick={onToggleArmed}
          title={
            armed
              ? "The bot may order this stock. Switch off to stop new orders on it."
              : cannotArm
                ? "24 stocks are already armed. Switch one off first."
                : "Let the bot order this stock on its next SMA cross"
          }
          className="flex min-h-11 items-center gap-2 rounded-md pr-2 text-sm text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span
            aria-hidden
            className={clsx(
              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
              armed ? "bg-emerald-500" : "bg-slate-500"
            )}
          >
            <span
              className={clsx(
                "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform",
                armed ? "translate-x-[22px]" : "translate-x-0.5"
              )}
            />
          </span>
          <span className={clsx("whitespace-nowrap", armed ? "font-semibold text-emerald-300" : "text-slate-300")}>
            {armed ? "Armed for trading" : "Not armed"}
          </span>
        </button>
        <button
          type="button"
          disabled={busy || onChart}
          onClick={onShowOnChart}
          className={clsx(
            "min-h-11 shrink-0 rounded-md px-3 text-xs font-semibold",
            onChart
              ? "cursor-default bg-sky-500/20 text-sky-200"
              : "text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
          )}
        >
          {onChart ? "On chart" : "Show on chart"}
        </button>
      </div>
    </li>
  );
}
