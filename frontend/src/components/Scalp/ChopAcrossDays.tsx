"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { CalendarRange, Download, Loader2 } from "lucide-react";
import { smaApi, type ChopScanRow, type ScanAcross } from "@/lib/smaApi";
import {
  CANDLE_CHOICES,
  CROSS_RULES,
  NO_FILTER,
  PHASE_OPTIONS,
  SCORE_OPTIONS,
  TREND_DIRS,
  TREND_KINDS,
  chopPasses,
  isCrossRule,
  type ChopFilter,
  type CrossRule,
  type PhaseFilter,
  type ScoreFilter,
} from "@/lib/chopFilter";
import type { TrendDir, TrendKind } from "@/lib/trendRank";
import { FilterField, FilterGroup, FilterRow, NumberFilter, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { GrowwLink } from "@/components/ui/growwLink";
import { NewTag } from "@/components/ui/NewTag";
import { saveFile } from "@/lib/chartTable";

const MAX_DAYS = 10;
const STORE = "scalp.history.across";

/** One stock on one day: every saved scan of that day in which it passed the filters. */
type Hit = {
  key: string;
  day: string;
  symbol: string;
  firstAt: string;
  lastAt: string;
  matched: number;
  scans: number;
  firstPrice: number | null;
  lastPrice: number | null;
  /** Price in the day's last scan (matched or not), to see how it went after the first match. */
  endPrice: number | null;
  movePct: number | null;
  trendFirst: string | null;
  scoreFirst: string | null;
  candle: number | null;
  candles: "NORMAL" | "HA";
};
/** The same stock over all the picked days. */
type Stock = { key: string; symbol: string; days: number; matched: number; firstDay: string; firstAt: string; lastDay: string; avgMove: number | null };

/** One stock in one saved scan: how its trend read at that time (the timeline view). */
type Seen = {
  key: string;
  day: string;
  at: string;
  symbol: string;
  ltp: number | null;
  trend: string | null;
  trendScore: number | null;
  checks: number | null;
  checksOf: number | null;
  score: string | null;
  phase: string | null;
  flips: number | null;
  dayMove: number | null;
  speed: number | null;
  candle: number | null;
  candles: "NORMAL" | "HA";
  /** What changed since the stock's previous scan that day (same candle): "" when nothing did. */
  change: string;
  /** +1 the trend got stronger, -1 weaker, 0 same. */
  dir: number;
};
type ScanSort = "day" | "time" | "symbol" | "ltp" | "trend" | "trendScore" | "checks" | "score" | "phase" | "flips" | "dayMove" | "speed" | "change";
type Table = "day" | "stock" | "scan";

type SortKey = "day" | "symbol" | "first" | "last" | "matched" | "firstPrice" | "move" | "trend";
type StockSort = "symbol" | "days" | "matched" | "first" | "avgMove";

const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const dayText = (day: string) =>
  new Date(`${day}T12:00:00+05:30`).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
const trendText = (t: string | null | undefined) =>
  t === "STRONG_BULLISH" ? "Strong ↑" : t === "STRONG_BEARISH" ? "Strong ↓" : "—";
const phaseText = (p: string | null) => (p === "BULL" ? "Strong up" : p === "BEAR" ? "Strong down" : p === "CHOP" ? "Choppy" : "—");
const scoreText = (s: string | null) => (s ? s[0] + s.slice(1).toLowerCase() : "—");
// Strength of a read for "stronger / weaker": a strong trend beats any score, then the trend score.
const strength = (r: ChopScanRow) => (r.trend ? 1000 : 0) + (r.trend_score ?? 0);
const checkCount = (r: ChopScanRow): [number | null, number | null] => {
  const c = r.trend_checks;
  if (!c) return [null, null];
  const vals = Object.values(c).filter((v) => typeof v === "boolean");
  return [vals.filter(Boolean).length, vals.length];
};
function changeText(prev: ChopScanRow | undefined, r: ChopScanRow): { change: string; dir: number } {
  if (!prev) return { change: "first scan", dir: 0 };
  const out: string[] = [];
  if (prev.trend !== r.trend) out.push(r.trend ? `became ${trendText(r.trend)}` : `lost ${trendText(prev.trend)}`);
  if (prev.phase !== r.phase && r.phase) out.push(`phase → ${phaseText(r.phase)}`);
  if (prev.score !== r.score && r.score) out.push(`→ ${scoreText(r.score)}`);
  const a = prev.trend_score ?? null;
  const b = r.trend_score ?? null;
  if (out.length === 0 && a != null && b != null && Math.abs(b - a) >= 10) out.push(`score ${a} → ${b}`);
  const d = strength(r) - strength(prev);
  return { change: out.join(" · "), dir: out.length === 0 ? 0 : d > 0 ? 1 : d < 0 ? -1 : 0 };
}
const pct = (v: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);

/**
 * The Trend quality filters over every saved scan of one or more days (History tab). A stock is listed for a day when
 * it passed the filters in at least one scan that day, with the first and last time it did and in how many scans.
 * Read-only: nothing is armed until Arm is pressed, and no order is placed.
 */
export function ChopAcrossDays({
  days,
  armed,
  arming,
  onArm,
  onArmMany,
}: {
  /** The days with saved scans, newest first. */
  days: { day: string; runs: number }[];
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [candle, setCandle] = useState<number>(0);
  const [candles, setCandles] = useState<"" | "NORMAL" | "HA">("");
  const [f, setF] = useState<ChopFilter>(NO_FILTER);
  const [query, setQuery] = useState("");
  const [table, setTable] = useState<Table>("day");
  const combine = table === "stock";
  // Timeline: only the scans in which something changed for that stock (became strong, phase or score moved).
  const [changesOnly, setChangesOnly] = useState(false);
  const [data, setData] = useState<ScanAcross | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof ChopFilter>(key: K, value: ChopFilter[K]) => setF((cur) => ({ ...cur, [key]: value }));

  // Remember the filters per browser.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (s.f && typeof s.f === "object") setF({ ...NO_FILTER, ...s.f, crossCount: isCrossRule(String(s.f.crossCount)) ? s.f.crossCount : "0" });
      if (typeof s.candle === "number") setCandle(s.candle);
      if (s.candles === "NORMAL" || s.candles === "HA") setCandles(s.candles);
    } catch {
      /* storage blocked: defaults */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify({ f, candle, candles }));
    } catch {
      /* storage blocked */
    }
  }, [f, candle, candles]);
  // Start on the latest day.
  useEffect(() => {
    if (picked.length === 0 && days.length > 0) setPicked([days[0].day]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  useEffect(() => {
    if (picked.length === 0) {
      setData(null);
      return;
    }
    let live = true;
    setLoading(true);
    smaApi
      .scanHistoryAcross(picked, candle || null, candles || null)
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [picked, candle, candles]);

  const hits: Hit[] = useMemo(() => {
    const out = new Map<string, Hit>();
    const scansPerDay = new Map<string, number>();
    const lastPrice = new Map<string, number | null>();
    for (const run of data?.runs ?? []) {
      scansPerDay.set(run.day, (scansPerDay.get(run.day) ?? 0) + 1);
      for (const r of run.rows as ChopScanRow[]) {
        const key = `${run.day}|${r.symbol}`;
        lastPrice.set(key, r.ltp ?? null);
        if (!matchesText(query, r.symbol) || !chopPasses(r, f)) continue;
        const h = out.get(key);
        if (!h) {
          out.set(key, {
            key,
            day: run.day,
            symbol: r.symbol,
            firstAt: run.as_of,
            lastAt: run.as_of,
            matched: 1,
            scans: 0,
            firstPrice: r.ltp ?? null,
            lastPrice: r.ltp ?? null,
            endPrice: null,
            movePct: null,
            trendFirst: r.trend ?? null,
            scoreFirst: r.score ?? null,
            candle: run.minutes ?? null,
            candles: run.candles,
          });
        } else {
          h.lastAt = run.as_of;
          h.matched += 1;
          h.lastPrice = r.ltp ?? null;
        }
      }
    }
    for (const h of out.values()) {
      h.scans = scansPerDay.get(h.day) ?? 0;
      h.endPrice = lastPrice.get(h.key) ?? null;
      h.movePct = h.firstPrice && h.endPrice ? ((h.endPrice - h.firstPrice) / h.firstPrice) * 100 : null;
    }
    return [...out.values()];
  }, [data, f, query]);

  const seen: Seen[] = useMemo(() => {
    const out: Seen[] = [];
    const last = new Map<string, ChopScanRow>();
    for (const run of data?.runs ?? []) {
      for (const r of run.rows as ChopScanRow[]) {
        const track = `${run.day}|${r.symbol}|${run.minutes}|${run.candles}`;
        const prev = last.get(track);
        last.set(track, r);
        if (!matchesText(query, r.symbol) || !chopPasses(r, f)) continue;
        const { change, dir } = changeText(prev, r);
        if (changesOnly && !change) continue;
        const [checks, checksOf] = checkCount(r);
        out.push({
          key: `${run.id}|${r.symbol}`,
          day: run.day,
          at: run.as_of,
          symbol: r.symbol,
          ltp: r.ltp ?? null,
          trend: r.trend ?? null,
          trendScore: r.trend_score ?? null,
          checks,
          checksOf,
          score: r.score ?? null,
          phase: r.phase ?? null,
          flips: r.crosses_today ?? null,
          dayMove: r.day_move_pct ?? null,
          speed: r.speed_pct_per_min ?? null,
          candle: run.minutes ?? null,
          candles: run.candles,
          change,
          dir,
        });
      }
    }
    return out;
  }, [data, f, query, changesOnly]);
  const scanSort = useSort<Seen, ScanSort>(seen, (r, k) => {
    switch (k) {
      case "day":
        return r.day;
      case "time":
        return r.at;
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "trend":
        return r.trend ? (r.trend === "STRONG_BULLISH" ? 2 : 1) : 0;
      case "trendScore":
        return r.trendScore;
      case "checks":
        return r.checks;
      case "score":
        return r.score === "TRENDING" ? 3 : r.score === "MIXED" ? 2 : r.score === "CHOPPY" ? 1 : 0;
      case "phase":
        return r.phase;
      case "flips":
        return r.flips;
      case "dayMove":
        return r.dayMove;
      case "speed":
        return r.speed;
      case "change":
        return r.dir;
    }
  });
  const pagedScans = usePaged(scanSort.sorted, "scalp.history.across.pageSize");
  const timelineOf = (symbol: string) => {
    setQuery(symbol);
    setTable("scan");
  };

  const stocks: Stock[] = useMemo(() => {
    const by = new Map<string, Hit[]>();
    for (const h of hits) by.set(h.symbol, [...(by.get(h.symbol) ?? []), h]);
    return [...by.entries()].map(([symbol, list]) => {
      const ordered = [...list].sort((a, b) => a.firstAt.localeCompare(b.firstAt));
      const moves = list.map((h) => h.movePct).filter((v): v is number => v != null);
      return {
        key: symbol,
        symbol,
        days: list.length,
        matched: list.reduce((n, h) => n + h.matched, 0),
        firstDay: ordered[0].day,
        firstAt: ordered[0].firstAt,
        lastDay: ordered[ordered.length - 1].day,
        avgMove: moves.length ? moves.reduce((a, b) => a + b, 0) / moves.length : null,
      };
    });
  }, [hits]);

  const { sorted, sort, onSort } = useSort<Hit, SortKey>(hits, (h, k) => {
    switch (k) {
      case "day":
        return h.firstAt;
      case "symbol":
        return h.symbol;
      case "first":
        return clock(h.firstAt);
      case "last":
        return clock(h.lastAt);
      case "matched":
        return h.matched;
      case "firstPrice":
        return h.firstPrice;
      case "move":
        return h.movePct;
      case "trend":
        return h.trendFirst;
    }
  });
  const byStock = useSort<Stock, StockSort>(stocks, (s, k) =>
    k === "symbol" ? s.symbol : k === "days" ? s.days : k === "matched" ? s.matched : k === "first" ? s.firstAt : s.avgMove
  );
  const paged = usePaged(sorted, "scalp.history.across.pageSize");
  const pagedStocks = usePaged(byStock.sorted, "scalp.history.across.pageSize");

  const scans = data?.runs.length ?? 0;
  const symbols = [...new Set(hits.map((h) => h.symbol))];
  const toggleDay = (d: string) =>
    setPicked((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : cur.length >= MAX_DAYS ? cur : [...cur, d]));

  const csv = () => {
    if (table === "scan") {
      const head = "day,time,stock,price,trend,trend_score,checks_passed,checks_of,score,phase,crosses_or_flips,day_move_pct,speed_pct_min,candle,candles,change";
      const lines = scanSort.sorted.map((r) =>
        [r.day, clock(r.at), r.symbol, r.ltp ?? "", r.trend ?? "", r.trendScore ?? "", r.checks ?? "", r.checksOf ?? "", r.score ?? "", r.phase ?? "",
          r.flips ?? "", r.dayMove?.toFixed(2) ?? "", r.speed ?? "", r.candle ?? "", r.candles, `"${r.change}"`].join(",")
      );
      saveFile(`trend-quality-timeline-${picked.slice().sort().join("_")}.csv`, [head, ...lines].join("\n"), "text/csv");
      return;
    }
    const head = combine
      ? "stock,days,scans_matched,first_day,first_time,last_day,avg_move_pct"
      : "day,stock,first_matched,last_matched,scans_matched,scans_that_day,price_first,price_day_end,move_pct,trend_first,score_first,candle,candles";
    const lines = combine
      ? byStock.sorted.map((s) => [s.symbol, s.days, s.matched, s.firstDay, clock(s.firstAt), s.lastDay, s.avgMove?.toFixed(2) ?? ""].join(","))
      : sorted.map((h) =>
          [h.day, h.symbol, clock(h.firstAt), clock(h.lastAt), h.matched, h.scans, h.firstPrice ?? "", h.endPrice ?? "", h.movePct?.toFixed(2) ?? "",
            h.trendFirst ?? "", h.scoreFirst ?? "", h.candle ?? "", h.candles].join(",")
        );
    saveFile(`trend-quality-across-${picked.slice().sort().join("_")}.csv`, [head, ...lines].join("\n"), "text/csv");
  };

  const sizeButtons = (on: readonly number[], key: "noCrossOn" | "trendOn", tone: string) => (
    <>
      {CANDLE_CHOICES.map((m) => {
        const active = on.includes(m);
        return (
          <button
            key={m}
            type="button"
            aria-pressed={active}
            onClick={() => set(key, active ? on.filter((x) => x !== m) : [...on, m].sort((a, b) => a - b))}
            className={clsx(
              "min-h-9 min-w-10 rounded-md px-2.5 text-xs font-semibold ring-1 ring-inset",
              active ? tone : "text-slate-400 ring-slate-700 hover:bg-white/5"
            )}
          >
            {m}m
          </button>
        );
      })}
      {on.length > 0 && (
        <button type="button" onClick={() => set(key, [])} className="text-xs text-slate-500 underline-offset-2 hover:underline">
          clear
        </button>
      )}
    </>
  );

  return (
    <section className="mb-4 rounded-xl border border-slate-800 bg-card p-4">
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <CalendarRange size={15} className="text-accentSky" /> Trend quality across days <NewTag id="tq-history-days" />
      </h3>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        The Trend quality filters run over every saved scan of the days you tick, not just one scan. A stock is listed for a
        day when it passed the filters in at least one scan that day, with the first and last time it did, in how many
        scans, and what its price did from the first match to the day&apos;s last scan. Information only: nothing here places
        an order.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="mr-1 text-slate-400">Days (up to {MAX_DAYS}):</span>
        {days.slice(0, 30).map((d) => {
          const on = picked.includes(d.day);
          return (
            <button
              key={d.day}
              type="button"
              aria-pressed={on}
              onClick={() => toggleDay(d.day)}
              className={clsx(
                "min-h-8 rounded-md px-2 ring-1 ring-inset",
                on ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-400 ring-slate-700 hover:bg-white/5"
              )}
            >
              {dayText(d.day)}
            </button>
          );
        })}
        <button type="button" onClick={() => setPicked(days.slice(0, 5).map((d) => d.day))} className="ml-1 text-slate-400 underline-offset-2 hover:underline">
          last 5
        </button>
        <button type="button" onClick={() => setPicked(days.slice(0, MAX_DAYS).map((d) => d.day))} className="text-slate-400 underline-offset-2 hover:underline">
          last {MAX_DAYS}
        </button>
        <button type="button" onClick={() => setPicked([])} className="text-slate-500 underline-offset-2 hover:underline">
          clear
        </button>
      </div>

      <FilterRow
        status={
          loading
            ? "Reading the saved scans…"
            : `${scans} saved scan${scans === 1 ? "" : "s"} on ${picked.length} day${picked.length === 1 ? "" : "s"} · ${symbols.length} stock${symbols.length === 1 ? "" : "s"} passed at least once (${hits.length} stock-days)`
        }
      >
        <FilterGroup title="Scans">
          <SelectFilter
            label="Candle"
            value={candle}
            onChange={(v) => setCandle(Number(v))}
            options={[{ value: 0, label: "Any size" }, ...CANDLE_CHOICES.map((m) => ({ value: m as number, label: `${m} min` }))]}
            className="w-28"
          />
          <SelectFilter
            label="Candles"
            value={candles}
            onChange={(v) => setCandles(v === "NORMAL" || v === "HA" ? v : "")}
            options={[
              { value: "", label: "Both" },
              { value: "NORMAL", label: "Normal" },
              { value: "HA", label: "Heikin Ashi" },
            ]}
            className="w-32"
          />
          <FilterField label="Table">
            <select
              value={table}
              onChange={(e) => setTable(e.target.value as Table)}
              aria-label="How to list the stocks"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              <option value="day">One row per stock per day</option>
              <option value="stock">One row per stock (all days)</option>
              <option value="scan">Every scan: how the trend formed</option>
            </select>
            <NewTag id="tq-history-timeline" />
            {table === "scan" && (
              <Toggle
                label="Only when it changed"
                checked={changesOnly}
                onChange={setChangesOnly}
                title="Show only the scans in which the stock's trend, phase or score changed since its previous scan that day"
              />
            )}
          </FilterField>
        </FilterGroup>
        <FilterGroup title="View">
          <SelectFilter label="Show" value={f.score} onChange={(v) => set("score", v as ScoreFilter)} options={SCORE_OPTIONS} className="w-44" />
          <TextFilter value={query} onChange={setQuery} />
          <NumberFilter
            label="Trend score at least"
            value={f.minTrendScore ?? 0}
            onChange={(v) => set("minTrendScore", Math.min(100, Math.max(0, Math.round(v))))}
            min={0}
            max={100}
            step={5}
            className="w-36"
          />
          <SelectFilter
            label="Checks at least"
            value={f.minChecks ?? 0}
            onChange={(v) => set("minChecks", Number(v))}
            options={[{ value: 0, label: "Any" }, ...[3, 4, 5, 6, 7].map((n) => ({ value: n, label: n === 7 ? "All 7" : `${n} of 7` }))]}
            className="w-28"
          />
          <SelectFilter
            label="Phase now"
            value={f.phase ?? "ANY"}
            onChange={(v) => set("phase", v as PhaseFilter)}
            options={PHASE_OPTIONS}
            className="w-36"
          />
          <NewTag id="tq-history-score-filters" />
          <NumberFilter
            label="Fast movers: |speed| ≥"
            value={f.minSpeed}
            onChange={(v) => set("minSpeed", Math.min(1, Math.max(0, Math.round(v * 1000) / 1000)))}
            min={0}
            max={0.3}
            step={0.01}
            suffix="%/min"
            className="w-44"
          />
          <FilterField label="Crosses today">
            <select
              value={f.crossCount}
              onChange={(e) => set("crossCount", (isCrossRule(e.target.value) ? e.target.value : "0") as CrossRule)}
              aria-label="How many SMA crosses today"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              {CROSS_RULES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400">on</span>
            {sizeButtons(f.noCrossOn, "noCrossOn", "bg-accentSky/15 text-accentSky ring-accentSky/50")}
          </FilterField>
          <FilterField label="Trend on">
            <select
              value={f.trendDir}
              onChange={(e) => set("trendDir", e.target.value as TrendDir)}
              aria-label="Trend direction on every ticked candle size"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              {TREND_DIRS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              value={f.trendKind}
              onChange={(e) => set("trendKind", e.target.value as TrendKind)}
              aria-label="How strong the trend must be"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              {TREND_KINDS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400">on</span>
            {sizeButtons(f.trendOn, "trendOn", "bg-profit/15 text-profit ring-profit/50")}
          </FilterField>
          {f.trendOn.length > 0 && (
            <>
              <NumberFilter
                label="Trend age at most (0 = any)"
                value={f.maxAge}
                onChange={(v) => set("maxAge", Math.min(1440, Math.max(0, Math.round(v))))}
                min={0}
                max={240}
                step={5}
                suffix=" min"
                className="w-40"
              />
              <NumberFilter
                label="SMA gap at least"
                value={f.minGap}
                onChange={(v) => set("minGap", Math.min(5, Math.max(0, Math.round(v * 100) / 100)))}
                min={0}
                max={1}
                step={0.01}
                suffix="%"
                className="w-36"
              />
              <FilterField label="Fading">
                <Toggle label="Hide fading trends" checked={f.hideFading} onChange={(v) => set("hideFading", v)} />
              </FilterField>
            </>
          )}
        </FilterGroup>
        <FilterGroup title="Price">
          <NumberFilter label="Price from (0 = any)" value={f.priceMin} onChange={(v) => set("priceMin", Math.max(0, v))} min={0} max={5000} step={50} prefix="₹ " className="w-40" />
          <NumberFilter label="Price to (0 = any)" value={f.priceMax} onChange={(v) => set("priceMax", Math.max(0, v))} min={0} max={5000} step={50} prefix="₹ " className="w-40" />
        </FilterGroup>
      </FilterRow>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}

      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        {onArmMany && symbols.length > 0 && (
          <button
            type="button"
            onClick={() => onArmMany(symbols.slice(0, 24))}
            className="min-h-8 rounded-md px-2.5 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
            title="Open the Arm ticked dialog for the stocks listed (at most 24, a bot's Trade list limit)"
          >
            Arm these {Math.min(24, symbols.length)}
          </button>
        )}
        {hits.length > 0 && (
          <button type="button" onClick={csv} className="flex min-h-8 items-center gap-1 rounded-md px-2.5 ring-1 ring-inset ring-slate-700 hover:bg-white/5">
            <Download size={13} /> CSV
          </button>
        )}
        {loading && <Loader2 size={13} className="animate-spin text-slate-400" />}
      </div>

      {picked.length === 0 ? (
        <p className="text-xs text-slate-500">Tick one or more days above.</p>
      ) : (table === "scan" ? seen.length === 0 && !changesOnly : hits.length === 0) && !loading ? (
        <p className="text-xs text-slate-500">
          {scans === 0 ? "No saved Trend quality scans on those days with this candle choice." : "No stock passed these filters in any scan of those days."}
        </p>
      ) : table === "scan" ? (
        <>
          <p className="mb-2 text-xs text-slate-400">
            Every saved scan in which the stock passed the filters, oldest first. Type a stock in Find a stock to follow one
            stock through the day; sort by time, trend, score or phase. &quot;What changed&quot; compares with that stock&apos;s
            previous scan of the same candle that day (green = stronger, red = weaker).
          </p>
          <Pager p={pagedScans} className="mb-2" noun="scans" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Day" k="day" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Time" k="time" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Stock" k="symbol" sort={scanSort.sort} onSort={scanSort.onSort} text />
                  <SortTh label="Price" k="ltp" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Trend" k="trend" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Trend score" k="trendScore" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Checks" k="checks" sort={scanSort.sort} onSort={scanSort.onSort} num title="Strong-trend checks passed" />
                  <SortTh label="Score" k="score" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Phase" k="phase" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <SortTh label="Crosses / flips" k="flips" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Day move" k="dayMove" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="Speed" k="speed" sort={scanSort.sort} onSort={scanSort.onSort} num />
                  <SortTh label="What changed" k="change" sort={scanSort.sort} onSort={scanSort.onSort} />
                  <th className="px-2 py-1.5 text-left">Scan</th>
                </tr>
              </thead>
              <tbody>
                {pagedScans.shown.map((r) => (
                  <tr key={r.key} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 tabular-nums">{dayText(r.day)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{clock(r.at)}</td>
                    <td className="px-2 py-1.5 font-semibold">
                      <button type="button" onClick={() => timelineOf(r.symbol)} className="hover:underline" title="Follow this stock through the day">
                        {r.symbol}
                      </button>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.ltp?.toFixed(2) ?? "—"}</td>
                    <td className={clsx("px-2 py-1.5", r.trend === "STRONG_BULLISH" ? "text-profit" : r.trend === "STRONG_BEARISH" ? "text-loss" : "text-slate-400")}>{trendText(r.trend)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.trendScore ?? "—"}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.checks != null ? `${r.checks}/${r.checksOf}` : "—"}</td>
                    <td className="px-2 py-1.5">{scoreText(r.score)}</td>
                    <td className={clsx("px-2 py-1.5", r.phase === "BULL" ? "text-profit" : r.phase === "BEAR" ? "text-loss" : "")}>{phaseText(r.phase)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.flips ?? "—"}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", (r.dayMove ?? 0) > 0 ? "text-profit" : (r.dayMove ?? 0) < 0 ? "text-loss" : "")}>{pct(r.dayMove)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.speed != null ? r.speed.toFixed(3) : "—"}</td>
                    <td className={clsx("px-2 py-1.5", r.dir > 0 ? "text-profit" : r.dir < 0 ? "text-loss" : "text-slate-400")}>{r.change || "—"}</td>
                    <td className="px-2 py-1.5 text-slate-400">{r.candle ? `${r.candle}m` : ""}{r.candles === "HA" ? " HA" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager p={pagedScans} className="mt-2" noun="scans" />
        </>
      ) : combine ? (
        <>
          <Pager p={pagedStocks} className="mb-2" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Stock" k="symbol" sort={byStock.sort} onSort={byStock.onSort} text />
                  <SortTh label="Days" k="days" sort={byStock.sort} onSort={byStock.onSort} num title="On how many of the picked days it passed at least once" />
                  <SortTh label="Scans passed" k="matched" sort={byStock.sort} onSort={byStock.onSort} num />
                  <SortTh label="First seen" k="first" sort={byStock.sort} onSort={byStock.onSort} />
                  <SortTh label="Avg move after" k="avgMove" sort={byStock.sort} onSort={byStock.onSort} num title="Average of each day's move from the first match to the day's last scan" />
                  <th className="px-2 py-1.5 text-right">Arm</th>
                </tr>
              </thead>
              <tbody>
                {pagedStocks.shown.map((s) => (
                  <tr key={s.key} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 font-semibold">
                      <GrowwLink symbol={s.symbol} />{" "}
                      <button type="button" onClick={() => timelineOf(s.symbol)} className="ml-1 text-[11px] font-normal text-accentSky hover:underline">
                        timeline
                      </button>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{s.days}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{s.matched}</td>
                    <td className="px-2 py-1.5 tabular-nums">{dayText(s.firstDay)} {clock(s.firstAt)}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", (s.avgMove ?? 0) > 0 ? "text-profit" : (s.avgMove ?? 0) < 0 ? "text-loss" : "")}>{pct(s.avgMove)}</td>
                    <td className="px-2 py-1.5 text-right"><ArmButton symbol={s.symbol} armed={armed} arming={arming} onArm={onArm} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager p={pagedStocks} className="mt-2" />
        </>
      ) : (
        <>
          <Pager p={paged} className="mb-2" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-xs">
              <thead className="text-slate-400">
                <tr>
                  <SortTh label="Day" k="day" sort={sort} onSort={onSort} />
                  <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
                  <SortTh label="First passed" k="first" sort={sort} onSort={onSort} title="The first saved scan that day in which it passed every filter" />
                  <SortTh label="Last passed" k="last" sort={sort} onSort={onSort} />
                  <SortTh label="Scans passed" k="matched" sort={sort} onSort={onSort} num title="In how many of that day's saved scans it passed" />
                  <SortTh label="Price then" k="firstPrice" sort={sort} onSort={onSort} num />
                  <SortTh label="Move to day's last scan" k="move" sort={sort} onSort={onSort} num title="From the price at the first pass to its price in the day's last saved scan" />
                  <SortTh label="Trend then" k="trend" sort={sort} onSort={onSort} />
                  <th className="px-2 py-1.5 text-left">Scan</th>
                  <th className="px-2 py-1.5 text-right">Arm</th>
                </tr>
              </thead>
              <tbody>
                {paged.shown.map((h) => (
                  <tr key={h.key} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 tabular-nums">{dayText(h.day)}</td>
                    <td className="px-2 py-1.5 font-semibold">
                      <GrowwLink symbol={h.symbol} />{" "}
                      <button type="button" onClick={() => timelineOf(h.symbol)} className="ml-1 text-[11px] font-normal text-accentSky hover:underline" title="See every scan of this stock: how its trend formed">
                        timeline
                      </button>
                    </td>
                    <td className="px-2 py-1.5 tabular-nums">{clock(h.firstAt)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{clock(h.lastAt)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{h.matched} of {h.scans}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{h.firstPrice?.toFixed(2) ?? "—"}</td>
                    <td className={clsx("px-2 py-1.5 text-right tabular-nums", (h.movePct ?? 0) > 0 ? "text-profit" : (h.movePct ?? 0) < 0 ? "text-loss" : "")}>{pct(h.movePct)}</td>
                    <td className="px-2 py-1.5">{trendText(h.trendFirst)}{h.scoreFirst ? ` · ${h.scoreFirst.toLowerCase()}` : ""}</td>
                    <td className="px-2 py-1.5 text-slate-400">{h.candle ? `${h.candle}m` : ""}{h.candles === "HA" ? " HA" : ""}</td>
                    <td className="px-2 py-1.5 text-right"><ArmButton symbol={h.symbol} armed={armed} arming={arming} onArm={onArm} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager p={paged} className="mt-2" />
        </>
      )}
    </section>
  );
}

function ArmButton({ symbol, armed, arming, onArm }: { symbol: string; armed: Map<string, string[]>; arming: string | null; onArm: (s: string) => void }) {
  const on = armed.get(symbol);
  return (
    <button
      type="button"
      disabled={arming === symbol}
      onClick={() => onArm(symbol)}
      className={clsx(
        "min-h-7 rounded-md px-2 text-[11px] ring-1 ring-inset",
        on?.length ? "text-profit ring-profit/40" : "text-slate-300 ring-slate-700 hover:bg-white/5"
      )}
      title={on?.length ? `Armed on ${on.join(", ")}` : "Arm this stock (Trade list only, no order)"}
    >
      {arming === symbol ? "…" : on?.length ? "Armed" : "Arm"}
    </button>
  );
}
