"use client";

import clsx from "clsx";
import { Loader2, Play, Square, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScanListPicker, resolveScanSymbols, useScanLists, type ScanList } from "@/components/Scalp/ScanLists";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { GrowwLink } from "@/components/ui/growwLink";
import { NewTag } from "@/components/ui/NewTag";
import { TextFilter, matchesText } from "@/components/ui/tableTools";
import { smaApi, type TrendHoldChart, type TrendHoldKind, type TrendHoldRow, type TrendHoldView as View } from "@/lib/smaApi";
import { GuideBar } from "./GuideBar";

/**
 * Trend hold test (backend trend_hold.py): when Trend quality shows a stock strong (7 of 7, 6 of 7 checks…) or in a
 * strong phase, how far does it go by the end of the day? Past days of the picked stocks, read on a bot's SMA periods
 * and candle; each stock entered at its first strong read, the trend's way, held to the square-off. Results as a
 * table, an average line and each pick's price by minute (or by second where the bots recorded them). Read-only
 * research; no order is placed; Arm only arms.
 */
const POLL_MS = 3000;
const BOTS = [1, 2, 3, 4, 5];
const SIZES = [1, 3, 5, 10, 15];
const STEPS = [1, 2, 3, 5, 10, 15, 30];
const CHECKS = [7, 6, 5, 4];
const TIMES = ["09:20", "09:30", "09:45", "10:00", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"];
const SQUARE_OFFS = ["12:00", "13:00", "14:00", "14:30", "15:00", "15:15", "15:25", "15:30"];
const KINDS: { id: TrendHoldKind; label: string; hint: string }[] = [
  { id: "TREND", label: "Strong trend (checks)", hint: "At least N of the 7 strong-trend checks pass for the SMAs' side." },
  { id: "PHASE", label: "Strong phase", hint: "The phase now is strong up / down (not choppy) for at least N candles." },
  { id: "EITHER", label: "Trend or phase", hint: "Either one, the trend's side first." },
  { id: "BOTH", label: "Trend and phase", hint: "Both, the same way." },
];

type Props = {
  armed: Map<string, string[]>;
  onArm?: (symbol: string) => void;
  onArmMany?: (symbols: string[]) => void;
  onStatus?: OnScanStatus;
};

const istDay = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
function lastWeekdays(n: number): [string, string] {
  const out: string[] = [];
  const d = new Date(Date.now() + 5.5 * 3600e3);
  while (out.length < n) {
    d.setUTCDate(d.getUTCDate() - 1);
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return [out[out.length - 1], out[0]];
}
const rs = (v: number | null | undefined) => (v == null ? "—" : `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);
const pc = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null ? "text-slate-400" : v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-300");
const toMin = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
const hhmmOf = (ts: number, sec = false) =>
  new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: sec ? "2-digit" : undefined, hour12: false, timeZone: "Asia/Kolkata" });

/* ---------- a small SVG line chart ---------- */
type Series = { name: string; color: string; points: [number, number][]; dashed?: boolean };
type Mark = { x: number; label: string; color: string };
type HLine = { y: number; label: string; color: string };

function LineChart({ series, marks = [], hlines = [], xFmt, yFmt, height = 260, zero = false }: {
  series: Series[]; marks?: Mark[]; hlines?: HLine[]; xFmt: (x: number) => string; yFmt: (y: number) => string; height?: number; zero?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 900, H = height, L = 64, R = 12, T = 12, B = 26;
  const all = series.flatMap((s) => s.points);
  if (!all.length) return <p className="text-xs text-slate-500">Nothing to draw.</p>;
  const xs = all.map((p) => p[0]);
  const ys = [...all.map((p) => p[1]), ...hlines.map((h) => h.y), ...(zero ? [0] : [])];
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.06;
  y0 -= pad; y1 += pad;
  const sx = (x: number) => L + ((x - x0) / (x1 - x0 || 1)) * (W - L - R);
  const sy = (y: number) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
  const yTicks = Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4);
  const xTicks = Array.from({ length: 7 }, (_, i) => x0 + ((x1 - x0) * i) / 6);
  const near = (s: Series, x: number) => {
    let best: [number, number] | null = null;
    for (const p of s.points) if (!best || Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p;
    return best;
  };
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full select-none"
        role="img"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHover(x0 + ((Math.min(Math.max(px, L), W - R) - L) / (W - L - R)) * (x1 - x0));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {yTicks.map((y) => (
          <g key={y}>
            <line x1={L} x2={W - R} y1={sy(y)} y2={sy(y)} stroke="currentColor" className="text-slate-700/60" strokeWidth={0.6} />
            <text x={L - 6} y={sy(y) + 3} textAnchor="end" fontSize={10} fill="currentColor" className="text-slate-400">{yFmt(y)}</text>
          </g>
        ))}
        {xTicks.map((x) => (
          <text key={x} x={sx(x)} y={H - 8} textAnchor={x === x0 ? "start" : x === x1 ? "end" : "middle"} fontSize={10} fill="currentColor" className="text-slate-400">{xFmt(x)}</text>
        ))}
        {zero && y0 < 0 && y1 > 0 ? <line x1={L} x2={W - R} y1={sy(0)} y2={sy(0)} stroke="currentColor" className="text-slate-400" strokeWidth={0.8} /> : null}
        {hlines.map((h) => (
          <g key={h.label}>
            <line x1={L} x2={W - R} y1={sy(h.y)} y2={sy(h.y)} stroke={h.color} strokeDasharray="4 3" strokeWidth={1} />
            <text x={W - R - 2} y={sy(h.y) - 3} textAnchor="end" fontSize={10} fill={h.color}>{h.label}</text>
          </g>
        ))}
        {marks.filter((m) => m.x >= x0 && m.x <= x1).map((m, i) => (
          <g key={`${m.label}${i}`}>
            <line x1={sx(m.x)} x2={sx(m.x)} y1={T} y2={H - B} stroke={m.color} strokeDasharray="3 3" strokeWidth={1} />
            <text x={sx(m.x) > W - 90 ? sx(m.x) - 3 : sx(m.x) + 3} textAnchor={sx(m.x) > W - 90 ? "end" : "start"} y={T + 10 + (i % 3) * 11} fontSize={10} fill={m.color}>{m.label}</text>
          </g>
        ))}
        {series.map((s) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={s.color}
            strokeWidth={1.6}
            strokeDasharray={s.dashed ? "5 3" : undefined}
            points={s.points.map((p) => `${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ")}
          />
        ))}
        {hover != null ? <line x1={sx(hover)} x2={sx(hover)} y1={T} y2={H - B} stroke="currentColor" className="text-slate-400" strokeWidth={0.6} /> : null}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
        {series.map((s) => {
          const p = hover != null ? near(s, hover) : null;
          return (
            <span key={s.name} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4" style={{ background: s.color }} aria-hidden />
              {s.name}
              {p ? <b className="text-slate-200">{xFmt(p[0])} · {yFmt(p[1])}</b> : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- one pick's price chart ---------- */
function PickChart({ row, squareOff, seconds, setSeconds }: { row: TrendHoldRow; squareOff: string; seconds: boolean; setSeconds: (v: boolean) => void }) {
  const [data, setData] = useState<TrendHoldChart | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [whole, setWhole] = useState(true);
  useEffect(() => {
    let live = true;
    setErr(null);
    smaApi
      .trendHoldChart(row.symbol, row.day, whole ? "09:15" : row.at, whole ? "15:30" : squareOff, seconds)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [row.symbol, row.day, row.at, squareOff, seconds, whole]);
  const base = useMemo(() => {
    const [y, m, d] = row.day.split("-").map(Number);
    return Date.UTC(y, m - 1, d) / 1000 - 5.5 * 3600;
  }, [row.day]);
  const at = (hhmm: string) => base + toMin(hhmm) * 60;
  const useSec = seconds && (data?.seconds.length ?? 0) > 1;
  const pts: [number, number][] = useSec ? (data?.seconds ?? []) : (data?.minutes ?? []).map((c) => [c[0] + 60, c[4]]);
  const marks: Mark[] = [
    { x: at(row.at), label: `in ${row.at}`, color: "#38bdf8" },
    ...(row.lost_at ? [{ x: at(row.lost_at), label: `lost ${row.lost_at}`, color: "#f59e0b" }] : []),
    { x: at(row.best_at), label: `best ${row.best_at}`, color: "#10b981" },
    { x: at(row.worst_at), label: `worst ${row.worst_at}`, color: "#f43f5e" },
    { x: at(row.exit_at), label: `out ${row.exit_at}`, color: "#a78bfa" },
  ];
  return (
    <div className="rounded-lg border border-border bg-surface2/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-300">
        <b className="text-sm text-slate-100">{row.symbol}</b>
        <span>{row.day}</span>
        <span className={row.side === "LONG" ? "text-emerald-300" : "text-rose-300"}>{row.side === "LONG" ? "Buy" : "Sell"} at {row.entry} ({row.at})</span>
        <span>out {row.exit} at {row.exit_at}: <b className={tone(row.pnl)}>{rs(row.pnl)}</b> ({pc(row.pct)})</span>
        <span className="text-slate-400">{row.why}</span>
        <span className="ml-auto inline-flex gap-3">
          <label className="inline-flex items-center gap-1"><input type="checkbox" checked={whole} onChange={(e) => setWhole(e.target.checked)} /> Whole day</label>
          <label className="inline-flex items-center gap-1" title="Seconds exist only for stocks a bot watched in the last 10 days (recorded prices). Else the minute line is shown.">
            <input type="checkbox" checked={seconds} onChange={(e) => setSeconds(e.target.checked)} /> By second
          </label>
        </span>
      </div>
      {err ? <p className="text-xs text-rose-300">{err}</p> : !data ? <p className="text-xs text-slate-400"><Loader2 size={12} className="inline animate-spin" /> Loading the price…</p> : (
        <>
          {seconds && !useSec ? <p className="mb-1 text-[11px] text-amber-300">No second-by-second prices were recorded for this stock that day; showing the price by minute.</p> : null}
          <LineChart
            series={[{ name: useSec ? "Price by second" : "Price by minute (close)", color: "#3b82f6", points: pts }]}
            marks={marks}
            hlines={[{ y: row.entry, label: `entry ${row.entry}`, color: "#38bdf8" }, { y: row.exit, label: `exit ${row.exit}`, color: "#a78bfa" }]}
            xFmt={(x) => hhmmOf(x, useSec)}
            yFmt={(y) => y.toFixed(2)}
          />
        </>
      )}
    </div>
  );
}

/* ---------- the tab ---------- */
export function TrendHold({ armed, onArm, onArmMany, onStatus }: Props) {
  const [lists, setLists] = useScanLists("sma.trendhold.lists", ["fno"]);
  const [[from, to], setRange] = useState<[string, string]>(() => lastWeekdays(10));
  const [bot, setBot] = useState(1);
  const [kind, setKind] = useState<TrendHoldKind>("TREND");
  const [minChecks, setMinChecks] = useState(7);
  const [strongOnly, setStrongOnly] = useState(true);
  const [phaseMin, setPhaseMin] = useState(5);
  const [side, setSide] = useState<"BOTH" | "UP" | "DOWN">("BOTH");
  const [minutes, setMinutes] = useState(0);
  const [candles, setCandles] = useState<"NORMAL" | "HA">("NORMAL");
  const [first, setFirst] = useState("10:00");
  const [last, setLast] = useState("14:00");
  const [every, setEvery] = useState(5);
  const [squareOff, setSquareOff] = useState("15:15");
  const [qty, setQty] = useState(0);
  const [priceMin, setPriceMin] = useState(0);
  const [priceMax, setPriceMax] = useState(0);
  const [maxDay, setMaxDay] = useState(20);
  const [data, setData] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sideF, setSideF] = useState<"ALL" | "LONG" | "SHORT">("ALL");
  const [heldF, setHeldF] = useState<"ALL" | "HELD" | "LOST">("ALL");
  const [checksF, setChecksF] = useState<number | null>(null);
  const [dayF, setDayF] = useState("");
  const [pick, setPick] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(false);
  const [unit, setUnit] = useState<"pct" | "rs">("pct");
  const ticks = useTicks();

  const load = useCallback(async (rows = true) => {
    try {
      const v = await smaApi.trendHold(rows);
      setData((cur) => (rows || !cur ? v : { ...cur, status: v.status, done: v.done, total: v.total, errors: v.errors }));
      onStatus?.({ when: v.finished_at, running: v.status === "RUNNING" });
      return v;
    } catch {
      return null;
    }
  }, [onStatus]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (data?.status !== "RUNNING") return;
    const id = setInterval(async () => {
      const v = await load(false);
      if (v && v.status !== "RUNNING") void load();
    }, POLL_MS);
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
      setData(
        await smaApi.trendHoldStart({
          symbols: symbols.slice(0, 250), date: from, end_date: to, bot, kind, min_checks: minChecks, strong_only: strongOnly,
          phase_min: phaseMin, side, minutes, candles, first, last, every_min: every, square_off: squareOff, qty,
          price_min: priceMin, price_max: priceMax, max_day: maxDay,
        })
      );
      setPick(null);
      if (symbols.length > 250) setMsg(`Only the first 250 of ${symbols.length} stocks are read.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      setData(await smaApi.trendHoldStop());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const running = data?.status === "RUNNING";
  const rep = data?.report;
  const params = data?.params ?? {};
  const pSquare = String(params.square_off ?? squareOff);
  const qtyRun = Number(params.qty ?? 0) || 1;
  const all = useMemo(() => data?.rows ?? [], [data?.rows]);
  const days = useMemo(() => Array.from(new Set(all.map((r) => r.day))).sort(), [all]);
  const checkOptions = useMemo(() => Array.from(new Set(all.map((r) => r.passed))).sort((a, b) => b - a), [all]);
  const rows = useMemo(
    () =>
      all.filter((r) => {
        if (sideF !== "ALL" && r.side !== sideF) return false;
        if (heldF === "HELD" && !r.held_to_end) return false;
        if (heldF === "LOST" && r.held_to_end) return false;
        if (checksF != null && r.passed !== checksF) return false;
        if (dayF && r.day !== dayF) return false;
        if (ticks.only && !ticks.ticked.has(r.symbol)) return false;
        return matchesText(query, r.symbol);
      }),
    [all, sideF, heldF, checksF, dayF, query, ticks.only, ticks.ticked]
  );
  const key = (r: TrendHoldRow) => `${r.symbol}|${r.day}`;
  const selected = rows.find((r) => key(r) === pick) ?? rows[0] ?? null;

  const sum = useMemo(() => {
    const n = rows.length;
    const pnl = rows.reduce((a, r) => a + r.pnl, 0);
    const net = rows.reduce((a, r) => a + r.net, 0);
    const lost = rows.reduce((a, r) => a + (r.lost_pnl ?? r.pnl), 0);
    const avg = (f: (r: TrendHoldRow) => number) => (n ? rows.reduce((a, r) => a + f(r), 0) / n : null);
    return {
      n, pnl, net, lost, held: rows.filter((r) => r.held_to_end).length, wins: rows.filter((r) => r.pnl > 0).length,
      avgPct: avg((r) => r.pct), avgBest: avg((r) => r.best_pct), avgWorst: avg((r) => r.worst_pct),
      best: avg((r) => r.best_rs), worst: avg((r) => r.worst_rs),
    };
  }, [rows]);

  // Average move from the entry, minute by minute (signed the trade's way), and the same split held / lost.
  const avgLines = useMemo(() => {
    const line = (part: TrendHoldRow[]): [number, number][] => {
      const len = Math.max(0, ...part.map((r) => r.curve.length));
      const out: [number, number][] = [];
      for (let i = 0; i < len; i++) {
        const have = part.filter((r) => r.curve[i] != null);
        if (have.length < Math.max(1, Math.ceil(part.length * 0.2))) break;
        // % of the entry, or ₹ for the run's quantity (curve % × entry × qty).
        const vals = have.map((r) => (unit === "pct" ? r.curve[i] : (r.curve[i] / 100) * r.entry * qtyRun));
        out.push([i + 1, vals.reduce((a, b) => a + b, 0) / vals.length]);
      }
      return out;
    };
    const held = rows.filter((r) => r.held_to_end);
    const lost = rows.filter((r) => !r.held_to_end);
    const s: Series[] = [{ name: `All picks (${rows.length})`, color: "#64748b", points: line(rows) }];
    if (held.length) s.push({ name: `Still strong at every check (${held.length})`, color: "#10b981", points: line(held) });
    if (lost.length) s.push({ name: `Reading lost on the way (${lost.length})`, color: "#f59e0b", points: line(lost), dashed: true });
    return s;
  }, [rows, unit, qtyRun]);

  const dayLine = useMemo(() => {
    const by = new Map<string, number>();
    for (const r of rows) by.set(r.day, (by.get(r.day) ?? 0) + r.pnl);
    let run = 0, runLost = 0;
    const lostBy = new Map<string, number>();
    for (const r of rows) lostBy.set(r.day, (lostBy.get(r.day) ?? 0) + (r.lost_pnl ?? r.pnl));
    const ds = Array.from(by.keys()).sort();
    return {
      days: ds,
      series: [
        { name: `Held to ${pSquare}`, color: "#38bdf8", points: ds.map((d, i) => [i, (run += by.get(d) ?? 0)] as [number, number]) },
        { name: "Closed when the reading was lost", color: "#f59e0b", dashed: true, points: ds.map((d, i) => [i, (runLost += lostBy.get(d) ?? 0)] as [number, number]) },
      ] as Series[],
    };
  }, [rows, pSquare]);

  type K = "symbol" | "day" | "at" | "side" | "passed" | "phase" | "price" | "entry" | "pct" | "pnl" | "net" | "best_pct" | "best_at" | "worst_pct" | "worst_at" | "lost_at" | "lost_pnl" | "exit";
  const value = (r: TrendHoldRow, k: K) => (k === "lost_at" ? r.lost_at ?? "~" : (r[k] as number | string | null));
  const { sorted, sort, onSort } = useSort<TrendHoldRow, K>(rows, value);

  const select = "min-h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100";
  const num = "min-h-9 w-24 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100";
  const td = "px-2 py-1 tabular-nums";
  const card = (label: string, val: React.ReactNode, cls = "text-slate-100", hint?: string) => (
    <div className="rounded-lg border border-border bg-surface2/40 px-3 py-2" title={hint}>
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("text-lg font-semibold tabular-nums", cls)}>{val}</div>
    </div>
  );
  const csv = () => {
    const head = ["day", "stock", "read_at", "side", "checks", "of", "trend", "phase", "price", "entry", "exit", "exit_at", "points", "pct", "pnl", "charges", "net", "best_pct", "best_at", "worst_pct", "worst_at", "lost_at", "lost_pnl", "held_to_end", "why"];
    const lines = rows.map((r) => [r.day, r.symbol, r.at, r.side, r.passed, r.of, r.trend ?? "", r.phase ?? "", r.price, r.entry, r.exit, r.exit_at, r.points, r.pct, r.pnl, r.charges, r.net, r.best_pct, r.best_at, r.worst_pct, r.worst_at, r.lost_at ?? "", r.lost_pnl ?? "", r.held_to_end, `"${r.why.replace(/"/g, "'")}"`].join(","));
    const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trend-hold-${days[0] ?? ""}-${days[days.length - 1] ?? ""}.csv`;
    a.click();
  };

  return (
    <section className="terminal-dark space-y-4 rounded-xl border border-border bg-surface p-3 shadow-card sm:p-4">
      <div className="max-w-3xl">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
          <TrendingUp size={18} className="text-emerald-300" aria-hidden /> Trend hold test <NewTag id="trend-hold-test" />
        </h2>
        <p className="mt-0.5 text-sm text-slate-300">
          Does a strong trend (7 of 7, 6 of 7 checks…) or a strong phase keep going to the end of the day? On past days of
          the picked stocks, read the way the Trend quality card reads them, on the bot&apos;s own averages and candle. Each
          stock is entered at its first strong read, the trend&apos;s way, with your quantity, and held to the square-off. The
          read keeps being taken, so you see whether it stayed strong and what closing when it was lost would have made.
          Practice arithmetic only; nothing is ordered.
        </p>
        <GuideBar terms={["trend", "strong_trend", "phase", "square_off", "pnl_before", "arm"]} className="mt-2" />
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Stocks
          <ScanListPicker value={lists as ScanList[]} onChange={setLists} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          From
          <input type="date" value={from} max={istDay(new Date())} onChange={(e) => setRange([e.target.value, to])} className={select} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          To
          <input type="date" value={to} max={istDay(new Date())} onChange={(e) => setRange([from, e.target.value])} className={select} disabled={running} />
        </label>
        <div className="flex gap-1 self-end">
          {[1, 5, 10, 20].map((n) => (
            <button key={n} type="button" disabled={running} onClick={() => setRange(lastWeekdays(n))} className="min-h-9 rounded-md px-2 ring-1 ring-inset ring-slate-700 hover:bg-white/5">
              Last {n}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1">
          Bot (averages, candle, qty)
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={select} disabled={running}>
            {BOTS.map((b) => <option key={b} value={b}>Bot {b}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Candle
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={select} disabled={running}>
            <option value={0}>Bot&apos;s own</option>
            {SIZES.map((m) => <option key={m} value={m}>{m} min</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Candles
          <select value={candles} onChange={(e) => setCandles(e.target.value as "NORMAL" | "HA")} className={select} disabled={running}>
            <option value="NORMAL">Normal</option>
            <option value="HA">Heikin Ashi</option>
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Pick on
          <select value={kind} onChange={(e) => setKind(e.target.value as TrendHoldKind)} className={select} disabled={running} title={KINDS.find((k) => k.id === kind)?.hint}>
            {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
          </select>
        </label>
        {kind !== "PHASE" ? (
          <>
            <label className="flex flex-col gap-1">
              Checks passed at least
              <select value={minChecks} onChange={(e) => setMinChecks(Number(e.target.value))} className={select} disabled={running}>
                {CHECKS.map((c) => <option key={c} value={c}>{c} of 7</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1.5 self-end pb-2" title="The card's Strong label also needs stack, VWAP and slope to pass (morning rules before 10:00).">
              <input type="checkbox" checked={strongOnly} onChange={(e) => setStrongOnly(e.target.checked)} disabled={running} /> Must read Strong on the card
            </label>
          </>
        ) : null}
        {kind !== "TREND" ? (
          <label className="flex flex-col gap-1">
            Phase at least (candles)
            <input type="number" min={1} max={60} value={phaseMin} onChange={(e) => setPhaseMin(Number(e.target.value) || 1)} className={num} disabled={running} />
          </label>
        ) : null}
        <label className="flex flex-col gap-1">
          Way
          <select value={side} onChange={(e) => setSide(e.target.value as "BOTH" | "UP" | "DOWN")} className={select} disabled={running}>
            <option value="BOTH">Up and down</option>
            <option value="UP">Up only (buy)</option>
            <option value="DOWN">Down only (sell)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Read from
          <select value={first} onChange={(e) => setFirst(e.target.value)} className={select} disabled={running}>
            {TIMES.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Last entry
          <select value={last} onChange={(e) => setLast(e.target.value)} className={select} disabled={running}>
            {TIMES.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Read every
          <select value={every} onChange={(e) => setEvery(Number(e.target.value))} className={select} disabled={running}>
            {STEPS.map((s) => <option key={s} value={s}>{s} min</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Hold until
          <select value={squareOff} onChange={(e) => setSquareOff(e.target.value)} className={select} disabled={running}>
            {SQUARE_OFFS.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Quantity (0 = bot&apos;s)
          <input type="number" min={0} value={qty} onChange={(e) => setQty(Math.max(0, Math.floor(Number(e.target.value) || 0)))} className={num} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          LTP from ₹ (0 = any)
          <input type="number" min={0} value={priceMin} onChange={(e) => setPriceMin(Math.max(0, Number(e.target.value) || 0))} className={num} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          LTP to ₹ (0 = any)
          <input type="number" min={0} value={priceMax} onChange={(e) => setPriceMax(Math.max(0, Number(e.target.value) || 0))} className={num} disabled={running} />
        </label>
        <label className="flex flex-col gap-1">
          Max picks a day
          <input type="number" min={1} max={100} value={maxDay} onChange={(e) => setMaxDay(Math.min(100, Math.max(1, Number(e.target.value) || 1)))} className={num} disabled={running} />
        </label>
        {running ? (
          <button type="button" onClick={stop} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-rose-600 px-3 font-semibold text-white">
            <Square size={13} /> Stop
          </button>
        ) : (
          <button type="button" onClick={run} disabled={busy} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-accentSky px-3 font-semibold text-on-accent disabled:opacity-50">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run the test
          </button>
        )}
        {running ? <span className="pb-2 text-slate-300"><Loader2 size={12} className="inline animate-spin" /> Reading {data?.done} of {data?.total} stocks…</span> : null}
      </div>
      {msg ? <p className="text-xs text-rose-300">{msg}</p> : null}
      {data?.errors?.length ? <p className="text-[11px] text-amber-300">Not read: {data.errors.slice(0, 6).map((e) => `${e.symbol || "test"} (${e.error})`).join("; ")}</p> : null}

      {rep ? (
        <>
          <div className="text-[11px] text-slate-400">
            Last run {data?.finished_at?.slice(0, 16).replace("T", " ")} · Bot {String(params.bot ?? "")} · SMA {String(params.sma_fast ?? "")}/{String(params.sma_slow ?? "")} on {String(params.minutes ?? "")}-min
            {params.candles === "HA" ? " Heikin Ashi" : ""} candles · {String(params.kind ?? "")} · reads {String(params.first ?? "")}–{String(params.last ?? "")} every {String(params.every_min ?? "")} min · hold to {pSquare} · {String(params.qty ?? "")} shares
            {rep.stopped ? " · stopped early" : ""}
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-200">
            {rep.answer.map((a) => <li key={a}>{a}</li>)}
          </ul>

          {all.length ? (
            <>
              <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
                <label className="flex flex-col gap-1">
                  Day
                  <select value={dayF} onChange={(e) => setDayF(e.target.value)} className={select}>
                    <option value="">All days</option>
                    {days.map((d) => <option key={d}>{d}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  Side
                  <select value={sideF} onChange={(e) => setSideF(e.target.value as "ALL" | "LONG" | "SHORT")} className={select}>
                    <option value="ALL">Both</option>
                    <option value="LONG">Buys</option>
                    <option value="SHORT">Sells</option>
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  Checks at the pick
                  <select value={checksF ?? ""} onChange={(e) => setChecksF(e.target.value === "" ? null : Number(e.target.value))} className={select}>
                    <option value="">Any</option>
                    {checkOptions.map((c) => <option key={c} value={c}>{c} of 7</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  Stayed strong?
                  <select value={heldF} onChange={(e) => setHeldF(e.target.value as "ALL" | "HELD" | "LOST")} className={select}>
                    <option value="ALL">All</option>
                    <option value="HELD">Yes, to the end</option>
                    <option value="LOST">Lost on the way</option>
                  </select>
                </label>
                <TextFilter value={query} onChange={setQuery} placeholder="Search stock" />
                <button type="button" onClick={csv} className="min-h-9 rounded-md px-3 ring-1 ring-inset ring-slate-700 hover:bg-white/5">CSV</button>
              </div>

              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
                {card("Picks", sum.n)}
                {card("Stayed strong", `${sum.held} (${sum.n ? Math.round((sum.held / sum.n) * 100) : 0}%)`, "text-slate-100", "Still read strong the same way at every check up to the square-off")}
                {card("In profit at the end", `${sum.wins} (${sum.n ? Math.round((sum.wins / sum.n) * 100) : 0}%)`)}
                {card("Avg to the end", pc(sum.avgPct), tone(sum.avgPct))}
                {card("Avg best on the way", pc(sum.avgBest), tone(sum.avgBest))}
                {card("Avg worst on the way", pc(sum.avgWorst), tone(sum.avgWorst))}
                {card(`P&L held to ${pSquare}`, rs(sum.pnl), tone(sum.pnl), `Before charges; after charges ${rs(sum.net)}`)}
                {card("Closed when lost", rs(sum.lost), tone(sum.lost), "If each trade had been closed at the first read that no longer passed (before charges)")}
              </div>

              <div className="rounded-lg border border-border bg-surface2/40 p-3">
                <div className="mb-1 flex flex-wrap items-center gap-3 text-xs text-slate-300">
                  <b className="text-sm text-slate-100">Average move after the pick, minute by minute</b>
                  <span className="text-slate-400">signed the trade&apos;s way (up = the trend kept going)</span>
                  <span className="ml-auto inline-flex gap-1">
                    {(["pct", "rs"] as const).map((u) => (
                      <button key={u} type="button" onClick={() => setUnit(u)} className={clsx("rounded px-2 py-0.5 ring-1 ring-inset ring-slate-700", unit === u && "bg-white/10 text-slate-100")}>
                        {u === "pct" ? "%" : "₹ a trade"}
                      </button>
                    ))}
                  </span>
                </div>
                <LineChart series={avgLines} zero xFmt={(x) => `${Math.round(x)} min`} yFmt={(y) => (unit === "pct" ? `${y.toFixed(2)}%` : rs(y))} />
              </div>

              {dayLine.days.length > 1 ? (
                <div className="rounded-lg border border-border bg-surface2/40 p-3">
                  <b className="mb-1 block text-sm text-slate-100">Running P&L by day (before charges)</b>
                  <LineChart series={dayLine.series} zero height={200} xFmt={(x) => dayLine.days[Math.round(x)]?.slice(5) ?? ""} yFmt={rs} />
                </div>
              ) : null}

              {selected ? <PickChart row={selected} squareOff={pSquare} seconds={seconds} setSeconds={setSeconds} /> : null}

              {rep.by_checks && rep.by_checks.length > 1 ? (
                <div className="overflow-x-auto">
                  <b className="text-sm text-slate-100">By checks passed at the pick (all picks)</b>
                  <table className="mt-1 min-w-[560px] text-xs">
                    <thead className="text-[11px] uppercase text-slate-500">
                      <tr><th className="px-2 text-left">Checks</th><th className="px-2 text-right">Picks</th><th className="px-2 text-right">Stayed strong</th><th className="px-2 text-right">In profit</th><th className="px-2 text-right">Avg to end</th><th className="px-2 text-right">Avg best</th><th className="px-2 text-right">P&L</th></tr>
                    </thead>
                    <tbody>
                      {rep.by_checks.map((b) => (
                        <tr key={b.passed} className="border-t border-border/60">
                          <td className={td}>{b.passed} of 7</td><td className={clsx(td, "text-right")}>{b.trades}</td><td className={clsx(td, "text-right")}>{b.held_pct}%</td>
                          <td className={clsx(td, "text-right")}>{b.win_pct}%</td><td className={clsx(td, "text-right", tone(b.avg_pct))}>{pc(b.avg_pct)}</td>
                          <td className={clsx(td, "text-right", tone(b.avg_best_pct))}>{pc(b.avg_best_pct)}</td><td className={clsx(td, "text-right", tone(b.pnl))}>{rs(b.pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
              <p className="text-[11px] text-slate-500">Click a row to draw that stock&apos;s price above.</p>
              <div className="max-h-[560px] overflow-auto">
                <table className="w-full min-w-[1200px] text-xs">
                  <thead className="sticky top-0 z-10 bg-surface text-[11px]">
                    <tr>
                      {onArmMany ? <TickHead ticks={ticks} symbols={sorted.map((r) => r.symbol)} /> : null}
                      <SortTh label="Day" k="day" sort={sort} onSort={onSort} text />
                      <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text />
                      <SortTh label="Read at" k="at" sort={sort} onSort={onSort} text />
                      <SortTh label="Side" k="side" sort={sort} onSort={onSort} text />
                      <SortTh label="Checks" k="passed" sort={sort} onSort={onSort} num />
                      <SortTh label="Phase" k="phase" sort={sort} onSort={onSort} text />
                      <SortTh label="LTP" k="price" sort={sort} onSort={onSort} num />
                      <SortTh label="Entry" k="entry" sort={sort} onSort={onSort} num />
                      <SortTh label="Exit" k="exit" sort={sort} onSort={onSort} num />
                      <SortTh label="To end %" k="pct" sort={sort} onSort={onSort} num />
                      <SortTh label="P&L ₹" k="pnl" sort={sort} onSort={onSort} num />
                      <SortTh label="After charges" k="net" sort={sort} onSort={onSort} num />
                      <SortTh label="Best %" k="best_pct" sort={sort} onSort={onSort} num />
                      <SortTh label="Best at" k="best_at" sort={sort} onSort={onSort} text />
                      <SortTh label="Worst %" k="worst_pct" sort={sort} onSort={onSort} num />
                      <SortTh label="Worst at" k="worst_at" sort={sort} onSort={onSort} text />
                      <SortTh label="Strong until" k="lost_at" sort={sort} onSort={onSort} text title="The first read that no longer passed; ✓ = strong at every check to the end" />
                      <SortTh label="If closed then ₹" k="lost_pnl" sort={sort} onSort={onSort} num />
                      {onArm ? <th className="px-1" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((r) => (
                      <tr
                        key={key(r)}
                        onClick={() => setPick(key(r))}
                        className={clsx("cursor-pointer border-t border-border/60 hover:bg-white/[0.03]", selected && key(selected) === key(r) && "bg-sky-500/10")}
                        title={r.why}
                      >
                        {onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null}
                        <td className={td}>{r.day}</td>
                        <td className={td}><GrowwLink symbol={r.symbol} /></td>
                        <td className={td}>{r.at}</td>
                        <td className={clsx(td, r.side === "LONG" ? "text-emerald-300" : "text-rose-300")}>{r.side === "LONG" ? "Buy" : "Sell"}</td>
                        <td className={clsx(td, "text-right")}>{r.passed}/{r.of}{r.trend ? " ★" : ""}</td>
                        <td className={td}>{r.phase ? `${r.phase} ${r.phase_candles}` : "—"}</td>
                        <td className={clsx(td, "text-right")}>{r.price.toFixed(2)}</td>
                        <td className={clsx(td, "text-right")}>{r.entry.toFixed(2)}</td>
                        <td className={clsx(td, "text-right")}>{r.exit.toFixed(2)}</td>
                        <td className={clsx(td, "text-right", tone(r.pct))}>{pc(r.pct)}</td>
                        <td className={clsx(td, "text-right", tone(r.pnl))}>{rs(r.pnl)}</td>
                        <td className={clsx(td, "text-right", tone(r.net))}>{rs(r.net)}</td>
                        <td className={clsx(td, "text-right", tone(r.best_pct))}>{pc(r.best_pct)}</td>
                        <td className={td}>{r.best_at}</td>
                        <td className={clsx(td, "text-right", tone(r.worst_pct))}>{pc(r.worst_pct)}</td>
                        <td className={td}>{r.worst_at}</td>
                        <td className={clsx(td, r.held_to_end ? "text-emerald-300" : "text-amber-300")}>{r.held_to_end ? `✓ ${pSquare}` : r.lost_at}</td>
                        <td className={clsx(td, "text-right", tone(r.lost_pnl))}>{r.lost_pnl == null ? "—" : rs(r.lost_pnl)}</td>
                        {onArm ? (
                          <td className="px-1">
                            <button type="button" onClick={(e) => { e.stopPropagation(); onArm(r.symbol); }} className="rounded px-2 py-0.5 text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10">
                              Arm
                            </button>
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
