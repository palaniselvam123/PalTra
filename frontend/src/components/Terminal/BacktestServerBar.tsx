"use client";

import clsx from "clsx";
import { ExternalLink, FlaskConical, Loader2, Power, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ServerRole } from "@/lib/api";
import { smaApi, type BacktestServerStatus } from "@/lib/smaApi";

const IDLE_CHOICES = [10, 15, 30, 60, 120, 240];
const BTN =
  "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset disabled:opacity-50";

function ago(epochSec: number | null): string {
  if (!epochSec) return "not yet";
  const min = Math.round((Date.now() / 1000 - epochSec) / 60);
  return min < 1 ? "just now" : `${min} min ago`;
}

/**
 * On the backtest server (backend/backtest_server.py): what this server is, its copy of the desk's
 * settings, its idle stop and a Stop button. On the desk: a link to the backtest server when one is set.
 */
export function BacktestServerBar() {
  const [role, setRole] = useState<ServerRole | null>(null);
  const [st, setSt] = useState<BacktestServerStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api
      .serverRole()
      .then(setRole)
      .catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    smaApi
      .backtestStatus()
      .then(setSt)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (role?.role !== "backtest") return;
    load();
    const id = window.setInterval(load, 15_000);
    return () => window.clearInterval(id);
  }, [role, load]);

  const act = async (what: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await what();
      setNote(done);
      load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (role?.role === "desk" && role.backtest_url) {
    return (
      <a
        href={`${role.backtest_url}/terminal/`}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-300 hover:underline"
        title="Backtests on their own machine, so they never slow the bots. It starts when opened and stops itself when idle."
      >
        <FlaskConical size={13} aria-hidden /> Run backtests on the backtest server <ExternalLink size={12} aria-hidden />
      </a>
    );
  }
  if (role?.role !== "backtest") return null;

  const left = st ? Math.max(0, Math.round(st.idle_left_sec / 60)) : null;
  return (
    <div role="note" className="space-y-2 rounded-xl border border-violet-400/40 bg-violet-500/[0.08] px-3 py-2 text-sm text-violet-100">
      <p>
        <span className="font-semibold">Backtest server</span>: backtests only, with practice money. The bots trade on the{" "}
        <a href={role.desk_url} className="underline" target="_blank" rel="noreferrer">
          main desk
        </a>
        ; nothing here sends an order. Settings and armed stocks are copied from the desk; the scanner&apos;s Arm can also arm
        stocks here.
      </p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-400">
        <span>
          Settings copied {st ? ago(st.synced_at) : "…"}
          {st?.sync_error ? <span className="text-rose-300"> · {st.sync_error}</span> : null}
        </span>
        {st?.scanner_armed ? (
          <span title="Armed here from the scanner's Arm. Kept when this server starts again; Sync from desk replaces them.">
            {st.scanner_armed} stock{st.scanner_armed === 1 ? "" : "s"} armed from the scanner
          </span>
        ) : null}
        <span className={clsx(st && !st.groww && "text-amber-300")}>
          Groww: {st ? (st.groww ? "desk session in use" : st.groww_error || "no session yet") : "…"}
        </span>
        <span>
          {st?.busy ? "Backtest running: stays on" : left != null ? `Stops itself in ${left} min if idle` : ""}
        </span>
        <label className="flex items-center gap-1">
          Idle stop after
          <select
            value={st?.idle_min ?? 30}
            disabled={!st || busy}
            onChange={(e) => act(() => smaApi.backtestIdle(Number(e.target.value)), "Idle time saved.")}
            className="min-h-8 rounded border border-white/15 bg-black/30 px-1 text-xs text-slate-100"
          >
            {IDLE_CHOICES.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || st?.busy}
          title={
            st?.busy
              ? "Wait for the running backtest to finish"
              : "Copy every bot's settings and armed stocks from the desk again (stocks armed here from the scanner are replaced too)"
          }
          onClick={() => {
            if (
              st?.scanner_armed &&
              !window.confirm(
                `Copy everything from the desk? The ${st.scanner_armed} stock(s) armed here from the scanner are replaced by the desk's Trade lists.`
              )
            )
              return;
            act(() => smaApi.backtestSync(), "Settings copied from the desk.");
          }}
          className={clsx(BTN, "text-sky-200 ring-sky-400/40 hover:bg-sky-500/10")}
        >
          {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <RefreshCw size={12} aria-hidden />} Sync from desk
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            const force = Boolean(st?.busy);
            const ask = force
              ? "A backtest is running. Stop the server anyway? The backtest can be resumed next time."
              : "Stop the backtest server now? Opening this page starts it again.";
            if (window.confirm(ask)) act(() => smaApi.backtestStop(force), "Stopping: the server will be off in a few seconds.");
          }}
          className={clsx(BTN, "text-rose-200 ring-rose-400/40 hover:bg-rose-500/10")}
        >
          <Power size={12} aria-hidden /> Stop server
        </button>
      </div>
      {note ? <p className="text-xs text-slate-400">{note}</p> : null}
    </div>
  );
}
