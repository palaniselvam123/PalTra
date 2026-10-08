"use client";

import clsx from "clsx";
import { LineChart, X } from "lucide-react";
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
  /** Which settings this stock sets for itself, e.g. "qty, RSI"; empty when it uses the shared ones. */
  ownStrategy?: string;
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
  /** Ticked for a bulk Unarm / Remove. */
  selected: boolean;
  onSelect: (checked: boolean) => void;
  /** Take the stock off this list (unarms it first). */
  onRemove: () => void;
};

export function StockCard({
  stock,
  busy,
  armLimitReached,
  onToggleArmed,
  onShowOnChart,
  selected,
  onSelect,
  onRemove,
}: Props) {
  const { symbol, ltp, changePct, side, qty, note, stopOff, armed, onChart, openNet, closedNet, closedTrades, dayNet, reject, rejectAt, ownStrategy } =
    stock;
  const traded = openNet != null || (closedTrades ?? 0) > 0;
  const cannotArm = !armed && armLimitReached;
  const today = dayNet ?? (closedNet ?? 0) + (openNet ?? 0);
  const pnlTitle = [
    openNet != null ? `Open ${inr(openNet)}` : null,
    (closedTrades ?? 0) > 0 ? `Closed ${inr(closedNet ?? 0)} (${closedTrades})` : null,
    `Today ${inr(today)}`,
  ]
    .filter(Boolean)
    .join(" · ");
  // One compact table row per stock (same controls and handlers as the old card).
  return (
    <>
      <tr
        className={clsx(
          "border-t border-white/5",
          onChart ? "bg-sky-500/[0.08]" : selected ? "bg-sky-500/[0.05]" : "hover:bg-white/[0.02]"
        )}
      >
        <td className="w-8 py-0 sm:w-10 sm:pl-1">
          <label className="flex h-11 w-8 cursor-pointer items-center justify-center sm:w-9" title={`Select ${symbol}`}>
            <input
              type="checkbox"
              checked={selected}
              onChange={(e) => onSelect(e.target.checked)}
              aria-label={`Select ${symbol}`}
              className="h-4 w-4 shrink-0 accent-sky-400"
            />
          </label>
        </td>
        <td className="max-w-[5.5rem] py-1 pr-1.5 sm:max-w-none sm:pr-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <span
              className={clsx("truncate text-sm font-semibold tracking-wide text-amber-300", onChart && "underline decoration-sky-400 underline-offset-4")}
              title={note ? `${symbol} · ${note}` : symbol}
            >
              {symbol}
            </span>
            {side === "FLAT" ? (
              <span className="hidden sm:inline-flex">
                <SideBadge side={side} />
              </span>
            ) : (
              <SideBadge side={side} />
            )}
            {side !== "FLAT" && qty > 0 ? <span className="shrink-0 font-mono text-xs text-slate-300">{qty}</span> : null}
            {side !== "FLAT" && stopOff ? <Badge color="amber">No stop</Badge> : null}
            {ownStrategy ? (
              <Badge color="violet" title={`Own strategy settings: ${ownStrategy}`}>
                Own
              </Badge>
            ) : null}
          </div>
          <div className="hidden truncate text-xs text-slate-400 lg:block" title={note || undefined}>
            {note || "No note yet"}
          </div>
        </td>
        <td className="whitespace-nowrap py-1 pr-1.5 text-right font-mono text-sm tabular-nums text-slate-100 sm:pr-2">{px(ltp)}</td>
        <td
          className={clsx(
            "whitespace-nowrap py-1 pr-1.5 text-right font-mono text-xs tabular-nums sm:pr-2",
            changePct == null ? "text-slate-400" : changePct >= 0 ? "text-emerald-400" : "text-rose-400"
          )}
          title={changePct == null ? "Day change is sent only for the stock on the chart" : "Change since the previous close"}
        >
          {changePct == null ? "—" : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
        </td>
        <td className="hidden whitespace-nowrap py-1 pr-2 text-right font-mono text-xs tabular-nums sm:table-cell" title={traded ? pnlTitle : undefined}>
          {traded ? <Money value={today} /> : <span className="text-slate-500">—</span>}
        </td>
        <td className="py-1 pr-1">
          <button
            type="button"
            role="switch"
            aria-checked={armed}
            aria-label={`${armed ? "Armed" : "Not armed"}: ${symbol}`}
            disabled={busy || cannotArm}
            onClick={onToggleArmed}
            title={
              armed
                ? "The bot may order this stock. Switch off to stop new orders on it."
                : cannotArm
                  ? "The most stocks this desk can arm are already armed. Switch one off first."
                  : "Let the bot order this stock on its next SMA cross"
            }
            className="flex min-h-11 items-center gap-2 rounded-md pr-1 text-sm text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
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
            <span className={clsx("hidden whitespace-nowrap md:inline", armed ? "font-semibold text-emerald-300" : "text-slate-300")}>
              {armed ? "Armed" : "Not armed"}
            </span>
          </button>
        </td>
        <td className="py-1 pr-1">
          <button
            type="button"
            disabled={busy || onChart}
            onClick={onShowOnChart}
            aria-label={onChart ? `${symbol} is on the chart` : `Show ${symbol} on the chart`}
            title={onChart ? "On the chart" : "Show on the chart"}
            className={clsx(
              "flex min-h-10 min-w-10 shrink-0 items-center justify-center whitespace-nowrap rounded-md text-xs font-semibold sm:px-2.5",
              onChart
                ? "cursor-default bg-sky-500/20 text-sky-200"
                : "text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
            )}
          >
            <LineChart size={16} aria-hidden className="sm:hidden" />
            <span className="hidden sm:inline">{onChart ? "On chart" : "Show on chart"}</span>
          </button>
        </td>
        <td className="py-1 pr-1">
          <button
            type="button"
            disabled={busy || onChart || side !== "FLAT"}
            onClick={onRemove}
            aria-label={`Remove ${symbol} from the list`}
            title={
              onChart
                ? "This stock is on the chart. Show another stock first."
                : side !== "FLAT"
                  ? "This stock has an open position. Close it first."
                  : armed
                    ? "Unarm and remove from this list"
                    : "Remove from this list"
            }
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-slate-400 ring-1 ring-inset ring-white/10 hover:bg-rose-500/10 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X size={16} aria-hidden />
          </button>
        </td>
      </tr>
      {reject ? (
        <tr>
          <td colSpan={8} className="px-2 pb-2">
            <div role="alert" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-xs text-rose-200" title={reject}>
              <span className="font-semibold">
                {symbol} order refused{rejectAt ? ` ${rejectAt}` : ""}:
              </span>{" "}
              {reject}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
