"use client";

import clsx from "clsx";
import { CandlestickChart, Loader2, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { GrowwLink } from "@/components/ui/growwLink";
import { TextFilter, matchesText } from "@/components/ui/tableTools";
import { smaApi, type SecondCandleRow, type SecondCandleView as View } from "@/lib/smaApi";
import { GuideBar } from "./GuideBar";

/**
 * Second candle test (backend second_candle.py): the owner's Most active routine, measured on every stock and past
 * day. Sort by volume vs usual, read the day's Nth candle (the second, 5 minutes, by default), enter its way at its
 * close; then: how far the price went with the trade and against it (and when), whether it reached the target and
 * how far it went against before that, the square-off, a trailing-stop sweep, and a target + stop-loss test that
 * can be changed here without running again (each trade keeps the minute every ₹ level was reached). Every number
 * on this card follows the filters. Read-only research; no order is placed; Arm only arms.
 */
const POLL_MS = 3000;
const DAYS = [1, 2, 3, 5, 10, 15, 20, 30];
const SIZES = [1, 3, 5, 10, 15];
const CANDLES = [1, 2, 3, 4, 5, 6];
const TOP_N = [0, 5, 10, 15, 20, 30, 50, 100];
const SQUARE_OFFS = ["10:30", "11:00", "12:00", "13:00", "14:00", "15:00", "15:15", "15:25"];
const HALF_HOURS = ["09:15", "09:45", "10:15", "10:45", "11:15", "11:45", "12:15", "12:45", "13:15", "13:45", "14:15", "14:45", "15:15"];
const LEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50];
const STOPS = [3, 5, 8, 10, 15, 20];

type Props = {
  armed: Map<string, string[]>;
  arming?: string | null;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  paused?: boolean;
  onStatus?: OnScanStatus;
};

const fmt = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : v.toFixed(d));
const signed = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`);
const minutesOf = (hhmm: string | null | undefined) => (hhmm ? Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)) : null);
const halfHour = (hhmm: string | null | undefined) => {
  const m = minutesOf(hhmm);
  if (m == null) return null;
  let label: string | null = null;
  for (const slot of HALF_HOURS) if (m >= minutesOf(slot)!) label = slot;
  return label;
};
const nearestLevel = (v: number) => LEVELS.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

/** The same reading as the backend's target_stop_outcome: the target first, else the stop, else the square-off. */
function outcome(r: SecondCandleRow, target: number, stop: number): { kind: "target" | "stop" | "close"; pts: number } {
  const t = r.fav_min?.[String(nearestLevel(target))] ?? null;
  const s = r.adv_min?.[String(nearestLevel(stop))] ?? null;
  if (t != null && (s == null || t < s)) return { kind: "target", pts: nearestLevel(target) };
  if (s != null) return { kind: "stop", pts: -nearestLevel(stop) };
  return { kind: "close", pts: r.close_pts };
}

export function SecondCandle({ armed, arming, onArm, onArmMany, paused, onStatus }: Props) {
  const [lists, setLists] = useScanLists("sma.second.lists", ["fno"]);
  const [days, setDays] = useState(10);
  const [minutes, setMinutes] = useState(5);
  const [candleNo, setCandleNo] = useState(2);
  const [topN, setTopN] = useState(20);
  const [target, setTarget] = useState(10);
  const [squareOff, setSquareOff] = useState("15:15");
  const [trailsText, setTrailsText] = useState("0.25, 0.5, 0.75, 1, 1.5, 2");
  const [data, setData] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // Filters (the numbers on the card follow them).
  const [place, setPlace] = useState<number | null>(null);
  const [side, setSide] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [day, setDay] = useState("");
  const [reached, setReached] = useState<"ALL" | "YES" | "NO">("ALL");
  const [targetFrom, setTargetFrom] = useState("");
  const [targetTo, setTargetTo] = useState("");
  const [query, setQuery] = useState("");
  // Target + stop test, changed here without a new run.
  const [testTarget, setTestTarget] = useState<number | null>(null);
  const [testStop, setTestStop] = useState(5);
  const [show, setShow] = useState<"days" | "stocks">("days");
  const ticks = useTicks();

  const load = useCallback(async () => {
    try {
      const v = await smaApi.secondCandle();
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
      const symbols = await resolveScanSymbols(lists as ScanList[], armed.keys());
      if (!symbols.length) {
        setMsg("No stocks in the picked lists.");
        return;
      }
      const trails = trailsText
        .split(/[,\s]+/)
        .map(Number)
        .filter((n) => Number.isFinite(n) && n > 0);
      setData(
        await smaApi.secondCandleStart({
          symbols: symbols.slice(0, 250),
          days,
          minutes,
          candle_no: candleNo,
          top_n: topN,
          target_points: target,
          square_off: squareOff,
          trails: trails.length ? trails : [0.25, 0.5, 0.75, 1, 1.5, 2],
          min_body_pct: 0,
        })
      );
      setPlace(null);
      if (symbols.length > 250) setMsg(`Only the first 250 of ${symbols.length} stocks are read.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      setData(await smaApi.secondCandleStop());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const running = data?.status === "RUNNING";
  const rep = data?.report;
  const params = data?.params;
  const ranked = useMemo(() => (data?.rows ?? []).filter((r) => !r.skipped && r.rank != null), [data?.rows]);
  const dayOptions = useMemo(() => Array.from(new Set(ranked.map((r) => r.day))).sort(), [ranked]);
  const topLimit = place ?? params?.top_n ?? 20;
  const ruleTarget = params?.target_points ?? target;
  const effTarget = testTarget ?? nearestLevel(ruleTarget);

  const rows = useMemo(() => {
    const from = minutesOf(targetFrom || null);
    const to = minutesOf(targetTo || null);
    return ranked.filter((r) => {
      if (topLimit > 0 && (r.rank ?? 0) > topLimit) return false;
      if (side !== "ALL" && r.side !== side) return false;
      if (day && r.day !== day) return false;
      if (reached === "YES" && !r.hit) return false;
      if (reached === "NO" && r.hit) return false;
      if (from != null || to != null) {
        const at = minutesOf(r.target_at ?? r.best_at);
        if (at == null) return false;
        if (from != null && at < from) return false;
        if (to != null && at > to) return false;
      }
      if (ticks.only && !ticks.ticked.has(r.symbol)) return false;
      return matchesText(query, r.symbol);
    });
  }, [ranked, topLimit, side, day, reached, targetFrom, targetTo, query, ticks.only, ticks.ticked]);

  const sum = useMemo(() => {
    const hits = rows.filter((r) => r.hit);
    const close = rows.map((r) => r.close_pts);
    return {
      trades: rows.length,
      hits: hits.length,
      hitPct: rows.length ? (hits.length / rows.length) * 100 : 0,
      avgBest: mean(rows.map((r) => r.best_pts)),
      avgWorst: mean(rows.map((r) => r.worst_pts)),
      avgAgainst: mean(hits.map((r) => r.against_before_target ?? 0)),
      maxAgainst: hits.length ? Math.max(...hits.map((r) => r.against_before_target ?? 0)) : null,
      avgMinutes: mean(hits.map((r) => r.minutes_to_target ?? 0)),
      totalClose: close.reduce((a, b) => a + b, 0),
      closeWinPct: rows.length ? (close.filter((v) => v > 0).length / rows.length) * 100 : 0,
      stocks: new Set(rows.map((r) => r.symbol)).size,
      days: new Set(rows.map((r) => r.day)).size,
    };
  }, [rows]);

  const trailTable = useMemo(() => {
    const keys = Array.from(new Set(rows.flatMap((r) => Object.keys(r.trail ?? {})))).sort((a, b) => Number(a) - Number(b));
    return keys.map((k) => {
      const pts = rows.map((r) => r.trail[k]).filter((v) => v != null);
      return { pct: Number(k), total: pts.reduce((a, b) => a + b, 0), avg: mean(pts), winPct: pts.length ? (pts.filter((v) => v > 0).length / pts.length) * 100 : 0, worst: pts.length ? Math.min(...pts) : 0, best: pts.length ? Math.max(...pts) : 0 };
    });
  }, [rows]);
  const bestTrail = trailTable.length ? trailTable.reduce((a, b) => (b.total > a.total ? b : a)) : null;

  const stopTable = useMemo(() => {
    const stops = Array.from(new Set([...STOPS, nearestLevel(testStop)])).sort((a, b) => a - b);
    return stops.map((s) => {
      const outs = rows.map((r) => outcome(r, effTarget, s));
      const pts = outs.map((o) => o.pts);
      return {
        stop: s,
        hitTarget: outs.filter((o) => o.kind === "target").length,
        hitStop: outs.filter((o) => o.kind === "stop").length,
        neither: outs.filter((o) => o.kind === "close").length,
        total: pts.reduce((a, b) => a + b, 0),
        avg: mean(pts),
        winPct: pts.length ? (pts.filter((v) => v > 0).length / pts.length) * 100 : 0,
      };
    });
  }, [rows, effTarget, testStop]);
  const bestStop = stopTable.length ? stopTable.reduce((a, b) => (b.total > a.total ? b : a)) : null;

  const whenTable = useMemo(
    () =>
      HALF_HOURS.map((slot) => ({
        slot,
        target: rows.filter((r) => r.hit && halfHour(r.target_at) === slot).length,
        best: rows.filter((r) => halfHour(r.best_at) === slot).length,
        worst: rows.filter((r) => halfHour(r.worst_at) === slot).length,
      })).filter((h) => h.target || h.best || h.worst),
    [rows]
  );

  const stockRows = useMemo(() => {
    const by = new Map<string, SecondCandleRow[]>();
    for (const r of rows) by.set(r.symbol, [...(by.get(r.symbol) ?? []), r]);
    return Array.from(by.entries()).map(([symbol, part]) => {
      const hits = part.filter((r) => r.hit);
      const trailTotals = Object.keys(part[0].trail ?? {}).map((k) => ({ k, total: part.reduce((a, r) => a + (r.trail[k] ?? 0), 0) }));
      const bt = trailTotals.length ? trailTotals.reduce((a, b) => (b.total > a.total ? b : a)) : null;
      const stopOuts = part.map((r) => outcome(r, effTarget, testStop));
      return {
        symbol,
        trades: part.length,
        hits: hits.length,
        hitPct: (hits.length / part.length) * 100,
        avgBest: mean(part.map((r) => r.best_pts)) ?? 0,
        avgWorst: mean(part.map((r) => r.worst_pts)) ?? 0,
        avgAgainst: mean(hits.map((r) => r.against_before_target ?? 0)),
        totalClose: part.reduce((a, r) => a + r.close_pts, 0),
        stopTotal: stopOuts.reduce((a, o) => a + o.pts, 0),
        bestTrailPct: bt ? Number(bt.k) : null,
        bestTrailTotal: bt ? bt.total : null,
        avgRank: mean(part.map((r) => r.rank ?? 0)) ?? 0,
      };
    });
  }, [rows, effTarget, testStop]);

  type DayKey = "day" | "symbol" | "rank" | "rvol" | "side" | "entry" | "best_pts" | "best_at" | "worst_pts" | "worst_at" | "hit" | "against_before_target" | "close_pts" | "stopPts" | "bestTrail";
  const dayValue = (r: SecondCandleRow, k: DayKey) => {
    if (k === "hit") return r.hit ? 1 : 0;
    if (k === "best_at" || k === "worst_at") return minutesOf(r[k]);
    if (k === "stopPts") return outcome(r, effTarget, testStop).pts;
    if (k === "bestTrail") return Math.max(...Object.values(r.trail ?? { x: 0 }));
    return r[k] as number | string | null;
  };
  const daySort = useSort<SecondCandleRow, DayKey>(rows, dayValue);
  type StockKey = "symbol" | "trades" | "hitPct" | "avgBest" | "avgWorst" | "avgAgainst" | "totalClose" | "stopTotal" | "bestTrailTotal" | "avgRank";
  const stockSort = useSort<(typeof stockRows)[number], StockKey>(stockRows, (r, k) => r[k]);

  const exportCsv = () => {
    const head = ["day", "stock", "rank", "vol_vs_usual", "side", "signal_at", "entry_at", "entry", "best_pts", "best_pct", "best_at", "worst_pts", "worst_pct", "worst_at", "reached_target", "target_at", "minutes_to_target", "against_before_target", "close_pts", "close_pct", ...Object.keys(rows[0]?.trail ?? {}).map((k) => `trail_${k}pct`)];
    const lines = rows.map((r) =>
      [r.day, r.symbol, r.rank, r.rvol, r.side, r.signal_at, r.entry_at, r.entry, r.best_pts, r.best_pct, r.best_at, r.worst_pts, r.worst_pct, r.worst_at, r.hit ? "yes" : "no", r.target_at ?? "", r.minutes_to_target ?? "", r.against_before_target ?? "", r.close_pts, r.close_pct, ...Object.keys(rows[0]?.trail ?? {}).map((k) => r.trail[k])].join(",")
    );
    const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "second-candle.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const select = "min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100";
  const th = "px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500";
  const td = "px-2 py-1 tabular-nums";
  const pos = (v: number) => (v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-300");
  return (
    <section className="terminal-dark space-y-4 rounded-xl border border-border bg-surface p-3 shadow-card sm:p-4">
      <div className="max-w-3xl">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
          <CandlestickChart size={18} className="text-amber-300" aria-hidden /> Second candle test
        </h2>
        <p className="mt-0.5 text-sm text-slate-300">
          Your Most active routine, checked on every stock and past day: rank by volume vs usual, read the day&apos;s second
          candle, buy on green or sell on red at its close. For each trade: how many ₹ it reached, how far it went against
          you first (so you know where a stop survives), when, and what a target, a stop or a trailing stop would have
          made. Every number below follows the filters. Practice arithmetic only; nothing is ordered.
        </p>
          <GuideBar lesson="scalp-second-candle" terms={["second_candle", "rvol", "candle", "target", "stop", "trail", "square_off", "pnl_before", "arm"]} className="mt-2" />
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks
          <ScanListPicker value={lists as ScanList[]} onChange={setLists} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          Last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={select} disabled={running}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} trading day{d === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Candle
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={select} disabled={running}>
            {SIZES.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Read the
          <select value={candleNo} onChange={(e) => setCandleNo(Number(e.target.value))} className={select} disabled={running}>
            {CANDLES.map((n) => (
              <option key={n} value={n}>
                {n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`} candle
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Top by volume vs usual
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className={select} disabled={running}>
            {TOP_N.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "All stocks" : `Top ${n}`}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Target ₹ a share
          <input type="number" min={0.5} step={0.5} value={target} onChange={(e) => setTarget(Number(e.target.value) || 10)} className={clsx(select, "w-20")} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          Square off at
          <select value={squareOff} onChange={(e) => setSquareOff(e.target.value)} className={select} disabled={running}>
            {SQUARE_OFFS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Trailing stops (% of entry)
          <input value={trailsText} onChange={(e) => setTrailsText(e.target.value)} className={clsx(select, "w-44")} disabled={running} />
        </label>
        {running ? (
          <button type="button" onClick={stop} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-rose-600/80 px-3 text-xs font-semibold text-white hover:bg-rose-600">
            <Square size={13} /> Stop
          </button>
        ) : (
          <button type="button" onClick={run} disabled={busy || paused} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500 px-3 text-xs font-semibold text-slate-900 hover:bg-sky-400 disabled:opacity-50">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run the test
          </button>
        )}
        {running && (
          <span className="inline-flex items-center gap-1.5 text-slate-300">
            <Loader2 size={13} className="animate-spin" /> {data?.done} of {data?.total} stocks
          </span>
        )}
      </div>
      {msg && <p className="text-xs text-amber-300">{msg}</p>}
      {data?.errors?.length ? <p className="text-xs text-amber-300">{data.errors.length} stock(s) could not be read: {data.errors.slice(0, 5).map((e) => `${e.symbol} (${e.error})`).join("; ")}{data.errors.length > 5 ? " …" : ""}</p> : null}

      {rep && params && (
        <>
          <div className="rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs text-slate-300">
            <div className="mb-1 font-semibold text-slate-200">
              Last run: {params.symbols} stocks, {params.days} day(s), the {params.candle_no === 1 ? "1st" : params.candle_no === 2 ? "2nd" : `${params.candle_no}th`} {params.minutes}-minute candle, target ₹{params.target_points}, square-off {params.square_off}, top {params.top_n === 0 ? "all" : params.top_n} by volume vs usual
            </div>
            <ul className="list-disc space-y-0.5 pl-5">
              {rep.answer.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
            <label className="flex flex-col gap-1">
              Place by volume
              <select value={place ?? params.top_n ?? 20} onChange={(e) => setPlace(Number(e.target.value))} className={select}>
                {TOP_N.map((n) => (
                  <option key={n} value={n}>
                    {n === 0 ? "Every place" : `Top ${n}`}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Side
              <select value={side} onChange={(e) => setSide(e.target.value as typeof side)} className={select}>
                <option value="ALL">Both</option>
                <option value="LONG">Green candle (buy)</option>
                <option value="SHORT">Red candle (sell)</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Day
              <select value={day} onChange={(e) => setDay(e.target.value)} className={select}>
                <option value="">Every day</option>
                {dayOptions.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Reached ₹{params.target_points}
              <select value={reached} onChange={(e) => setReached(e.target.value as typeof reached)} className={select}>
                <option value="ALL">All</option>
                <option value="YES">Reached</option>
                <option value="NO">Did not</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Target (or best) time from
              <input type="time" value={targetFrom} onChange={(e) => setTargetFrom(e.target.value)} className={select} />
            </label>
            <label className="flex flex-col gap-1">
              to
              <input type="time" value={targetTo} onChange={(e) => setTargetTo(e.target.value)} className={select} />
            </label>
            <TextFilter value={query} onChange={setQuery} />
            <button type="button" onClick={exportCsv} disabled={!rows.length} className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50">
              CSV
            </button>
          </div>

          {/* Summary of the filtered rows */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
            <Tile label="Trades" value={`${sum.trades} (${sum.stocks} stocks, ${sum.days} days)`} />
            <Tile label={`Reached ₹${params.target_points}`} value={`${sum.hits} (${fmt(sum.hitPct, 0)}%)`} tone={sum.hitPct - 50} />
            <Tile label="Avg with the trade" value={`₹${fmt(sum.avgBest)}`} tone={1} />
            <Tile label="Avg against" value={`₹${fmt(sum.avgWorst)}`} tone={-1} />
            <Tile label="Against before target" value={sum.avgAgainst == null ? "—" : `₹${fmt(sum.avgAgainst)} avg · ₹${fmt(sum.maxAgainst)} max`} />
            <Tile label="Minutes to target" value={sum.avgMinutes == null ? "—" : fmt(sum.avgMinutes, 0)} />
            <Tile label="Held to square-off" value={`₹${signed(sum.totalClose)} a share`} tone={sum.totalClose} />
            <Tile label="Closed in profit" value={`${fmt(sum.closeWinPct, 0)}%`} tone={sum.closeWinPct - 50} />
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            {/* Target + stop test */}
            <div className="rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs">
              <div className="mb-1 flex flex-wrap items-center gap-2 font-semibold text-slate-200">
                Target and stop-loss
                <select value={effTarget} onChange={(e) => setTestTarget(Number(e.target.value))} className={clsx(select, "min-h-7 text-xs")} aria-label="Target ₹ a share">
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>
                      target ₹{l}
                    </option>
                  ))}
                </select>
                <select value={nearestLevel(testStop)} onChange={(e) => setTestStop(Number(e.target.value))} className={clsx(select, "min-h-7 text-xs")} aria-label="Stop ₹ a share for the per-stock table">
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>
                      stop ₹{l}
                    </option>
                  ))}
                </select>
              </div>
              <p className="mb-2 text-slate-500">Enter with this target and stop: how many hit the target first, how many the stop, how many neither (closed at the square-off), and the ₹ a share in all. The same minute for both counts as the stop.</p>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className={th}>Stop ₹</th>
                    <th className={clsx(th, "text-right")}>Target first</th>
                    <th className={clsx(th, "text-right")}>Stop first</th>
                    <th className={clsx(th, "text-right")}>Neither</th>
                    <th className={clsx(th, "text-right")}>Win %</th>
                    <th className={clsx(th, "text-right")}>₹ a share in all</th>
                  </tr>
                </thead>
                <tbody>
                  {stopTable.map((s) => (
                    <tr key={s.stop} className={clsx("border-t border-white/5", bestStop && s.stop === bestStop.stop && "bg-emerald-500/10")}>
                      <td className={td}>₹{s.stop}</td>
                      <td className={clsx(td, "text-right text-emerald-300")}>{s.hitTarget}</td>
                      <td className={clsx(td, "text-right text-rose-300")}>{s.hitStop}</td>
                      <td className={clsx(td, "text-right text-slate-400")}>{s.neither}</td>
                      <td className={clsx(td, "text-right")}>{fmt(s.winPct, 0)}%</td>
                      <td className={clsx(td, "text-right font-semibold", pos(s.total))}>{signed(s.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Trailing stop sweep */}
            <div className="rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs">
              <div className="mb-1 font-semibold text-slate-200">Trailing stop</div>
              <p className="mb-2 text-slate-500">The stop follows the best price at this distance (% of the entry). ₹ a share each trade made with it, against ₹{signed(sum.totalClose)} holding to the square-off.</p>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className={th}>Distance</th>
                    <th className={clsx(th, "text-right")}>₹ in all</th>
                    <th className={clsx(th, "text-right")}>Avg</th>
                    <th className={clsx(th, "text-right")}>Win %</th>
                    <th className={clsx(th, "text-right")}>Worst</th>
                    <th className={clsx(th, "text-right")}>Best</th>
                  </tr>
                </thead>
                <tbody>
                  {trailTable.map((t) => (
                    <tr key={t.pct} className={clsx("border-t border-white/5", bestTrail && t.pct === bestTrail.pct && "bg-emerald-500/10")}>
                      <td className={td}>{t.pct}%</td>
                      <td className={clsx(td, "text-right font-semibold", pos(t.total))}>{signed(t.total)}</td>
                      <td className={clsx(td, "text-right", pos(t.avg ?? 0))}>{signed(t.avg)}</td>
                      <td className={clsx(td, "text-right")}>{fmt(t.winPct, 0)}%</td>
                      <td className={clsx(td, "text-right text-rose-300")}>{signed(t.worst)}</td>
                      <td className={clsx(td, "text-right text-emerald-300")}>{signed(t.best)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* When */}
            <div className="rounded-lg border border-slate-800 bg-surface2/60 p-3 text-xs">
              <div className="mb-1 font-semibold text-slate-200">When it happens</div>
              <p className="mb-2 text-slate-500">Trades per half hour: when the target was reached, when the best price came, when the worst came.</p>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className={th}>From</th>
                    <th className={clsx(th, "text-right")}>Target</th>
                    <th className={clsx(th, "text-right")}>Best</th>
                    <th className={clsx(th, "text-right")}>Worst</th>
                  </tr>
                </thead>
                <tbody>
                  {whenTable.map((h) => (
                    <tr key={h.slot} className="border-t border-white/5">
                      <td className={td}>{h.slot}</td>
                      <td className={clsx(td, "text-right text-emerald-300")}>{h.target || ""}</td>
                      <td className={clsx(td, "text-right")}>{h.best || ""}</td>
                      <td className={clsx(td, "text-right text-rose-300")}>{h.worst || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1 text-xs">
            {(["days", "stocks"] as const).map((k) => (
              <button key={k} type="button" onClick={() => setShow(k)} className={clsx("rounded-md px-2 py-1", show === k ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:text-slate-200")}>
                {k === "days" ? "Each stock and day" : "Per stock"}
              </button>
            ))}
          </div>
          {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}

          {show === "days" && (
            <div className="max-h-[32rem] overflow-auto rounded-lg border border-slate-800">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 bg-surface">
                  <tr>
                    {onArmMany ? <TickHead ticks={ticks} symbols={rows.map((r) => r.symbol)} /> : null}
                    <SortTh label="Day" k="day" sort={daySort.sort} onSort={daySort.onSort} text />
                    <SortTh label="Stock" k="symbol" sort={daySort.sort} onSort={daySort.onSort} text />
                    <SortTh label="Place" k="rank" sort={daySort.sort} onSort={daySort.onSort} num title="Rank by volume vs usual that day" />
                    <SortTh label="Vol vs usual" k="rvol" sort={daySort.sort} onSort={daySort.onSort} num />
                    <SortTh label="Side" k="side" sort={daySort.sort} onSort={daySort.onSort} text />
                    <SortTh label="Entry" k="entry" sort={daySort.sort} onSort={daySort.onSort} num />
                    <SortTh label="With the trade" k="best_pts" sort={daySort.sort} onSort={daySort.onSort} num title="The most the price went your way, ₹ a share and %" />
                    <SortTh label="At" k="best_at" sort={daySort.sort} onSort={daySort.onSort} num />
                    <SortTh label="Against" k="worst_pts" sort={daySort.sort} onSort={daySort.onSort} num title="The most the price went against you" />
                    <SortTh label="At" k="worst_at" sort={daySort.sort} onSort={daySort.onSort} num />
                    <SortTh label={`₹${params.target_points}?`} k="hit" sort={daySort.sort} onSort={daySort.onSort} num title="Reached the target, and when" />
                    <SortTh label="Against before" k="against_before_target" sort={daySort.sort} onSort={daySort.onSort} num title="How far it went against you before reaching the target: a stop nearer than this cuts the winner" />
                    <SortTh label="Square-off" k="close_pts" sort={daySort.sort} onSort={daySort.onSort} num />
                    <SortTh label={`T ₹${effTarget} / S ₹${nearestLevel(testStop)}`} k="stopPts" sort={daySort.sort} onSort={daySort.onSort} num title="With the target and stop chosen above" />
                    <SortTh label="Best trail" k="bestTrail" sort={daySort.sort} onSort={daySort.onSort} num />
                    {onArm ? <th className={th} /> : null}
                  </tr>
                </thead>
                <tbody>
                  {daySort.sorted.map((r) => {
                    const o = outcome(r, effTarget, testStop);
                    const bt = Object.entries(r.trail ?? {}).reduce<[string, number] | null>((a, [k, v]) => (a == null || v > a[1] ? [k, v] : a), null);
                    return (
                      <tr key={`${r.day}-${r.symbol}`} className="border-t border-white/5">
                        {onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                        <td className={clsx(td, "text-slate-400")}>{r.day}</td>
                        <td className={clsx(td, "font-semibold text-slate-100")}>
                          <GrowwLink symbol={r.symbol} />
                        </td>
                        <td className={clsx(td, "text-right")}>{r.rank}</td>
                        <td className={clsx(td, "text-right")}>{r.rvol == null ? "—" : `${fmt(r.rvol, 1)}×`}</td>
                        <td className={clsx(td, r.side === "LONG" ? "text-emerald-300" : "text-rose-300")}>{r.side === "LONG" ? "Buy" : "Sell"}</td>
                        <td className={clsx(td, "text-slate-300")}>
                          {r.entry_at} @ {fmt(r.entry)}
                        </td>
                        <td className={clsx(td, "text-right text-emerald-300")}>
                          +{fmt(r.best_pts)} <span className="text-slate-500">({fmt(r.best_pct)}%)</span>
                        </td>
                        <td className={clsx(td, "text-slate-400")}>{r.best_at}</td>
                        <td className={clsx(td, "text-right text-rose-300")}>
                          −{fmt(r.worst_pts)} <span className="text-slate-500">({fmt(r.worst_pct)}%)</span>
                        </td>
                        <td className={clsx(td, "text-slate-400")}>{r.worst_at}</td>
                        <td className={clsx(td, r.hit ? "text-emerald-300" : "text-slate-500")}>{r.hit ? `✓ ${r.target_at} (${r.minutes_to_target} min)` : "—"}</td>
                        <td className={clsx(td, "text-right", r.against_before_target == null ? "text-slate-500" : "text-amber-300")}>{r.against_before_target == null ? "—" : `−${fmt(r.against_before_target)}`}</td>
                        <td className={clsx(td, "text-right", pos(r.close_pts))}>{signed(r.close_pts)}</td>
                        <td className={clsx(td, "text-right", pos(o.pts))} title={o.kind === "target" ? "target first" : o.kind === "stop" ? "stop first" : "neither: square-off"}>
                          {signed(o.pts)} <span className="text-slate-500">{o.kind === "target" ? "T" : o.kind === "stop" ? "S" : "sq"}</span>
                        </td>
                        <td className={clsx(td, "text-right", pos(bt?.[1] ?? 0))}>{bt ? `${bt[0]}%: ${signed(bt[1])}` : "—"}</td>
                        {onArm ? (
                          <td className={td}>
                            <button type="button" onClick={() => onArm(r.symbol)} disabled={arming === r.symbol} className="rounded px-2 py-0.5 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50">
                              {armed.has(r.symbol) ? "Armed" : "Arm"}
                            </button>
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!rows.length && <p className="p-3 text-xs text-slate-500">No trades match these filters.</p>}
            </div>
          )}

          {show === "stocks" && (
            <div className="max-h-[32rem] overflow-auto rounded-lg border border-slate-800">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 bg-surface">
                  <tr>
                    {onArmMany ? <TickHead ticks={ticks} symbols={stockRows.map((r) => r.symbol)} /> : null}
                    <SortTh label="Stock" k="symbol" sort={stockSort.sort} onSort={stockSort.onSort} text />
                    <SortTh label="Days" k="trades" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label={`Reached ₹${params.target_points}`} k="hitPct" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Avg with" k="avgBest" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Avg against" k="avgWorst" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Against before target" k="avgAgainst" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Square-off ₹" k="totalClose" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label={`T ₹${effTarget} / S ₹${nearestLevel(testStop)}`} k="stopTotal" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Best trail" k="bestTrailTotal" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    <SortTh label="Avg place" k="avgRank" sort={stockSort.sort} onSort={stockSort.onSort} num />
                    {onArm ? <th className={th} /> : null}
                  </tr>
                </thead>
                <tbody>
                  {stockSort.sorted.map((r) => (
                    <tr key={r.symbol} className="border-t border-white/5">
                      {onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                      <td className={clsx(td, "font-semibold text-slate-100")}>
                        <GrowwLink symbol={r.symbol} />
                      </td>
                      <td className={clsx(td, "text-right")}>{r.trades}</td>
                      <td className={clsx(td, "text-right")}>
                        {r.hits} of {r.trades} ({fmt(r.hitPct, 0)}%)
                      </td>
                      <td className={clsx(td, "text-right text-emerald-300")}>+{fmt(r.avgBest)}</td>
                      <td className={clsx(td, "text-right text-rose-300")}>−{fmt(r.avgWorst)}</td>
                      <td className={clsx(td, "text-right text-amber-300")}>{r.avgAgainst == null ? "—" : `−${fmt(r.avgAgainst)}`}</td>
                      <td className={clsx(td, "text-right", pos(r.totalClose))}>{signed(r.totalClose)}</td>
                      <td className={clsx(td, "text-right", pos(r.stopTotal))}>{signed(r.stopTotal)}</td>
                      <td className={clsx(td, "text-right", pos(r.bestTrailTotal ?? 0))}>{r.bestTrailPct == null ? "—" : `${r.bestTrailPct}%: ${signed(r.bestTrailTotal)}`}</td>
                      <td className={clsx(td, "text-right")}>{fmt(r.avgRank, 1)}</td>
                      {onArm ? (
                        <td className={td}>
                          <button type="button" onClick={() => onArm(r.symbol)} disabled={arming === r.symbol} className="rounded px-2 py-0.5 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50">
                            {armed.has(r.symbol) ? "Armed" : "Arm"}
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
              {!stockRows.length && <p className="p-3 text-xs text-slate-500">No trades match these filters.</p>}
            </div>
          )}
        </>
      )}
      {!rep && !running && data && <p className="text-xs text-slate-500">No run yet. Pick the stocks and the days and press &quot;Run the test&quot;.</p>}
    </section>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-surface2/60 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("text-sm font-semibold tabular-nums", tone == null ? "text-slate-100" : tone > 0 ? "text-emerald-300" : tone < 0 ? "text-rose-300" : "text-slate-100")}>{value}</div>
    </div>
  );
}
