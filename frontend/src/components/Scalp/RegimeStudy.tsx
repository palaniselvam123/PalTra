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
  return (
    <section className="space-y-3 rounded-xl border border-border bg-surface p-3 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Sideways test: does a reading hold for the next hour?</h2>
          <p className="mt-0.5 max-w-3xl text-xs text-slate-400">
            For past days of the picked stocks, at 10:00, 11:00, 12:00, 13:00, 14:00 and 14:30 it reads six chop signals on
            the window just finished and checks what the stock did in the window after. A good signal shows very different
            &quot;next window trended&quot; numbers from one bucket to the next. Read-only: no order is placed.
          </p>
        </div>
        <Link
          href="/learn/?lesson=sideways-test"
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <BookOpen size={14} aria-hidden /> How to use it (guide)
        </Link>
      </div>

      <div className="flex flex-wrap items-end gap-2 text-xs text-slate-300">
        <ScanListPicker value={lists} onChange={setLists} disabled={running} />
        <Pick label="Past days" value={days} options={DAYS} onChange={setDays} disabled={running} />
        <Pick label="Candle (min)" value={minutes} options={SIZES} onChange={setMinutes} disabled={running} />
        <Pick label="Window (min)" value={span} options={WINDOWS} onChange={setSpan} disabled={running} />
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
          <Loader2 size={13} className="animate-spin" aria-hidden /> Reading stock {data.done} of {data.total}… (one stock at a
          time, so the bots keep Groww&apos;s limit)
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
          <div className="rounded-lg border border-border bg-surface2 p-3 text-sm text-slate-200">
            <div>
              {rep.samples} checks on {rep.stocks} stocks over {rep.days} days ({data?.params.minutes}-min candles,{" "}
              {data?.params.window}-min windows{rep.stopped ? ", stopped early" : ""}).{" "}
              <b>Base rate:</b> the next window trended <b>{rep.base_trended_pct}%</b> of the time, with{" "}
              {rep.base_next_crosses} SMA crosses on average; following the SMA side made {pct(rep.base_cross_pnl_pct)} per
              window before charges.
            </div>
            <div className="mt-1 text-xs text-slate-400">
              {rep.best
                ? `Best signal: ${rep.signals[0].label} (its buckets are ${rep.signals[0].spread} points apart). Use its strongest bucket as a filter and test it in a backtest.`
                : "No signal separates trending from sideways next windows by 10 points or more: none of them predicts the next hour on these stocks."}
            </div>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {rep.signals.map((s) => (
              <SignalCard key={s.key} s={s} base={rep.base_trended_pct ?? 0} minBucket={rep.min_bucket ?? 30} />
            ))}
          </div>
        </>
      ) : rep ? (
        <p className="text-xs text-slate-400">The last test found no checks (no candles for those days?).</p>
      ) : null}
    </section>
  );
}

function pct(v: number | undefined) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}%`;
}

function Pick({ label, value, options, onChange, disabled }: { label: string; value: number; options: number[]; onChange: (v: number) => void; disabled?: boolean }) {
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
          </option>
        ))}
      </select>
    </label>
  );
}

function SignalCard({ s, base, minBucket }: { s: RegimeSignal; base: number; minBucket: number }) {
  const strong = s.spread >= 20;
  const some = s.spread >= 10;
  return (
    <div className={clsx("rounded-lg border p-3", strong ? "border-emerald-400/40" : some ? "border-amber-400/30" : "border-white/10")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">{s.label}</h3>
        <span className={clsx("text-xs", strong ? "text-emerald-300" : some ? "text-amber-300" : "text-slate-500")}>
          spread {s.spread} pts · {s.verdict}
        </span>
      </div>
      <table className="mt-2 w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500">
            <th className="py-1 font-normal">Reading</th>
            <th className="py-1 text-right font-normal">Checks</th>
            <th className="py-1 font-normal">Next window trended</th>
            <th className="py-1 text-right font-normal">Next crosses</th>
            <th className="py-1 text-right font-normal">SMA-follow P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {s.buckets.map((b) => {
            const thin = b.samples < minBucket;
            return (
              <tr key={b.bucket} className={clsx("border-t border-white/5", thin && "text-slate-600")}>
                <td className="py-1">{b.bucket}</td>
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
                        <span className="absolute inset-y-0 w-0.5 bg-slate-500" style={{ left: `${Math.min(100, base)}%` }} />
                      </span>
                      <span className="tabular-nums">
                        {b.trended_pct}% ({(b.vs_base ?? 0) >= 0 ? "+" : ""}
                        {b.vs_base})
                      </span>
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
        Grey rows have fewer than {minBucket} checks: too few to trust. The dark tick on each bar is the base rate.
      </p>
    </div>
  );
}
