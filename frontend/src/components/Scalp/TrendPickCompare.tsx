"use client";

import clsx from "clsx";
import { GitCompare, Loader2, Square, Trash2, Trophy } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import {
  smaApi,
  type TrendCompare,
  type TrendCompareResult,
  type TrendCompareRow,
  type TrendPickDirection,
  type ActivePickParams,
} from "@/lib/smaApi";
import { lastClosedWeekdays } from "@/lib/tradingDays";

const TIMES = ["09:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"];
const DEFAULT_TIMES = ["10:00", "11:00", "12:00"];
const RANK_TEXT: Record<string, string> = {
  value: "money traded",
  rvol: "volume vs usual",
  change: "biggest move",
  vwap: "distance from average price",
  speed: "speed",
  range: "day range",
};
const BIAS_TEXT: Record<string, string> = {
  BOTH: "bullish and bearish",
  LONG: "bullish only",
  SHORT: "bearish only",
  ANY: "any bias",
};
const MAX_TIMES = 8;

type Key =
  | "rank"
  | "time"
  | "gross"
  | "net"
  | "trades"
  | "win_rate"
  | "green_days"
  | "per_day"
  | "best_day"
  | "worst_day"
  | "drawdown"
  | "best_days"
  | "stocks_picked";

const STATUS_TEXT: Record<string, string> = {
  WAITING: "Waiting",
  RUNNING: "Playing…",
  DONE: "Done",
  SKIPPED: "Skipped",
  STOPPED: "Stopped",
  INTERRUPTED: "Cut short",
  ERROR: "Error",
};

function money(v: number | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function tone(v: number | undefined): string {
  if (v == null || v === 0) return "text-slate-300";
  return v > 0 ? "text-emerald-400" : "text-rose-400";
}

/**
 * Compare pick times (backend trend_compare.py): the strong-trend pick backtest above, played once per ticked time on
 * the same days, stocks, bot and settings, one after another, then side by side. Practice money; nothing is armed.
 */
export function TrendPickCompare({
  armed,
  lists,
  days,
  topN,
  direction,
  minutes,
  bot,
  playHandoffs,
  active,
}: {
  armed: Map<string, string[]>;
  lists: string[];
  days: number;
  topN: number;
  direction: TrendPickDirection;
  minutes: number;
  bot: number;
  playHandoffs: boolean;
  /** Compare the Most active picks (active_picks.py) instead of the strong-trend ones. */
  active?: ActivePickParams;
}) {
  const source = active ? "ACTIVE" : "TREND";
  const [times, setTimes] = useState<string[]>(DEFAULT_TIMES);
  const [items, setItems] = useState<TrendCompare[]>([]);
  const [running, setRunning] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [view, setView] = useState<(TrendCompare & { result: TrendCompareResult }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const loadList = useCallback(async () => {
    try {
      const got = await smaApi.trendCompares();
      const mine = got.items.filter((c) => (c.source ?? "TREND") === source);
      got.items = mine;
      setItems(mine);
      setRunning(got.running);
      const runningMine = got.running != null && mine.some((c) => c.id === got.running) ? got.running : null;
      setOpenId((cur) => cur ?? runningMine ?? mine[0]?.id ?? null);
    } catch {
      /* the list is optional */
    }
  }, [source]);

  const loadView = useCallback(async (id: number) => {
    try {
      setView(await smaApi.trendCompare(id));
    } catch {
      setView(null);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (openId == null) {
      setView(null);
      return;
    }
    void loadView(openId);
  }, [openId, loadView]);

  // While a comparison plays, refresh the list and the open one every few seconds.
  useEffect(() => {
    if (running == null) return;
    const timer = setInterval(() => {
      void loadList();
      if (openId != null) void loadView(openId);
    }, 5000);
    return () => clearInterval(timer);
  }, [running, openId, loadList, loadView]);

  const toggle = (t: string) =>
    setTimes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : cur.length >= MAX_TIMES ? cur : [...cur, t].sort()));

  const start = async () => {
    if (times.length < 2) {
      setNote({ ok: false, text: "Tick at least two times to compare." });
      return;
    }
    const range = lastClosedWeekdays(days);
    setBusy(true);
    setNote(null);
    try {
      const universe = await resolveScanSymbols(lists as ScanList[], armed.keys());
      if (!universe.length) throw new Error("No stocks in the picked lists.");
      const got = await smaApi.trendCompareStart({
        date: range[0],
        end_date: range[range.length - 1],
        universe: universe.slice(0, 250),
        times,
        top_n: topN,
        direction,
        minutes,
        bot,
        lists,
        play_handoffs: playHandoffs,
        source,
        ...(active ?? {}),
      });
      setOpenId(got.id);
      setRunning(got.id);
      setNote({
        ok: true,
        text: `Started: ${times.join(", ")} on Bot ${bot}'s player, ${range[0]} → ${range[range.length - 1]}, one time after another at full speed. Each time downloads the candles again, so this takes a while; the table fills in as each time finishes.`,
      });
      void loadList();
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not start the comparison" });
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    await smaApi.trendCompareStop().catch(() => undefined);
    setTimeout(() => void loadList(), 1500);
  };

  const remove = async (id: number) => {
    if (!window.confirm("Forget this comparison? Its backtest runs stay in the Backtests tab.")) return;
    try {
      await smaApi.trendCompareDelete(id);
      setOpenId(null);
      void loadList();
    } catch (e: unknown) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Could not delete" });
    }
  };

  const select = "rounded border border-slate-700 bg-base px-1.5 py-1 text-xs text-slate-200";
  return (
    <div className="rounded-lg border border-slate-800 bg-surface2/40 p-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
        <GitCompare size={14} className="text-accentSky" /> Compare pick times: which time of day works best?
      </h3>
      <p className="mt-0.5 max-w-3xl text-xs text-slate-500">
        The backtest above, played once for each time you tick, on the same days, stocks, bot and settings (the choices
        above), one after another. Then the times are set side by side: P&amp;L, winners, green days, the biggest drop, and
        which time did best on each day. Keep the bot&apos;s settings unchanged while it runs.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="mr-1 text-slate-400">Times:</span>
        {TIMES.map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={times.includes(t)}
            onClick={() => toggle(t)}
            className={clsx(
              "min-h-8 rounded-md px-2 font-mono ring-1 ring-inset",
              times.includes(t)
                ? "bg-accentSky/20 text-accentSky ring-accentSky/50"
                : "text-slate-400 ring-slate-700 hover:bg-white/5"
            )}
          >
            {t}
          </button>
        ))}
        <button
          type="button"
          disabled={busy || running != null}
          onClick={() => void start()}
          className="ml-2 inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky/15 px-3 font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/25 disabled:opacity-40"
          title={running != null ? "A comparison is already running" : undefined}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <GitCompare size={13} />} Compare {times.length} times
        </button>
        {running != null && (
          <button
            type="button"
            onClick={() => void stop()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 font-semibold text-rose-300 ring-1 ring-inset ring-rose-500/40 hover:bg-rose-500/10"
          >
            <Square size={12} /> Stop
          </button>
        )}
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        {times.length} time{times.length === 1 ? "" : "s"} × {days} day{days === 1 ? "" : "s"} = {times.length * days} backtest day
        {times.length * days === 1 ? "" : "s"} (2 to {MAX_TIMES} times).
      </p>
      {note && <p className={clsx("mt-1 text-xs", note.ok ? "text-accentSky" : "text-amber-300")}>{note.text}</p>}

      {items.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <label className="flex items-center gap-2">
            Comparison
            <select value={openId ?? ""} onChange={(e) => setOpenId(e.target.value ? Number(e.target.value) : null)} className={select}>
              {items.map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.id} · {c.times.join(" / ")} · {c.start_date} → {c.end_date} · Bot {c.bot}
                  {c.id === running ? " · running" : ` · ${c.status.toLowerCase()}`}
                </option>
              ))}
            </select>
          </label>
          {openId != null && openId !== running && (
            <button
              type="button"
              onClick={() => void remove(openId)}
              className="inline-flex min-h-8 items-center gap-1 rounded px-2 text-slate-400 hover:bg-white/5 hover:text-rose-300"
            >
              <Trash2 size={12} /> Forget
            </button>
          )}
        </div>
      )}
      {view && <CompareView view={view} running={view.id === running} />}
    </div>
  );
}

function CompareView({ view, running }: { view: TrendCompare & { result: TrendCompareResult }; running: boolean }) {
  const { result } = view;
  const { sorted, sort, onSort } = useSort<TrendCompareRow, Key>(result.rows, (r, k) => (r[k] as number | string | undefined) ?? null);
  const done = useMemo(() => result.rows.filter((r) => r.has_result), [result.rows]);
  const dirText = { BOTH: "strong bullish and bearish", BULLISH: "strong bullish", BEARISH: "strong bearish" }[view.direction];
  const best = (t: string, key: keyof TrendCompareResult["best"]) => result.best[key] === t;
  const cell = (t: string, key: keyof TrendCompareResult["best"], text: React.ReactNode, cls = "") => (
    <td className={clsx("px-2 py-1 text-right font-mono", cls, best(t, key) && "bg-emerald-500/10 font-semibold")}>
      {text}
      {best(t, key) && <Trophy size={10} className="ml-1 inline text-amber-300" aria-label="best" />}
    </td>
  );

  return (
    <div className="mt-3 space-y-3">
      <p className="text-xs text-slate-500">
        {view.start_date} → {view.end_date} ·{" "}
        {view.active
          ? `top ${view.top_n} Most active by ${RANK_TEXT[view.active.rank]}, ${BIAS_TEXT[view.active.bias]}, at least ₹${view.active.min_value_cr} cr traded`
          : `top ${view.top_n} ${dirText} on ${view.minutes}-min candles`}{" "}
        · {view.universe} stocks
        · Bot {view.bot}&apos;s settings{view.play_handoffs ? " with hand-offs to Bot 2 / Bot 3 played" : ""} · P&amp;L before charges unless marked.
      </p>
      {done.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-200">
          {result.answer.map((line) => (
            <li key={line} className={clsx(/changed|Only/.test(line) && "text-amber-300")}>
              {line}
            </li>
          ))}
        </ul>
      )}
      {running && <p className="text-xs text-accentSky">Running — the table fills in as each time finishes.</p>}
      {view.note && <p className="text-xs text-amber-300">{view.note}</p>}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-xs">
          <thead className="text-[10px] text-slate-400">
            <tr>
              <SortTh label="Rank" k="rank" sort={sort} onSort={onSort} num />
              <SortTh label="Pick at" k="time" sort={sort} onSort={onSort} text />
              <th className="px-1 py-1 text-left uppercase tracking-wider">Status</th>
              <SortTh label="P&L" k="gross" sort={sort} onSort={onSort} num title="Before charges" />
              <SortTh label="After charges" k="net" sort={sort} onSort={onSort} num />
              <SortTh label="Trades" k="trades" sort={sort} onSort={onSort} num />
              <SortTh label="Win %" k="win_rate" sort={sort} onSort={onSort} num />
              <SortTh label="Green days" k="green_days" sort={sort} onSort={onSort} num />
              <SortTh label="Per day" k="per_day" sort={sort} onSort={onSort} num />
              <SortTh label="Best day" k="best_day" sort={sort} onSort={onSort} num />
              <SortTh label="Worst day" k="worst_day" sort={sort} onSort={onSort} num />
              <SortTh label="Biggest drop" k="drawdown" sort={sort} onSort={onSort} num title="The worst fall of the running total from its high" />
              <SortTh label="Top on days" k="best_days" sort={sort} onSort={onSort} num title="Days on which this time did best of all" />
              <SortTh label="Stocks" k="stocks_picked" sort={sort} onSort={onSort} num title="Different stocks picked over the days" />
              <th className="px-1 py-1 text-right uppercase tracking-wider">Run</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.time} className="border-t border-slate-800">
                <td className="px-2 py-1 text-right font-mono text-slate-300">{r.rank ?? "—"}</td>
                <td className="px-2 py-1 font-mono font-semibold text-slate-100">{r.time}</td>
                <td className="px-2 py-1 text-slate-400" title={r.note || undefined}>
                  {STATUS_TEXT[r.status] ?? r.status}
                  {r.has_result && r.days_total && r.days_done != null && r.days_done < r.days_total
                    ? ` · ${r.days_done}/${r.days_total} days`
                    : ""}
                  {r.note && !r.has_result ? <span className="block text-[10px] text-amber-300">{r.note}</span> : null}
                </td>
                {r.has_result ? (
                  <>
                    {cell(r.time, "gross", money(r.gross), tone(r.gross))}
                    {cell(r.time, "net", money(r.net), tone(r.net))}
                    <td className="px-2 py-1 text-right font-mono text-slate-300">{r.trades}</td>
                    {cell(r.time, "win_rate", `${(r.win_rate ?? 0).toFixed(0)}%`)}
                    {cell(r.time, "green_days", `${r.green_days} of ${r.days_played}`)}
                    <td className={clsx("px-2 py-1 text-right font-mono", tone(r.per_day))}>{money(r.per_day)}</td>
                    <td className={clsx("px-2 py-1 text-right font-mono", tone(r.best_day))}>{money(r.best_day)}</td>
                    <td className={clsx("px-2 py-1 text-right font-mono", tone(r.worst_day))}>{money(r.worst_day)}</td>
                    {cell(r.time, "drawdown", money(r.drawdown), tone(r.drawdown))}
                    {cell(r.time, "best_days", String(r.best_days ?? 0))}
                    <td className="px-2 py-1 text-right font-mono text-slate-300">{r.stocks_picked}</td>
                  </>
                ) : (
                  <td colSpan={11} className="px-2 py-1 text-slate-500">
                    {r.status === "WAITING" ? "Not played yet" : r.status === "RUNNING" ? "Playing…" : "No result"}
                  </td>
                )}
                <td className="px-2 py-1 text-right font-mono text-slate-400">{r.run_id ? `#${r.run_id}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {result.days.length > 0 && done.length > 1 && (
        <details className="rounded border border-slate-800 p-2" open={result.days.length <= 10}>
          <summary className="cursor-pointer text-xs font-semibold text-slate-300">Day by day (before charges; best time each day highlighted)</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-2 py-1 text-left">Day</th>
                  {done.map((r) => (
                    <th key={r.time} className="px-2 py-1 text-right font-mono">
                      {r.time}
                    </th>
                  ))}
                  <th className="px-2 py-1 text-left">Best</th>
                </tr>
              </thead>
              <tbody>
                {result.days.map((d) => (
                  <tr key={d.date} className="border-t border-slate-800">
                    <td className="px-2 py-1 font-mono text-slate-300">{d.date}</td>
                    {done.map((r) => (
                      <td
                        key={r.time}
                        className={clsx(
                          "px-2 py-1 text-right font-mono",
                          tone(d.by_time[r.time]),
                          d.best === r.time && "bg-emerald-500/10 font-semibold"
                        )}
                      >
                        {money(d.by_time[r.time])}
                      </td>
                    ))}
                    <td className="px-2 py-1 font-mono text-slate-400">{d.best ?? "tie"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <p className="text-[11px] text-slate-500">
        Each time is an ordinary run in the terminal&apos;s Backtests tab (run number in the last column), with that
        day&apos;s picks and every trade. Forgetting a comparison keeps those runs.
      </p>
    </div>
  );
}
