"use client";

import clsx from "clsx";
import { BookOpen, Loader2, Play, Square } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists } from "@/components/Scalp/ScanLists";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { smaApi, type RegimeSignal, type RegimeStudy as Study } from "@/lib/smaApi";

/**
 * Sideways test (backend regime_study.py): does a "sideways" or "trending" reading hold for the next hour? For past
 * days of the picked stocks, every check time (10:00-14:30) reads six chop signals on the window just finished and
 * what the stock did in the window after, then shows per signal how often the next window trended. Read-only: no
 * order, nothing in the strategy reads it. Guide: /learn/?lesson=sideways-test.
 */
const POLL_MS = 3000;
const DAYS = [5, 10, 15, 20, 30];
const SIZES = [1, 3, 5, 10, 15];
const WINDOWS = [30, 60, 90];

export function RegimeStudy({ armed, paused, onStatus }: { armed: Map<string, string[]>; paused?: boolean; onStatus?: OnScanStatus }) {
  const [lists, setLists] = useScanLists("sma.regime.lists", ["armed"]);
  const [days, setDays] = useState(10);
  const [minutes, setMinutes] = useState(5);
  const [span, setSpan] = useState(60);
  const [data, setData] = useState<Study | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const v = await smaApi.regimeStudy();
      setData(v);
      onStatus?.({ when: v.finished_at, running: v.status === "RUNNING" });
    } catch {
      /* the server may be waking up */
    }
  }, [onStatus]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (data?.status !== "RUNNING") return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [data?.status, load]);

  const run = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const symbols = await resolveScanSymbols(lists, armed.keys());
      if (!symbols.length) {
        setMsg("No stocks in the picked lists.");
        return;
      }
      const v = await smaApi.regimeStudyStart({ symbols: symbols.slice(0, 200), days, minutes, window: span });
      setData(v);
      if (symbols.length > 200) setMsg(`Only the first 200 of ${symbols.length} stocks are read.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      setData(await smaApi.regimeStudyStop());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const running = data?.status === "RUNNING";
  const rep = data?.report;
  const win = data?.params.window ?? span;
  return (
    <section className="terminal-dark space-y-3 rounded-xl border border-border bg-surface p-3 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Sideways test: can we tell early if a stock will trend or go sideways?</h2>
          <ol className="mt-1 max-w-3xl list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
            <li>It goes back over past days of your stocks and stops at 10:00, 11:00, 12:00, 13:00, 14:00 and 14:30.</li>
            <li>At each stop it looks at a few simple clues from the last {span} minutes (for example: how many SMA crosses there were).</li>
            <li>Then it checks what really happened in the next {span} minutes: did the stock trend (move clearly one way) or stay sideways?</li>
          </ol>
          <p className="mt-1 max-w-3xl text-xs text-slate-400">
            A clue is useful when it tells you something: for example &quot;after 0 crosses the stock trended 60% of the time, after 3
            crosses only 20%&quot;. Nothing here trades; it only reads past candles.
          </p>
        </div>
        <Link
          href="/learn/?lesson=sideways-test"
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <BookOpen size={14} aria-hidden /> Full guide
        </Link>
      </div>

      <div className="flex flex-wrap items-end gap-2 text-xs text-slate-300">
        <ScanListPicker value={lists} onChange={setLists} disabled={running} />
        <Pick label="How many past days" value={days} options={DAYS} onChange={setDays} disabled={running} suffix=" days" />
        <Pick label="Candle size" value={minutes} options={SIZES} onChange={setMinutes} disabled={running} suffix=" min" />
        <Pick label="Look at the last" value={span} options={WINDOWS} onChange={setSpan} disabled={running} suffix=" min" />
        {running ? (
          <button type="button" onClick={stop} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-rose-200 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/10">
            <Square size={13} aria-hidden /> Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={run}
            disabled={busy || paused}
            title={paused ? "Scans are paused" : undefined}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/20 px-3 font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/30 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Play size={13} aria-hidden />} Run test
          </button>
        )}
      </div>
      {msg ? <p className="text-xs text-amber-300">{msg}</p> : null}
      {running ? (
        <p className="flex items-center gap-2 text-xs text-sky-300">
          <Loader2 size={13} className="animate-spin" aria-hidden /> Reading stock {data.done} of {data.total}… (one at a time, so
          the bots keep their Groww limit)
        </p>
      ) : null}
      {data?.errors?.length ? (
        <p className="text-xs text-slate-500">
          Skipped: {data.errors.map((e) => `${e.symbol || "?"} (${e.error})`).slice(0, 6).join(", ")}
          {data.errors.length > 6 ? ` and ${data.errors.length - 6} more` : ""}
        </p>
      ) : null}

      {rep && rep.samples > 0 ? (
        <>
          <Answer rep={rep} win={win} />
          <h3 className="pt-1 text-sm font-semibold text-slate-200">Each clue, best first</h3>
          <div className="grid gap-3 lg:grid-cols-2">
            {rep.signals.map((s) => (
              <SignalCard key={s.key} s={s} base={rep.base_trended_pct ?? 0} minBucket={rep.min_bucket ?? 30} win={win} />
            ))}
          </div>
          <p className="text-xs text-slate-500">
            Tested on {rep.samples} stops, {rep.stocks} stocks, {rep.days} days, {data?.params.minutes}-minute candles
            {rep.stopped ? " (stopped early)" : ""}.
          </p>
        </>
      ) : rep ? (
        <p className="text-xs text-slate-400">The last test found nothing to read (no candles for those days?).</p>
      ) : null}
    </section>
  );
}

/** Each clue in plain words: what it is, and how to read each of its groups. */
const CLUES: Record<string, { name: string; what: string; group: (b: string) => string }> = {
  crosses: {
    name: "Number of SMA crosses",
    what: "How many times the fast and slow SMA crossed in the last minutes. Many crosses = choppy.",
    group: (b) => (b === "0" ? "no cross" : b === "1" ? "1 cross" : b === "3+" ? "3 or more crosses" : `${b} crosses`),
  },
  turns: {
    name: "Number of SMA 9 turns",
    what: "How many times the fast SMA changed direction (a V or U). Many turns = going nowhere.",
    group: (b) => (b === "0" ? "no turn" : b === "1" ? "1 turn" : b === "3+" ? "3 or more turns" : `${b} turns`),
  },
  adx: {
    name: "ADX (trend strength)",
    what: "A standard trend-strength number. Low = weak or no trend, high = strong trend.",
    group: (b) => `ADX ${b}`,
  },
  efficiency: {
    name: "How straight the price moved",
    what: "0 = it went up and down and ended where it started; 1 = it went in a straight line one way.",
    group: (b) => `straightness ${b}`,
  },
  overlap: {
    name: "Candles on top of each other",
    what: "How much each candle covers the one before. Lots of overlap = sideways.",
    group: (b) => `overlap ${b}`,
  },
  gap: {
    name: "Gap between the two SMAs",
    what: "How far apart the fast and slow SMA are. A tiny gap usually means no trend.",
    group: (b) => `SMA gap ${b}`,
  },
};

function clue(key: string, label: string) {
  return CLUES[key] ?? { name: label, what: "", group: (b: string) => b };
}

function rating(spread: number): { word: string; tone: string; border: string } {
  if (spread >= 20) return { word: "Good clue", tone: "bg-emerald-500/20 text-emerald-200", border: "border-emerald-400/40" };
  if (spread >= 10) return { word: "Some use", tone: "bg-amber-500/20 text-amber-200", border: "border-amber-400/30" };
  return { word: "Not useful", tone: "bg-slate-500/20 text-slate-300", border: "border-white/10" };
}

/** The two groups with enough stops that trended the most and the least next. */
function extremes(s: RegimeSignal, minBucket: number) {
  const usable = s.buckets.filter((b) => b.samples >= minBucket && b.trended_pct != null);
  if (usable.length < 2) return null;
  const sorted = [...usable].sort((a, b) => (b.trended_pct ?? 0) - (a.trended_pct ?? 0));
  return { hi: sorted[0], lo: sorted[sorted.length - 1] };
}

function Answer({ rep, win }: { rep: NonNullable<Study["report"]>; win: number }) {
  const top = rep.signals[0];
  const ends = top ? extremes(top, rep.min_bucket ?? 30) : null;
  const c = top ? clue(top.key, top.label) : null;
  return (
    <div className="rounded-lg border border-sky-400/30 bg-sky-500/10 p-3 text-sm text-slate-100">
      <p className="text-base font-semibold text-slate-100">
        {rep.best && c ? `Answer: yes, “${c.name}” is the best clue.` : "Answer: no clue predicts it well on these stocks."}
      </p>
      <p className="mt-1">
        Normally the stock trended in the next {win} minutes <b>{rep.base_trended_pct}%</b> of the time (and went sideways the rest).
      </p>
      {rep.best && ends && c ? (
        <p className="mt-1">
          With <b>{c.group(ends.hi.bucket)}</b> it trended <b>{ends.hi.trended_pct}%</b> of the time; with{" "}
          <b>{c.group(ends.lo.bucket)}</b> only <b>{ends.lo.trended_pct}%</b>. So when you see {c.group(ends.lo.bucket)}, expect
          sideways: a good time to hand the stock to the sideways bot (Bot 2) or skip it. Try it as a filter in a backtest first.
        </p>
      ) : (
        <p className="mt-1 text-slate-300">
          Every clue gave about the same result as the normal rate, so none of them is worth using to pick trending stocks here.
          Try other stocks, more days or a different candle size.
        </p>
      )}
    </div>
  );
}

function pct(v: number | undefined) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}%`;
}

function Pick({
  label,
  value,
  options,
  onChange,
  disabled,
  suffix = "",
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (v: number) => void;
  disabled?: boolean;
  suffix?: string;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] text-slate-400">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-0.5 min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
            {suffix}
          </option>
        ))}
      </select>
    </label>
  );
}

function SignalCard({ s, base, minBucket, win }: { s: RegimeSignal; base: number; minBucket: number; win: number }) {
  const r = rating(s.spread);
  const c = clue(s.key, s.label);
  const ends = extremes(s, minBucket);
  return (
    <div className={clsx("rounded-lg border p-3", r.border)}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">{c.name}</h3>
        <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", r.tone)}>{r.word}</span>
      </div>
      {c.what ? <p className="mt-0.5 text-xs text-slate-400">{c.what}</p> : null}
      <p className="mt-2 text-sm text-slate-200">
        {ends ? (
          <>
            {c.group(ends.hi.bucket)}: trended next <b>{ends.hi.trended_pct}%</b> of the time · {c.group(ends.lo.bucket)}:{" "}
            <b>{ends.lo.trended_pct}%</b>
          </>
        ) : (
          "Too few stops to say."
        )}
      </p>
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-sky-300">Show all the numbers</summary>
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="py-1 font-normal">When the clue was</th>
              <th className="py-1 text-right font-normal">Stops</th>
              <th className="py-1 font-normal">Trended in the next {win} min</th>
              <th className="py-1 text-right font-normal" title="Average SMA crosses in the next window: more = choppier">
                Crosses after
              </th>
              <th
                className="py-1 text-right font-normal"
                title="Average result of simply following the SMA side (flip on each cross) in the next window, before charges"
              >
                SMA-follow result
              </th>
            </tr>
          </thead>
          <tbody>
            {s.buckets.map((b) => {
              const thin = b.samples < minBucket;
              return (
                <tr key={b.bucket} className={clsx("border-t border-white/5", thin && "text-slate-600")}>
                  <td className="py-1">{c.group(b.bucket)}</td>
                  <td className="py-1 text-right tabular-nums">{b.samples}</td>
                  <td className="py-1">
                    {b.trended_pct == null ? (
                      "—"
                    ) : (
                      <span className="flex items-center gap-2">
                        <span className="relative h-2 w-24 overflow-hidden rounded bg-slate-500/25" aria-hidden>
                          <span
                            className={clsx("absolute inset-y-0 left-0", b.trended_pct >= base ? "bg-emerald-400/70" : "bg-rose-400/70")}
                            style={{ width: `${Math.min(100, b.trended_pct)}%` }}
                          />
                          <span className="absolute inset-y-0 w-0.5 bg-slate-300" style={{ left: `${Math.min(100, base)}%` }} />
                        </span>
                        <span className="tabular-nums">{b.trended_pct}%</span>
                      </span>
                    )}
                  </td>
                  <td className="py-1 text-right tabular-nums">{b.next_crosses ?? "—"}</td>
                  <td className={clsx("py-1 text-right tabular-nums", (b.cross_pnl_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300")}>
                    {pct(b.cross_pnl_pct)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="mt-1 text-[11px] text-slate-500">
          Green bar = trended more than normal ({base}%, the light line), red = less. Grey rows had fewer than {minBucket} stops:
          too few to trust.
        </p>
      </details>
    </div>
  );
}
