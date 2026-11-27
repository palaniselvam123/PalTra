"use client";

import { SKIP_FIRST_CHOICES, SKIP_FIRST_HELP, skipFirstLabel } from "@/lib/skipFirst";
import { NewTag } from "@/components/ui/NewTag";
import { DAY_CLOSE, PICK_TIMES, fitUntil, untilAfter } from "@/lib/pickWindow";
import { Bot, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import { replayActive, smaApi, type AutoArmBody, type ScalpAlerts } from "@/lib/smaApi";
import { BacktestQueuePanel } from "@/components/Scalp/BacktestQueue";
import { lastClosedWeekdays, todayInSession } from "@/lib/tradingDays";
import { ResumePickRuns } from "@/components/Scalp/ResumePickRuns";

const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
// The longest range one replay run takes (replay.MAX_RANGE_DAYS).
const MAX_SPAN = 45;
const STEPS = [1, 2, 3, 5, 10, 15, 30];
const SIZES = [1, 3, 5, 10, 15];
const SPEEDS: { value: number; label: string }[] = [
  { value: 1, label: "1× (as live, minute by minute)" },
  { value: 10, label: "10×" },
  { value: 60, label: "60× (a minute a second)" },
  { value: 300, label: "300× (fastest)" },
];
const TIMES = PICK_TIMES;

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
  const [until, setUntil] = useState(DAY_CLOSE);
  const [every, setEvery] = useState(5);
  const [minutes, setMinutes] = useState(5);
  const [candles, setCandles] = useState<"NORMAL" | "HA">("NORMAL");
  const [skipFirst, setSkipFirst] = useState(0);
  const [bot, setBot] = useState(1);
  const [speed, setSpeed] = useState(300);
  const [handoffs, setHandoffs] = useState(true);
  const [th, setTh] = useState<Rules | null>(null);
  // The Trend quality scan's own candle (size and kind): the live auto-arm reads that scan, so the backtest starts on it.
  const [scanCandle, setScanCandle] = useState<{ minutes: number; candles: "NORMAL" | "HA" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    smaApi
      .chopScan()
      .then((c) => {
        const m = Number(c.auto?.minutes ?? 0);
        if (!SIZES.includes(m)) return;
        const kind = c.auto?.candles === "HA" ? "HA" : "NORMAL";
        setScanCandle({ minutes: m, candles: kind });
        setMinutes(m);
        setCandles(kind);
      })
      .catch(() => undefined);
    smaApi
      .scalpAlerts()
      .then((a) => {
        setAlerts(a);
        setTh((now) => now ?? rulesFrom(a));
        setBot(a.arm_strong_on ? a.arm_strong_bot : a.arm_pressure_on ? a.arm_pressure_bot : 1);
      })
      .catch(() => undefined);
  }, []);

  // 0 = today so far (the market is open): played up to the last closed minute, then squared off there.
  const today = todayInSession();
  // -1 = custom dates: From / To (weekdays in between are played; at most MAX_SPAN calendar days, as the server allows).
  const [from, setFrom] = useState(() => lastClosedWeekdays(5)[0]);
  const [to, setTo] = useState(() => todayInSession() ?? lastClosedWeekdays(1)[0]);
  const latest = today ?? lastClosedWeekdays(1)[0];
  /** The form as an auto-arm start body (without the bot); throws with a plain message when something is missing. */
  const form = async (): Promise<Omit<AutoArmBody, "bot">> => {
    const todayNow = todayInSession();
    if (days === 0 && !todayNow) throw new Error("Today's session is not on now: pick Last 1 trading day (after 15:30 it is today).");
    if (days === -1) {
      const span = (Date.parse(to) - Date.parse(from)) / 86_400_000;
      if (!from || !to || !(span >= 0)) throw new Error("Pick a From date on or before the To date.");
      if (span > MAX_SPAN) throw new Error(`At most ${MAX_SPAN} calendar days (about 30 trading days) in one run.`);
      if (to > (todayNow ?? lastClosedWeekdays(1)[0]))
        throw new Error("The To date cannot be after the last day with candles (today only while the market is open, from 09:20).");
    }
    const range = days === -1 ? [from, to] : days === 0 ? [todayNow as string] : lastClosedWeekdays(days);
    if (!th) throw new Error("The picking rules are still loading; try again in a moment.");
    const universe = source === "ACTIVE" ? [] : await resolveScanSymbols(lists, armed.keys());
    if (source !== "ACTIVE" && !universe.length) throw new Error("No stocks in the picked lists.");
    return {
      date: range[0],
      end_date: range[range.length - 1],
      universe: universe.slice(0, 250),
      source,
      first,
      last,
      every_min: every,
      minutes,
      candles,
      skip_candles: skipFirst,
      until: fitUntil(until, last),
      play_handoffs: handoffs,
      min_checks: th.min_checks,
      side: th.side,
      stack: th.stack || th.widen > 0,
      widen: th.widen,
      third: th.third,
      pct: th.pct,
      pressure_side: th.pressure_side,
      min_value_cr: th.min_value_cr,
      min_rvol: th.min_rvol,
      basis: th.basis,
      flow_pct: th.flow_pct,
      max_day: th.max_day,
      price_min: th.price_min,
      price_max: th.price_max,
      force_strong: th.force,
      force_pressure: th.force,
    };
  };

  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      const body = await form();
      const current = (await smaApi.replayAll().catch(() => null))?.[String(bot)] ?? null;
      if (current && replayActive(current) && current.status !== "FINISHED") {
        if (!window.confirm(`Bot ${bot}'s replay is already running. Stop it and start this one instead?`)) return;
      }
      const info = await smaApi.replayAutoArm({ ...body, bot, speed });
      const extra = info.notes?.length ? ` Note: ${info.notes.join(" ")}` : "";
      setNote({
        ok: true,
        text: `Started on Bot ${bot}: ${body.date} → ${body.end_date}. Each day plays from ${first}; a stock is armed at the minute the auto-arm would have armed it and traded from then on. Follow it on the terminal (Bot ${bot}'s replay bar and chart); the trades and each day's armed stocks land in the Backtests tab.${extra}`,
      });
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the replay" });
    } finally {
      setBusy(false);
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  const rule = th ? ruleText(th, source) : "";
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
          the bot trades it from then on, waiting for its own next signal as it would live. The picking rules below start from the
          Alerts tab and are sent with the run{rule ? ` (${rule})` : ""}. Practice money only: nothing is armed for real and no order is sent.
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
          <span>
            Last <NewTag id="autoarm-today" />
          </span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select}>
            {today || days === 0 ? (
              <option value={0} title="Today's session up to the last minute that has closed; positions are squared off there.">
                Today so far{today ? ` (${today.slice(8)} ${new Date(`${today}T00:00:00Z`).toLocaleString("en-IN", { month: "short", timeZone: "UTC" })})` : ""}
              </option>
            ) : null}
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading day{d === 1 ? "" : "s"}
              </option>
            ))}
            <option value={-1}>Custom dates…</option>
          </select>
        </label>
        {days === -1 ? (
          <>
            <label className="flex flex-col gap-1">
              <span>
                From <NewTag id="autoarm-dates" />
              </span>
              <input type="date" value={from} max={to || latest} onChange={(e) => setFrom(e.target.value)} className={select} />
            </label>
            <label className="flex flex-col gap-1">
              To
              <input type="date" value={to} min={from} max={latest} onChange={(e) => setTo(e.target.value)} className={select} />
            </label>
          </>
        ) : null}
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
        <label className="flex flex-col gap-1">
          <span>
            Trade until <NewTag id="bt-time-window" />
          </span>
          <select value={fitUntil(until, last)} onChange={(e) => setUntil(e.target.value)} className={select}>
            {untilAfter(last).map((t) => (
              <option key={t} value={t}>
                {t === DAY_CLOSE ? "15:30 (close)" : t}
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
            <label className="flex flex-col gap-1">
              <span>
                candles <NewTag id="tq-backtest-ha" />
              </span>
              <select value={candles} onChange={(e) => setCandles(e.target.value as "NORMAL" | "HA")} className={select}>
                <option value="NORMAL">Normal</option>
                <option value="HA">Heikin Ashi</option>
              </select>
            </label>
            <label className="flex flex-col gap-1" title={SKIP_FIRST_HELP}>
          <span>
            Skip today&apos;s first candles <NewTag id="tq-skip-backtest" />
          </span>
          <select value={skipFirst} onChange={(e) => setSkipFirst(Number(e.target.value))} className={select}>
            {SKIP_FIRST_CHOICES.map((n) => (
              <option key={n} value={n}>
                {skipFirstLabel(n, minutes)}
              </option>
            ))}
          </select>
        </label>
          </>
        ) : null}
        {source !== "ACTIVE" && scanCandle && (scanCandle.minutes !== minutes || scanCandle.candles !== candles) ? (
          <p className="w-full rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-300 ring-1 ring-inset ring-amber-500/30">
            Your Trend quality scan reads {scanCandle.minutes}-minute {scanCandle.candles === "HA" ? "Heikin Ashi " : ""}candles, but this
            backtest will read {minutes}-minute {candles === "HA" ? "Heikin Ashi " : ""}candles. A stock that was 7/7 in the scan's History can
            read weaker here and not be armed.{" "}
            <button
              type="button"
              onClick={() => {
                setMinutes(scanCandle.minutes);
                setCandles(scanCandle.candles);
              }}
              className="font-semibold underline underline-offset-2"
            >
              Use {scanCandle.minutes} min {scanCandle.candles === "HA" ? "Heikin Ashi" : "normal"}
            </button>{" "}
            <NewTag id="autoarm-scan-candle" />
          </p>
        ) : null}
        {th ? (
          <RulesBox
            th={th}
            source={source}
            onChange={setTh}
            onReset={alerts ? () => setTh(rulesFrom(alerts)) : undefined}
            select={select}
          />
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
      <BacktestQueuePanel form={form} bot={bot} />
    </section>
  );
}

/** The picking rules a run is sent with (backend AutoArmStart / auto_arm.AutoArmRule). */
type Rules = {
  min_checks: number;
  side: "BOTH" | "UP" | "DOWN";
  /** Three SMAs (fast / slow / third) stacked the trend's way; widen N > 0 = both gaps widening N candles too. */
  stack: boolean;
  widen: number;
  third: number;
  pct: number;
  pressure_side: "BOTH" | "BUY" | "SELL";
  min_value_cr: number;
  min_rvol: number;
  basis: "WAITING" | "EXECUTED" | "BOTH";
  flow_pct: number;
  max_day: number;
  price_min: number;
  price_max: number;
  /** "Force order when armed": the replayed bot enters at once at each arm minute (both sources). */
  force: boolean;
};

function rulesFrom(a: ScalpAlerts): Rules {
  return {
    min_checks: Math.min(7, Math.max(5, Number(a.strong_min_checks) || 6)),
    side: a.strong_side ?? "BOTH",
    stack: Boolean(a.arm_strong_stack),
    widen: Number(a.arm_strong_widen) || 0,
    third: Number(a.arm_strong_third) || 50,
    pct: Number(a.pressure_pct) || 70,
    pressure_side: a.pressure_side ?? "BOTH",
    min_value_cr: Number(a.pressure_min_value_cr) || 0,
    min_rvol: Number(a.pressure_min_rvol) || 0,
    basis: a.pressure_basis ?? "WAITING",
    flow_pct: Number(a.pressure_flow_pct) || 60,
    max_day: Number(a.arm_max_day) || 10,
    price_min: Number(a.arm_price_min) || 0,
    price_max: Number(a.arm_price_max) || 0,
    force: Boolean(a.arm_strong_force || a.arm_pressure_force),
  };
}

function ruleText(t: Rules, source: "TREND" | "ACTIVE" | "BOTH"): string {
  return [
    source !== "ACTIVE"
      ? `strong ${t.side === "BOTH" ? "up or down" : t.side.toLowerCase()} trend, ${t.min_checks === 7 ? "all 7 checks" : `${t.min_checks} of 7 checks or more`}${
          t.stack || t.widen ? `, three SMAs (third SMA ${t.third}) stacked${t.widen ? ` with both gaps widening ${t.widen}+ candles` : ""}` : ""
        }`
      : null,
    source !== "TREND"
      ? `${t.pressure_side === "BOTH" ? "buyers or sellers" : t.pressure_side === "BUY" ? "buyers" : "sellers"} at ${
          t.basis === "EXECUTED"
            ? `${t.flow_pct}% or more of the shares traded`
            : t.basis === "BOTH"
              ? `${t.pct}% or more waiting and ${t.flow_pct}% or more executed`
              : `${t.pct}% or more of the waiting orders`
        }${t.min_value_cr ? `, ₹${t.min_value_cr} cr traded` : ""}${t.min_rvol ? `, volume ${t.min_rvol}× usual` : ""}`
      : null,
    `at most ${t.max_day} a day`,
    t.price_min || t.price_max ? `priced ${t.price_min ? `₹${t.price_min}` : "any"} to ${t.price_max ? `₹${t.price_max}` : "any"}` : null,
    t.force ? "the bot enters at once when a stock is armed" : null,
  ]
    .filter(Boolean)
    .join("; ");
}

/** The picking rules on the form itself (owner's case: a backtest on another server used that server's Alerts tab, 6 of 7,
 *  while the owner had set All 7: stocks that were never 7/7 were armed). */
function RulesBox({
  th,
  source,
  onChange,
  onReset,
  select,
}: {
  th: Rules;
  source: "TREND" | "ACTIVE" | "BOTH";
  onChange: (t: Rules) => void;
  onReset?: () => void;
  select: string;
}) {
  const set = <K extends keyof Rules>(k: K, v: Rules[K]) => onChange({ ...th, [k]: v });
  const num = (v: string, lo: number, hi: number) => Math.min(hi, Math.max(lo, Number(v) || 0));
  const box = `${select} w-20`;
  return (
    <div className="basis-full space-y-2 rounded-md p-2 ring-1 ring-inset ring-white/10">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
        <b className="text-slate-100">Picking rules for this backtest</b> <NewTag id="autoarm-rules" />
        <span className="text-slate-400">filled from the Alerts tab; change them here for this run only</span>
        {onReset ? (
          <button type="button" onClick={onReset} className="min-h-8 rounded-md px-2 text-slate-400 ring-1 ring-inset ring-white/15">
            Reset to the Alerts tab
          </button>
        ) : null}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {source !== "ACTIVE" ? (
          <>
            <label className="flex flex-col gap-1">
              Strong trend: checks that must pass
              <select value={th.min_checks} onChange={(e) => set("min_checks", Number(e.target.value))} className={select}>
                <option value={5}>5 of 7 or more</option>
                <option value={6}>6 of 7 or more</option>
                <option value={7}>All 7 only</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Direction
              <select value={th.side} onChange={(e) => set("side", e.target.value as Rules["side"])} className={select}>
                <option value="BOTH">Up and down</option>
                <option value="UP">Up only</option>
                <option value="DOWN">Down only</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="flex items-center gap-1">
                Three SMAs stacked
                <NewTag id="arm-stack-widen" />
              </span>
              <select
                value={th.stack || th.widen > 0 ? String(th.widen) : "off"}
                onChange={(e) =>
                  onChange(e.target.value === "off" ? { ...th, stack: false, widen: 0 } : { ...th, stack: true, widen: Number(e.target.value) })
                }
                className={select}
              >
                <option value="off">Not needed</option>
                <option value="0">Stacked the trend&apos;s way</option>
                {[1, 2, 3, 5, 8].map((n) => (
                  <option key={n} value={String(n)}>
                    Stacked, gaps widening {n}+ candle{n === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
            </label>
            {th.stack || th.widen > 0 ? (
              <label className="flex flex-col gap-1">
                Third SMA
                <select value={th.third} onChange={(e) => set("third", Number(e.target.value))} className={select}>
                  {[21, 30, 50, 100, 200].map((n) => (
                    <option key={n} value={n}>
                      SMA {n}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </>
        ) : null}
        {source !== "TREND" ? (
          <>
            <label className="flex flex-col gap-1">
              Most active: judge by
              <select value={th.basis} onChange={(e) => set("basis", e.target.value as Rules["basis"])} className={select}>
                <option value="WAITING">Waiting orders</option>
                <option value="EXECUTED">Executed volume</option>
                <option value="BOTH">Both</option>
              </select>
            </label>
            {th.basis !== "EXECUTED" ? (
              <label className="flex flex-col gap-1">
                waiting at least %
                <input type="number" min={50} max={99} value={th.pct} onChange={(e) => set("pct", num(e.target.value, 50, 99))} className={box} />
              </label>
            ) : null}
            {th.basis !== "WAITING" ? (
              <label className="flex flex-col gap-1">
                executed at least %
                <input type="number" min={50} max={95} value={th.flow_pct} onChange={(e) => set("flow_pct", num(e.target.value, 50, 95))} className={box} />
              </label>
            ) : null}
            <label className="flex flex-col gap-1">
              Side
              <select value={th.pressure_side} onChange={(e) => set("pressure_side", e.target.value as Rules["pressure_side"])} className={select}>
                <option value="BOTH">Buyers or sellers</option>
                <option value="BUY">Buyers only</option>
                <option value="SELL">Sellers only</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              ₹ cr traded at least
              <input type="number" min={0} step={1} value={th.min_value_cr} onChange={(e) => set("min_value_cr", num(e.target.value, 0, 100000))} className={box} />
            </label>
            <label className="flex flex-col gap-1">
              Vol vs usual at least ×
              <input type="number" min={0} step={0.1} value={th.min_rvol} onChange={(e) => set("min_rvol", num(e.target.value, 0, 100))} className={box} />
            </label>
          </>
        ) : null}
        <label className="flex flex-col gap-1">
          At most a day
          <input type="number" min={1} max={50} value={th.max_day} onChange={(e) => set("max_day", num(e.target.value, 1, 50) || 1)} className={box} />
        </label>
        <label className="flex flex-col gap-1">
          Price from ₹
          <input type="number" min={0} value={th.price_min} onChange={(e) => set("price_min", num(e.target.value, 0, 1000000))} className={box} />
        </label>
        <label className="flex flex-col gap-1">
          to ₹ (0 = any)
          <input type="number" min={0} value={th.price_max} onChange={(e) => set("price_max", num(e.target.value, 0, 1000000))} className={box} />
        </label>
        <label
          className="flex min-h-9 items-center gap-2 text-xs text-slate-200"
          title="At each stock's arm minute the bot enters at once: SMA entry by the SMA lines, candle colour entry by the last closed colour candle."
        >
          <input type="checkbox" checked={th.force} onChange={(e) => set("force", e.target.checked)} />
          Force order when armed <NewTag id="auto-arm-force" link={false} />
        </label>
      </div>
    </div>
  );
}
