"use client";

import { NewTag } from "@/components/ui/NewTag";
import { smaApi, type AutoArmBody, type BacktestQueue, type QueueItem } from "@/lib/smaApi";
import clsx from "clsx";
import { CalendarClock, ListOrdered, Loader2, Pause, Play, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const BOTS = [1, 2, 3, 4];

const STATUS: Record<QueueItem["status"], { text: string; cls: string }> = {
  QUEUED: { text: "Waiting", cls: "bg-slate-500/20 text-slate-300" },
  PLAYING: { text: "Playing", cls: "bg-sky-500/20 text-sky-300" },
  DONE: { text: "Done", cls: "bg-emerald-500/20 text-emerald-300" },
  FAILED: { text: "Not started", cls: "bg-rose-500/20 text-rose-300" },
  STOPPED: { text: "Stopped", cls: "bg-amber-500/20 text-amber-300" },
  INTERRUPTED: { text: "Cut short", cls: "bg-amber-500/20 text-amber-300" },
  CANCELLED: { text: "Cancelled", cls: "bg-slate-500/20 text-slate-400" },
};

function clock(ts: number | null): string {
  if (!ts) return "";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(
    new Date(ts * 1000),
  );
}

function templateText(t: Partial<AutoArmBody>): string {
  if (!t || !t.source) return "not saved yet";
  const src = t.source === "TREND" ? "Trend quality" : t.source === "ACTIVE" ? "Most active" : "Trend quality + Most active";
  const stocks = t.source === "ACTIVE" ? "" : ` · ${t.universe?.length ?? 0} stocks read`;
  return `${src} · arm ${t.first}-${t.last} every ${t.every_min} min · ${t.minutes}-min ${t.candles === "HA" ? "Heikin Ashi " : ""}candles · trade until ${t.until || "15:30"}${stocks}${t.play_handoffs ? " · hand-offs played" : ""}`;
}

function BotChips({ value, onChange }: { value: number[]; onChange: (v: number[]) => void }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {BOTS.map((b) => {
        const on = value.includes(b);
        return (
          <button
            key={b}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== b) : [...value, b].sort())}
            className={clsx(
              "min-h-8 rounded-md px-2 text-xs ring-1 ring-inset",
              on ? "bg-sky-500/20 font-semibold text-sky-200 ring-sky-400/50" : "text-slate-400 ring-white/15",
            )}
          >
            Bot {b}
          </button>
        );
      })}
    </span>
  );
}

/**
 * Backtest queue and daily schedule (backend backtest_queue.py), under the auto-arm replay form. Owner's case: two
 * auto-arm backtests played at once froze the backtest server. Queued backtests play one after another (never next to
 * another replay); the schedule queues today's auto-arm backtest on the ticked bots every weekday after a time.
 * Practice money only: nothing is armed or ordered.
 */
export function BacktestQueuePanel({ form, bot }: { form: () => Promise<Omit<AutoArmBody, "bot">>; bot: number }) {
  const [q, setQ] = useState<BacktestQueue | null>(null);
  const [bots, setBots] = useState<number[]>([bot]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [at, setAt] = useState("15:45");
  const [schedBots, setSchedBots] = useState<number[]>([bot]);
  const [loadedSched, setLoadedSched] = useState(false);

  useEffect(() => setBots((b) => (b.length === 1 ? [bot] : b)), [bot]);

  const load = useCallback(async () => {
    try {
      const next = await smaApi.backtestQueue();
      setQ(next);
      if (!loadedSched) {
        setAt(next.schedule.at);
        setSchedBots(next.schedule.bots);
        setLoadedSched(true);
      }
    } catch {
      /* the server may be busy; the next poll tries again */
    }
  }, [loadedSched]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(t);
  }, [load]);

  const act = async (key: string, fn: () => Promise<BacktestQueue | null>, ok?: string) => {
    setBusy(key);
    setMsg(null);
    try {
      const next = await fn();
      if (next) setQ(next);
      if (ok) setMsg({ ok: true, text: ok });
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Could not do that" });
    } finally {
      setBusy(null);
    }
  };

  const addToQueue = () =>
    act(
      "add",
      async () => {
        if (!bots.length) throw new Error("Tick at least one bot.");
        const body = await form();
        let last: BacktestQueue | null = null;
        for (const b of bots) last = await smaApi.backtestQueueAdd({ ...body, bot: b });
        return last;
      },
      `Queued on ${bots.map((b) => `Bot ${b}`).join(", ")}. They play one after another; follow each on its bot's replay bar.`,
    );

  const saveSchedule = (on: boolean, withTemplate: boolean) =>
    act(
      "sched",
      async () => {
        if (!schedBots.length) throw new Error("Tick at least one bot for the schedule.");
        let template: Partial<AutoArmBody> | undefined;
        if (withTemplate) {
          const body = await form();
          const { date: _d, end_date: _e, ...rest } = body;
          void _d;
          void _e;
          template = rest;
        }
        return smaApi.backtestQueueSchedule({ on, at, bots: schedBots, ...(template ? { template } : {}) });
      },
      withTemplate ? "Saved: the schedule uses the settings in the form above." : on ? "Schedule on." : "Schedule off.",
    );

  const s = q?.schedule;
  const items = q?.items ?? [];
  const finished = items.some((i) => !["QUEUED", "PLAYING"].includes(i.status));
  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";

  return (
    <div className="mt-3 space-y-3 rounded-md p-3 ring-1 ring-inset ring-white/10">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
        <ListOrdered size={14} className="text-sky-300" />
        <b className="text-sm text-slate-100">Backtest queue</b> <NewTag id="bt-queue" />
        <span className="text-slate-400">one after another, so the server never plays two at once</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
        Queue the form above on
        <BotChips value={bots} onChange={setBots} />
        <button
          type="button"
          onClick={() => void addToQueue()}
          disabled={busy !== null}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/50 disabled:opacity-50"
        >
          {busy === "add" ? <Loader2 size={13} className="animate-spin" /> : null}
          Add to queue
        </button>
        {q ? (
          <>
            <button
              type="button"
              onClick={() => void act("pause", () => smaApi.backtestQueuePause(!q.paused))}
              disabled={busy !== null}
              className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/15"
            >
              {q.paused ? <Play size={13} /> : <Pause size={13} />}
              {q.paused ? "Resume the queue" : "Pause the queue"}
            </button>
            {finished ? (
              <button
                type="button"
                onClick={() => void act("clear", () => smaApi.backtestQueueClear())}
                disabled={busy !== null}
                className="min-h-9 rounded-md px-2 text-slate-400 ring-1 ring-inset ring-white/15"
              >
                Clear finished
              </button>
            ) : null}
          </>
        ) : null}
      </div>
      {q ? (
        <p className="text-xs text-slate-400">
          {q.paused ? "Paused: nothing new starts (a playing backtest carries on). " : ""}
          {q.waiting} waiting{q.playing ? ` · playing: ${q.playing.label}` : ""}. A queued backtest starts only while no replay is
          playing on any bot, at full speed.
        </p>
      ) : (
        <p className="text-xs text-slate-500">Reading the queue…</p>
      )}
      {items.length ? (
        <ul className="divide-y divide-white/5 text-xs">
          {items.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2 py-1.5">
              <span className={clsx("rounded px-1.5 py-0.5 font-semibold", STATUS[i.status]?.cls)}>{STATUS[i.status]?.text ?? i.status}</span>
              <span className="text-slate-200">{i.label}</span>
              {i.origin === "schedule" ? (
                <span className="inline-flex items-center gap-1 text-slate-400">
                  <CalendarClock size={12} /> scheduled
                </span>
              ) : null}
              {i.run_id ? <span className="text-slate-400">run #{i.run_id}</span> : null}
              <span className="text-slate-500">
                {i.finished_at ? `ended ${clock(i.finished_at)}` : i.started_at ? `started ${clock(i.started_at)}` : `added ${clock(i.added_at)}`}
              </span>
              {i.note ? <span className={i.status === "FAILED" ? "text-amber-300" : "text-slate-400"}>{i.note}</span> : null}
              {i.status !== "PLAYING" ? (
                <button
                  type="button"
                  onClick={() => void act(`rm-${i.id}`, () => smaApi.backtestQueueRemove(i.id))}
                  disabled={busy !== null}
                  aria-label={i.status === "QUEUED" ? "Take it off the queue" : "Remove from the list"}
                  title={i.status === "QUEUED" ? "Take it off the queue" : "Remove from the list"}
                  className="ml-auto inline-flex min-h-8 min-w-8 items-center justify-center rounded text-slate-400 hover:text-rose-300"
                >
                  <Trash2 size={13} />
                </button>
              ) : (
                <span className="ml-auto text-slate-500">stop it on Bot {i.body.bot}&apos;s replay bar</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="space-y-2 border-t border-white/10 pt-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
          <CalendarClock size={14} className="text-sky-300" />
          <b className="text-sm text-slate-100">Every weekday, backtest today</b> <NewTag id="bt-schedule" />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
          <label className="flex min-h-8 items-center gap-1.5">
            <input type="checkbox" checked={Boolean(s?.on)} disabled={busy !== null || !s} onChange={(e) => void saveSchedule(e.target.checked, false)} />
            On, after
          </label>
          <input
            type="time"
            min="15:31"
            max="23:59"
            value={at}
            onChange={(e) => setAt(e.target.value)}
            onBlur={() => s && at !== s.at && void saveSchedule(s.on, false)}
            className={select}
            aria-label="Time of the daily backtest (IST, after the close)"
          />
          IST, on
          <BotChips value={schedBots} onChange={(v) => setSchedBots(v)} />
          <button
            type="button"
            onClick={() => void saveSchedule(Boolean(s?.on), true)}
            disabled={busy !== null}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/50 disabled:opacity-50"
          >
            {busy === "sched" ? <Loader2 size={13} className="animate-spin" /> : null}
            Use the form&apos;s settings and these bots
          </button>
        </div>
        <p className="text-xs text-slate-400">
          Settings: {templateText(s?.template ?? {})}. Each ticked bot gets its own backtest of that day, queued one after
          another. The days in the form are not used: it is always today.
        </p>
        {s?.last_note ? <p className="text-xs text-slate-400">Last: {s.last_note}</p> : null}
        <p className="text-xs text-slate-500">
          The backtest server stops itself when idle; the main desk wakes it at this time (it needs BACKTEST_URL and
          BACKTEST_LINK_TOKEN on the desk, like Pull from backtest server). Most active needs that day&apos;s saved scans, so
          keep its Auto scan on during the market. The bots must be in PAPER.
        </p>
      </div>
      {msg ? <p className={msg.ok ? "text-xs text-emerald-300" : "text-xs font-semibold text-amber-300"}>{msg.text}</p> : null}
    </div>
  );
}
