"use client";

import { useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { History, Loader2 } from "lucide-react";
import { replayActive, smaApi } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";

const DAYS = [5, 10, 20];
const PICK_TIMES = ["09:30", "09:45", "10:00", "10:30", "11:00", "12:00"];
const TOP_N = [1, 2, 3, 5];
const MAX_UNIVERSE = 60;

type Props = {
  /** The stocks the page is rating now: the pool each day picks from. */
  universe: string[];
  minAtr: number;
  minValue: number;
};

/**
 * "What if I had traded this page's picks?" For each past day, the stocks are
 * scored at the pick time exactly as this page scores them live, the top N
 * become that day's picks, and the SMA bot trades only those for the rest of
 * the day. Practice money; nothing is armed and no order is sent.
 */
export function ScalpPickBacktest({ universe, minAtr, minValue }: Props) {
  const [days, setDays] = useState(10);
  const [pickTime, setPickTime] = useState("09:45");
  const [topN, setTopN] = useState(3);
  const [requireBias, setRequireBias] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const pool = universe.slice(0, MAX_UNIVERSE);

  const run = async () => {
    if (!pool.length) return;
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const current = await smaApi.replayInfo().catch(() => null);
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm("A replay is already running. Stop it and start this backtest instead?")) return;
      }
      await smaApi.replayScalpPicks({
        date: range[0],
        end_date: range[range.length - 1],
        universe: pool,
        pick_time: pickTime,
        top_n: topN,
        min_atr_pct: minAtr,
        min_value_cr: minValue,
        require_bias: requireBias,
      });
      setNote({
        ok: true,
        text: `Started: ${range[0]} → ${range[range.length - 1]}, top ${topN} of ${pool.length} stocks at ${pickTime} each day. Downloading candles takes a few minutes, then each day replays; results build up in the SMA terminal's Backtests tab with the day's picks.`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-bg px-1.5 py-1 text-xs text-slate-200";
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <History size={15} className="text-accentViolet" /> Test this page&apos;s picks on past days
      </h2>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        For each past day, every stock in the list below is scored at the pick time the way this page scores it live
        (your Min ATR and Min traded filters), the top picks are taken, and the SMA bot trades only those from that
        minute until square-off. Groww keeps no past bid/ask, so the spread check is skipped. Practice money only —
        nothing is armed and no order is sent.
      </p>
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading days
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Pick at
          <select value={pickTime} onChange={(e) => setPickTime(e.target.value)} className={select}>
            {PICK_TIMES.map((t) => (
              <option key={t} value={t}>
                {t} IST
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Picks per day
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className={select}>
            {TOP_N.map((n) => (
              <option key={n} value={n}>
                Top {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-9 cursor-pointer items-center gap-1.5 self-end">
          <input type="checkbox" checked={requireBias} onChange={(e) => setRequireBias(e.target.checked)} className="accent-bot" />
          Only stocks with a LONG/SHORT bias
        </label>
        <button
          type="button"
          disabled={busy || pool.length === 0}
          onClick={run}
          className="inline-flex min-h-9 items-center gap-1.5 self-end rounded-md bg-accentViolet/15 px-3 font-semibold text-accentViolet ring-1 ring-inset ring-accentViolet/40 hover:bg-accentViolet/25 disabled:opacity-40"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <History size={13} />} Run scalp-pick backtest
        </button>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Picks from {pool.length} stock{pool.length === 1 ? "" : "s"}
        {universe.length > MAX_UNIVERSE ? ` (the first ${MAX_UNIVERSE} of ${universe.length})` : ""} · Min ATR {minAtr}%/min ·
        Min traded ₹{minValue} cr by the pick time.
      </p>
      {note && (
        <p className={clsx("mt-2 text-xs", note.ok ? "text-accentViolet" : "text-amber-300")}>
          {note.text}{" "}
          {note.ok && (
            <Link href="/terminal" className="font-semibold text-accentSky underline-offset-2 hover:underline">
              Open the SMA terminal →
            </Link>
          )}
        </p>
      )}
    </section>
  );
}
