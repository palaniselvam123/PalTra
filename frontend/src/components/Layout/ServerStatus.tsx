"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, resolveApiBase } from "@/lib/api";
import { SERVER_STORE } from "@/components/ServerAccent";
import { NewTag } from "@/components/ui/NewTag";

/**
 * Server status bar (display only; every page, from the root layout). The pages used to show only a spinner or a
 * frozen screen while the server restarted after a deploy, woke up (the backtest server stops itself when idle) or was
 * busy. This asks the public `/api/health` every `POLL_MS` and says what is going on, with a running count:
 *  - no answer: "Server not answering for 42 s", why that usually is (restart after a deploy, waking up), the next try
 *    and Retry now;
 *  - back: how long it was away and, when `/api/role` reports another `version`, that a new version is live with a
 *    Reload button (the page still runs the old code until it reloads);
 *  - slow: `/api/health` does no work, so its reply time is the server's own delay; over `SLOW_MS` twice in a row
 *    shows "Server busy: replies take 4.2 s".
 * Nothing is sent but GETs; nothing in the trading code reads it.
 */

const POLL_MS = 5_000;
const DOWN_POLL_MS = 3_000;
const TIMEOUT_MS = 8_000;
const SLOW_MS = 2_500;
const VERSION_CHECK_MS = 60_000;
const BACK_SHOW_MS = 8_000;

type Phase =
  | { kind: "ok" }
  | { kind: "slow"; ms: number }
  | { kind: "down"; since: number; tries: number; last: string }
  | { kind: "back"; away: number; version: string | null; at: number };

function shortVersion(v: string | null | undefined): string | null {
  if (!v || v === "dev") return v ?? null;
  return v.slice(0, 7);
}

function secs(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 90 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

function serverKind(): "desk" | "backtest" | "scanner" {
  try {
    const v = localStorage.getItem(SERVER_STORE);
    if (v === "backtest" || v === "scanner") return v;
  } catch {
    /* blocked storage: treat as the desk */
  }
  return "desk";
}

/** One health check: the reply time in ms, or why there was none. */
async function ping(): Promise<{ ok: true; ms: number } | { ok: false; why: string }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const started = performance.now();
  try {
    const res = await fetch(`${resolveApiBase()}/api/health`, { cache: "no-store", signal: controller.signal });
    const ms = performance.now() - started;
    if (res.ok) return { ok: true, ms };
    // Fly's proxy answers 502 / 503 while the app is not up.
    return { ok: false, why: `answered ${res.status}` };
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return { ok: false, why: `no reply in ${TIMEOUT_MS / 1000} s` };
    return { ok: false, why: "no connection" };
  } finally {
    clearTimeout(timer);
  }
}

export function ServerStatus() {
  const [phase, setPhase] = useState<Phase>({ kind: "ok" });
  const [now, setNow] = useState(() => Date.now());
  const [nextAt, setNextAt] = useState(0);
  const [newVersion, setNewVersion] = useState<string | null>(null);
  const phaseRef = useRef<Phase>(phase);
  const slowRun = useRef(0);
  const loadVersion = useRef<string | null | undefined>(undefined);
  const lastVersionCheck = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busy = useRef(false);

  const set = (p: Phase) => {
    phaseRef.current = p;
    setPhase(p);
  };

  const checkVersion = useCallback(async (): Promise<string | null> => {
    lastVersionCheck.current = Date.now();
    const role = await api.serverRole().catch(() => null);
    const v = role?.version ?? null;
    if (loadVersion.current === undefined) loadVersion.current = v;
    else if (v && loadVersion.current && v !== loadVersion.current) setNewVersion(v);
    return v;
  }, []);

  const check = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    if (timer.current) clearTimeout(timer.current);
    try {
      const r = await ping();
      const cur = phaseRef.current;
      if (!r.ok) {
        slowRun.current = 0;
        if (cur.kind === "down") set({ ...cur, tries: cur.tries + 1, last: r.why });
        else set({ kind: "down", since: Date.now() - (r.why.startsWith("no reply") ? TIMEOUT_MS : 0), tries: 1, last: r.why });
      } else if (cur.kind === "down") {
        const v = await checkVersion();
        set({ kind: "back", away: Date.now() - cur.since, version: v, at: Date.now() });
      } else {
        slowRun.current = r.ms > SLOW_MS ? slowRun.current + 1 : 0;
        if (slowRun.current >= 2) set({ kind: "slow", ms: r.ms });
        else if (cur.kind === "slow" || (cur.kind === "back" && Date.now() - cur.at > BACK_SHOW_MS)) set({ kind: "ok" });
        if (Date.now() - lastVersionCheck.current > VERSION_CHECK_MS) void checkVersion();
      }
    } finally {
      busy.current = false;
      const wait = phaseRef.current.kind === "down" ? DOWN_POLL_MS : POLL_MS;
      setNextAt(Date.now() + wait);
      timer.current = setTimeout(() => {
        if (document.visibilityState === "visible") void check();
        else timer.current = setTimeout(() => void check(), wait);
      }, wait);
    }
  }, [checkVersion]);

  useEffect(() => {
    void check();
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [check]);

  // A running clock only while something is shown.
  const showing = phase.kind !== "ok" || newVersion !== null;
  useEffect(() => {
    if (!showing) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [showing]);

  // Hide "back" after a few seconds unless a new version is waiting.
  useEffect(() => {
    if (phase.kind !== "back" || newVersion) return;
    const t = setTimeout(() => {
      if (phaseRef.current.kind === "back") set({ kind: "ok" });
    }, BACK_SHOW_MS);
    return () => clearTimeout(t);
  }, [phase, newVersion]);

  if (!showing) return null;

  const kind = serverKind();
  const name = kind === "backtest" ? "The backtest server" : kind === "scanner" ? "The scanner server" : "The server";
  const reload = (
    <button
      type="button"
      onClick={() => window.location.reload()}
      className="min-h-9 shrink-0 rounded-md bg-emerald-600 px-3 text-sm font-semibold text-white hover:bg-emerald-500"
    >
      Reload page
    </button>
  );

  let tone = "border-amber-500/50 bg-amber-500/15 text-amber-100";
  let body: React.ReactNode = null;
  let action: React.ReactNode = null;

  if (phase.kind === "down") {
    tone = "border-rose-500/60 bg-rose-500/15 text-rose-100";
    const away = now - phase.since;
    const next = Math.min(DOWN_POLL_MS / 1000, Math.max(0, Math.ceil((nextAt - now) / 1000)));
    const why =
      kind === "backtest"
        ? "It stops itself when idle and Fly starts it again on this visit, or it is restarting after a deploy. Waking up usually takes 20-60 s."
        : "Usually a restart after a deploy (about 1-2 min) or a restart by Fly. Prices, positions and P&L on screen are from before it went quiet.";
    body = (
      <>
        <span className="font-semibold">
          {name} is not answering · {secs(away)}
        </span>
        <span className="opacity-90"> — {why}</span>
        <span className="block text-xs opacity-80 sm:inline sm:pl-2">
          Try {phase.tries} ({phase.last}) · next in {next} s
        </span>
      </>
    );
    action = (
      <button
        type="button"
        onClick={() => void check()}
        className="min-h-9 shrink-0 rounded-md bg-rose-600 px-3 text-sm font-semibold text-white hover:bg-rose-500"
      >
        Retry now
      </button>
    );
  } else if (phase.kind === "back" || newVersion) {
    tone = "border-emerald-500/60 bg-emerald-500/15 text-emerald-100";
    const away = phase.kind === "back" ? phase.away : 0;
    body = (
      <>
        {phase.kind === "back" && <span className="font-semibold">Back online after {secs(away)}. </span>}
        {newVersion ? (
          <span>
            A new version is live ({shortVersion(newVersion)}
            {loadVersion.current ? `, this page runs ${shortVersion(loadVersion.current)}` : ""}). Reload to use it.
          </span>
        ) : (
          <span>Same version as before.</span>
        )}
      </>
    );
    action = newVersion ? reload : null;
  } else if (phase.kind === "slow") {
    body = (
      <>
        <span className="font-semibold">{name} is busy: replies take {(phase.ms / 1000).toFixed(1)} s</span>
        <span className="opacity-90"> (normally under 0.5 s). A backtest, a big scan or many open tabs can cause this; pages will load slowly until it eases.</span>
      </>
    );
  }

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex justify-center px-4 sm:bottom-4 sm:pr-24">
      <div className="pointer-events-auto w-full max-w-3xl rounded-xl bg-base shadow-lg">
        <div
          role={phase.kind === "down" ? "alert" : "status"}
          aria-live="polite"
          className={`flex flex-col gap-2 rounded-xl border px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between ${tone}`}
        >
          <p className="min-w-0">
            {body} <NewTag id="server-status" />
          </p>
          {action}
        </div>
      </div>
    </div>
  );
}
