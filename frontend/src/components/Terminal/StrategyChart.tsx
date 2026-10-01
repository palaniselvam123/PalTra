"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { History, Loader2, Radio } from "lucide-react";
import clsx from "clsx";
import { ColorType, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi } from "lightweight-charts";
import { parseClock } from "@/lib/format";
import { Skeleton } from "./ui";
import { inr, px, smaApi, type Candle, type ChartPayload, type SmaState, type TradeRow } from "@/lib/smaApi";

type Props = {
  chart: ChartPayload | null;
  state: SmaState | null;
  /** Trade rows already loaded for the blotter. Used only to mark exits. */
  trades?: TradeRow[];
  closing: boolean;
  onClose: () => void;
};

const SESSION_OPEN_MIN = 9 * 60 + 15;
const SESSION_CLOSE_MIN = 15 * 60 + 30;

function istMinutes(sec: number): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(sec * 1000));
  const hh = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const mm = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return hh * 60 + mm;
}

/** Drop bars outside 09:15–15:30 IST that carry no SMA. They are the practice
tape after the close and only squash the real session on screen. */
function sessionCandles(rows: Candle[]): Candle[] {
  return rows.filter((c) => {
    if (c.sma9 != null || c.sma21 != null) return true;
    const m = istMinutes(c.time);
    return m >= SESSION_OPEN_MIN && m <= SESSION_CLOSE_MIN;
  });
}

type Marker = {
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
};

function compactPrice(value: number): string {
  return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

/** Entry markers from the API plus exit markers from closed trades on this stock. */
function chartMarkers(chart: ChartPayload, rows: Candle[], trades: TradeRow[], symbol: string): Marker[] {
  if (rows.length === 0) return [];
  const first = rows[0].time;
  const last = rows[rows.length - 1].time;
  const times = new Set(rows.map((c) => c.time));
  const snap = (sec: number) => sec - (sec % 60);
  const out: Marker[] = chart.markers
    .filter((m) => times.has(snap(m.time)) || (m.time >= first && m.time <= last))
    .map((m): Marker => {
      if (m.kind === "EXIT") {
        const net = m.net_pnl;
        return {
          time: snap(m.time),
          position: m.direction === "LONG" ? "aboveBar" : "belowBar",
          color: net == null ? "#CBD5E1" : net >= 0 ? "#34D399" : "#FB7185",
          shape: "circle",
          text: `EXIT ${compactPrice(m.price)}`,
        };
      }
      return {
        time: snap(m.time),
        position: m.direction === "LONG" ? "belowBar" : "aboveBar",
        color: m.direction === "LONG" ? "#34D399" : "#FB7185",
        shape: m.direction === "LONG" ? "arrowUp" : "arrowDown",
        text: `${m.direction === "LONG" ? "BUY" : "SELL"} ${compactPrice(m.price)}`,
      };
    });
  for (const t of trades) {
    if (t.symbol.toUpperCase() !== symbol || t.exit_price == null || !t.exit_time) continue;
    const when = parseClock(t.exit_time);
    if (!when) continue;
    const sec = snap(Math.floor(when.getTime() / 1000));
    if (sec < first || sec > last) continue;
    const net = t.net_pnl ?? t.gross_pnl;
    out.push({
      time: sec,
      position: t.direction === "LONG" ? "aboveBar" : "belowBar",
      color: net == null ? "#CBD5E1" : net >= 0 ? "#34D399" : "#FB7185",
      shape: "circle",
      text: `EXIT ${compactPrice(t.exit_price)}`,
    });
  }
  return out.sort((a, b) => a.time - b.time);
}

type SmaHover = { sma9: number | null; sma21: number | null };

function latestSma(chart: ChartPayload | null): SmaHover {
  const rows = chart?.candles ?? [];
  let sma9: number | null = null;
  let sma21: number | null = null;
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    if (sma9 == null && rows[i].sma9 != null) sma9 = rows[i].sma9;
    if (sma21 == null && rows[i].sma21 != null) sma21 = rows[i].sma21;
    if (sma9 != null && sma21 != null) break;
  }
  return { sma9, sma21 };
}

function istClock(time: unknown): string {
  const sec = typeof time === "number" ? time : 0;
  if (!sec) return "";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(sec * 1000));
}

function istStamp(time: unknown): string {
  const sec = typeof time === "number" ? time : 0;
  if (!sec) return "";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(sec * 1000));
}

function istDay(time: unknown): string {
  const sec = typeof time === "number" ? time : 0;
  if (!sec) return "";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" }).format(
    new Date(sec * 1000)
  );
}

/** IST wall clock as the value a datetime-local input takes: YYYY-MM-DDTHH:MM. */
function istInput(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

/** From = 09:15 IST `daysBack` calendar days ago, To = now. */
function presetRange(daysBack: number): { from: string; to: string } {
  const now = new Date();
  const day = istInput(new Date(now.getTime() - daysBack * 86_400_000)).slice(0, 10);
  return { from: `${day}T09:15`, to: istInput(now) };
}

const MAX_RANGE_DAYS = 30;

function rangeProblem(from: string, to: string): string | null {
  if (!from || !to) return "Pick both From and To.";
  const a = Date.parse(`${from}:00+05:30`);
  const b = Date.parse(`${to}:00+05:30`);
  if (Number.isNaN(a) || Number.isNaN(b)) return "Pick both From and To.";
  if (a >= b) return "From must be before To.";
  if (b - a > MAX_RANGE_DAYS * 86_400_000) return `Pick ${MAX_RANGE_DAYS} days or less.`;
  return null;
}

function rangeLabel(from: string, to: string): string {
  const show = (v: string) => istStamp(Math.floor(Date.parse(`${v.replace(" ", "T")}:00+05:30`) / 1000));
  return `${show(from)} → ${show(to)} IST`;
}

function lineValue(row: unknown): number | null {
  if (row && typeof row === "object" && "value" in row && typeof row.value === "number") return row.value;
  return null;
}

function entryLook(gross: number | null): { title: string; color: string } {
  if (gross == null) return { title: "Entry", color: "#94a3b8" };
  return {
    title: `P&L ${gross >= 0 ? "+" : ""}${inr(gross)}`,
    color: gross >= 0 ? "#10B981" : "#F43F5E",
  };
}

function livePnl(state: SmaState | null): { gross: number; pct: number; points: number } | null {
  const pos = state?.position;
  if (!pos || !state || state.ltp <= 0 || pos.entry_price <= 0) return null;
  const points = pos.direction === "LONG" ? state.ltp - pos.entry_price : pos.entry_price - state.ltp;
  return {
    points,
    gross: points * pos.qty,
    pct: (points / pos.entry_price) * 100,
  };
}

export function StrategyChart({ chart, state, trades = [], closing, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const smaFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const smaSlowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const atrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slLine = useRef<IPriceLine | null>(null);
  const entryLine = useRef<IPriceLine | null>(null);
  const pnlGrossRef = useRef<number | null>(null);
  const stopOn = state?.position ? state.position.stop_active !== false : state?.stop_enabled !== false;
  const stopOnRef = useRef(stopOn);
  stopOnRef.current = stopOn;
  const [hover, setHover] = useState<SmaHover | null>(null);
  const symbol = (state?.symbol ?? "").toUpperCase();
  const [range, setRange] = useState(() => presetRange(0));
  const [past, setPast] = useState<(ChartPayload & { symbol: string; from: string; to: string }) | null>(null);
  const [loadingPast, setLoadingPast] = useState(false);
  const [pastError, setPastError] = useState<string | null>(null);
  const pastSeq = useRef(0);
  // A different chart stock leaves the past view; its candles belong to the old stock.
  useEffect(() => {
    pastSeq.current += 1;
    setPast(null);
    setPastError(null);
    setLoadingPast(false);
  }, [symbol]);
  const view = past ?? chart;

  const loadPast = (next: { from: string; to: string }) => {
    setRange(next);
    const problem = rangeProblem(next.from, next.to);
    if (problem) {
      setPastError(problem);
      return;
    }
    if (!symbol) {
      setPastError("Pick a stock first.");
      return;
    }
    const seq = ++pastSeq.current;
    setLoadingPast(true);
    setPastError(null);
    smaApi
      .history(symbol, next.from, next.to)
      .then((payload) => {
        if (seq !== pastSeq.current) return;
        if (payload.candles.length === 0) {
          setPastError("Groww has no 1-minute candles in that range. Markets may have been closed.");
          return;
        }
        setPast(payload);
      })
      .catch((err: unknown) => {
        if (seq !== pastSeq.current) return;
        setPastError(err instanceof Error ? err.message : "Past candles did not load.");
      })
      .finally(() => {
        if (seq === pastSeq.current) setLoadingPast(false);
      });
  };
  const backToLive = () => {
    pastSeq.current += 1;
    setPast(null);
    setPastError(null);
    setLoadingPast(false);
  };

  useEffect(() => {
    if (!rootRef.current) return;
    const instance = createChart(rootRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#151921" },
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: { color: "#1c2230" },
        horzLines: { color: "#1c2230" },
      },
      rightPriceScale: { borderColor: "#1c2230" },
      timeScale: {
        borderColor: "#1c2230",
        timeVisible: true,
        secondsVisible: false,
        // 0 year, 1 month, 2 day: a new day starts here, so show the date.
        tickMarkFormatter: (time: unknown, kind: number) => (kind <= 2 ? istDay(time) : istClock(time)),
      },
      localization: {
        timeFormatter: (time: unknown) => {
          const stamp = istStamp(time);
          return stamp ? `${stamp} IST` : "";
        },
      },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    const candles = instance.addCandlestickSeries({
      upColor: "#10B981",
      downColor: "#F43F5E",
      borderUpColor: "#10B981",
      borderDownColor: "#F43F5E",
      wickUpColor: "#10B981",
      wickDownColor: "#F43F5E",
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.28 } });
    const fast = instance.addLineSeries({
      color: "#F43F5E",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 9",
    });
    const slow = instance.addLineSeries({
      color: "#3B82F6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 21",
    });
    const atr = instance.addLineSeries({
      color: "#A78BFA",
      lineWidth: 2,
      priceScaleId: "atr",
      priceLineVisible: false,
      lastValueVisible: true,
      title: "ATR 14",
    });
    instance.priceScale("atr").applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });

    apiRef.current = instance;
    candleRef.current = candles;
    smaFastRef.current = fast;
    smaSlowRef.current = slow;
    atrRef.current = atr;

    const onCrosshair = (param: { time?: unknown; seriesData: Map<unknown, unknown> }) => {
      if (param.time == null) {
        setHover(null);
        return;
      }
      setHover({
        sma9: lineValue(param.seriesData.get(fast)),
        sma21: lineValue(param.seriesData.get(slow)),
      });
    };
    instance.subscribeCrosshairMove(onCrosshair);

    const observer = new ResizeObserver(() => {
      if (rootRef.current) instance.applyOptions({ width: rootRef.current.clientWidth, height: rootRef.current.clientHeight });
    });
    observer.observe(rootRef.current);
    return () => {
      instance.unsubscribeCrosshairMove(onCrosshair);
      observer.disconnect();
      instance.remove();
      apiRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = view;
    if (!chart || !candleRef.current || !smaFastRef.current || !smaSlowRef.current || !atrRef.current) return;
    const rows = sessionCandles(chart.candles);
    candleRef.current.setData(
      rows.map((c) => ({ time: c.time as never, open: c.open, high: c.high, low: c.low, close: c.close }))
    );
    smaFastRef.current.setData(
      rows.filter((c) => c.sma9 != null).map((c) => ({ time: c.time as never, value: c.sma9 as number }))
    );
    smaSlowRef.current.setData(
      rows.filter((c) => c.sma21 != null).map((c) => ({ time: c.time as never, value: c.sma21 as number }))
    );
    atrRef.current.setData(
      rows.filter((c) => c.atr14 != null).map((c) => ({ time: c.time as never, value: c.atr14 as number }))
    );
    // Past candles carry their own exit markers; the blotter only covers recent trades.
    candleRef.current.setMarkers(
      chartMarkers(chart, rows, past ? [] : trades, symbol).map((m) => ({ ...m, time: m.time as never }))
    );

    if (slLine.current) {
      candleRef.current.removePriceLine(slLine.current);
      slLine.current = null;
    }
    if (entryLine.current) {
      candleRef.current.removePriceLine(entryLine.current);
      entryLine.current = null;
    }
    // The stop line is drawn only when this position really has a stop.
    if (!past && chart.sl_trigger && stopOnRef.current) {
      slLine.current = candleRef.current.createPriceLine({
        price: chart.sl_trigger,
        color: "#F59E0B",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: `${chart.atr_multiplier ?? 1.5}× ATR SL`,
      });
    }
    if (!past && chart.entry_price) {
      const look = entryLook(pnlGrossRef.current);
      entryLine.current = candleRef.current.createPriceLine({
        price: chart.entry_price,
        color: look.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: look.title,
      });
    }
  }, [view, past, trades, symbol, stopOn]);

  // A new past range opens fitted to the screen; going back to live jumps to the latest bar.
  useEffect(() => {
    const api = apiRef.current;
    if (!api) return;
    if (past) api.timeScale().fitContent();
    else api.timeScale().scrollToRealTime();
  }, [past]);

  const pnl = livePnl(state);
  const pos = state?.position;
  const pnlGross = pnl?.gross ?? null;
  pnlGrossRef.current = pnlGross;
  useEffect(() => {
    if (!entryLine.current) return;
    entryLine.current.applyOptions(entryLook(pnlGross));
  }, [pnlGross]);

  const latest = latestSma(view);
  const sma9 = hover ? hover.sma9 : latest.sma9;
  const sma21 = hover ? hover.sma21 : latest.sma21;

  return (
    <section className="min-w-0 max-w-full overflow-hidden rounded-xl border border-white/5 bg-[#151921]">
      <div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-4">
        <h2 className="flex flex-wrap items-center gap-x-2 text-sm font-semibold text-slate-100">
          {state?.symbol ? <span className="text-amber-300">{state.symbol}</span> : null}
          <span className="font-normal text-slate-300">1-minute</span>
          {past ? (
            <span className="rounded-md bg-violet-500/15 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-violet-200 ring-1 ring-inset ring-violet-400/35">
              Past
            </span>
          ) : null}
        </h2>
        <ul aria-label="Chart legend" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-300">
          <LegendItem swatch={<span className="block h-0.5 w-5 rounded bg-[#F43F5E]" />}>
            SMA 9 <span className="font-mono text-slate-100">{px(sma9)}</span>
          </LegendItem>
          <LegendItem swatch={<span className="block h-0.5 w-5 rounded bg-[#3B82F6]" />}>
            SMA 21 <span className="font-mono text-slate-100">{px(sma21)}</span>
          </LegendItem>
          {past ? null : (
            <LegendItem swatch={<span className="block w-5 border-t-2 border-dashed border-amber-400" />}>
              {stopOn ? `${chart?.atr_multiplier ?? state?.atr_multiplier ?? 1.5}× ATR stop` : <span className="font-semibold text-amber-300">Stop OFF</span>}
            </LegendItem>
          )}
          <LegendItem swatch={<span className="block h-0.5 w-5 rounded bg-[#A78BFA]" />}>ATR 14</LegendItem>
          <LegendItem swatch={<span className="text-emerald-400">▲</span>}>Buy</LegendItem>
          <LegendItem swatch={<span className="text-rose-400">▼</span>}>Sell</LegendItem>
          <LegendItem swatch={<span className="text-slate-300">●</span>}>Exit</LegendItem>
        </ul>
      </div>
      <RangeBar
        range={range}
        past={past}
        loading={loadingPast}
        error={pastError}
        onChange={setRange}
        onLoad={loadPast}
        onLive={backToLive}
      />
      <div className="relative">
        <div ref={rootRef} className="h-[320px] w-full sm:h-[460px] lg:h-[520px]" />
        {!view && (
          <div aria-busy="true" aria-label="Loading chart" className="absolute inset-0 z-[5] flex flex-col justify-end gap-2 bg-[#151921] p-4">
            <Skeleton className="h-2/3 w-full opacity-60" />
            <Skeleton className="h-1/6 w-full opacity-40" />
          </div>
        )}
        {pos && pnl && !past && (
          <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[240px] rounded-lg border border-white/10 bg-[#0B0E14]/90 p-2.5 shadow-lg">
            <div className="text-[11px] uppercase tracking-wider text-slate-400">
              {pos.direction} {pos.qty.toLocaleString("en-IN")} · entry {px(pos.entry_price)}
            </div>
            <div className={`mt-1 font-mono text-lg font-semibold ${pnl.gross >= 0 ? "text-[#10B981]" : "text-[#F43F5E]"}`}>
              {pnl.gross >= 0 ? "+" : ""}
              {inr(pnl.gross)}
            </div>
            <div className="font-mono text-[11px] text-slate-400">
              {pnl.points >= 0 ? "+" : ""}
              {pnl.points.toFixed(2)} pts · {pnl.pct >= 0 ? "+" : ""}
              {pnl.pct.toFixed(2)}%
            </div>
            <button
              type="button"
              disabled={closing}
              onClick={onClose}
              className="pointer-events-auto mt-2 min-h-11 w-full rounded-md border border-rose-400/50 bg-rose-500/15 px-2 text-sm font-semibold text-rose-200 hover:bg-rose-500/25 disabled:opacity-50 sm:min-h-9"
            >
              {closing ? "Closing…" : "Close position"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

const PRESETS: { label: string; days: number }[] = [
  { label: "Today", days: 0 },
  { label: "5 days", days: 4 },
  { label: "30 days", days: 29 },
];

function RangeBar({
  range,
  past,
  loading,
  error,
  onChange,
  onLoad,
  onLive,
}: {
  range: { from: string; to: string };
  past: { from: string; to: string; candles: Candle[] } | null;
  loading: boolean;
  error: string | null;
  onChange: (next: { from: string; to: string }) => void;
  onLoad: (next: { from: string; to: string }) => void;
  onLive: () => void;
}) {
  const max = istInput(new Date());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onLoad(range);
  };
  return (
    <div className="border-t border-white/5 px-3 py-2 sm:px-4">
      <form onSubmit={submit} aria-label="Past candles" className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 basis-full flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400 sm:basis-auto">
          From (IST)
          <input
            type="datetime-local"
            value={range.from}
            max={max}
            onChange={(e) => onChange({ ...range, from: e.target.value })}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm normal-case tracking-normal text-slate-100 [color-scheme:dark]"
          />
        </label>
        <label className="flex min-w-0 basis-full flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wider text-slate-400 sm:basis-auto">
          To (IST)
          <input
            type="datetime-local"
            value={range.to}
            max={max}
            onChange={(e) => onChange({ ...range, to: e.target.value })}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm normal-case tracking-normal text-slate-100 [color-scheme:dark]"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="flex min-h-9 items-center gap-1.5 rounded-md bg-violet-500/20 px-3 text-sm font-semibold text-violet-100 ring-1 ring-inset ring-violet-400/40 hover:bg-violet-500/30 disabled:opacity-50"
        >
          {loading ? <Loader2 size={14} aria-hidden className="animate-spin" /> : <History size={14} aria-hidden />}
          {loading ? "Loading…" : "Show candles"}
        </button>
        <div className="flex items-center gap-1" role="group" aria-label="Quick ranges">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              disabled={loading}
              onClick={() => onLoad(presetRange(p.days))}
              className="min-h-9 rounded-md px-2 text-xs font-semibold text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
        </div>
        {past ? (
          <button
            type="button"
            onClick={onLive}
            className="flex min-h-9 items-center gap-1.5 rounded-md px-3 text-sm font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10 sm:ml-auto"
          >
            <Radio size={14} aria-hidden />
            Back to live
          </button>
        ) : null}
      </form>
      {past ? (
        <p className="mt-1.5 text-xs text-slate-400">
          {past.candles.length.toLocaleString("en-IN")} candles from Groww · {rangeLabel(past.from, past.to)}. Live
          updates are paused on the chart until you go back to live.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className={clsx("mt-1.5 text-xs text-rose-300")}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function LegendItem({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden className="flex w-5 items-center justify-center">
        {swatch}
      </span>
      {children}
    </li>
  );
}
