"use client";

import Link from "next/link";
import clsx from "clsx";
import { Flame, Loader2 } from "lucide-react";
import { useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { TrendPickCompare } from "@/components/Scalp/TrendPickCompare";
import { replayActive, smaApi, type ActivePickBias, type ActivePickRank } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { ResumePickRuns } from "@/components/Scalp/ResumePickRuns";

const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
const PICK_TIMES = ["09:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "13:00", "14:00"];
const TOP_N = [5, 10, 15, 20];
const BOTS = [1, 2, 3, 4];
const MIN_VALUES = [0, 1, 5, 10, 25, 50, 100];
const RANKS: { value: ActivePickRank; label: string }[] = [
  { value: "rvol", label: "Volume vs usual" },
  { value: "value", label: "Money traded" },
  { value: "change", label: "Biggest move" },
  { value: "vwap", label: "Furthest from average price" },
  { value: "speed", label: "Fastest move now (speed)" },
  { value: "range", label: "Widest day range" },
];
const BIASES: { value: ActivePickBias; label: string }[] = [
  { value: "BOTH", label: "Bullish and bearish bias" },
  { value: "LONG", label: "Bullish bias only" },
  { value: "SHORT", label: "Bearish bias only" },
  { value: "ANY", label: "Any (bias or not)" },
];

/**
 * The Most active card's backtest (backend active_picks.py): like the strong-trend pick backtest, but the day's picks
 * are the Most active ones at the pick time, ranked by the chosen column. Practice money only.
 */
export function ActivePickBacktest({ armed }: { armed: Map<string, string[]> }) {
  const [lists, setLists] = useScanLists("sma.activepick.lists", ["fno"]);
  const [days, setDays] = useState(10);
  const [pickTime, setPickTime] = useState("10:00");
  const [topN, setTopN] = useState(10);
  const [rank, setRank] = useState<ActivePickRank>("rvol");
  const [bias, setBias] = useState<ActivePickBias>("BOTH");
  const [minValue, setMinValue] = useState(5);
  const [bot, setBot] = useState(1);
  const [playHandoffs, setPlayHandoffs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async () => {
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const universe = await resolveScanSymbols(lists as ScanList[], armed.keys());
      if (!universe.length) throw new Error("No stocks in the picked lists.");
      const current = (await smaApi.replayAll().catch(() => null))?.[String(bot)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${bot}'s replay is already running. Stop it and start this backtest instead?`)) return;
      }
      await smaApi.replayActivePicks({
        date: range[0],
        end_date: range[range.length - 1],
        universe: universe.slice(0, 250),
        pick_time: pickTime,
        top_n: topN,
        rank,
        bias,
        min_value_cr: minValue,
        bot,
        play_handoffs: playHandoffs,
      });
      const by = RANKS.find((r) => r.value === rank)?.label.toLowerCase();
      setNote({
        ok: true,
        text: `Started on Bot ${bot}: ${range[0]} → ${range[range.length - 1]}, each day the top ${topN} Most active stocks at ${pickTime} by ${by} out of ${Math.min(universe.length, 250)}, traded from ${pickTime}${playHandoffs ? ", hand-offs to Bot 2 / Bot 3 played inside the run" : ""}. Downloading the candles takes a few minutes; the result builds up in the terminal's Backtests tab, with each day's picks.`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the backtest" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <div className="space-y-4">
      <ResumePickRuns kinds={["ACTIVE"]} label="Most active pick" />
      <div>
        <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Flame size={14} className="text-amber-400" /> Backtest the Most active picks
        </h3>
        <p className="max-w-3xl text-xs text-slate-500">
          For each past day, at the time you pick, every stock in the lists is read the way this card reads it (move from
          yesterday&apos;s close, money traded, volume against its usual by that time, distance from the average price, speed,
          day range, bias), only from the candles closed by then. The top ones by the column you choose become that day&apos;s
          picks, and the bot you choose trades only those, from the pick time to the square-off, with its own settings. The
          buyers / sellers waiting column is left out: past order books are not kept. Practice money only — nothing is armed and
          no order is sent.
        </p>
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
          Rank by
          <select value={rank} onChange={(e) => setRank(e.target.value as ActivePickRank)} className={select}>
            {RANKS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Which
          <select value={bias} onChange={(e) => setBias(e.target.value as ActivePickBias)} className={select}>
            {BIASES.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label}
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
        <label className="flex flex-col gap-1" title="Leave out stocks that had traded less than this by the pick time">
          Traded at least
          <select value={minValue} onChange={(e) => setMinValue(Number(e.target.value))} className={select}>
            {MIN_VALUES.map((v) => (
              <option key={v} value={v}>
                {v ? `₹${v} cr` : "Any amount"}
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
          title="A stock the bot's own settings hand to Bot 2 or Bot 3 stays in the backtest and trades on that bot's settings (marked 'as Bot 2' / 'as Bot 3'). The live Trade lists are not touched."
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
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Flame size={13} />} Run on past days
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
        direction="BOTH"
        minutes={5}
        bot={bot}
        playHandoffs={playHandoffs}
        active={{ rank, bias, min_value_cr: minValue }}
      />
    </div>
  );
}
