"use client";

import { usePinned } from "@/lib/usePinned";
import { useTitle } from "@/lib/useTitle";
import { ReplayScrub } from "@/components/Terminal/ReplayBar";
import { PinToggle } from "@/components/ui/PinToggle";
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

function routedFor(info: ReplayInfo | null, runId: number | null, pastDay = false): boolean {
  // A page opened on a replay day follows it while it loads, plays or is paused: while it loads the
  // chart stays blank rather than showing the whole day from Groww before the replay has started. A
  // finished run has no live replay chart any more: its day is drawn from Groww with the run's trades.
  const live = pastDay ? ["LOADING", "PLAYING", "PAUSED"] : ["LOADING", "PLAYING", "PAUSED", "FINISHED"];
  if (!info || !live.includes(info.status)) return false;
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
  useTitle(symbol ? `${symbol} · SMA chart · PalTra` : "SMA chart · PalTra");
  const following = useRef(false);
  // A past replay run's own candle size (its saved settings, that stock's own setting first): the live state on
  // this page belongs to the desk's bot, not to the run, so a finished run would otherwise read as 1 minute.
  const [runCandle, setRunCandle] = useState<number | null>(null);
  const [live, setLive] = useState(false);
  // The replay this page follows is still loading its candles (its chart answers 409 until it starts).
  const loadingRef = useRef(false);
  // The replay is still on (loading, playing or paused) by its own status: a missed chart answer while it is
  // on keeps the last chart instead of switching to the "ended" view and back on the next poll.
  const replayOnRef = useRef(false);
  // The day the run is replaying now, while that is not this page's day (null otherwise).
  const [otherDay, setOtherDay] = useState<string | null>(null);
  // Chart answers that failed in a row; the page gives up on the live replay chart only after a few.
  const misses = useRef(0);
  const bars = useRef(400);
  // A candle size the bot's own candle cannot make (from the chart's candle-size menu); null = the bot's own.
  const barInterval = useRef<number | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const name = (q.get("symbol") ?? "").toUpperCase().replace(/[^A-Z0-9&_-]/g, "");
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
    // A past replay day shows at once; it switches to following if that run is still playing.
    if (p.date) setPin({ seq: 1, date: p.date, runId: p.runId, symbol: name });
    if (p.runId != null) {
      smaApi
        .replayRun(p.runId)
        .then((run) => {
          const s = run.settings as Record<string, unknown>;
          const stocks = (s.stock_settings && typeof s.stock_settings === "object" ? s.stock_settings : {}) as Record<
            string,
            Record<string, unknown> | undefined
          >;
          const size = Number(stocks[name]?.candle_minutes ?? s.candle_minutes ?? 1);
          setRunCandle(Number.isFinite(size) && size > 0 ? size : 1);
        })
        .catch(() => undefined);
    }
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
        loadingRef.current = info.status === "LOADING";
        const routed = routedFor(info, p.runId, Boolean(p.date));
        // A run of several days plays one day at a time: follow it only while it is on this page's day. Once
        // it has moved to another day (a stock not picked that day has no candles there), show this day from
        // Groww with the run's trades instead of a blank chart.
        const sameDay = !p.date || !info.date || info.date === p.date;
        const onDay = routed && sameDay;
        replayOnRef.current = onDay;
        setOtherDay(routed && !sameDay ? (info.date ?? null) : null);
        if (p.date) follow = onDay;
        else follow = !routed; // a live chart while no replay is on
        setReplayRouting(onDay && Boolean(p.date));
        if (following.current && !follow && p.date) {
          // The replay this page followed has ended or moved on: show its day from Groww with the run's trades.
          setPin((prev) => ({ seq: (prev?.seq ?? 1) + 1, date: p.date, runId: p.runId, symbol: p.symbol }));
        }
        if (!following.current && follow && p.date) {
          // It is still playing: follow it live instead of the past view.
          setPin((prev) => ({ seq: (prev?.seq ?? 1) + 1, date: null, runId: null, symbol: null }));
        }
      } catch {
        // The replay status is optional here. A missed answer keeps what the page was doing (following or
        // not) instead of dropping out of the replay for one poll and back in on the next.
        if (p.date) follow = following.current;
      }
    }
    following.current = follow;
    setLive(follow);
    if (follow) {
      try {
        const [next, nextChart] = await Promise.all([smaApi.state(), smaApi.chart(bars.current, p.symbol, barInterval.current)]);
        setState(next);
        misses.current = 0;
        // A replay's chart can answer with no candles for a moment (a new day or a rewind building its engine,
        // a stock not picked yet that day): keep the last candles on screen instead of a blank chart.
        setChart((prev) =>
          p.date && prev && prev.candles.length > 0 && nextChart.candles.length === 0 ? prev : nextChart
        );
        setNote(null);
      } catch (err: unknown) {
        misses.current += 1;
        if (p.date && loadingRef.current) {
          // Still loading the replay's candles: nothing to draw yet (the last chart, if any, stays).
          setNote("Loading the replay…");
          return;
        }
        if (p.date && replayOnRef.current && misses.current < 5) {
          // The replay is still playing by its status; one chart answer was missed (a busy server). Keep the
          // last chart and try again on the next poll.
          return;
        }
        if (p.date) {
          // The replay's live chart is gone (ended, stopped or the server restarted): show its day instead of a blank chart.
          following.current = false;
          setReplayRouting(false);
          setNote(null);
          setPin((prev) => ({ seq: (prev?.seq ?? 1) + 1, date: p.date, runId: p.runId, symbol: p.symbol }));
          return;
        }
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
  const [pinned, setPinned] = usePinned("chart-page-header");

  return (
    <div className="terminal-dark min-h-screen w-full min-w-0 bg-[#0B0E14] text-slate-200">
      <header
        className={`${pinned ? "sticky" : "relative"} top-0 z-30 flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-[#0B0E14] px-3 py-2 sm:px-4`}
      >
        <div className="flex min-w-0 items-center gap-3">
          <PinToggle pinned={pinned} onChange={setPinned} what="this header" />
          <a
            href={back}
            className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
          >
            <ArrowLeft size={14} aria-hidden /> Terminal
          </a>
          <span className="text-base font-semibold text-amber-300">{symbol || "—"}</span>
          <span className="rounded px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ring-white/15 text-slate-300">
            {p?.date
              ? following.current
                ? `Replay · ${replayRun?.clock ? new Date(replayRun.clock).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : p.date}`
                : `Replay ${p.date}${p.runId != null ? ` · run ${p.runId}` : ""} · ${otherDay ? `day done (run now on ${otherDay})` : "ended"}`
              : research
                ? "Research desk"
                : desk !== "live"
                  ? `${state?.bot_name ?? desk.replace("bot", "Bot ")} · ${state?.mode ?? "PAPER"}`
                : (state?.mode ?? "PAPER")}
          </span>
        </div>
        {p?.date && following.current && replayRun && (replayRun.status === "PLAYING" || replayRun.status === "PAUSED") ? (
          <div className="order-last flex w-full items-center gap-2 rounded-md bg-violet-500/10 px-2 py-1 ring-1 ring-inset ring-violet-400/30 lg:order-none lg:w-auto lg:min-w-[380px]">
            <ReplayScrub info={replayRun} onChanged={setReplay} />
          </div>
        ) : null}
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
            state={
              runCandle != null && !live
                ? { ...(stateForSymbol(state, symbol) ?? ({ symbol } as SmaState)), candle_minutes: runCandle }
                : stateForSymbol(state, symbol) ?? ({ symbol } as SmaState)
            }
            trades={chartTrades}
            allTrades={trades}
            pin={pin}
            closing={false}
            onLiveBars={(count, interval) => {
              bars.current = count;
              barInterval.current = interval ?? null;
            }}
          />
        )}
      </main>
    </div>
  );
}
