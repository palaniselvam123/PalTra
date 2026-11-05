"use client";

import clsx from "clsx";
import { BookOpen, CheckCircle2, FastForward, Loader2, Pause, Play, Search, Square, XCircle } from "lucide-react";
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
    <section className="terminal-dark space-y-4 rounded-xl border border-border bg-surface p-3 shadow-card sm:p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="max-w-3xl">
          <h2 className="text-lg font-semibold text-slate-100">Sideways test</h2>
          <p className="mt-0.5 text-sm text-slate-300">
            If a stock was choppy in the last hour, will it stay choppy in the next hour? This test checks it on your own stocks
            and past days, so you know which stocks to skip or give to the sideways bot (Bot 2).
          </p>
        </div>
        <Link
          href="/learn/?lesson=sideways-test"
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <BookOpen size={14} aria-hidden /> Full guide
        </Link>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Mood kind="trend" />
        <Mood kind="side" />
      </div>

      <div>
        <h3 className="text-sm font-semibold text-slate-200">How the test works</h3>
        <ol className="mt-2 grid gap-2 sm:grid-cols-3">
          <Step n={1} icon={<Pause size={16} aria-hidden />} title="It pauses past days">
            At 10:00, 11:00, 12:00, 13:00, 14:00 and 14:30 on each past day.
          </Step>
          <Step n={2} icon={<Search size={16} aria-hidden />} title={`It looks back ${span} min`}>
            And notes simple clues, like how many times the SMA lines crossed.
          </Step>
          <Step n={3} icon={<FastForward size={16} aria-hidden />} title={`It looks ahead ${span} min`}>
            Did the stock really trend, or stay sideways? Then it counts which clues were right.
          </Step>
        </ol>
      </div>

      <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
        <h3 className="text-sm font-semibold text-slate-200">Choose and run</h3>
        <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-slate-300">
          <label className="block">
            <span className="mb-0.5 block text-[11px] text-slate-400">Which stocks</span>
            <ScanListPicker value={lists} onChange={setLists} disabled={running} />
          </label>
          <Pick label="Past days" hint="10 or more is best" value={days} options={DAYS} onChange={setDays} disabled={running} suffix=" days" />
          <Pick label="Candle size" hint="Same as your bot" value={minutes} options={SIZES} onChange={setMinutes} disabled={running} suffix=" min" />
          <Pick label="Look back and ahead" hint="One 'hour' of the test" value={span} options={WINDOWS} onChange={setSpan} disabled={running} suffix=" min" />
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
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/20 px-4 font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/30 disabled:opacity-50"
            >
              {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Play size={13} aria-hidden />} Run test
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-slate-500">It only reads past candles. It never places an order and never changes a setting.</p>
      </div>

      {msg ? <p className="text-xs text-amber-300">{msg}</p> : null}
      {running ? (
        <div className="space-y-1">
          <p className="flex items-center gap-2 text-xs text-sky-300">
            <Loader2 size={13} className="animate-spin" aria-hidden /> Reading stock {data.done} of {data.total}… one at a time, so
            the bots keep their Groww limit.
          </p>
          <div className="h-2 overflow-hidden rounded-full bg-white/10" aria-hidden>
            <div className="h-full rounded-full bg-sky-400/70" style={{ width: `${data.total ? (100 * data.done) / data.total : 0}%` }} />
          </div>
        </div>
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
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Every clue, best first</h3>
            <p className="text-xs text-slate-400">
              Green bar = how often the stock trended next after this clue&apos;s &quot;trend&quot; reading; amber = after its
              &quot;sideways&quot; reading. The line is the normal rate. Far apart = a useful clue.
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {rep.signals.map((s) => (
              <SignalCard key={s.key} s={s} base={rep.base_trended_pct ?? 0} minBucket={rep.min_bucket ?? 30} win={win} />
            ))}
          </div>
          <p className="text-xs text-slate-500">
            Tested on {rep.samples} pauses, {rep.stocks} stocks, {rep.days} days, {data?.params.minutes}-minute candles
            {rep.stopped ? " (stopped early)" : ""}.
          </p>
        </>
      ) : rep ? (
        <p className="text-xs text-slate-400">The last test found nothing to read (no candles for those days?).</p>
      ) : (
        <p className="rounded-lg border border-dashed border-white/15 p-3 text-sm text-slate-400">
          No test yet. Pick your stocks above and press <b>Run test</b>. The answer shows here.
        </p>
      )}

      <details className="rounded-lg border border-white/10 p-3 text-sm">
        <summary className="cursor-pointer font-semibold text-slate-200">Words used on this page</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-[max-content_1fr]">
          <dt className="font-semibold text-slate-200">Trended</dt>
          <dd className="text-slate-400">In that time the price moved at least one normal step (ATR), mostly in one direction.</dd>
          <dt className="font-semibold text-slate-200">Sideways</dt>
          <dd className="text-slate-400">It went up and down and ended near where it started.</dd>
          <dt className="font-semibold text-slate-200">SMA 9 / SMA 21</dt>
          <dd className="text-slate-400">Average price of the last 9 / 21 candles: the red and blue lines on your chart.</dd>
          <dt className="font-semibold text-slate-200">Cross</dt>
          <dd className="text-slate-400">The red line passing the blue line. Your bots buy or sell on it.</dd>
          <dt className="font-semibold text-slate-200">ADX</dt>
          <dd className="text-slate-400">A trend-strength number: under 20 = weak trend, above 25 = strong trend.</dd>
          <dt className="font-semibold text-slate-200">Normal rate</dt>
          <dd className="text-slate-400">How often the stocks trended in any hour. Every clue is compared with it.</dd>
        </dl>
      </details>
    </section>
  );
}

/** A small picture of the two market moods: one clean cross and a ride, or many crosses going nowhere. */
function Mood({ kind }: { kind: "trend" | "side" }) {
  const trend = kind === "trend";
  return (
    <div className={clsx("rounded-lg border p-3", trend ? "border-emerald-400/40" : "border-amber-400/40")}>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className={clsx("text-sm font-semibold", trend ? "text-emerald-300" : "text-amber-300")}>{trend ? "Trending" : "Sideways (choppy)"}</h3>
        <span className={clsx("text-xs", trend ? "text-emerald-300" : "text-amber-300")}>{trend ? "Bots earn here" : "Bots lose here"}</span>
      </div>
      <svg viewBox="0 0 240 80" className="mt-1 h-auto w-full max-w-[300px]" role="img" aria-label={trend ? "Price rising with one SMA cross" : "Price zig-zagging with many SMA crosses"}>
        {trend ? (
          <>
            <polyline points="8,70 50,64 92,54 134,42 176,30 232,16" fill="none" className="text-blue-300" stroke="currentColor" strokeWidth="2.5" />
            <polyline points="8,74 50,62 92,48 134,34 176,22 232,8" fill="none" className="text-rose-300" stroke="currentColor" strokeWidth="2.5" />
            <circle cx="40" cy="64" r="5" className="text-amber-300" fill="currentColor" />
          </>
        ) : (
          <>
            <polyline points="8,42 232,40" fill="none" className="text-blue-300" stroke="currentColor" strokeWidth="2.5" />
            <polyline points="8,52 30,30 54,52 78,28 102,52 126,30 150,52 174,28 198,52 232,30" fill="none" className="text-rose-300" stroke="currentColor" strokeWidth="2.5" />
            {[19, 42, 66, 90, 114, 138, 162, 186, 214].map((x) => (
              <circle key={x} cx={x} cy={41} r="4" className="text-amber-300" fill="currentColor" />
            ))}
          </>
        )}
      </svg>
      <p className="text-xs text-slate-400">
        {trend
          ? "One cross (the dot), then a long ride. Your SMA bots make their money in moves like this."
          : "The lines cross again and again (each dot is a cross). Every cross is a small loss plus Groww charges."}
      </p>
    </div>
  );
}

function Step({ n, icon, title, children }: { n: number; icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-2 rounded-lg border border-white/10 p-2.5">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-sky-500/20 text-sky-300" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-slate-100">
          {n}. {title}
        </p>
        <p className="text-xs text-slate-400">{children}</p>
      </div>
    </li>
  );
}

/** Each clue in plain words: what it is, and how to read each of its groups. */
const CLUES: Record<string, { name: string; what: string; group: (b: string) => string }> = {
  crosses: {
    name: "How many SMA crosses",
    what: "How often the red and blue lines crossed. Many crosses = choppy.",
    group: (b) => (b === "0" ? "no cross" : b === "1" ? "1 cross" : b === "3+" ? "3 or more crosses" : `${b} crosses`),
  },
  turns: {
    name: "How many SMA 9 turns",
    what: "How often the red line changed direction. Many turns = going nowhere.",
    group: (b) => (b === "0" ? "no turn" : b === "1" ? "1 turn" : b === "3+" ? "3 or more turns" : `${b} turns`),
  },
  adx: {
    name: "ADX (trend strength)",
    what: "A trend-strength number. Low = weak or no trend, high = strong trend.",
    group: (b) => `ADX ${b}`,
  },
  efficiency: {
    name: "How straight the price moved",
    what: "0 = up and down and back to the start; 1 = a straight line one way.",
    group: (b) => `straightness ${b}`,
  },
  overlap: {
    name: "Candles sitting on each other",
    what: "How much each candle covers the one before. Lots of overlap = sideways.",
    group: (b) => `overlap ${b}`,
  },
  gap: {
    name: "Space between the two SMAs",
    what: "How far apart the red and blue lines are. Almost touching = usually no trend.",
    group: (b) => `SMA gap ${b}`,
  },
};

function clue(key: string, label: string) {
  return CLUES[key] ?? { name: label, what: "", group: (b: string) => b };
}

function rating(spread: number): { word: string; tone: string; border: string } {
  if (spread >= 20) return { word: "Good clue", tone: "bg-emerald-500/20 text-emerald-200", border: "border-emerald-400/40" };
  if (spread >= 10) return { word: "Some use", tone: "bg-amber-500/20 text-amber-200", border: "border-amber-400/30" };
  return { word: "Not useful", tone: "bg-white/10 text-slate-300", border: "border-white/10" };
}

/** The two groups with enough pauses that trended the most and the least next. */
function extremes(s: RegimeSignal, minBucket: number) {
  const usable = s.buckets.filter((b) => b.samples >= minBucket && b.trended_pct != null);
  if (usable.length < 2) return null;
  const sorted = [...usable].sort((a, b) => (b.trended_pct ?? 0) - (a.trended_pct ?? 0));
  return { hi: sorted[0], lo: sorted[sorted.length - 1] };
}

/** One horizontal bar: a value out of 100 with the normal rate marked. */
function Bar({ label, value, base, tone }: { label: string; value: number; base: number; tone: "trend" | "side" | "base" }) {
  return (
    <div className="grid grid-cols-[minmax(0,9rem)_1fr_3rem] items-center gap-2 text-xs sm:grid-cols-[minmax(0,12rem)_1fr_3rem]">
      <span className="truncate text-slate-300" title={label}>
        {label}
      </span>
      <span className="relative h-3 overflow-hidden rounded-full bg-white/10">
        <span
          className={clsx(
            "absolute inset-y-0 left-0 rounded-full",
            tone === "trend" ? "bg-emerald-400/80" : tone === "side" ? "bg-amber-400/80" : "bg-slate-400/60"
          )}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
        {tone !== "base" ? <span className="absolute inset-y-0 w-0.5 bg-slate-300" style={{ left: `${Math.min(100, base)}%` }} /> : null}
      </span>
      <span className="text-right font-mono tabular-nums text-slate-200">{Math.round(value)}%</span>
    </div>
  );
}

function Answer({ rep, win }: { rep: NonNullable<Study["report"]>; win: number }) {
  const top = rep.signals[0];
  const ends = top ? extremes(top, rep.min_bucket ?? 30) : null;
  const c = top ? clue(top.key, top.label) : null;
  const yes = Boolean(rep.best && ends && c);
  const base = rep.base_trended_pct ?? 0;
  return (
    <div className={clsx("space-y-3 rounded-lg border p-3 sm:p-4", yes ? "border-emerald-400/40 bg-emerald-500/10" : "border-white/15 bg-white/[0.04]")}>
      <p className="flex items-start gap-2 text-base font-semibold text-slate-100">
        {yes ? <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-300" aria-hidden /> : <XCircle size={20} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />}
        {yes && c ? `Yes. "${c.name}" tells you early if the next ${win} minutes will be choppy.` : "No clue works well on these stocks yet."}
      </p>
      {yes && ends && c ? (
        <>
          <div className="space-y-1.5">
            <Bar label="Normally" value={base} base={base} tone="base" />
            <Bar label={`After ${c.group(ends.hi.bucket)}`} value={ends.hi.trended_pct ?? 0} base={base} tone="trend" />
            <Bar label={`After ${c.group(ends.lo.bucket)}`} value={ends.lo.trended_pct ?? 0} base={base} tone="side" />
          </div>
          <p className="text-sm text-slate-200">
            Read it like this: normally the stock trended in <b>{Math.round(base)}</b> out of 100 hours. After{" "}
            <b>{c.group(ends.hi.bucket)}</b> it trended in <b>{Math.round(ends.hi.trended_pct ?? 0)}</b>; after{" "}
            <b>{c.group(ends.lo.bucket)}</b> only in <b>{Math.round(ends.lo.trended_pct ?? 0)}</b>.
          </p>
          <div className="rounded-md bg-black/20 p-2.5 text-sm text-slate-200">
            <p className="font-semibold">What you can do with it</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-300">
              <li>
                When a stock shows <b>{c.group(ends.lo.bucket)}</b>, expect sideways: skip it on Bot 1 / Bot 3, or let Bot 2 (the
                sideways bot) trade it.
              </li>
              <li>Try it on a PAPER bot first and replay the same days with and without it. Keep it only if the P&amp;L is better.</li>
              <li>Run this test again next week. A clue that keeps winning is one you can trust.</li>
            </ul>
          </div>
        </>
      ) : (
        <p className="text-sm text-slate-300">
          Normally the stock trended in <b>{Math.round(base)}</b> out of 100 hours, and every clue gave about the same. So none of
          them helps you pick trending stocks here. Try more days, other stocks or another candle size.
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
  hint,
  value,
  options,
  onChange,
  disabled,
  suffix = "",
}: {
  label: string;
  hint?: string;
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
      {hint ? <span className="mt-0.5 block text-[11px] text-slate-500">{hint}</span> : null}
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
      {ends ? (
        <div className="mt-2 space-y-1.5">
          <Bar label={`After ${c.group(ends.hi.bucket)}`} value={ends.hi.trended_pct ?? 0} base={base} tone="trend" />
          <Bar label={`After ${c.group(ends.lo.bucket)}`} value={ends.lo.trended_pct ?? 0} base={base} tone="side" />
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-400">Too few pauses to say.</p>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-sky-300">Show all the numbers</summary>
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="py-1 font-normal">When the clue was</th>
              <th className="py-1 text-right font-normal">Pauses</th>
              <th className="py-1 text-right font-normal">Trended next {win} min</th>
              <th className="py-1 text-right font-normal" title="Average SMA crosses in the next window: more = choppier">
                Crosses after
              </th>
              <th className="py-1 text-right font-normal" title="Average result of simply following the SMA side in the next window, before charges">
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
                  <td className="py-1 text-right tabular-nums">{b.trended_pct == null ? "—" : `${b.trended_pct}%`}</td>
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
          Normal rate: {base}%. Grey rows had fewer than {minBucket} pauses: too few to trust.
        </p>
      </details>
    </div>
  );
}
