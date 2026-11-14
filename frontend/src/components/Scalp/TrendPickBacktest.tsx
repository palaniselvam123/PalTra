"use client";

import Link from "next/link";
import clsx from "clsx";
import { CalendarClock, Loader2, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import { TrendPickCompare } from "@/components/Scalp/TrendPickCompare";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { replayActive, smaApi, type TrendPickDirection, type TrendPicksAuto } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { GuideBar } from "./GuideBar";
import { ResumePickRuns } from "@/components/Scalp/ResumePickRuns";

const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
const PICK_TIMES = ["09:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "13:00", "14:00"];
const TOP_N = [5, 10, 15, 20];
const SIZES = [1, 3, 5, 10, 15];
const BOTS = [1, 2, 3, 4];
const RUN_AT = ["15:35", "15:45", "16:00", "17:00", "18:00", "20:00"];
const DIRS: { value: TrendPickDirection; label: string }[] = [
  { value: "BOTH", label: "Strong bullish and bearish" },
  { value: "BULLISH", label: "Strong bullish only" },
  { value: "BEARISH", label: "Strong bearish only" },
];

/**
 * The owner's daily routine, automated: at a time of day, take the Trend quality card's strong bullish / bearish stocks,
 * the top N, and backtest them on a bot (backend trend_picks.py). Run it on past days in one go, or switch on the daily
 * run after the close. Practice money only: nothing is armed and no order is sent.
 */
export function TrendPickBacktest({ armed }: { armed: Map<string, string[]> }) {
  const [lists, setLists] = useScanLists("sma.trendpick.lists", ["fno"]);
  const [days, setDays] = useState(10);
  const [pickTime, setPickTime] = useState("10:00");
  const [topN, setTopN] = useState(10);
  const [direction, setDirection] = useState<TrendPickDirection>("BOTH");
  const [minutes, setMinutes] = useState(5);
  const [bot, setBot] = useState(1);
  const [playHandoffs, setPlayHandoffs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [auto, setAuto] = useState<TrendPicksAuto | null>(null);
  const [autoNote, setAutoNote] = useState<string | null>(null);

  useEffect(() => {
    smaApi
      .trendPicksAuto()
      .then(setAuto)
      .catch(() => undefined);
  }, []);

  const run = async () => {
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const universe = await resolveScanSymbols(lists, armed.keys());
      if (!universe.length) throw new Error("No stocks in the picked lists.");
      const current = (await smaApi.replayAll().catch(() => null))?.[String(bot)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${bot}'s replay is already running. Stop it and start this backtest instead?`)) return;
      }
      await smaApi.replayTrendPicks({
        date: range[0],
        end_date: range[range.length - 1],
        universe: universe.slice(0, 250),
        pick_time: pickTime,
        top_n: topN,
        direction,
        minutes,
        bot,
        play_handoffs: playHandoffs,
      });
      setNote({
        ok: true,
        text: `Started on Bot ${bot}: ${range[0]} → ${range[range.length - 1]}, each day the top ${topN} strong trends at ${pickTime} out of ${Math.min(universe.length, 250)} stocks, traded from ${pickTime}${playHandoffs ? ", hand-offs to Bot 2 / Bot 3 played inside the run" : ""}. Downloading the candles takes a few minutes; the result builds up in the terminal's Backtests tab, with each day's picks.`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBusy(false);
    }
  };

  const saveAuto = async (change: Partial<TrendPicksAuto>) => {
    setAutoNote(null);
    try {
      setAuto(await smaApi.saveTrendPicksAuto(change));
    } catch (e: unknown) {
      setAutoNote(e instanceof Error ? e.message : "Could not save");
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <section className="space-y-4 rounded-xl border border-slate-800 bg-card p-4">
      <ResumePickRuns kinds={["TREND"]} label="Strong-trend pick" />
      <div>
        <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <TrendingUp size={15} className="text-accentSky" /> Strong-trend picks: backtest them by themselves
        </h2>
        <p className="max-w-3xl text-xs text-slate-500">
          What you do by hand in the Trend quality history, done for you: on each day, at the time you pick, every stock in
          the lists is read the way the Trend quality card reads it (the same 7 strong-trend checks, only the candles closed
          by then). The strong bullish / bearish ones are ranked by trend score and the top ones become that day&apos;s picks.
          The bot you choose trades only those, from the pick time to the square-off, with its own settings. Practice money
          only — nothing is armed and no order is sent.
        </p>
          <GuideBar lesson="scalp-backtest" terms={["backtest", "strong_trend", "bot", "handoff", "pnl_before", "win_pct", "green_days", "drawdown", "profit_factor", "per_trade", "paper"]} className="mt-2" />
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks
          <ScanListPicker value={lists as ScanList[]} onChange={setLists} />
        </label>
        <label className="flex flex-col gap-1">
          Last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading day{d === 1 ? "" : "s"}
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
          Which
          <select value={direction} onChange={(e) => setDirection(e.target.value as TrendPickDirection)} className={select}>
            {DIRS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          How many
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className={select}>
            {TOP_N.map((n) => (
              <option key={n} value={n}>
                Top {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Trend read on
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={select}>
            {SIZES.map((m) => (
              <option key={m} value={m}>
                {m}-min candles
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Trade them on
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select}>
            {BOTS.map((b) => (
              <option key={b} value={b}>
                Bot {b}&apos;s settings
              </option>
            ))}
          </select>
        </label>
        <label
          className="flex min-h-9 items-center gap-1.5 self-end text-xs text-slate-200"
          title="When Bot N's own settings move a stock (a recovery or losing V-turn, choppy after N losing trades or a stop-loss to Bot 2; a breakout or strong trend to Bot 3), the stock stays in this backtest and trades from then on with that bot's settings, marked 'as Bot 2' / 'as Bot 3'. The hand-offs happen only if they are switched on in the chosen bot's Settings (7. Advanced). The live Trade lists are not touched."
        >
          <input type="checkbox" checked={playHandoffs} onChange={(e) => setPlayHandoffs(e.target.checked)} className="accent-violet-400" />
          Play hand-offs (Bot 2 / Bot 3)
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() => void run()}
          className="inline-flex min-h-9 items-center gap-1.5 self-end rounded-md bg-accentSky/15 px-3 font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/25 disabled:opacity-40"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <TrendingUp size={13} />} Run on past days
        </button>
      </div>
      {note && (
        <p className={clsx("text-xs", note.ok ? "text-accentSky" : "text-amber-300")}>
          {note.text}{" "}
          {note.ok && (
            <Link href={bot === 1 ? "/terminal/" : `/terminal/?desk=bot${bot}`} className="font-semibold underline-offset-2 hover:underline">
              Open Bot {bot} →
            </Link>
          )}
        </p>
      )}

      <TrendPickCompare
        armed={armed}
        lists={lists}
        days={days}
        topN={topN}
        direction={direction}
        minutes={minutes}
        bot={bot}
        playHandoffs={playHandoffs}
      />

      <div className="rounded-lg border border-slate-800 bg-surface2/40 p-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <CalendarClock size={14} className="text-accentViolet" /> Every trading day, by itself
        </h3>
        <p className="mt-0.5 text-xs text-slate-500">
          After the close, the server backtests that day&apos;s strong-trend picks with the settings below and keeps the result in
          Backtests, so a report builds up day after day without pressing anything. (To paper-trade the picks live in the
          day, use the terminal&apos;s Scan &amp; arm schedule.)
        </p>
        {auto ? (
          <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-slate-400">
            <label className="inline-flex min-h-9 items-center gap-2 text-sm font-semibold text-slate-200">
              <input type="checkbox" checked={auto.on} onChange={(e) => void saveAuto({ on: e.target.checked })} className="accent-[#10B981]" />
              On
            </label>
            <label className="flex flex-col gap-1">
              Run at
              <select value={auto.run_at} onChange={(e) => void saveAuto({ run_at: e.target.value })} className={select}>
                {RUN_AT.map((t) => (
                  <option key={t} value={t}>
                    {t} IST
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Pick at
              <select value={auto.pick_time} onChange={(e) => void saveAuto({ pick_time: e.target.value })} className={select}>
                {PICK_TIMES.map((t) => (
                  <option key={t} value={t}>
                    {t} IST
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Which
              <select value={auto.direction} onChange={(e) => void saveAuto({ direction: e.target.value as TrendPickDirection })} className={select}>
                {DIRS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              How many
              <select value={auto.top_n} onChange={(e) => void saveAuto({ top_n: Number(e.target.value) })} className={select}>
                {TOP_N.map((n) => (
                  <option key={n} value={n}>
                    Top {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Trend read on
              <select value={auto.minutes} onChange={(e) => void saveAuto({ minutes: Number(e.target.value) })} className={select}>
                {SIZES.map((m) => (
                  <option key={m} value={m}>
                    {m}-min candles
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Trade them on
              <select value={auto.bot} onChange={(e) => void saveAuto({ bot: Number(e.target.value) })} className={select}>
                {BOTS.map((b) => (
                  <option key={b} value={b}>
                    Bot {b}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Stocks
              <ScanListPicker value={auto.lists as ScanList[]} onChange={(next) => void saveAuto({ lists: next as TrendPicksAuto["lists"] })} />
            </label>
            <label className="inline-flex min-h-9 items-center gap-1.5 self-end text-xs text-slate-200" title="When Bot N's own settings move a stock (a recovery or losing V-turn, choppy after N losing trades or a stop-loss to Bot 2; a breakout or strong trend to Bot 3), the stock stays in this backtest and trades from then on with that bot's settings, marked 'as Bot 2' / 'as Bot 3'. The hand-offs happen only if they are switched on in the chosen bot's Settings (7. Advanced). The live Trade lists are not touched.">
              <input
                type="checkbox"
                checked={auto.play_handoffs !== false}
                onChange={(e) => void saveAuto({ play_handoffs: e.target.checked })}
                className="accent-violet-400"
              />
              Play hand-offs (Bot 2 / Bot 3)
            </label>
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-500">Loading…</p>
        )}
        {auto?.last_day ? (
          <p className="mt-2 text-xs text-slate-400">
            Last run {auto.last_day}
            {auto.last_run_id ? ` · run #${auto.last_run_id}` : ""}
            {auto.last_note ? ` · ${auto.last_note}` : ""}
          </p>
        ) : null}
        {autoNote ? <p className="mt-1 text-xs text-amber-300">{autoNote}</p> : null}
      </div>
    </section>
  );
}
