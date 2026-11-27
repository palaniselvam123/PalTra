"use client";

import clsx from "clsx";
import { Loader2, RotateCcw, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NewTag } from "@/components/ui/NewTag";
import type { StrategyLabChoice, StrategyLabJob, StrategyLabNumber, StrategyLabNumberValue, StrategyLabRules } from "@/lib/smaApi";

type Values = Record<string, StrategyLabNumberValue>;

const input = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";

const same = (a: StrategyLabNumberValue | undefined, b: StrategyLabNumberValue | undefined) =>
  (a ?? null) === (b ?? null) || (a != null && b != null && String(a) === String(b));

/** The Strategy lab's Numbers panel: the numbers behind each rule as the run read them (the bot's Settings, or the
 *  changes the run was read with); change them and read the days again. The bot's Settings stay as they are until
 *  "Apply to bot" / "Backtest this in the replay", which carry the changed numbers over. */
export function LabNumbers({
  job,
  rules,
  choice,
  busy,
  onRead,
}: {
  job: StrategyLabJob;
  rules: StrategyLabRules;
  choice: StrategyLabChoice;
  busy: boolean;
  onRead: (numbers: Values) => void;
}) {
  const specs = useMemo(() => rules.numbers ?? [], [rules.numbers]);
  const read: Values = useMemo(
    () => job.fields ?? ((job.settings?.fields as Values | undefined) ?? {}),
    [job.fields, job.settings]
  );
  const changedRun = useMemo(() => job.numbers ?? {}, [job.numbers]);
  const [draft, setDraft] = useState<Values>(read);
  const [all, setAll] = useState(false);
  useEffect(() => setDraft(read), [read]);

  const labels = useMemo(() => {
    const out = new Map<string, string>([["general", "General"]]);
    rules.entries.forEach((r) => out.set(`entry:${r.name}`, `Check · ${r.label}`));
    rules.exits.forEach((r) => out.set(`exit:${r.name}`, `Exit · ${r.label}`));
    rules.stops.forEach((r) => out.set(`stop:${r.name}`, `Stop · ${r.label}`));
    rules.targets.forEach((r) => out.set(`target:${r.name}`, `Target · ${r.label}`));
    return out;
  }, [rules]);
  const ticked = useMemo(
    () =>
      new Set([
        "general",
        ...choice.entries.map((n) => `entry:${n}`),
        ...choice.exits.map((n) => `exit:${n}`),
        `stop:${choice.stop}`,
        ...choice.targets.map((n) => `target:${n}`),
      ]),
    [choice]
  );
  // Rules with a number changed in the lab are always shown, ticked or not.
  const shown = useMemo(() => {
    const out = new Set(ticked);
    for (const s of specs) if (s.field in changedRun) out.add(s.rule);
    return out;
  }, [ticked, specs, changedRun]);
  const groups = useMemo(() => {
    const out = new Map<string, StrategyLabNumber[]>();
    for (const s of specs) {
      if (!all && !shown.has(s.rule)) continue;
      out.set(s.rule, [...(out.get(s.rule) ?? []), s]);
    }
    return Array.from(out.entries());
  }, [specs, all, shown]);

  if (!specs.length) return null;
  const known = Object.keys(read).length > 0;
  const edits = specs.filter((s) => !same(draft[s.field], read[s.field]));
  const send = (): Values => {
    const out: Values = { ...changedRun };
    for (const s of edits) {
      const v = draft[s.field];
      if (v === null || v === "") delete out[s.field];
      else out[s.field] = v;
    }
    return out;
  };
  const set = (spec: StrategyLabNumber, raw: string | boolean) => {
    let v: StrategyLabNumberValue = raw as StrategyLabNumberValue;
    if (spec.kind === "num" || spec.kind === "int") v = raw === "" ? null : spec.kind === "int" ? Math.round(Number(raw)) : Number(raw);
    setDraft((d) => ({ ...d, [spec.field]: v }));
  };

  return (
    <details className="rounded-lg border border-slate-800 bg-base/60 p-3 text-xs" open={Object.keys(changedRun).length > 0}>
      <summary className="cursor-pointer font-semibold text-slate-200">
        Numbers behind the rules
        {Object.keys(changedRun).length ? (
          <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-200">{Object.keys(changedRun).length} changed in the lab</span>
        ) : null}{" "}
        <NewTag id="lab-numbers" link={false} />
      </summary>
      <p className="mt-2 text-slate-400">
        The numbers this run read: Bot {job.bot}&apos;s Settings, or the ones you changed here (amber). Change any, then
        &quot;Read the days again with these numbers&quot;: the bot&apos;s Settings stay as they are. Lab numbers are for every
        stock (they replace a stock&apos;s own setting). &quot;Apply to bot&quot; and &quot;Backtest this in the replay&quot;
        carry the amber ones over.
      </p>
      {!known ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-amber-200">This run was read before the lab kept its numbers. Read the same stocks and days again to see and change them.</span>
          <button
            type="button"
            onClick={() => onRead({})}
            disabled={busy}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Read the days again
          </button>
        </div>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-slate-300">
              <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show every rule&apos;s numbers (not only the ticked ones)
            </label>
          </div>
          <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {groups.map(([rule, list]) => (
              <div key={rule} className={clsx("rounded-md p-2 ring-1 ring-inset", ticked.has(rule) ? "ring-slate-700" : "ring-slate-800 opacity-75")}>
                <div className="mb-1 font-medium text-slate-300">{labels.get(rule) ?? rule}</div>
                {list.map((spec) => {
                  const v = draft[spec.field];
                  const edited = !same(v, read[spec.field]);
                  const fromLab = spec.field in changedRun;
                  return (
                    <label key={spec.field} className="flex items-center justify-between gap-2 py-0.5 text-slate-400">
                      <span className={clsx(fromLab && "text-amber-200", edited && "text-accentSky")}>{spec.label}</span>
                      {spec.kind === "bool" ? (
                        <input type="checkbox" checked={Boolean(v)} onChange={(e) => set(spec, e.target.checked)} />
                      ) : spec.kind === "choice" ? (
                        <select value={String(v ?? "")} onChange={(e) => set(spec, e.target.value)} className={input}>
                          {v == null && <option value="">default</option>}
                          {(spec.choices ?? []).map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type={spec.kind === "text" ? "text" : "number"}
                          step={spec.kind === "int" ? 1 : "any"}
                          value={v == null ? "" : String(v)}
                          placeholder="default"
                          onChange={(e) => set(spec, e.target.value)}
                          className={clsx(input, spec.kind === "text" ? "w-20" : "w-20 text-right")}
                        />
                      )}
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => onRead(send())}
              disabled={busy || (!edits.length && !Object.keys(changedRun).length)}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-accentSky ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Read the days again with these numbers
            </button>
            {edits.length ? (
              <button type="button" onClick={() => setDraft(read)} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-slate-800">
                Undo my {edits.length} edit(s)
              </button>
            ) : null}
            {Object.keys(changedRun).length ? (
              <button
                type="button"
                onClick={() => onRead({})}
                disabled={busy}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-slate-800 disabled:opacity-50"
              >
                <RotateCcw size={13} /> Back to Bot {job.bot}&apos;s numbers
              </button>
            ) : null}
            <span className="text-slate-500">Same stocks and days; the candles are cached, so it is quick.</span>
          </div>
        </>
      )}
    </details>
  );
}
