"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { StrategyChart } from "@/components/Terminal/StrategyChart";
import { ThemeToggle } from "@/components/Terminal/Header";
import {
  setDesk,
  deskBot,
  DESKS,
  type Desk,
  setReplayRouting,
  smaApi,
  stateForSymbol,
  type ChartPayload,
  type ReplayInfo,
  type SmaState,
  type TradeRow,
} from "@/lib/smaApi";

type Pin = { seq: number; date: string | null; runId: number | null; symbol: string | null };

function routedFor(info: ReplayInfo | null, runId: number | null): boolean {
  if (!info || !["PLAYING", "PAUSED", "FINISHED"].includes(info.status)) return false;
  return runId == null || info.run_id == null || info.run_id === runId;
}

/**
 * One stock's chart on its own page, opened from the terminal's stock tabs.
 *
 * `?symbol=TCS` follows that stock on the live (or `desk=research`) desk.
 * With `date` and `run` it follows that replay while it plays, and shows the
 * replayed day with the run's trades once it has ended. Read-only: no orders
 * are placed from here.
 */
export default function StockChartPage() {
  const [symbol, setSymbol] = useState("");
  const [research, setResearch] = useState(false);
  const [state, setState] = useState<SmaState | null>(null);
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [replay, setReplay] = useState<ReplayInfo | null>(null);
  const [pin, setPin] = useState<Pin | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const params = useRef<{ symbol: string; date: string | null; runId: number | null; research: boolean; desk: Desk } | null>(
    null
  );
  const [desk, setDeskName] = useState<Desk>("live");
  const following = useRef(false);
  const bars = useRef(400);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const name = (q.get("symbol") ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    const run = q.get("run");
    const p = {
      symbol: name,
      date: q.get("date"),
      runId: run && /^\d+$/.test(run) ? Number(run) : null,
      research: q.get("desk") === "research",
      desk: (DESKS as string[]).includes(q.get("desk") ?? "") ? (q.get("desk") as Desk) : "live",
    };
    params.current = p;
    setDesk(p.desk);
    setDeskName(p.desk);
    setSymbol(name);
    setResearch(p.research);
    document.title = name ? `${name} · SMA chart` : "SMA chart";
    // A past replay day shows at once; it switches to following if that run is still playing.
    if (p.date) setPin({ seq: 1, date: p.date, runId: p.runId, symbol: name });
  }, []);

  const poll = useCallback(async (n: number) => {
    const p = params.current;
    if (!p || !p.symbol) return;
    let follow = !p.date;
    // Only the main desk follows a replay.
    if (p.desk === "live") {
      try {
        const info = await smaApi.replayInfo();
        setReplay(info);
        const routed = routedFor(info, p.runId);
        if (p.date) follow = routed;
        else follow = !routed; // a live chart while no replay is on
        setReplayRouting(routed && Boolean(p.date));
        if (following.current && !follow && p.date) {
          // The replay this page followed has ended: show its day from Groww with the run's trades.
          setPin((prev) => ({ seq: (prev?.seq ?? 1) + 1, date: info.date ?? p.date, runId: p.runId, symbol: p.symbol }));
        }
        if (!following.current && follow && p.date) {
          // It is still playing: follow it live instead of the past view.
          setPin((prev) => ({ seq: (prev?.seq ?? 1) + 1, date: null, runId: null, symbol: null }));
        }
      } catch {
        /* the replay status is optional here */
      }
    }
    following.current = follow;
    if (follow) {
      try {
        const [next, nextChart] = await Promise.all([smaApi.state(), smaApi.chart(bars.current, p.symbol)]);
        setState(next);
        setChart(nextChart);
        setNote(null);
      } catch (err: unknown) {
        setNote(err instanceof Error ? err.message : "The chart did not load.");
      }
    }
    if (n % 3 === 0) smaApi.trades().then(setTrades).catch(() => {});
  }, []);

  useEffect(() => {
    let n = 0;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (stop) return;
      if (typeof document === "undefined" || !document.hidden) poll(n++);
      timer = setTimeout(tick, following.current ? 2000 : 10000);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [poll]);

  const p = params.current;
  const replayRun = p?.date ? replay : null;
  const chartTrades = trades.filter((t) => {
    const mode = (t.mode ?? "PAPER").toUpperCase();
    if (p?.date) return mode === "REPLAY" && (p.runId == null || t.run_id === p.runId);
    if (research) return mode === "RESEARCH";
    const bot = deskBot(desk);
    if (bot != null && (t.bot ?? 1) !== bot) return false;
    return mode === (state?.mode ?? "PAPER").toUpperCase();
  });
  const back = desk === "live" ? "/terminal/" : `/terminal/?desk=${desk}`;

  return (
    <div className="terminal-dark min-h-screen w-full min-w-0 bg-[#0B0E14] text-slate-200">
      <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-[#0B0E14] px-3 py-2 sm:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <a
            href={back}
            className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
          >
            <ArrowLeft size={14} aria-hidden /> Terminal
          </a>
          <span className="text-base font-semibold text-amber-300">{symbol || "—"}</span>
          <span className="rounded px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ring-white/15 text-slate-300">
            {p?.date
              ? following.current
                ? `Replay · ${replayRun?.clock ? new Date(replayRun.clock).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : p.date}`
                : `Replay ${p.date}${p.runId != null ? ` · run ${p.runId}` : ""} · ended`
              : research
                ? "Research desk"
                : desk !== "live"
                  ? `${state?.bot_name ?? desk.replace("bot", "Bot ")} · ${state?.mode ?? "PAPER"}`
                : (state?.mode ?? "PAPER")}
          </span>
        </div>
        <ThemeToggle />
      </header>
      <main className="mx-auto w-full min-w-0 space-y-3 px-3 py-3 sm:px-4">
        {note ? (
          <div role="status" className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            {note}
          </div>
        ) : null}
        {!symbol ? (
          <p className="text-sm text-slate-400">No stock in the address. Open a stock from the terminal&apos;s stock tabs.</p>
        ) : (
          <StrategyChart
            chart={chart}
            state={stateForSymbol(state, symbol) ?? ({ symbol } as SmaState)}
            trades={chartTrades}
            allTrades={trades}
            pin={pin}
            closing={false}
            onLiveBars={(count) => {
              bars.current = count;
            }}
          />
        )}
      </main>
    </div>
  );
}
