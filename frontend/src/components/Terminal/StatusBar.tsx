"use client";

import clsx from "clsx";
import type { ReactNode } from "react";
import { inr, type SmaConfig, type SmaState } from "@/lib/smaApi";
import { Skeleton, pnlTone } from "./ui";

const BOT: Record<string, { label: string; short?: string; dot: string; text: string }> = {
  RUNNING: { label: "Running", dot: "bg-emerald-400", text: "text-emerald-300" },
  PAUSED: { label: "Paused", dot: "bg-amber-300", text: "text-amber-200" },
  STOPPED: { label: "Stopped", dot: "bg-slate-400", text: "text-slate-200" },
  DAY_COMPLETED: { label: "Day completed", short: "Day done", dot: "bg-sky-300", text: "text-sky-200" },
  HALTED: { label: "Halted", dot: "bg-rose-400", text: "text-rose-300" },
};

const SOURCE: Record<string, string> = {
  GROWW: "Groww live",
  "LAST CLOSE": "Last close",
  SIMULATOR: "Simulator",
  ERROR: "No quotes",
};

type Props = {
  state: SmaState | null;
  config: SmaConfig | null;
  connected: boolean;
  busy: boolean;
  onModeClick: () => void;
};

/** One always-visible strip with everything that decides what the bot may do. */
export function StatusBar({ state, config, connected, busy, onModeClick }: Props) {
  const mode = state?.mode ?? config?.trading_mode ?? null;
  const live = mode === "LIVE";
  const bot = state ? BOT[state.bot_status] ?? { label: state.bot_status, dot: "bg-slate-400", text: "text-slate-200" } : null;
  const source = state ? SOURCE[state.data_source] ?? state.data_source : null;
  const stopOn = state?.stop_enabled ?? config?.use_stop;
  const mult = state?.atr_multiplier ?? config?.atr_multiplier ?? 1.5;
  const cutoff = config?.entry_cutoff_time ?? (config ? "15:00" : null);
  const used = state?.trades_today ?? null;
  const cap = state?.max_trades ?? config?.max_trades_per_day ?? null;
  const nearCap = used != null && cap != null && cap > 0 && used / cap >= 0.9;
  const net = state ? state.kpis?.net ?? state.realized_net_pnl ?? 0 : null;

  return (
    <div role="status" aria-label="Terminal status" className="grid grid-cols-3 gap-1.5 sm:flex sm:flex-wrap sm:items-stretch">
      <button
        type="button"
        disabled={busy || mode == null}
        onClick={onModeClick}
        title={live ? "Real Groww orders are on. Press to go back to PAPER." : "Practice fills only. Press to review switching to LIVE."}
        className={clsx(
          "col-span-2 flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-bold tracking-wide ring-1 ring-inset sm:col-span-1 sm:min-w-[9rem]",
          live ? "bg-rose-600 text-white ring-rose-300/60" : "bg-blue-600/20 text-blue-200 ring-blue-400/50",
          mode == null && "bg-white/5 text-slate-400 ring-white/10"
        )}
      >
        <span aria-hidden className={clsx("h-2 w-2 rounded-full", live ? "animate-pulse bg-white" : "bg-blue-300")} />
        {mode == null ? "Mode…" : live ? "LIVE MONEY" : "PAPER"}
      </button>
      <Cell label="Net today" title="Closed trades today, after charges">
        {net == null ? (
          <Skeleton className="h-4 w-20" />
        ) : (
          <span className={clsx("font-mono font-semibold", pnlTone(net))} title="Closed trades today, after charges">
            {net > 0 ? "+" : ""}
            {inr(net)}
          </span>
        )}
      </Cell>
      <Cell label="Bot">
        {bot ? (
          <span className={clsx("flex items-center gap-1.5", bot.text)}>
            <span aria-hidden className={clsx("h-2 w-2 shrink-0 rounded-full", bot.dot)} />
            {bot.short ? (
              <>
                <span className="sm:hidden">{bot.short}</span>
                <span className="hidden sm:inline">{bot.label}</span>
              </>
            ) : (
              bot.label
            )}
          </span>
        ) : (
          <Skeleton className="h-4 w-16" />
        )}
      </Cell>
      <Cell label="Data">
        {source ? (
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden
              title={connected ? "Live stream connected" : "Live stream reconnecting"}
              className={clsx("h-2 w-2 rounded-full", connected ? "bg-emerald-400" : "bg-rose-400")}
            />
            <span className={state?.data_source === "ERROR" ? "text-rose-300" : "text-slate-100"}>{source}</span>
          </span>
        ) : (
          <Skeleton className="h-4 w-16" />
        )}
      </Cell>
      <Cell label="Stop" title="Exchange stop-loss on new entries">
        {stopOn == null ? (
          <Skeleton className="h-4 w-12" />
        ) : stopOn ? (
          <span className="text-emerald-300">
            ON
            <span className="hidden sm:inline">
              {" · "}
              {(state?.stop_type ?? config?.stop_type) === "SMA_GAP" && !live ? "SMA gap" : `${mult}× ATR`}
            </span>
          </span>
        ) : (
          <span className="font-bold text-amber-300">OFF</span>
        )}
      </Cell>
      <Cell label="Cut-off" title="No new entries from this time">
        {cutoff ? <span className="text-slate-100">{cutoff}</span> : <Skeleton className="h-4 w-12" />}
      </Cell>
      <Cell label="Trades" className="col-span-2 sm:col-span-1">
        {used == null || cap == null ? (
          <Skeleton className="h-4 w-10" />
        ) : (
          <span className={clsx("font-mono", nearCap ? "text-amber-300" : "text-slate-100")}>
            {used}/{cap}
          </span>
        )}
      </Cell>
    </div>
  );
}

function Cell({
  label,
  title,
  children,
  className,
}: {
  label: string;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      title={title}
      className={clsx(
        "flex min-h-10 min-w-0 flex-col justify-center rounded-lg bg-white/[0.04] px-2 py-1 ring-1 ring-inset ring-white/10 sm:min-h-11 sm:px-3",
        className
      )}
    >
      <span className="truncate text-[11px] font-medium uppercase leading-4 tracking-wider text-slate-400">{label}</span>
      <span className="truncate text-sm font-semibold leading-5">{children}</span>
    </div>
  );
}
