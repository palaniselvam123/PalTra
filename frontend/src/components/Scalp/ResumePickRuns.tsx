"use client";

/**
 * "Carry on?" for Scalp-page backtests cut short by a deploy, a restart or Stop (display + one button).
 *
 * Lists this kind's runs that still have days to play (GET /api/replay/resumable, bots 1-4, `kind`), and Resume
 * carries a run on from its first unfinished day on its own bot's replay player: the finished days keep their picks
 * and trades, the unfinished day plays again, with the rule, stock list and settings the run was started with.
 * Practice money only; nothing is armed and no order is sent.
 */
import { Loader2, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { smaApi, type ResumableRun } from "@/lib/smaApi";

const HIDE_KEY = "scalp.resume.hidden";
const BOTS = [1, 2, 3, 4];

export type PickKind = "TREND" | "ACTIVE" | "AUTOARM" | "SCALP";

function shortDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
}

function hiddenIds(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(HIDE_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch {
    return [];
  }
}

export function ResumePickRuns({ kinds, label }: { kinds: PickKind[]; label: string }) {
  const [runs, setRuns] = useState<ResumableRun[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const kindKey = kinds.join(",");

  const load = useCallback(async () => {
    const lists = await Promise.all(BOTS.map((b) => smaApi.replayResumable(b).catch(() => [] as ResumableRun[])));
    const hidden = hiddenIds();
    const wanted = kindKey.split(",");
    setRuns(
      lists
        .flat()
        .filter((r) => r.kind && wanted.includes(r.kind) && !hidden.includes(String(r.id)))
        .sort((a, b) => b.id - a.id)
        .slice(0, 3),
    );
  }, [kindKey]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const hide = (id: number) => {
    try {
      localStorage.setItem(HIDE_KEY, JSON.stringify([...hiddenIds(), String(id)].slice(-50)));
    } catch {
      /* private window */
    }
    setRuns((rs) => rs.filter((r) => r.id !== id));
  };

  const resume = async (run: ResumableRun) => {
    setBusy(run.id);
    setError(null);
    try {
      await smaApi.replayResume(run.id, 300, run.bot);
      setNote(
        `Run #${run.id} carries on from day ${run.days_done + 1} on Bot ${run.bot}'s replay. Follow it on the terminal (Bot ${run.bot}) or in the Backtests tab.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Resume failed");
    } finally {
      setBusy(null);
    }
  };

  if (!runs.length && !note && !error) return null;
  return (
    <div className="mb-3 space-y-1.5">
      {runs.map((run) => (
        <div
          key={run.id}
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-violet-400/30 bg-violet-500/[0.06] px-3 py-2 text-xs"
        >
          <RotateCcw size={13} aria-hidden className="text-slate-400" />
          <span className="text-slate-300">
            {label} run #{run.id} on Bot {run.bot} ({shortDay(run.start_date)} → {shortDay(run.end_date)}) stopped after day{" "}
            {run.days_done} of {run.days_total}
            {run.status === "INTERRUPTED" ? " when the server restarted (a deploy)" : ""}. Carry on from day {run.days_done + 1}
            {run.next_day ? ` (${shortDay(run.next_day)})` : ""}?
          </span>
          <span className="ml-auto flex gap-1.5">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void resume(run)}
              title="The finished days keep their picks and trades; the unfinished day picks and plays again, with the rule, stock list and settings the run was started with"
              className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-xs font-semibold text-violet-300 ring-1 ring-inset ring-violet-400/40 hover:bg-violet-500/10 disabled:opacity-50"
            >
              {busy === run.id ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <Play size={13} aria-hidden />} Resume
            </button>
            <button type="button" onClick={() => hide(run.id)} className="min-h-8 rounded-md px-2 text-xs text-slate-400 hover:text-slate-200">
              Not now
            </button>
          </span>
        </div>
      ))}
      {note ? <p className="text-xs text-emerald-300">{note}</p> : null}
      {error ? (
        <p role="alert" className="text-xs text-rose-300">
          {error}
        </p>
      ) : null}
    </div>
  );
}
