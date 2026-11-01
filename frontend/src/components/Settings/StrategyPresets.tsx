"use client";

import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { BookmarkPlus, History, RotateCcw, Trash2 } from "lucide-react";
import { smaApi, type SmaConfig, type StrategyPreset, type StrategyPresets as Listing, type StrategySnapshot } from "@/lib/smaApi";

/**
 * Saved strategies and the history of saves for this desk: "Save current as…" names the
 * numbers on screen; every Save is kept automatically (last 30) so "what was it before?"
 * has an answer. Load puts a copy's numbers into the form; the usual Save then applies
 * them. Each entry says how many fields differ from the form now, and which.
 */
export function StrategyPresets({
  current,
  labels,
  refreshKey,
  onLoad,
}: {
  /** The strategy fields the form would save now. */
  current: Partial<SmaConfig>;
  /** Plain names for the fields, for the differences list. */
  labels: Record<string, string>;
  /** Bumped after each Save, so the history reloads. */
  refreshKey: number;
  onLoad: (settings: Partial<SmaConfig>, note: string) => void;
}) {
  const [data, setData] = useState<Listing | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    smaApi
      .strategyPresets()
      .then(setData)
      .catch((e: unknown) => setNote(e instanceof Error ? e.message : "Could not load the saved strategies"));
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const run = async (work: () => Promise<Listing>, done: string) => {
    setBusy(true);
    setNote(null);
    try {
      setData(await work());
      setNote(done);
    } catch (e: unknown) {
      setNote(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  const same = (a: unknown, b: unknown) => {
    if (typeof a === "boolean" || typeof b === "boolean") return Boolean(a) === Boolean(b);
    const na = Number(a);
    const nb = Number(b);
    if (a !== "" && b !== "" && Number.isFinite(na) && Number.isFinite(nb)) return na === nb;
    return String(a ?? "") === String(b ?? "");
  };
  const diffs = (settings: Partial<SmaConfig>) =>
    (Object.keys(settings) as (keyof SmaConfig)[])
      .filter((k) => k in current && !same(settings[k], current[k]))
      .map((k) => ({ key: k, label: labels[k] ?? k, from: current[k], to: settings[k] }));
  const show = (v: unknown) => (typeof v === "boolean" ? (v ? "on" : "off") : String(v ?? "—"));

  const entry = (id: string, title: string, when: string, settings: Partial<SmaConfig>, extra?: React.ReactNode) => {
    const d = diffs(settings);
    const isOpen = open === id;
    return (
      <li key={id} className="rounded-md border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-semibold text-slate-100">{title}</span>
          <span className="text-slate-400">{when}</span>
          <button
            type="button"
            onClick={() => setOpen(isOpen ? null : id)}
            aria-expanded={isOpen}
            className={clsx("rounded-full px-2 py-0.5 ring-1 ring-inset", d.length ? "text-amber-200 ring-amber-400/40 hover:bg-amber-400/10" : "text-slate-400 ring-white/10")}
          >
            {d.length ? `${d.length} ${d.length === 1 ? "field differs" : "fields differ"} from now` : "same as now"}
          </button>
          <span className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              disabled={busy}
              onClick={() => onLoad(settings, title)}
              className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
            >
              <RotateCcw size={12} aria-hidden /> Load
            </button>
            {extra}
          </span>
        </div>
        {isOpen && d.length ? (
          <ul className="mt-1.5 grid gap-x-4 gap-y-0.5 border-t border-white/10 pt-1.5 sm:grid-cols-2">
            {d.map((x) => (
              <li key={x.key} className="flex justify-between gap-2 font-mono">
                <span className="truncate font-sans text-slate-400">{x.label}</span>
                <span className="shrink-0">
                  <span className="text-slate-500">{show(x.from)}</span> → <span className="text-slate-100">{show(x.to)}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </li>
    );
  };

  const mine = data?.presets.filter((p) => p.desk === data.desk) ?? [];
  const others = data?.presets.filter((p) => p.desk !== data.desk) ?? [];
  const deskName = (d: string) => (d === "research" ? "Research" : `Bot ${d}`);
  const presetRow = (p: StrategyPreset) =>
    entry(
      `p${p.id}`,
      p.name,
      `${p.desk === data?.desk ? "" : `${deskName(p.desk)} · `}${p.saved_at}`,
      p.settings,
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          if (window.confirm(`Delete the saved strategy "${p.name}"?`)) void run(() => smaApi.deleteStrategyPreset(p.id), "Deleted.");
        }}
        aria-label={`Delete ${p.name}`}
        className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-md text-rose-300 ring-1 ring-inset ring-rose-400/30 hover:bg-rose-500/10 disabled:opacity-50"
      >
        <Trash2 size={12} aria-hidden />
      </button>
    );

  return (
    <details className="group/presets mt-3 rounded-lg border border-white/10 bg-white/[0.02]">
      <summary className="flex min-h-10 cursor-pointer list-none flex-wrap items-center gap-2 px-3 text-sm font-semibold text-slate-100 [&::-webkit-details-marker]:hidden">
        <BookmarkPlus size={15} aria-hidden className="text-sky-300" />
        Saved strategies &amp; history
        <span className="text-xs font-normal text-slate-400">
          {data ? `${mine.length} saved here · ${data.history.length} earlier save${data.history.length === 1 ? "" : "s"}` : "…"}
        </span>
      </summary>
      <div className="px-3 pb-3">
        <form
          className="mt-1 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = name.trim();
            if (!n) return;
            void run(() => smaApi.saveStrategyPreset(n, current), `Saved "${n}" with the numbers on screen (unsaved edits included).`).then(() => setName(""));
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Save the current settings as
            <input
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Scalp 1-min, Trend 5-min…"
              className="min-h-9 w-56 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            />
          </label>
          <button
            type="submit"
            disabled={busy || !name.trim()}
            className="min-h-9 rounded-md bg-sky-600 px-3 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
          >
            Save as
          </button>
          {note ? <span className="text-xs text-slate-400">{note}</span> : null}
        </form>
        <p className="mt-1 text-[11px] text-slate-500">
          A preset keeps the strategy numbers only (not the Trade list, PAPER/LIVE or per-stock settings). Load puts them in the
          form; press Save to make the bot use them.
        </p>
        {mine.length ? <ul className="mt-2 space-y-1.5">{mine.map(presetRow)}</ul> : <p className="mt-2 text-xs text-slate-400">No saved strategy on this desk yet.</p>}
        {others.length ? (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-slate-400">Saved on other desks ({others.length})</summary>
            <ul className="mt-1.5 space-y-1.5">{others.map(presetRow)}</ul>
          </details>
        ) : null}
        <h5 className="mt-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
          <History size={13} aria-hidden /> Earlier saves on this desk (automatic, last 30)
        </h5>
        {data?.history.length ? (
          <ul className="mt-1.5 space-y-1.5">
            {data.history.map((h: StrategySnapshot, i) => entry(`h${i}`, i === 0 ? "Last save" : `Save ${data.history.length - i}`, h.saved_at, h.settings))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-slate-400">Nothing yet: each Save from now on is kept here.</p>
        )}
      </div>
    </details>
  );
}
