"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Loader2, Pencil, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { smaApi, type ScanArmPreset, type ScanArmRun, type ScanArmState } from "@/lib/smaApi";

const SIZES = [1, 2, 3, 5, 10, 15];
const LISTS: { v: ScanArmPreset["lists"][number]; label: string }[] = [
  { v: "fno", label: "All F&O" },
  { v: "nifty50", label: "Nifty 50" },
  { v: "nifty100", label: "Nifty 100" },
  { v: "niftybank", label: "Nifty Bank" },
  { v: "armed", label: "Armed stocks" },
];
const DIRS: { v: ScanArmPreset["direction"]; label: string }[] = [
  { v: "SAME", label: "Bullish or bearish" },
  { v: "UP", label: "Bullish" },
  { v: "DOWN", label: "Bearish" },
];

type Draft = Omit<ScanArmPreset, "id" | "fired" | "last" | "rule"> & { id?: string };

const blank = (bot: 1 | 2 | 3 | 4): Draft => ({
  name: "Strong trend",
  bot,
  lists: ["fno"],
  minutes: 5,
  sizes: [5],
  direction: "SAME",
  kind: "STRONG",
  max_age_min: 0,
  min_gap_pct: 0,
  skip_fading: false,
  top_n: 5,
  times: [],
  on: false,
});

/** Ready-made recipes for "New": the owner's two examples. */
const TEMPLATES: { label: string; make: (bot: 1 | 2 | 3 | 4) => Draft }[] = [
  {
    label: "Strong bullish / bearish at 10:00 and 10:15",
    make: (bot) => ({ ...blank(bot), name: "Strong trend 10:00", times: ["10:00", "10:15"], on: true }),
  },
  {
    label: "Same trend on 1m, 5m and 15m → Bot 3",
    make: () => ({ ...blank(3), name: "1m/5m/15m trend", sizes: [1, 5, 15], kind: "SIDE", top_n: 5 }),
  },
];

const hhmm = (iso?: string | null) => (iso ? iso.slice(11, 16) : "");

function RunLine({ run }: { run: ScanArmRun }) {
  return (
    <div className="text-xs">
      <p className="text-slate-300">
        <span className="font-mono text-slate-400">{hhmm(run.at)}</span>{" "}
        <span className="font-semibold">{run.name}</span>
        <span className="text-slate-500"> · {run.trigger === "schedule" ? `scheduled ${run.time ?? ""}` : run.trigger}</span>
        {run.bot_name ? <span className="text-slate-500"> · {run.bot_name}</span> : null}
        {run.mode === "LIVE" ? <span className="ml-1 rounded bg-loss/20 px-1 text-[10px] font-semibold text-loss">LIVE</span> : null}
      </p>
      {run.error ? (
        <p className="text-amber-300">{run.error}</p>
      ) : (
        <p className="text-slate-400">
          {run.matched.length} of {run.scanned} matched
          {run.armed.length ? (
            <>
              {" · armed "}
              <span className="text-emerald-300">{run.armed.join(", ")}</span>
            </>
          ) : null}
          {run.already.length ? ` · already armed ${run.already.join(", ")}` : ""}
          {run.skipped.length ? (
            <span className="text-amber-300"> · not armed {run.skipped.map((s) => `${s.symbol} (${s.why})`).join("; ")}</span>
          ) : null}
        </p>
      )}
    </div>
  );
}

function Chips<T extends string | number>({
  options,
  value,
  onChange,
  multi,
}: {
  options: { v: T; label: string }[];
  value: T[];
  onChange: (next: T[]) => void;
  multi?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((o) => {
        const on = value.includes(o.v);
        return (
          <button
            key={String(o.v)}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(multi ? (on ? value.filter((x) => x !== o.v) : [...value, o.v]) : [o.v])}
            className={clsx(
              "min-h-8 rounded-md px-2 text-xs ring-1 ring-inset",
              on ? "bg-accentSky/15 font-semibold text-accentSky ring-accentSky/50" : "text-slate-400 ring-white/10 hover:bg-white/5"
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Editor({ draft, onCancel, onSaved }: { draft: Draft; onCancel: () => void; onSaved: () => void }) {
  const [d, setD] = useState<Draft>(draft);
  const [timeText, setTimeText] = useState(draft.times.join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));

  const save = async () => {
    const times = timeText
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean)
      .map((t) => (/^\d:\d\d$/.test(t) ? `0${t}` : t));
    if (!d.sizes.length) return setError("Tick at least one candle size.");
    if (!d.lists.length) return setError("Tick at least one stock list.");
    setBusy(true);
    setError(null);
    try {
      await smaApi.scanArmSave({ ...d, times, on: d.on && times.length > 0 });
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const num = (k: "max_age_min" | "min_gap_pct" | "top_n", label: string, step: number, hint: string) => (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      <input
        type="number"
        min={0}
        step={step}
        value={d[k]}
        onChange={(e) => set(k, Number(e.target.value) || 0)}
        className="h-8 w-24 rounded-md border border-border bg-surface px-1.5 font-mono text-xs text-slate-200"
      />
      <span className="text-[10px] text-slate-500">{hint}</span>
    </label>
  );

  return (
    <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.03] p-3">
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Name
          <input
            value={d.name}
            maxLength={40}
            onChange={(e) => set("name", e.target.value)}
            className="h-8 w-52 rounded-md border border-border bg-surface px-1.5 text-xs text-slate-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Arm on
          <select
            value={d.bot}
            onChange={(e) => set("bot", Number(e.target.value) as 1 | 2 | 3 | 4)}
            className="h-8 rounded-md border border-border bg-surface px-1.5 text-xs text-slate-200"
          >
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                Bot {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div>
        <p className="mb-1 text-xs text-slate-400">Stocks to scan</p>
        <Chips options={LISTS} value={d.lists} onChange={(v) => set("lists", v)} multi />
      </div>
      <div>
        <p className="mb-1 text-xs text-slate-400">Trend on every one of these candle sizes</p>
        <Chips options={SIZES.map((m) => ({ v: m, label: `${m}m` }))} value={d.sizes} onChange={(v) => set("sizes", [...v].sort((a, b) => a - b))} multi />
      </div>
      <div className="flex flex-wrap gap-4">
        <div>
          <p className="mb-1 text-xs text-slate-400">Judged by</p>
          <Chips
            options={[
              { v: "STRONG" as const, label: "Strong trend" },
              { v: "SIDE" as const, label: "SMA side" },
            ]}
            value={[d.kind]}
            onChange={(v) => set("kind", v[0])}
          />
        </div>
        <div>
          <p className="mb-1 text-xs text-slate-400">Direction</p>
          <Chips options={DIRS} value={[d.direction]} onChange={(v) => set("direction", v[0])} />
        </div>
      </div>
      <div className="flex flex-wrap gap-4">
        {num("top_n", "Arm at most", 1, "best ranks first")}
        {num("max_age_min", "Trend age at most (min)", 5, "0 = any age")}
        {num("min_gap_pct", "SMA gap at least (%)", 0.05, "0 = any gap")}
        <label className="flex items-center gap-2 self-center text-xs text-slate-300">
          <input type="checkbox" checked={d.skip_fading} onChange={(e) => set("skip_fading", e.target.checked)} />
          Skip fading trends
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Run by itself at (IST, 09:15-15:30)
          <input
            value={timeText}
            placeholder="10:00, 10:15"
            onChange={(e) => setTimeText(e.target.value)}
            className="h-8 w-52 rounded-md border border-border bg-surface px-1.5 font-mono text-xs text-slate-200"
          />
        </label>
        <label className="flex items-center gap-2 pb-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={d.on} onChange={(e) => set("on", e.target.checked)} />
          Schedule on (trading days, PAPER bots only)
        </label>
      </div>
      {error ? <p className="text-xs text-amber-300">{error}</p> : null}
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void save()}
          className="inline-flex h-8 items-center gap-1 rounded-md bg-accentSky/20 px-3 text-xs font-semibold text-accentSky hover:bg-accentSky/30 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : null} Save
        </button>
        <button type="button" onClick={onCancel} className="h-8 rounded-md px-3 text-xs text-slate-400 hover:bg-white/5">
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * Scan & arm (scan_arm.py): saved recipes that run the Trend quality scan and arm the matching stocks on
 * a bot, now (Run now) or by themselves at set times. Arming only: the bot trades them on its next signal.
 */
export function ScanArm({ defaultBot, onClose }: { defaultBot: 1 | 2 | 3 | 4; onClose: () => void }) {
  const [state, setState] = useState<ScanArmState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const load = useCallback(() => {
    smaApi
      .scanArm()
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Scan & arm did not load"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  // Poll while a run is going (a scan takes a few minutes).
  useEffect(() => {
    if (!state?.running) return;
    const id = window.setInterval(load, 2000);
    return () => window.clearInterval(id);
  }, [state?.running, load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !editing && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editing]);

  const run = async (p: ScanArmPreset) => {
    setNote(null);
    setStarting(p.id);
    try {
      try {
        await smaApi.scanArmRun(p.id);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "";
        if (!/LIVE/.test(msg)) throw e;
        const ok = window.confirm(
          `Bot ${p.bot} is LIVE. Arm the stocks this scan finds on it? Bot ${p.bot} can then buy or sell them with real money on its next signal.`
        );
        if (!ok) return;
        await smaApi.scanArmRun(p.id, true);
      }
      setNote(`${p.name}: scanning… the stocks it finds are armed on Bot ${p.bot} when the scan ends.`);
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Could not start");
    } finally {
      setStarting(null);
      load();
    }
  };

  const remove = async (p: ScanArmPreset) => {
    if (!window.confirm(`Delete "${p.name}"?`)) return;
    await smaApi.scanArmDelete(p.id).catch((e: unknown) => setNote(e instanceof Error ? e.message : "Could not delete"));
    load();
  };

  const toggle = async (p: ScanArmPreset) => {
    await smaApi.scanArmSave({ id: p.id, on: !p.on }).catch((e: unknown) => setNote(e instanceof Error ? e.message : "Could not save"));
    load();
  };

  const blocked = state && (!state.scans_enabled || state.paused);

  return createPortal(
    <div
      className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && !editing && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Scan and arm"
        className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl"
      >
        <div className="mb-1 flex items-center gap-2">
          <h2 className="mr-auto font-semibold text-slate-100">Scan &amp; arm</h2>
          <button type="button" aria-label="Refresh" onClick={load} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <RefreshCw size={16} />
          </button>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          Runs the Trend quality scan and arms the best-ranked stocks whose trend points the same way on every ticked candle
          size. Arming only: no order is placed here. The bot trades them on its next SMA cross; tick &quot;Join the morning
          trend&quot; on that bot (Settings → 1. When to enter) to enter a trend already running.
          {state?.arms_desk ? " On this server the stocks go on the main desk's bots." : ""}
        </p>
        {error ? <p className="mb-2 text-xs text-amber-300">{error}</p> : null}
        {blocked ? (
          <p className="mb-2 rounded-md bg-amber-500/10 px-2 py-1.5 text-xs text-amber-300">
            {!state?.scans_enabled
              ? "Scans are off on this server (SCANS=off): use Scan & arm on the scanner server's terminal."
              : "Scans are paused (top of the Scalp page), so nothing can run."}
          </p>
        ) : null}
        {state?.running ? (
          <p className="mb-2 flex items-center gap-1.5 rounded-md bg-accentSky/10 px-2 py-1.5 text-xs text-accentSky">
            <Loader2 size={13} className="animate-spin" /> {state.running.name}: {state.running.stage}
          </p>
        ) : null}
        {note ? <p className="mb-2 text-xs text-slate-300">{note}</p> : null}

        {editing ? (
          <Editor
            draft={editing}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              load();
            }}
          />
        ) : (
          <>
            <ul className="space-y-2">
              {(state?.presets ?? []).map((p) => (
                <li key={p.id} className="rounded-lg border border-white/10 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-100">{p.name}</span>
                    <span className="text-xs text-slate-400">→ Bot {p.bot}</span>
                    <span className="ml-auto flex gap-1">
                      <button
                        type="button"
                        disabled={!!state?.running || !!blocked || starting === p.id}
                        onClick={() => void run(p)}
                        className="inline-flex h-8 items-center gap-1 rounded-md bg-emerald-500/15 px-2.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-40"
                      >
                        {starting === p.id ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run now
                      </button>
                      <button
                        type="button"
                        aria-label={`Edit ${p.name}`}
                        onClick={() => setEditing({ ...p })}
                        className="rounded-md p-2 text-slate-400 hover:bg-white/5"
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${p.name}`}
                        onClick={() => void remove(p)}
                        className="rounded-md p-2 text-slate-400 hover:bg-white/5"
                      >
                        <Trash2 size={14} />
                      </button>
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {p.rule} · {p.lists.map((l) => LISTS.find((x) => x.v === l)?.label ?? l).join(" + ")}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    {p.times.length ? (
                      <label className="flex items-center gap-1.5 text-slate-300">
                        <input type="checkbox" checked={p.on} onChange={() => void toggle(p)} />
                        Runs by itself at <span className="font-mono">{p.times.join(", ")}</span>
                      </label>
                    ) : (
                      <span className="text-slate-500">No schedule: Run now only</span>
                    )}
                  </div>
                  {p.last ? (
                    <div className="mt-1.5 border-t border-white/5 pt-1.5">
                      <RunLine run={p.last} />
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
            {state && !state.presets.length ? (
              <p className="text-xs text-slate-400">No recipes yet. Start from one of these:</p>
            ) : null}
            <div className="mt-3 flex flex-wrap gap-2">
              {TEMPLATES.map((t) => (
                <button
                  key={t.label}
                  type="button"
                  onClick={() => setEditing(t.make(defaultBot))}
                  className="inline-flex h-8 items-center gap-1 rounded-md border border-white/10 px-2.5 text-xs text-slate-300 hover:bg-white/5"
                >
                  <Plus size={13} /> {t.label}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setEditing(blank(defaultBot))}
                className="inline-flex h-8 items-center gap-1 rounded-md border border-white/10 px-2.5 text-xs text-slate-300 hover:bg-white/5"
              >
                <Plus size={13} /> New recipe
              </button>
            </div>
            <p className="mt-2 text-[11px] text-slate-500">
              A scheduled run only arms a PAPER bot and sends the result as a Telegram; a time missed by more than{" "}
              {state?.grace_min ?? 15} minutes (a restart) is skipped. A LIVE bot is armed only from Run now, after you confirm.
            </p>
            {state?.recent.length ? (
              <div className="mt-3">
                <p className="mb-1 text-xs font-semibold text-slate-300">Last runs</p>
                <div className="space-y-1.5">
                  {state.recent.map((r) => (
                    <RunLine key={`${r.at}-${r.preset}`} run={r} />
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>,
    document.body
  );
}
