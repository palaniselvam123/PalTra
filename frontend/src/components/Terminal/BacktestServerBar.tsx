"use client";

import clsx from "clsx";
import { ExternalLink, FlaskConical, Loader2, Power, RefreshCw, Rocket } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ServerRole } from "@/lib/api";
import { smaApi, type BacktestServerStatus } from "@/lib/smaApi";

const STOP_CHOICES = ["15:30", "15:45", "16:00", "16:30", "17:00", "18:00"];
const IDLE_CHOICES = [10, 15, 30, 60, 120, 240];
const BTN =
  "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset disabled:opacity-50";

// "Test and deploy" in GitHub Actions: Run workflow → target "main-desk" ships a tested version to the main desk.
const DEPLOY_PAGE = "https://github.com/palaniselvam123/PalTra/actions/workflows/test-and-deploy.yml";

function shortVersion(v?: string): string {
  return v && v !== "dev" ? v.slice(0, 7) : "dev";
}

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
      <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-xs text-slate-500" title="The version (git commit) the main desk runs. New versions reach the backtest server first.">
        Desk version {shortVersion(role.version)}
      </span>
      <a
        href={`${role.backtest_url}/terminal/`}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-300 hover:underline"
        title="Backtests on their own machine, so they never slow the bots. It starts when opened and stops itself when idle."
      >
        <FlaskConical size={13} aria-hidden /> Run backtests on the backtest server <ExternalLink size={12} aria-hidden />
      </a>
      </span>
    );
  }
  if (role?.role !== "backtest") return null;

  const left = st ? Math.max(0, Math.round(st.idle_left_sec / 60)) : null;
  return (
    <div role="note" className="space-y-2 rounded-xl border border-violet-400/40 bg-violet-500/[0.08] px-3 py-2 text-sm text-violet-100">
      <p>
        <span className="font-semibold">Backtest server</span>: backtests, and Start bot runs the bots here on today&apos;s
        live prices with practice money only (this server&apos;s own Groww login; no alerts). Real orders only ever go from the{" "}
        <a href={role.desk_url} className="underline" target="_blank" rel="noreferrer">
          main desk
        </a>
        ; nothing here sends an order. Settings and armed stocks are copied from the desk when you press Sync from desk
        {" "}(or at every start, if ticked below); the scanner&apos;s Arm can also arm stocks here.
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
          {st?.busy
            ? "Backtest running: stays on"
            : left != null
              ? st?.after_hours
                ? `Market hours are over: stops in ${left} min`
                : `Stops itself in ${left} min if idle`
              : ""}
        </span>
        <label
          className="flex items-center gap-1.5"
          title="Use this server only in market hours. After the stop time (and on weekends) it stops once no backtest or scan is running, even with this page open."
        >
          <input
            type="checkbox"
            checked={Boolean(st?.market_hours_only)}
            disabled={!st || busy}
            onChange={(e) => act(() => smaApi.backtestHours(e.target.checked, st?.stop_at), e.target.checked ? "Market hours only: on." : "Market hours only: off.")}
            className="accent-violet-400"
          />
          Market hours only, stop after
          <select
            value={st?.stop_at ?? "15:45"}
            disabled={!st || busy}
            onChange={(e) => act(() => smaApi.backtestHours(Boolean(st?.market_hours_only), e.target.value), "Stop time saved.")}
            className="min-h-8 rounded border border-white/15 bg-black/30 px-1 text-xs text-slate-100"
          >
            {STOP_CHOICES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
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
        <label
          className="flex items-center gap-1.5"
          title="Off: this server keeps its own settings and armed stocks after a deploy or restart; only Sync from desk copies the desk's. On: the desk's are copied at every start."
        >
          <input
            type="checkbox"
            checked={Boolean(st?.auto_sync)}
            disabled={!st || busy}
            onChange={(e) =>
              act(
                () => smaApi.backtestAutoSync(e.target.checked),
                e.target.checked ? "The desk's settings will be copied at every start." : "Settings are copied only when you press Sync from desk."
              )
            }
            className="accent-violet-400"
          />
          Copy from desk at every start
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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
        <span title="The git commit this server runs. Every merge to main is deployed here first; the main desk only gets it when you send it there.">
          This server runs version <span className="font-mono text-violet-200">{shortVersion(role.version)}</span>
        </span>
        <a
          href={DEPLOY_PAGE}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-semibold text-emerald-300 hover:underline"
          title={`Opens GitHub Actions. Press "Run workflow", choose target "main-desk" and paste ${role.version && role.version !== "dev" ? role.version : "this version"} as the commit. The main desk restarts for about a minute; during market hours you must tick the market-hours box too.`}
        >
          <Rocket size={12} aria-hidden /> Tested OK? Deploy this version to the main desk <ExternalLink size={11} aria-hidden />
        </a>
        {role.version && role.version !== "dev" ? (
          <button
            type="button"
            onClick={() => {
              navigator.clipboard?.writeText(role.version ?? "").then(
                () => setNote("Version copied: paste it as the commit in Run workflow."),
                () => setNote(`Version: ${role.version}`)
              );
            }}
            className="text-slate-300 underline decoration-dotted hover:text-slate-100"
          >
            Copy version
          </button>
        ) : null}
      </div>
      {note ? <p className="text-xs text-slate-400">{note}</p> : null}
    </div>
  );
}
