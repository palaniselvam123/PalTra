"use client";

import { useEffect, useState, type FormEvent } from "react";
import { History, Loader2, Pause, Play, RotateCcw, Square } from "lucide-react";
import clsx from "clsx";
import { smaApi, replayActive, type ReplayInfo, type ResumableRun } from "@/lib/smaApi";

const HIDE_KEY = "replay.resumeHidden";
const SESSION_START = 9 * 60 + 15;
const SESSION_END = 15 * 60 + 30;

/** "05 Oct" from YYYY-MM-DD. */
function shortDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
}

/** The last finished weekday, as YYYY-MM-DD in IST. */
function lastTradingDay(): string {
  const fmt = (d: Date) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const hh = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const mm = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  let d = new Date();
  // Today counts only after the close.
  if (hh * 60 + mm < SESSION_END) d = new Date(d.getTime() - 86_400_000);
  for (let i = 0; i < 7; i += 1) {
    const wd = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short" }).format(d);
    if (wd !== "Sat" && wd !== "Sun") break;
    d = new Date(d.getTime() - 86_400_000);
  }
  return fmt(d);
}

export function clockParts(iso: string | null): { label: string; minute: number } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const label = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
  const p = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const n = (t: string) => Number(p.find((x) => x.type === t)?.value ?? 0);
  return { label, minute: n("hour") * 60 + n("minute") + n("second") / 60 };
}

/** How far the replay is, 0 to 1 (over every day of a multi-day run). */
export function replayProgress(info: ReplayInfo | null): number {
  if (!info) return 0;
  if (info.status === "FINISHED") return 1;
  const clock = clockParts(info.clock ?? null);
  const dayProgress = clock ? Math.min(1, Math.max(0, (clock.minute - SESSION_START) / (SESSION_END - SESSION_START))) : 0;
  const daysTotal = info.days_total ?? 1;
  return daysTotal > 1 ? Math.min(1, ((info.day_index ?? 0) + dayProgress) / daysTotal) : dayProgress;
}

/** The replay in the terminal's top strip: replayed time, day, progress, pause and stop. */
export function ReplayChip({ info, onChanged }: { info: ReplayInfo; onChanged?: (info: ReplayInfo) => void }) {
  const [busy, setBusy] = useState(false);
  const clock = clockParts(info.clock ?? null);
  const short = clock ? clock.label.replace(/^\w+,?\s*/, "").replace(/\s*\d{4},?/, "").replace(/:\d{2}$/, "") : "—";
  const daysTotal = info.days_total ?? 1;
  const playing = info.status === "PLAYING";
  const act = async (action: "pause" | "play" | "stop") => {
    setBusy(true);
    try {
      const next = await smaApi.replayControl(action);
      onChanged?.(next);
    } catch {
      /* the replay bar below shows the error on its next poll */
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <span className="min-w-0">
        <span className="block truncate font-mono text-xs text-violet-100">
          {info.status === "LOADING" ? "Loading…" : short}
          {daysTotal > 1 ? <span className="ml-1 font-sans text-[11px] font-normal text-violet-300">D{Math.min((info.day_index ?? 0) + 1, daysTotal)}/{daysTotal}</span> : null}
        </span>
        <span className="mt-0.5 block h-1 w-full overflow-hidden rounded-full bg-black/40" aria-hidden>
          <span className="block h-full rounded-full bg-violet-400" style={{ width: `${replayProgress(info) * 100}%` }} />
        </span>
      </span>
      {info.status === "PLAYING" || info.status === "PAUSED" ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => void act(playing ? "pause" : "play")}
          aria-label={playing ? "Pause replay" : "Play replay"}
          title={playing ? "Pause replay" : "Play replay"}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-violet-100 ring-1 ring-inset ring-violet-300/40 hover:bg-violet-400/15 disabled:opacity-50"
        >
          {playing ? <Pause size={12} aria-hidden /> : <Play size={12} aria-hidden />}
        </button>
      ) : null}
      <button
        type="button"
        disabled={busy}
        onClick={() => void act("stop")}
        aria-label="Stop replay"
        title="Stop replay. Open replay positions close at the replay price."
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-white/10 text-white hover:bg-white/15 disabled:opacity-50"
      >
        <Square size={11} aria-hidden />
      </button>
    </span>
  );
}

type Props = {
  info: ReplayInfo | null;
  /** The desk is in LIVE mode: replay is refused. */
  live: boolean;
  armedCount: number;
  onChanged: (info: ReplayInfo) => void;
  /** The desk's bot: its settings and armed stocks are what a replay started here plays. */
  bot?: number;
  botName?: string;
};

/** Practise on a past day's real Groww candles. Never sends an order. */
export function ReplayBar({ info, live, armedCount, onChanged, bot = 1, botName }: Props) {
  const [day, setDay] = useState(lastTradingDay);
  const [endDay, setEndDay] = useState(lastTradingDay);
  const [start, setStart] = useState("09:15");
  const [speed, setSpeed] = useState(60);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const active = replayActive(info);

  const run = async (fn: () => Promise<ReplayInfo>) => {
    setBusy(true);
    setMsg(null);
    try {
      onChanged(await fn());
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Replay request failed");
    } finally {
      setBusy(false);
    }
  };

  // A run a deploy (or Stop) cut short: offer to carry on from the first unfinished day.
  const [resumable, setResumable] = useState<ResumableRun | null>(null);
  useEffect(() => {
    if (active) return;
    let live = true;
    smaApi
      .replayResumable(bot)
      .then((runs) => {
        if (!live) return;
        let hidden: string[] = [];
        try {
          hidden = JSON.parse(localStorage.getItem(HIDE_KEY) ?? "[]");
        } catch {
          /* private window */
        }
        setResumable(runs.find((r) => !hidden.includes(String(r.id))) ?? null);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [active, bot, info?.status]);
  const hideResume = (id: number) => {
    try {
      const hidden: string[] = JSON.parse(localStorage.getItem(HIDE_KEY) ?? "[]");
      localStorage.setItem(HIDE_KEY, JSON.stringify([...hidden, String(id)].slice(-50)));
    } catch {
      /* private window */
    }
    setResumable(null);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(() => smaApi.replayStart(day, start, speed, endDay && endDay !== day ? endDay : undefined, undefined, bot));
  };

  if (!active) {
    return (
      <section aria-label="Replay a past day" className="rounded-xl border border-violet-400/25 bg-violet-500/[0.06]">
        {resumable ? (
          <div role="status" className="flex flex-wrap items-center gap-2 border-b border-amber-400/30 bg-amber-400/[0.08] px-3 py-2 text-sm">
            <RotateCcw size={15} aria-hidden className="text-amber-300" />
            <span className="text-amber-100">
              Run #{resumable.id} ({shortDay(resumable.start_date)} → {shortDay(resumable.end_date)}) stopped after day{" "}
              {resumable.days_done} of {resumable.days_total}
              {resumable.status === "INTERRUPTED" ? " when the server restarted" : ""}. Carry on from day {resumable.days_done + 1}
              {resumable.next_day ? ` (${shortDay(resumable.next_day)})` : ""}?
            </span>
            <span className="ml-auto flex gap-1.5">
              <button
                type="button"
                disabled={busy || live}
                onClick={() => void run(() => smaApi.replayResume(resumable.id, speed, resumable.bot))}
                className="inline-flex min-h-8 items-center gap-1 rounded-md bg-violet-500 px-3 text-xs font-semibold text-white hover:bg-violet-400 disabled:opacity-50"
                title={live ? "Switch this bot to PAPER to replay" : "Finished days keep their results; the unfinished day plays again from 09:15 with the run's saved settings"}
              >
                {busy ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <Play size={13} aria-hidden />} Resume
              </button>
              <button
                type="button"
                onClick={() => hideResume(resumable.id)}
                className="min-h-8 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
              >
                Not now
              </button>
            </span>
            {msg ? <p role="alert" className="basis-full text-xs text-rose-300">{msg}</p> : null}
          </div>
        ) : null}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm"
        >
          <History size={16} aria-hidden className="text-violet-300" />
          <span className="font-semibold text-violet-100">Replay past days</span>
          <span className="hidden text-xs text-slate-400 sm:inline">
            — practise on real Groww candles from one day or a range up to 45 days (about 30 trading days). Practice money only.
          </span>
          <span className="ml-auto text-xs text-violet-300">{open ? "Hide" : "Set up"}</span>
        </button>
        {open || info?.status === "ERROR" ? (
          <form onSubmit={submit} className="flex flex-wrap items-end gap-2 border-t border-violet-400/15 px-3 py-2">
            <label className="flex flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400">
              From
              <input
                type="date"
                value={day}
                onChange={(e) => {
                  setDay(e.target.value);
                  if (!endDay || endDay < e.target.value) setEndDay(e.target.value);
                }}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm normal-case tracking-normal text-slate-100 [color-scheme:dark]"
              />
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400">
              To
              <input
                type="date"
                value={endDay}
                min={day}
                onChange={(e) => setEndDay(e.target.value)}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm normal-case tracking-normal text-slate-100 [color-scheme:dark]"
              />
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400">
              Start (IST)
              <input
                type="time"
                value={start}
                min="09:15"
                max="15:14"
                onChange={(e) => setStart(e.target.value)}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm normal-case tracking-normal text-slate-100 [color-scheme:dark]"
              />
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400">
              Speed
              <select
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm normal-case tracking-normal text-slate-100"
              >
                <option value={1}>1× real time</option>
                <option value={10}>10×</option>
                <option value={60}>60× (1 candle / s)</option>
                <option value={300}>300×</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={busy || live || armedCount === 0}
              className="flex min-h-9 items-center gap-1.5 rounded-md bg-violet-500 px-3 text-sm font-semibold text-white hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? <Loader2 size={14} aria-hidden className="animate-spin" /> : <Play size={14} aria-hidden />}
              {endDay && endDay !== day ? "Start multi-day replay" : "Start replay"}
            </button>
            <p className="basis-full text-[11px] leading-snug text-slate-400">
              {live
                ? `Switch ${botName ?? "this bot"} to PAPER to replay its settings. `
                : armedCount === 0
                  ? "Arm at least one stock in the Stocks panel first. "
                  : `Replays the ${armedCount} armed stock${armedCount > 1 ? "s" : ""} with ${botName ? `${botName}'s` : "your current"} settings. `}
              The bot trades each day as it would live (crosses, filters, stop, target, 15:00 cut-off, 15:15
              square-off), then moves to the next trading day; weekends and holidays are skipped. A range is up to 45
              days (about 30 trading days). Each run, with the settings it used, is saved in the Backtests tab of the trade blotter.
              Replay trades go to a separate REPLAY book and never touch today’s PAPER or LIVE results. Needs a Groww
              login for the candles; nothing is ever sent to Groww.
            </p>
            {msg || info?.error ? (
              <p role="alert" className="basis-full text-xs text-rose-300">
                {msg || info?.error}
              </p>
            ) : null}
          </form>
        ) : null}
      </section>
    );
  }

  const clock = clockParts(info?.clock ?? null);
  const daysTotal = info?.days_total ?? 1;
  const multi = daysTotal > 1;
  const dayIndex = info?.day_index ?? 0;
  const progress = replayProgress(info);
  const playing = info?.status === "PLAYING";
  const lagging = playing && info && info.effective_speed > 0 && info.effective_speed < info.speed * 0.8;
  // Playing or paused, the bar shrinks: the top strip already shows the time, day, pause and stop.
  const compact = info?.status === "PLAYING" || info?.status === "PAUSED";

  return (
    <section
      aria-label="Replay"
      className={clsx(
        "rounded-xl bg-violet-500/[0.10]",
        compact ? "border border-violet-400/40 px-2 py-1" : "border-2 border-violet-400/60 px-3 py-2 shadow-[0_0_24px_rgba(139,92,246,0.15)]"
      )}
    >
      <div className={clsx("flex flex-wrap items-center gap-x-3", compact ? "gap-y-1" : "gap-y-2")}>
        <span className="rounded-md bg-violet-500 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider text-white">
          Replay
        </span>
        {info?.status === "LOADING" ? (
          <span className="flex items-center gap-2 text-sm text-violet-100">
            <Loader2 size={14} aria-hidden className="animate-spin" />
            Loading Groww candles… {info.loaded}/{info.total} stocks
          </span>
        ) : (
          <span className={clsx("font-mono text-violet-50", compact ? "text-xs" : "text-sm")} aria-live="off">
            {clock?.label ?? "—"} IST
          </span>
        )}
        {multi && info?.status !== "LOADING" ? (
          <span className="rounded-md bg-violet-400/20 px-2 py-0.5 text-xs font-semibold text-violet-100">
            Day {Math.min(dayIndex + 1, daysTotal)} of {daysTotal}
          </span>
        ) : null}
        {info?.status === "FINISHED" ? (
          <span className="text-xs font-semibold text-emerald-300">
            {multi ? "Run finished — results in the Backtests tab" : "Day finished"}
          </span>
        ) : null}
        {lagging ? (
          <span className="text-[11px] text-amber-300" title="The server is busy; the replay plays as fast as it can.">
            running at ~{Math.round(info!.effective_speed)}×
          </span>
        ) : null}
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <span role="group" aria-label="Replay speed" className="inline-flex rounded-md ring-1 ring-inset ring-violet-300/30">
            {(info?.speeds ?? [1, 10, 60, 300]).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={info?.speed === s}
                disabled={busy || info?.status === "LOADING" || info?.status === "FINISHED"}
                onClick={() => void run(() => smaApi.replayControl("speed", s))}
                className={clsx(
                  "min-h-8 min-w-10 px-2 font-mono text-xs first:rounded-l-md last:rounded-r-md disabled:opacity-50",
                  info?.speed === s ? "bg-violet-400/30 font-semibold text-white" : "text-violet-100 hover:bg-violet-400/10"
                )}
              >
                {s}×
              </button>
            ))}
          </span>
          {info?.status === "PLAYING" || info?.status === "PAUSED" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(() => smaApi.replayControl(playing ? "pause" : "play"))}
              className="flex min-h-8 items-center gap-1 rounded-md px-2.5 text-xs font-semibold text-violet-50 ring-1 ring-inset ring-violet-300/40 hover:bg-violet-400/15 disabled:opacity-50"
            >
              {playing ? <Pause size={13} aria-hidden /> : <Play size={13} aria-hidden />}
              {playing ? "Pause" : "Play"}
            </button>
          ) : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(() => smaApi.replayControl("stop"))}
            title="End the replay. The chart stays on the replayed day; use Back to live on the chart for today. Open replay positions close at the replay price."
            className="flex min-h-8 items-center gap-1 rounded-md bg-white/10 px-2.5 text-xs font-semibold text-white hover:bg-white/15 disabled:opacity-50"
          >
            <Square size={12} aria-hidden />
            {info?.status === "FINISHED" ? "End replay" : "Stop replay"}
          </button>
        </span>
      </div>
      <div className={clsx("overflow-hidden rounded-full bg-black/40", compact ? "mt-1 h-1" : "mt-2 h-1.5")} aria-hidden>
        <div className="h-full rounded-full bg-violet-400 transition-[width]" style={{ width: `${progress * 100}%` }} />
      </div>
      <div className={clsx("mt-1 flex-wrap justify-between gap-x-3 text-[11px] text-violet-200/80", compact ? "hidden" : "flex")}>
        <span>
          {info?.symbols.length ? `${info.symbols.join(", ")} · ` : ""}practice only — no orders reach Groww
        </span>
        <span>{multi && info?.date && info?.end_date ? `${info.days?.[0] ?? info.date} → ${info.end_date}` : "09:15 → 15:30"}</span>
      </div>
      {msg || info?.error ? (
        <p role="alert" className="mt-1 text-xs text-amber-300">
          {msg || info?.error}
        </p>
      ) : null}
    </section>
  );
}
