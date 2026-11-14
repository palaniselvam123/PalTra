"use client";

import { Bot, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { replayActive, smaApi, type ScalpAlerts } from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";
import { ResumePickRuns } from "@/components/Scalp/ResumePickRuns";

const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
const STEPS = [1, 2, 3, 5, 10, 15, 30];
const SIZES = [1, 3, 5, 10, 15];
const SPEEDS: { value: number; label: string }[] = [
  { value: 1, label: "1× (as live, minute by minute)" },
  { value: 10, label: "10×" },
  { value: 60, label: "60× (a minute a second)" },
  { value: 300, label: "300× (fastest)" },
];
const TIMES = ["09:20", "09:30", "09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "14:30", "15:00"];

/**
 * Auto-arm replay (backend auto_arm.py): test the Scalp auto-arm on past days, after the market too. Each day is played
 * like a live one: every stock is armed at the minute the auto-arm would have armed it (strong trend from the candles
 * closed by then; buyers / sellers from the saved Most active scans) and the bot trades it only from then on. Practice
 * money only: no stock is armed for real and no order is sent.
 */
export function AutoArmReplay({ armed }: { armed: Map<string, string[]> }) {
  const [lists, setLists] = useScanLists("sma.autoarm.lists", ["fno"]);
  const [alerts, setAlerts] = useState<ScalpAlerts | null>(null);
  const [days, setDays] = useState(5);
  const [source, setSource] = useState<"TREND" | "ACTIVE" | "BOTH">("BOTH");
  const [first, setFirst] = useState("09:30");
  const [last, setLast] = useState("14:30");
  const [every, setEvery] = useState(5);
  const [minutes, setMinutes] = useState(5);
  const [bot, setBot] = useState(1);
  const [speed, setSpeed] = useState(300);
  const [handoffs, setHandoffs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    smaApi
      .scalpAlerts()
      .then((a) => {
        setAlerts(a);
        setBot(a.arm_strong_on ? a.arm_strong_bot : a.arm_pressure_on ? a.arm_pressure_bot : 1);
      })
      .catch(() => undefined);
  }, []);

  const run = async () => {
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const universe = source === "ACTIVE" ? [] : await resolveScanSymbols(lists, armed.keys());
      if (source !== "ACTIVE" && !universe.length) throw new Error("No stocks in the picked lists.");
      const current = (await smaApi.replayAll().catch(() => null))?.[String(bot)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${bot}'s replay is already running. Stop it and start this one instead?`)) return;
      }
      const info = await smaApi.replayAutoArm({
        date: range[0],
        end_date: range[range.length - 1],
        universe: universe.slice(0, 250),
        source,
        first,
        last,
        every_min: every,
        minutes,
        bot,
        speed,
        play_handoffs: handoffs,
      });
      const extra = info.notes?.length ? ` Note: ${info.notes.join(" ")}` : "";
      setNote({
        ok: true,
        text: `Started on Bot ${bot}: ${range[0]} → ${range[range.length - 1]}. Each day plays from ${first}; a stock is armed at the minute the auto-arm would have armed it and traded from then on. Follow it on the terminal (Bot ${bot}'s replay bar and chart); the trades and each day's armed stocks land in the Backtests tab.${extra}`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the replay" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  const rule = alerts
    ? [
        source !== "ACTIVE" ? `strong ${alerts.strong_side === "BOTH" ? "up or down" : alerts.strong_side.toLowerCase()} trend, ${alerts.strong_min_checks} of 7 checks or more` : null,
        source !== "TREND"
          ? `${alerts.pressure_side === "BOTH" ? "buyers or sellers" : alerts.pressure_side === "BUY" ? "buyers" : "sellers"} at ${alerts.pressure_pct}% or more${alerts.pressure_min_value_cr ? `, ₹${alerts.pressure_min_value_cr} cr traded` : ""}`
          : null,
        alerts.arm_price_min || alerts.arm_price_max
          ? `priced ${alerts.arm_price_min ? `₹${alerts.arm_price_min}` : "any"} to ${alerts.arm_price_max ? `₹${alerts.arm_price_max}` : "any"}`
          : null,
      ]
        .filter(Boolean)
        .join("; ")
    : "";
  return (
    <section className="space-y-3 rounded-xl border border-slate-800 bg-card p-4">
      <ResumePickRuns kinds={["AUTOARM"]} label="Auto-arm replay" />
      <div>
        <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <Bot size={15} className="text-accentSky" /> Auto-arm replay: test the auto-arm on past days
        </h2>
        <p className="max-w-3xl text-xs text-slate-500">
          Works after the market too: past days are played back from Groww&apos;s 1-minute candles on the replay&apos;s own clock, like
          live ticks. Every few minutes each stock is read the way the Trend quality scan reads it (only the candles closed by
          then), and the day&apos;s saved Most active scans give when buyers or sellers took over. A stock is armed at that minute and
          the bot trades it from then on, waiting for its own next signal as it would live. The thresholds are the ones on the
          Alerts tab{rule ? ` (${rule})` : ""}. Practice money only: nothing is armed for real and no order is sent.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks found by
          <select value={source} onChange={(e) => setSource(e.target.value as typeof source)} className={select}>
            <option value="BOTH">Trend quality and Most active</option>
            <option value="TREND">Trend quality only</option>
            <option value="ACTIVE">Most active only (needs saved scans)</option>
          </select>
        </label>
        {source !== "ACTIVE" ? (
          <label className="flex flex-col gap-1">
            Stocks to read
            <ScanListPicker value={lists as ScanList[]} onChange={setLists} />
          </label>
        ) : null}
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
          Arm from
          <select value={first} onChange={(e) => setFirst(e.target.value)} className={select}>
            {TIMES.filter((t) => t < last).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          until
          <select value={last} onChange={(e) => setLast(e.target.value)} className={select}>
            {TIMES.filter((t) => t > first).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {source !== "ACTIVE" ? (
          <>
            <label className="flex flex-col gap-1">
              Read the trend every
              <select value={every} onChange={(e) => setEvery(Number(e.target.value))} className={select}>
                {STEPS.map((m) => (
                  <option key={m} value={m}>
                    {m} min
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              on candles of
              <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={select}>
                {SIZES.map((m) => (
                  <option key={m} value={m}>
                    {m} min
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}
        <label className="flex flex-col gap-1">
          Bot
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                Bot {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Speed
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className={select}>
            {SPEEDS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-8 items-center gap-1.5">
          <input type="checkbox" checked={handoffs} onChange={(e) => setHandoffs(e.target.checked)} />
          Play hand-offs (Bot 2 / Bot 3)
        </label>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky px-3 font-semibold text-on-accent disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : null}
          Start the replay
        </button>
      </div>
      <p className="text-xs text-slate-500">
        Trend quality reads each stock about once every {every} min ({source === "ACTIVE" ? "not used" : "up to 200 stocks take a few minutes per day before it plays"}).
        Most active needs that day&apos;s saved scans: keep its Auto scan on during the market (on the backtest server they are read
        from the desk). The bot must be in PAPER.
      </p>
      {note ? <p className={note.ok ? "text-xs text-emerald-300" : "text-xs font-semibold text-amber-300"}>{note.text}</p> : null}
    </section>
  );
}
