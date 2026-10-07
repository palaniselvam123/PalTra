"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { BarChartHorizontal, ChevronDown, ChevronUp, Eye, EyeOff, GripHorizontal, History, Loader2, Maximize2, Minimize2, Pin, PinOff, Radio, Ruler, Table2, X } from "lucide-react";
import clsx from "clsx";
import {
  ColorType,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesPrimitive,
  type ISeriesPrimitivePaneView,
  type SeriesAttachedParameter,
  type Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import { parseClock } from "@/lib/format";
import { VALUE_AREA_SHARE, volumeProfile, type VolumeProfile } from "@/lib/volumeProfile";
import { Skeleton } from "./ui";
import { ChartDataTable } from "./ChartDataTable";
import { inr, px, smaApi, type Candle, type ChartPayload, type SmaState, type TradeRow } from "@/lib/smaApi";

type Props = {
  chart: ChartPayload | null;
  state: SmaState | null;
  /** Trade rows already loaded for the blotter. Used only to mark exits. */
  trades?: TradeRow[];
  /** Every loaded trade (all books); a past day picks its own from these for the high/low lines. */
  allTrades?: TradeRow[];
  closing: boolean;
  /** Closes the chart stock's position. Left out on a read-only chart (no Close button). */
  onClose?: () => void;
  /** Asks the page for this many 1-minute bars on the live chart (bigger candles need more). */
  onLiveBars?: (count: number) => void;
  /**
   * Set by the page when a replay starts or ends. `date` holds the chart on that
   * past day (with `runId`'s trades) until the user changes it; null goes live.
   * A new `seq` applies it again.
   */
  pin?: { seq: number; date: string | null; runId: number | null; symbol: string | null } | null;
  /** Told when "Hold view" turns on or off, with the stock on screen, so the page can keep that stock. */
  onHoldChange?: (held: boolean, symbol: string) => void;
};

export const BAR_MINUTES = [1, 5, 15, 30, 60] as const;
export type BarMinutes = (typeof BAR_MINUTES)[number];
const BAR_KEY = "sma.chart.interval";

/** 1-minute bars the live chart needs so SMA 21 is formed on the bigger candles.
 * 1-minute asks for a whole session (375 minutes) so the volume profile covers the day. */
export function liveBarsFor(bar: number): number {
  return bar <= 1 ? 400 : Math.min(2500, bar * 120);
}

function barLabel(bar: number): string {
  return bar === 60 ? "1h" : `${bar}m`;
}

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

/** Start of the `bar`-minute candle holding `sec`, counted from the 09:15 IST open. */
function bucketStart(sec: number, bar: number): number {
  const minute = sec - (sec % 60);
  if (bar <= 1) return minute;
  const ist = Math.floor(((minute + 19_800) % 86_400) / 60);
  const offset = (((ist - SESSION_OPEN_MIN) % bar) + bar) % bar;
  return minute - offset * 60;
}

function resampleCandles(rows: Candle[], bar: number): Candle[] {
  const out: Candle[] = [];
  for (const c of rows) {
    const t = bucketStart(c.time, bar);
    const last = out[out.length - 1];
    if (last && last.time === t) {
      last.high = Math.max(last.high, c.high);
      last.low = Math.min(last.low, c.low);
      last.close = c.close;
      if (c.volume != null) last.volume = (last.volume ?? 0) + c.volume;
      // The filters read 1-minute values; the bar shows them as of its last minute.
      if (c.vwap != null) last.vwap = c.vwap;
      if (c.rsi14 != null) last.rsi14 = c.rsi14;
    } else {
      out.push({
        time: t,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        sma9: null,
        sma21: null,
        atr14: null,
        vwap: c.vwap ?? null,
        rsi14: c.rsi14 ?? null,
        volume: c.volume ?? null,
      });
    }
  }
  return out;
}

/** SMA 9, SMA 21 and Wilder ATR 14, the same maths as the backend's enrich(). */
function withIndicators(rows: Candle[], formingLast: boolean): Candle[] {
  const sma = (i: number, n: number) => {
    if (i + 1 < n) return null;
    let sum = 0;
    for (let k = i - n + 1; k <= i; k += 1) sum += rows[k].close;
    return sum / n;
  };
  let atr: number | null = null;
  const out = rows.map((c, i) => {
    const prev = i > 0 ? rows[i - 1].close : null;
    const tr = prev == null ? c.high - c.low : Math.max(c.high - c.low, Math.abs(c.high - prev), Math.abs(c.low - prev));
    atr = atr == null ? tr : atr + (tr - atr) / 14;
    return { ...c, sma9: sma(i, 9), sma21: sma(i, 21), atr14: i >= 13 ? atr : null };
  });
  // The forming candle's lines would repaint, so they stop on the last closed one.
  if (formingLast && out.length) {
    const last = out[out.length - 1];
    out[out.length - 1] = { ...last, sma9: null, sma21: null, atr14: null, vwap: null, rsi14: null };
  }
  return out;
}

type Ohlc = { time: number; open: number; high: number; low: number; close: number; prevClose: number | null };

function ohlcAt(rows: Candle[], index: number): Ohlc | null {
  const c = rows[index];
  if (!c) return null;
  return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, prevClose: index > 0 ? rows[index - 1].close : null };
}

/** One end of a measurement: a candle time and a price on it. */
type MeasurePoint = { time: number; price: number };
type Measure = { a: MeasurePoint | null; b: MeasurePoint | null };
const NO_MEASURE: Measure = { a: null, b: null };

function spanText(seconds: number): string {
  const total = Math.round(Math.abs(seconds) / 60);
  const d = Math.floor(total / 1440);
  const h = Math.floor((total % 1440) / 60);
  const m = total % 60;
  return [d ? `${d}d` : "", h ? `${h}h` : "", `${m}m`].filter(Boolean).join(" ");
}

/** One order on the chart: a small box at the bar and fill price. */
type TradeBox = {
  time: number;
  price: number;
  kind: "ENTRY" | "EXIT";
  direction: string;
  text: string;
  color: string;
};

/** Green for a trade that made money, red for a loss, grey while it is open or unknown. */
const BOX_OPEN_COLOR = "#64748B";

function resultColor(net: number | null | undefined): string {
  return net == null ? BOX_OPEN_COLOR : net >= 0 ? "#059669" : "#E11D48";
}

/** The closed trade a marker belongs to: same stock, side, fill price and bar. */
function entryResult(
  m: { time: number; direction: string; price: number; net_pnl?: number | null },
  lookup: TradeRow[],
  symbol: string,
  snap: (sec: number) => number
): number | null {
  if (m.net_pnl != null) return m.net_pnl;
  const bar = snap(m.time);
  for (const t of lookup) {
    if (t.exit_price == null || t.symbol.toUpperCase() !== symbol || t.direction !== m.direction) continue;
    if (Math.abs(t.entry_price - m.price) > 1e-6) continue;
    const when = t.entry_time ? parseClock(t.entry_time) : null;
    if (!when || snap(Math.floor(when.getTime() / 1000)) !== bar) continue;
    return t.net_pnl ?? t.gross_pnl ?? null;
  }
  return null;
}

/** The number part of a trade id ("P-12" -> "12", "R7-3" -> "3"). */
function refNumber(ref: string | null | undefined): string {
  if (!ref) return "";
  const dash = ref.lastIndexOf("-");
  return dash >= 0 ? ref.slice(dash + 1) : ref.replace(/^#/, "");
}

/** Entry and exit boxes: "B 12" / "S 12" where the order filled, "X 12" where it closed,
 * white text on green (profit), red (loss) or grey (still open). */
class TradeBoxes implements ISeriesPrimitive<Time> {
  private boxes: TradeBox[] = [];
  private host: SeriesAttachedParameter<Time> | null = null;
  private readonly views: ISeriesPrimitivePaneView[];

  constructor() {
    this.views = [{ zOrder: () => "top", renderer: () => ({ draw: (target) => this.draw(target) }) }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.host = param;
  }

  detached(): void {
    this.host = null;
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return this.views;
  }

  set(boxes: TradeBox[]): void {
    this.boxes = boxes;
    this.host?.requestUpdate();
  }

  private draw(target: CanvasRenderingTarget2D): void {
    const host = this.host;
    if (!host || this.boxes.length === 0) return;
    const scale = host.chart.timeScale();
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      const h = 14;
      const gap = 7;
      for (const b of this.boxes) {
        const x = scale.timeToCoordinate(b.time as Time);
        const y = host.series.priceToCoordinate(b.price);
        if (x == null || y == null || x < -40 || x > mediaSize.width + 40) continue;
        const w = Math.ceil(ctx.measureText(b.text).width) + 8;
        // A buy entry sits under its fill and a sell entry above it; the exit goes the other way.
        const below = (b.direction === "LONG") === (b.kind === "ENTRY");
        const top = below ? y + gap : y - gap - h;
        ctx.strokeStyle = b.color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x, below ? top : top + h);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, 2, 0, Math.PI * 2);
        ctx.fillStyle = b.color;
        ctx.fill();
        ctx.beginPath();
        ctx.roundRect(x - w / 2, top, w, h, 3);
        ctx.fill();
        ctx.fillStyle = "#FFFFFF";
        ctx.fillText(b.text, x, top + h / 2 + 0.5);
      }
    });
  }
}

const VWAP_COLOR = "#22D3EE";
const RSI_COLOR = "#F472B6";


const BB_COLOR = "#C4B5FD";

/** Bollinger Bands on the candles as drawn (population SD, like the bot's
 * filter). The forming candle is left out on the live chart so the bands do
 * not repaint. */
function bollingerRows(
  rows: Candle[],
  period: number,
  k: number,
  formingLast: boolean
): { time: number; upper: number; mid: number; lower: number }[] {
  const closed = formingLast ? rows.slice(0, -1) : rows;
  const out: { time: number; upper: number; mid: number; lower: number }[] = [];
  const n = Math.max(2, Math.round(period));
  for (let i = n - 1; i < closed.length; i += 1) {
    let sum = 0;
    for (let j = i - n + 1; j <= i; j += 1) sum += closed[j].close;
    const mid = sum / n;
    let sq = 0;
    for (let j = i - n + 1; j <= i; j += 1) sq += (closed[j].close - mid) ** 2;
    const sd = Math.sqrt(sq / n);
    out.push({ time: closed[i].time, mid, upper: mid + k * sd, lower: mid - k * sd });
  }
  return out;
}

const PROFILE_KEY = "sma.chart.profile";
const CHART_HIDDEN_KEY = "sma.chart.hidden";
const TABLE_KEY = "sma.chart.table";
const NO_MARKERS: ChartPayload["markers"] = [];
const BOX_SMALL_KEY = "sma.chart.posbox.small";
const BOX_POS_KEY = "sma.chart.posbox.pos";
const POC_COLOR = "#FACC15";
const VALUE_AREA_COLOR = "#38BDF8";

/** Today's volume profile: a sideways histogram on the right of the price
 * pane, drawn under the candles. Value-area rows are blue, the POC row yellow. */
class VolumeProfileBars implements ISeriesPrimitive<Time> {
  private profile: VolumeProfile | null = null;
  private host: SeriesAttachedParameter<Time> | null = null;
  private readonly views: ISeriesPrimitivePaneView[];

  constructor() {
    this.views = [{ zOrder: () => "bottom", renderer: () => ({ draw: (target) => this.draw(target) }) }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.host = param;
  }

  detached(): void {
    this.host = null;
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return this.views;
  }

  set(profile: VolumeProfile | null): void {
    this.profile = profile;
    this.host?.requestUpdate();
  }

  private draw(target: CanvasRenderingTarget2D): void {
    const host = this.host;
    const profile = this.profile;
    if (!host || !profile || profile.rows.length === 0) return;
    const max = Math.max(...profile.rows.map((r) => r.volume));
    if (max <= 0) return;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      // At most a quarter of the pane, so the latest candles stay readable.
      const room = Math.min(bitmapSize.width * 0.25, 220 * hr);
      const right = bitmapSize.width;
      for (const row of profile.rows) {
        if (row.volume <= 0) continue;
        const yTop = host.series.priceToCoordinate(row.high);
        const yBottom = host.series.priceToCoordinate(row.low);
        if (yTop == null || yBottom == null) continue;
        const top = Math.round(Math.min(yTop, yBottom) * vr);
        const height = Math.max(1, Math.round(Math.abs(yBottom - yTop) * vr) - Math.round(vr));
        const width = Math.max(1, Math.round((row.volume / max) * room));
        const isPoc = profile.poc >= row.low && profile.poc <= row.high;
        const inValue = row.low >= profile.val - 1e-9 && row.high <= profile.vah + 1e-9;
        ctx.fillStyle = isPoc
          ? "rgba(250, 204, 21, 0.5)"
          : inValue
            ? "rgba(56, 189, 248, 0.28)"
            : "rgba(148, 163, 184, 0.16)";
        ctx.fillRect(right - width, top, width, height);
      }
    });
  }
}

function tradeBoxes(
  chart: ChartPayload,
  rows: Candle[],
  symbol: string,
  bar = 1,
  lookup: TradeRow[] = []
): TradeBox[] {
  if (rows.length === 0) return [];
  const first = rows[0].time;
  const last = rows[rows.length - 1].time;
  const times = new Set(rows.map((c) => c.time));
  const snap = (sec: number) => bucketStart(sec, bar);
  const out: TradeBox[] = chart.markers
    .filter((m) => m.kind === "ENTRY" || m.kind === "EXIT")
    .filter((m) => times.has(snap(m.time)) || (m.time >= first && m.time < last + bar * 60))
    .map((m): TradeBox => {
      const n = refNumber(m.trade_ref);
      const entry = m.kind === "ENTRY";
      const net = m.open ? null : entryResult(m, lookup, symbol, snap);
      const letter = entry ? (m.direction === "LONG" ? "B" : "S") : "X";
      return {
        time: snap(m.time),
        price: m.price,
        kind: entry ? "ENTRY" : "EXIT",
        direction: m.direction,
        text: n ? `${letter} ${n}` : letter,
        color: resultColor(net),
      };
    });
  return out.sort((a, b) => a.time - b.time);
}

/** Chart canvas colours per page theme (the canvas cannot read CSS). */
const CHART_LOOK = {
  dark: { background: "#151921", text: "#94a3b8", grid: "#1c2230" },
  light: { background: "#ffffff", text: "#475569", grid: "#e5eaf1" },
} as const;

function pageTheme(): "dark" | "light" {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** The page theme, following the switch on <html data-theme> as it changes. */
function usePageTheme(): "dark" | "light" {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    setTheme(pageTheme());
    const watch = new MutationObserver(() => setTheme(pageTheme()));
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => watch.disconnect();
  }, []);
  return theme;
}

type SmaHover = { sma9: number | null; sma21: number | null };

function latestSma(rows: Candle[]): SmaHover {
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
  // The line stays (colour = open P&L sign); its text is left off the chart.
  if (gross == null) return { title: "", color: "#94a3b8" };
  return {
    title: "",
    color: gross >= 0 ? "#10B981" : "#F43F5E",
  };
}

/** ₹ the open position makes (+) or loses (−) if it closes at `level`. */
function pnlAt(direction: "LONG" | "SHORT", entry: number, qty: number, level: number): number {
  return (direction === "LONG" ? level - entry : entry - level) * qty;
}

function signedInr(value: number): string {
  return `${value >= 0 ? "+" : "−"}${inr(Math.abs(value))}`;
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

export function StrategyChart({ chart, state, trades = [], allTrades, closing, onClose, onLiveBars, pin, onHoldChange }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const profileBarsRef = useRef<VolumeProfileBars | null>(null);
  const tradeBoxesRef = useRef<TradeBoxes | null>(null);
  const theme = usePageTheme();
  useEffect(() => {
    const look = CHART_LOOK[theme];
    apiRef.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: look.background }, textColor: look.text },
      grid: { vertLines: { color: look.grid }, horzLines: { color: look.grid } },
      rightPriceScale: { borderColor: look.grid },
      timeScale: { borderColor: look.grid },
    });
  }, [theme]);
  const profileLines = useRef<IPriceLine[]>([]);
  const smaFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const smaSlowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const atrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbRefs = useRef<ISeriesApi<"Line">[]>([]);
  const rsiBands = useRef<IPriceLine[]>([]);
  const slLine = useRef<IPriceLine | null>(null);
  const entryLine = useRef<IPriceLine | null>(null);
  const targetLine = useRef<IPriceLine | null>(null);
  const pnlGrossRef = useRef<number | null>(null);
  // Entry, stop and target of the open position, kept in view by autoscale.
  const levelsRef = useRef<number[]>([]);
  const stopOn = state?.position ? state.position.stop_active !== false : state?.stop_enabled !== false;
  const [hover, setHover] = useState<SmaHover | null>(null);
  const [hoverOhlc, setHoverOhlc] = useState<Ohlc | null>(null);
  const rowsRef = useRef<Candle[]>([]);
  const sectionRef = useRef<HTMLElement>(null);
  // Hide the chart (header stays) and the open-position box's place and size.
  const [chartHidden, setChartHidden] = useState(false);
  // The candles as a table under the chart (sort, filter, columns, CSV / PDF).
  const [showTable, setShowTable] = useState(false);
  const [boxSmall, setBoxSmall] = useState(false);
  const [boxPos, setBoxPos] = useState<{ x: number; y: number } | null>(null);
  const boxDrag = useRef<{ dx: number; dy: number } | null>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try {
      if (localStorage.getItem(CHART_HIDDEN_KEY) === "1") setChartHidden(true);
      if (localStorage.getItem(TABLE_KEY) === "1") setShowTable(true);
      const small = localStorage.getItem(BOX_SMALL_KEY);
      // Phones start with the compact box so it does not cover the candles.
      setBoxSmall(small == null ? window.matchMedia("(max-width: 639px)").matches : small === "1");
      const saved = JSON.parse(localStorage.getItem(BOX_POS_KEY) || "null");
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) setBoxPos(saved);
    } catch {
      /* private mode */
    }
  }, []);
  const remember = (key: string, value: string) => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* private mode */
    }
  };
  const toggleChart = () => {
    setChartHidden((on) => {
      remember(CHART_HIDDEN_KEY, on ? "0" : "1");
      return !on;
    });
  };
  const toggleTable = () => {
    setShowTable((on) => {
      remember(TABLE_KEY, on ? "0" : "1");
      return !on;
    });
  };
  const toggleBox = () => {
    setBoxSmall((on) => {
      remember(BOX_SMALL_KEY, on ? "0" : "1");
      return !on;
    });
  };
  /** Keep the box inside the chart, so a resize or rotate never loses it. */
  const clampBox = (x: number, y: number) => {
    const plot = plotRef.current?.getBoundingClientRect();
    const box = boxRef.current?.getBoundingClientRect();
    if (!plot || !box) return { x, y };
    return {
      x: Math.min(Math.max(0, x), Math.max(0, plot.width - box.width)),
      y: Math.min(Math.max(0, y), Math.max(0, plot.height - box.height)),
    };
  };
  const startBoxDrag = (e: ReactPointerEvent) => {
    const plot = plotRef.current?.getBoundingClientRect();
    const box = boxRef.current?.getBoundingClientRect();
    if (!plot || !box) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    boxDrag.current = { dx: e.clientX - box.left, dy: e.clientY - box.top };
  };
  const moveBox = (e: ReactPointerEvent) => {
    const drag = boxDrag.current;
    const plot = plotRef.current?.getBoundingClientRect();
    if (!drag || !plot) return;
    setBoxPos(clampBox(e.clientX - plot.left - drag.dx, e.clientY - plot.top - drag.dy));
  };
  const endBoxDrag = () => {
    if (!boxDrag.current) return;
    boxDrag.current = null;
    setBoxPos((pos) => {
      if (pos) remember(BOX_POS_KEY, JSON.stringify(pos));
      return pos;
    });
  };
  const [full, setFull] = useState(false);
  const [bar, setBar] = useState<BarMinutes>(1);
  const measureLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const measuringRef = useRef(false);
  measuringRef.current = measuring;
  const [snapClose, setSnapClose] = useState(false);
  const snapCloseRef = useRef(false);
  snapCloseRef.current = snapClose;
  const [measure, setMeasure] = useState<Measure>(NO_MEASURE);
  useEffect(() => {
    try {
      const saved = Number(localStorage.getItem(BAR_KEY));
      if ((BAR_MINUTES as readonly number[]).includes(saved)) setBar(saved as BarMinutes);
    } catch {
      /* private mode */
    }
  }, []);
  useEffect(() => {
    onLiveBars?.(liveBarsFor(bar));
  }, [bar, onLiveBars]);
  const symbol = (state?.symbol ?? "").toUpperCase();
  // "Hold view": new candles keep coming, but the zoom and scroll stay where
  // the user left them (a replay otherwise drags the chart along every bar).
  const [hold, setHold] = useState(false);
  const holdRef = useRef(false);
  holdRef.current = hold;
  const [range, setRange] = useState(() => presetRange(0));
  const [past, setPast] = useState<(ChartPayload & { symbol: string; from: string; to: string; interval?: number }) | null>(null);
  const [loadingPast, setLoadingPast] = useState(false);
  const [pastError, setPastError] = useState<string | null>(null);
  const pastSeq = useRef(0);
  // A different chart stock leaves the past view; its candles belong to the old stock.
  // A blank symbol (the page reloading its state) is not a change of stock.
  const pastSymbolRef = useRef<string | null>(null);
  // Set when a replay pins the chart: the page then reloads its live state, and
  // that first symbol it reports is not the user picking another stock.
  const pinHoldRef = useRef(false);
  useEffect(() => {
    if (!symbol || pastSymbolRef.current === symbol) return;
    if (pinHoldRef.current) {
      pinHoldRef.current = false;
      return;
    }
    pastSymbolRef.current = null;
    pastSeq.current += 1;
    setPast(null);
    setPastError(null);
    setLoadingPast(false);
  }, [symbol]);
  const view = past ?? chart;

  // Candles as drawn: live 1-minute bars merged into the chosen size, or the
  // past range Groww already built at that size.
  const rows = useMemo(() => {
    if (!view) return [];
    const base = sessionCandles(view.candles);
    const source = past ? past.interval ?? 1 : 1;
    if (bar === source) return base;
    if (bar > source && bar % source === 0) return withIndicators(resampleCandles(base, bar), !past);
    return base;
  }, [view, past, bar]);
  rowsRef.current = rows;
  const snapToBar = useCallback((sec: number) => bucketStart(sec, bar), [bar]);

  // Volume profile of the latest session on screen, from the candles as sent
  // (1-minute live; the chosen size on a past range).
  const [showProfile, setShowProfile] = useState(true);
  useEffect(() => {
    try {
      if (localStorage.getItem(PROFILE_KEY) === "off") setShowProfile(false);
    } catch {
      /* private mode */
    }
  }, []);
  const toggleProfile = () => {
    setShowProfile((on) => {
      try {
        localStorage.setItem(PROFILE_KEY, on ? "off" : "on");
      } catch {
        /* private mode */
      }
      return !on;
    });
  };
  const profile = useMemo(
    () => (showProfile && view ? volumeProfile(sessionCandles(view.candles)) : null),
    [view, showProfile]
  );
  useEffect(() => {
    profileBarsRef.current?.set(profile);
    const series = candleRef.current;
    if (!series) return;
    for (const line of profileLines.current) series.removePriceLine(line);
    profileLines.current = [];
    if (!profile) return;
    // Lines only, no labels: the POC and value-area figures are in the header.
    const line = (price: number, color: string, style: LineStyle) =>
      series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: false, title: "" });
    profileLines.current = [
      line(profile.poc, POC_COLOR, LineStyle.Solid),
      line(profile.vah, VALUE_AREA_COLOR, LineStyle.Dotted),
      line(profile.val, VALUE_AREA_COLOR, LineStyle.Dotted),
    ];
  }, [profile]);

  // The replay run whose trades the past view marks; null = the default book.
  const pastRunRef = useRef<number | null>(null);
  const loadPast = (
    next: { from: string; to: string },
    size: number = bar,
    runId: number | null = null,
    stock: string = symbol
  ) => {
    pastRunRef.current = runId;
    setRange(next);
    const problem = rangeProblem(next.from, next.to);
    if (problem) {
      setPastError(problem);
      return;
    }
    if (!stock) {
      setPastError("Pick a stock first.");
      return;
    }
    const seq = ++pastSeq.current;
    setLoadingPast(true);
    setPastError(null);
    smaApi
      .history(stock, next.from, next.to, size, runId)
      .then((payload) => {
        if (seq !== pastSeq.current) return;
        if (payload.candles.length === 0) {
          setPastError("Groww has no candles in that range. Markets may have been closed.");
          return;
        }
        pastSymbolRef.current = stock.toUpperCase();
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
  // Candle times change with the size, range or stock, so an old measurement no longer fits.
  useEffect(() => {
    setMeasure(NO_MEASURE);
  }, [bar, past, symbol]);
  useEffect(() => {
    const line = measureLineRef.current;
    if (!line) return;
    const pts = [measure.a, measure.b].filter((p): p is MeasurePoint => p != null);
    const byTime = new Map(pts.map((p) => [p.time, p]));
    const data = Array.from(byTime.values()).sort((x, y) => x.time - y.time);
    const up = measure.a && measure.b ? measure.b.price >= measure.a.price : true;
    line.applyOptions({ color: up ? "#34D399" : "#FB7185" });
    line.setData(data.map((p) => ({ time: p.time as never, value: p.price })));
  }, [measure]);
  useEffect(() => {
    if (!measuring) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMeasure(NO_MEASURE);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [measuring]);
  const toggleMeasure = () => {
    setMeasuring((on) => !on);
    setMeasure(NO_MEASURE);
  };

  const pickBar = (next: BarMinutes) => {
    setBar(next);
    try {
      localStorage.setItem(BAR_KEY, String(next));
    } catch {
      /* private mode */
    }
    if (past) loadPast({ from: past.from.replace(" ", "T"), to: past.to.replace(" ", "T") }, next, pastRunRef.current);
  };

  const toggleFull = () => {
    if (full) {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      setFull(false);
      return;
    }
    setFull(true);
    // Phones without the Fullscreen API still get the chart filling the window.
    sectionRef.current?.requestFullscreen?.().catch(() => {});
  };
  useEffect(() => {
    if (!full) return;
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFull(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      document.removeEventListener("keydown", onKey);
    };
  }, [full]);

  const backToLive = () => {
    pastSeq.current += 1;
    pastRunRef.current = null;
    pastSymbolRef.current = null;
    setPast(null);
    setPastError(null);
    setLoadingPast(false);
  };

  // A replay that ends (finished, stopped, or gone after a server restart)
  // leaves the chart on the day it was playing, not today. A new replay
  // shows its own chart again.
  const pinSeq = pin?.seq;
  useEffect(() => {
    if (pinSeq == null || !pin) return;
    if (pin.date && (pin.symbol || symbol)) {
      pinHoldRef.current = true;
      loadPast({ from: `${pin.date}T09:15`, to: `${pin.date}T15:30` }, bar, pin.runId, (pin.symbol || symbol).toUpperCase());
    }
    else backToLive();
    // Only a new pin applies; later chart changes are the user's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinSeq]);

  useEffect(() => {
    if (!rootRef.current) return;
    // Phones: smaller axis text, and line names stay in the legend instead of
    // widening every price tag on the axis over the candles.
    const narrow = window.matchMedia("(max-width: 639px)").matches;
    // No line names on the chart: SMA, VWAP and RSI readings live in the header line.
    const tag = (_name: string) => "";
    const look = CHART_LOOK[pageTheme()];
    const instance = createChart(rootRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: look.background },
        textColor: look.text,
        fontSize: narrow ? 10 : 12,
      },
      grid: {
        vertLines: { color: look.grid },
        horzLines: { color: look.grid },
      },
      rightPriceScale: { borderColor: look.grid },
      timeScale: {
        borderColor: look.grid,
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
    const profileBars = new VolumeProfileBars();
    candles.attachPrimitive(profileBars);
    profileBarsRef.current = profileBars;
    const boxes = new TradeBoxes();
    candles.attachPrimitive(boxes);
    tradeBoxesRef.current = boxes;
    // Stretch the price scale so the open position's entry, stop and target
    // lines stay on screen instead of being cut off above or below the candles.
    candles.applyOptions({
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const res = original();
        const levels = levelsRef.current;
        if (!res || levels.length === 0) return res;
        return {
          ...res,
          priceRange: {
            minValue: Math.min(res.priceRange.minValue, ...levels),
            maxValue: Math.max(res.priceRange.maxValue, ...levels),
          },
        };
      },
    });
    const fast = instance.addLineSeries({
      color: "#F43F5E",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: tag("SMA 9"),
    });
    const slow = instance.addLineSeries({
      color: "#3B82F6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: tag("SMA 21"),
    });
    const atr = instance.addLineSeries({
      color: "#A78BFA",
      lineWidth: 2,
      priceScaleId: "atr",
      priceLineVisible: false,
      lastValueVisible: false,
      title: tag("ATR 14"),
    });
    instance.priceScale("atr").applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
    // VWAP sits on the price scale; RSI gets its own 0–100 strip at the bottom.
    const vwap = instance.addLineSeries({
      color: VWAP_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      title: tag("VWAP"),
      visible: false,
    });
    const rsi = instance.addLineSeries({
      color: RSI_COLOR,
      lineWidth: 2,
      priceScaleId: "rsi",
      priceLineVisible: false,
      lastValueVisible: false,
      title: tag("RSI 14"),
      visible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
    });
    instance.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
    vwapRef.current = vwap;
    rsiRef.current = rsi;
    // Bollinger: upper and lower dashed, middle faint. Drawn only when the filter is on.
    bbRefs.current = [
      { color: BB_COLOR, style: LineStyle.Dashed, width: 1 },
      { color: "rgba(196, 181, 253, 0.45)", style: LineStyle.Dotted, width: 1 },
      { color: BB_COLOR, style: LineStyle.Dashed, width: 1 },
    ].map((look) =>
      instance.addLineSeries({
        color: look.color,
        lineWidth: look.width as 1,
        lineStyle: look.style,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      })
    );

    apiRef.current = instance;
    candleRef.current = candles;
    smaFastRef.current = fast;
    smaSlowRef.current = slow;
    atrRef.current = atr;
    const measureLine = instance.addLineSeries({
      color: "#FBBF24",
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      pointMarkersVisible: true,
      pointMarkersRadius: 4,
    });
    measureLineRef.current = measureLine;

    // Measure: first click is the start, second the end, a third starts again.
    const onClick = (param: { time?: unknown; point?: { x: number; y: number } }) => {
      if (!measuringRef.current || typeof param.time !== "number" || !param.point) return;
      const t = param.time;
      const candle = rowsRef.current.find((c) => c.time === t);
      if (!candle) return;
      let price: number | null;
      if (snapCloseRef.current) {
        price = candle.close;
      } else {
        // The exact price where you clicked: on the body, a wick, or anywhere in that candle's column.
        price = candles.coordinateToPrice(param.point.y);
      }
      if (price == null || !Number.isFinite(price)) return;
      const pt = { time: t, price: Math.round(price * 100) / 100 };
      setMeasure((cur) => (cur.a && !cur.b ? { a: cur.a, b: pt } : { a: pt, b: null }));
    };
    instance.subscribeClick(onClick);

    const onCrosshair = (param: { time?: unknown; seriesData: Map<unknown, unknown> }) => {
      if (param.time == null) {
        setHover(null);
        setHoverOhlc(null);
        return;
      }
      const list = rowsRef.current;
      const at = list.findIndex((c) => c.time === param.time);
      setHoverOhlc(at >= 0 ? ohlcAt(list, at) : null);
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
      instance.unsubscribeClick(onClick);
      observer.disconnect();
      profileBarsRef.current = null;
      profileLines.current = [];
      instance.remove();
      apiRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = view;
    if (!chart || !candleRef.current || !smaFastRef.current || !smaSlowRef.current || !atrRef.current) return;
    // Held: remember the time span on screen and put it back after the new data.
    const kept = holdRef.current ? apiRef.current?.timeScale().getVisibleRange() ?? null : null;
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
    // Each line shows only when its setting is in use. An older server sends no
    // filters block: keep ATR as before and hide the rest.
    const filters = chart.filters;
    const showAtr = filters ? filters.atr_stop : true;
    const showVwap = Boolean(filters?.use_vwap);
    const showRsi = Boolean(filters?.use_rsi);
    atrRef.current.applyOptions({ visible: showAtr });
    // Both strips at once share the bottom: ATR above, RSI below.
    apiRef.current?.priceScale("atr").applyOptions({
      scaleMargins: showRsi ? { top: 0.72, bottom: 0.15 } : { top: 0.75, bottom: 0.02 },
    });
    apiRef.current?.priceScale("rsi").applyOptions({
      scaleMargins: showAtr ? { top: 0.87, bottom: 0.01 } : { top: 0.75, bottom: 0.02 },
    });
    if (vwapRef.current) {
      vwapRef.current.applyOptions({ visible: showVwap });
      vwapRef.current.setData(
        showVwap ? rows.filter((c) => c.vwap != null).map((c) => ({ time: c.time as never, value: c.vwap as number })) : []
      );
    }
    const showBb = Boolean(filters?.use_bollinger) || (filters?.bb_exit ?? "OFF") !== "OFF";
    const bands = showBb ? bollingerRows(rows, filters?.bb_period ?? 20, filters?.bb_std ?? 2, !past) : null;
    bbRefs.current.forEach((series, i) => {
      series.applyOptions({ visible: showBb });
      series.setData(
        bands ? bands.map((b) => ({ time: b.time as never, value: [b.upper, b.mid, b.lower][i] })) : []
      );
    });
    if (rsiRef.current) {
      const series = rsiRef.current;
      series.applyOptions({ visible: showRsi });
      series.setData(
        showRsi ? rows.filter((c) => c.rsi14 != null).map((c) => ({ time: c.time as never, value: c.rsi14 as number })) : []
      );
      for (const line of rsiBands.current) series.removePriceLine(line);
      rsiBands.current = [];
      if (showRsi && filters) {
        const band = (price: number, color: string) =>
          series.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
        rsiBands.current = [
          band(filters.rsi_long_min, "rgba(52, 211, 153, 0.6)"),
          band(filters.rsi_long_max, "rgba(52, 211, 153, 0.6)"),
          band(filters.rsi_short_min, "rgba(251, 113, 133, 0.6)"),
          band(filters.rsi_short_max, "rgba(251, 113, 133, 0.6)"),
        ];
      }
    }
    candleRef.current.setMarkers([]);
    tradeBoxesRef.current?.set(tradeBoxes(chart, rows, (past?.symbol ?? symbol).toUpperCase(), bar, trades));

    if (entryLine.current) {
      candleRef.current.removePriceLine(entryLine.current);
      entryLine.current = null;
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
    if (kept) {
      try {
        apiRef.current?.timeScale().setVisibleRange(kept);
      } catch {
        /* the held span is no longer in the data (another stock or size) */
      }
    }
  }, [view, rows, bar, past, trades, allTrades, symbol]);

  // A new past range opens fitted to the screen; going back to live jumps to the latest bar.
  useEffect(() => {
    const api = apiRef.current;
    if (!api || holdRef.current) return;
    // A short live series (few big candles) fills the width instead of hugging the right edge.
    if (past || rowsRef.current.length <= 150) api.timeScale().fitContent();
    else api.timeScale().scrollToRealTime();
  }, [past, bar]);

  const pnl = livePnl(state);
  const pos = state?.position;
  const pnlGross = pnl?.gross ?? null;
  pnlGrossRef.current = pnlGross;
  useEffect(() => {
    if (!entryLine.current) return;
    entryLine.current.applyOptions(entryLook(pnlGross));
  }, [pnlGross]);

  // Stop and target follow the live position (about every second), so a
  // trailing stop slides on the chart as it moves. The chart payload only
  // refreshes every 15 s, so it is the fallback.
  const slLevel = past || !pos || !stopOn ? null : (pos.sl_trigger ?? chart?.sl_trigger ?? null);
  const targetLevel = past || !pos ? null : (pos.target ?? chart?.target ?? null);
  const slCash = pos && slLevel != null ? pnlAt(pos.direction, pos.entry_price, pos.qty, slLevel) : null;
  const targetCash = pos && targetLevel != null ? pnlAt(pos.direction, pos.entry_price, pos.qty, targetLevel) : null;
  levelsRef.current =
    past || !pos ? [] : [pos.entry_price, slLevel, targetLevel].filter((v): v is number => v != null && v > 0);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    const place = (
      ref: { current: IPriceLine | null },
      price: number | null,
      color: string
    ) => {
      if (price == null || price <= 0) {
        if (ref.current) series.removePriceLine(ref.current);
        ref.current = null;
        return;
      }
      // Lines only: no text on the chart (the position box names them).
      if (ref.current) ref.current.applyOptions({ price });
      else ref.current = series.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, title: "" });
    };
    place(slLine, slLevel, "#F59E0B");
    place(targetLine, targetLevel, "#34D399");
  }, [slLevel, targetLevel]);

  const latest = latestSma(rows);
  const filters = view?.filters;
  const blocked = view?.blocked ?? [];
  const lastBlocked = blocked.length ? blocked[blocked.length - 1] : null;
  const hoverRow = hoverOhlc ? rows.find((c) => c.time === hoverOhlc.time) ?? null : null;
  const lastOf = (key: "vwap" | "rsi14") => {
    for (let i = rows.length - 1; i >= 0; i -= 1) if (rows[i][key] != null) return rows[i][key] as number;
    return null;
  };
  const shownVwap = hoverRow ? hoverRow.vwap ?? null : lastOf("vwap");
  const shownRsi = hoverRow ? hoverRow.rsi14 ?? null : lastOf("rsi14");
  const ohlc = hoverOhlc ?? ohlcAt(rows, rows.length - 1);
  const sma9 = hover ? hover.sma9 : latest.sma9;
  const sma21 = hover ? hover.sma21 : latest.sma21;
  // (SMA 9 − SMA 21) ÷ SMA 21 × 100, the gap the gap filter and gap mode read.
  const smaGap = sma9 != null && sma21 != null && sma21 !== 0 ? ((sma9 - sma21) / sma21) * 100 : null;
  // The stock whose candles are drawn: a past range keeps its own stock.
  const shownSymbol = (past?.symbol ?? chart?.symbol ?? state?.symbol ?? "").toUpperCase();
  const toggleHold = () => {
    const next = !hold;
    setHold(next);
    onHoldChange?.(next, shownSymbol);
    if (!next) {
      const api = apiRef.current;
      if (api) {
        if (past || rowsRef.current.length <= 150) api.timeScale().fitContent();
        else api.timeScale().scrollToRealTime();
      }
    }
  };

  return (
    <section
      ref={sectionRef}
      aria-label="Price chart"
      className={clsx(
        "min-w-0 max-w-full overflow-hidden border-white/5 bg-[#151921]",
        full ? "fixed inset-0 z-[60] flex flex-col overflow-y-auto" : "rounded-xl border"
      )}
    >
      <div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-4">
        <h2 className="flex flex-wrap items-center gap-x-2 text-sm font-semibold text-slate-100">
          {shownSymbol ? <span className="text-amber-300">{shownSymbol}</span> : null}
          <span className="font-normal text-slate-300">{bar === 60 ? "1-hour" : `${bar}-minute`}</span>
          <span role="group" aria-label="Candle size" className="ml-1 inline-flex rounded-md ring-1 ring-inset ring-white/10">
            {BAR_MINUTES.map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={bar === m}
                disabled={loadingPast}
                onClick={() => pickBar(m)}
                className={clsx(
                  "min-h-8 min-w-9 px-2 font-mono text-xs first:rounded-l-md last:rounded-r-md disabled:opacity-50",
                  bar === m ? "bg-sky-500/25 font-semibold text-sky-100" : "font-normal text-slate-300 hover:bg-white/5"
                )}
              >
                {barLabel(m)}
              </button>
            ))}
          </span>
          <button
            type="button"
            onClick={toggleFull}
            aria-label={full ? "Exit full screen" : "Full screen"}
            title={full ? "Exit full screen (Esc)" : "Full screen"}
            className="flex min-h-8 min-w-9 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
          >
            {full ? <Minimize2 size={15} aria-hidden /> : <Maximize2 size={15} aria-hidden />}
          </button>
          <button
            type="button"
            onClick={toggleHold}
            aria-pressed={hold}
            title={
              hold
                ? "Holding this view: new candles still arrive, but the zoom, scroll and stock stay put. Press to follow the latest candle again."
                : "Hold this view: keep the zoom, scroll and stock you are looking at while the replay or market moves on"
            }
            className={clsx(
              "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
              hold ? "bg-violet-400/20 font-semibold text-violet-100 ring-violet-400/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
            )}
          >
            {hold ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
            {hold ? "Holding" : "Hold view"}
          </button>
          <button
            type="button"
            onClick={toggleMeasure}
            aria-pressed={measuring}
            title="Measure profit or loss between two candles"
            className={clsx(
              "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
              measuring ? "bg-amber-400/20 font-semibold text-amber-100 ring-amber-400/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
            )}
          >
            <Ruler size={14} aria-hidden />
            Measure
          </button>
          <button
            type="button"
            onClick={toggleProfile}
            aria-pressed={showProfile}
            title="Volume profile: how many shares traded at each price today"
            className={clsx(
              "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
              showProfile ? "bg-sky-400/15 font-semibold text-accentSky ring-sky-400/40" : "text-slate-300 ring-white/10 hover:bg-white/5"
            )}
          >
            <BarChartHorizontal size={14} aria-hidden />
            Profile
          </button>
          <button
            type="button"
            onClick={toggleChart}
            aria-pressed={chartHidden}
            aria-controls="sma-chart-body"
            title={chartHidden ? "Show the chart" : "Hide the chart (the header stays)"}
            className="flex min-h-8 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
          >
            {chartHidden ? <Eye size={14} aria-hidden /> : <EyeOff size={14} aria-hidden />}
            {chartHidden ? "Show chart" : "Hide chart"}
          </button>
          <button
            type="button"
            onClick={toggleTable}
            aria-pressed={showTable}
            title={showTable ? "Hide the data table" : "Show these candles as a table: prices, SMA gap, VWAP, RSI, candle names and trade P&L"}
            className={clsx(
              "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
              showTable ? "bg-sky-400/15 font-semibold text-accentSky ring-sky-400/40" : "text-slate-300 ring-white/10 hover:bg-white/5"
            )}
          >
            <Table2 size={14} aria-hidden />
            Table
          </button>
          {past ? (
            <span className="rounded-md bg-violet-500/15 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-violet-200 ring-1 ring-inset ring-violet-400/35">
              Past
            </span>
          ) : null}
        </h2>
        <ul aria-label="Chart legend" className={clsx("flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-300", chartHidden && "hidden")}>
          {showProfile ? (
            profile ? (
              <>
                <LegendItem swatch={<span className="block h-2.5 w-4 rounded-sm bg-[#FACC15]/70" />}>
                  POC <span className="font-mono text-slate-100">{px(profile.poc)}</span>
                </LegendItem>
                <LegendItem swatch={<span className="block h-2.5 w-4 rounded-sm bg-[#38BDF8]/50" />}>
                  Value area {Math.round(VALUE_AREA_SHARE * 100)}%{" "}
                  <span className="font-mono text-slate-100">
                    {px(profile.val)}–{px(profile.vah)}
                  </span>
                </LegendItem>
              </>
            ) : (
              <LegendItem swatch={<span className="block h-2.5 w-4 rounded-sm bg-slate-500/40" />}>
                Volume profile: no volume yet
              </LegendItem>
            )
          ) : null}
          {past ? null : (
            <LegendItem swatch={<span className="block w-5 border-t-2 border-dashed border-amber-400" />}>
              {!stopOn ? (
                <span className="font-semibold text-amber-300">Stop OFF</span>
              ) : chart?.trailing ? (
                "Moving stop · target"
              ) : chart?.tsl_step ? (
                `Trailing stop · every ₹${chart.tsl_step}`
              ) : (state?.stop_type ?? "ATR") === "TSL" && !chart?.sl_trigger ? (
                "Trailing stop"
              ) : (
                `${chart?.atr_multiplier ?? state?.atr_multiplier ?? 1.5}× ATR stop`
              )}
            </LegendItem>
          )}
          {filters?.use_bollinger || (filters?.bb_exit ?? "OFF") !== "OFF" ? (
            <LegendItem swatch={<span className="block w-5 border-t-2 border-dashed border-[#C4B5FD]" />}>
              Bollinger {filters?.bb_period ?? 20}/{filters?.bb_std ?? 2}σ
            </LegendItem>
          ) : null}
        </ul>
      </div>
      <div id="sma-chart-body" className={clsx(chartHidden && "hidden", full && !chartHidden && "flex flex-1 flex-col")}>
      <OhlcLine
        ohlc={ohlc}
        hovering={hoverOhlc != null}
        sma9={sma9}
        sma21={sma21}
        gap={smaGap}
        vwap={shownVwap}
        rsi={shownRsi}
      />
      {lastBlocked ? (
        <p className="px-3 pb-2 text-xs text-slate-300 sm:px-4" role="status">
          <span className="font-semibold text-slate-200">
            Last skipped cross · {istClock(lastBlocked.time)} {lastBlocked.direction === "LONG" ? "BUY" : "SELL"}:
          </span>{" "}
          {lastBlocked.reason}
        </p>
      ) : null}
      {measuring ? (
        <MeasureBar
          measure={measure}
          rows={rows}
          snapClose={snapClose}
          defaultQty={state?.position?.qty ?? 1}
          onSnap={setSnapClose}
          onClear={() => setMeasure(NO_MEASURE)}
          onClose={toggleMeasure}
        />
      ) : null}
      <RangeBar
        range={range}
        past={past}
        loading={loadingPast}
        error={pastError}
        onChange={setRange}
        onLoad={loadPast}
        onLive={backToLive}
      />
      <div ref={plotRef} className={clsx("relative", full && "min-h-[240px] flex-1")}>
        <div
          ref={rootRef}
          className={clsx("w-full", full ? "absolute inset-0" : "h-[320px] sm:h-[460px] lg:h-[520px]", measuring && "cursor-crosshair")}
        />
        {!view && (
          <div aria-busy="true" aria-label="Loading chart" className="absolute inset-0 z-[5] flex flex-col justify-end gap-2 bg-[#151921] p-4">
            <Skeleton className="h-2/3 w-full opacity-60" />
            <Skeleton className="h-1/6 w-full opacity-40" />
          </div>
        )}
        {pos && pnl && !past && (
          <div
            ref={boxRef}
            style={boxPos ? { left: boxPos.x, top: boxPos.y } : undefined}
            className={clsx(
              "pointer-events-none absolute z-10 rounded-lg border border-white/10 bg-[#0B0E14]/90 shadow-lg",
              !boxPos && "left-2 top-2 sm:left-3 sm:top-3",
              boxSmall ? "max-w-[170px] p-1.5" : "max-w-[200px] p-2 sm:max-w-[240px] sm:p-2.5"
            )}
          >
            {/* Drag here to move the box off the candles; the arrow folds it to one line. */}
            <div className="pointer-events-auto flex items-center gap-1">
              <div
                role="button"
                tabIndex={-1}
                aria-label="Drag to move the position box"
                title="Drag to move"
                onPointerDown={startBoxDrag}
                onPointerMove={moveBox}
                onPointerUp={endBoxDrag}
                onPointerCancel={endBoxDrag}
                onDoubleClick={() => {
                  setBoxPos(null);
                  remember(BOX_POS_KEY, "null");
                }}
                className="flex min-w-0 flex-1 cursor-move touch-none select-none items-center gap-1 text-[10px] uppercase tracking-wider text-slate-400 sm:text-[11px]"
              >
                <GripHorizontal size={12} aria-hidden className="shrink-0 text-slate-500" />
                <span className="truncate">
                  {pos.direction} {pos.qty.toLocaleString("en-IN")} · entry {px(pos.entry_price)}
                </span>
              </div>
              {boxSmall && onClose ? (
                <button
                  type="button"
                  onClick={onClose}
                  disabled={closing}
                  aria-label="Close position"
                  title="Close this position (asks first)"
                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-rose-300 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/20 disabled:opacity-50"
                >
                  {closing ? <Loader2 size={12} aria-hidden className="animate-spin" /> : <X size={13} aria-hidden />}
                </button>
              ) : null}
              <button
                type="button"
                onClick={toggleBox}
                aria-label={boxSmall ? "Show stop, target and close" : "Fold the position box"}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-white/10 hover:text-slate-100"
              >
                {boxSmall ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
              </button>
            </div>
            <div
              className={clsx(
                "font-mono font-semibold",
                boxSmall ? "text-sm" : "mt-1 text-[1rem] leading-6 sm:text-lg",
                pnl.gross >= 0 ? "text-[#10B981]" : "text-[#F43F5E]"
              )}
            >
              {pnl.gross >= 0 ? "+" : ""}
              {inr(pnl.gross)}
            </div>
            {boxSmall ? null : (
            <>
            <div className="font-mono text-[11px] text-slate-400">
              {pnl.points >= 0 ? "+" : ""}
              {pnl.points.toFixed(2)} pts · {pnl.pct >= 0 ? "+" : ""}
              {pnl.pct.toFixed(2)}%
            </div>
            <dl className="mt-2 space-y-0.5 border-t border-white/10 pt-1.5 font-mono text-[11px]">
              <div className="flex justify-between gap-3">
                <dt className="font-sans text-amber-300">{pos.tsl_step ? "Trailing stop" : pos.trailing ? "Moving stop" : "Stop"}</dt>
                <dd className="text-right text-slate-200">
                  {slLevel == null ? (
                    <span className="font-sans font-semibold text-amber-300">OFF</span>
                  ) : (
                    <>
                      {px(slLevel)}
                      {slCash != null ? (
                        <span className={slCash >= 0 ? "text-[#10B981]" : "text-[#F43F5E]"}> {signedInr(slCash)}</span>
                      ) : null}
                    </>
                  )}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="font-sans text-emerald-300">Target</dt>
                <dd className="text-right text-slate-200">
                  {targetLevel == null ? (
                    <span className="font-sans text-slate-400">none</span>
                  ) : (
                    <>
                      {px(targetLevel)}
                      {targetCash != null ? <span className="text-[#10B981]"> {signedInr(targetCash)}</span> : null}
                    </>
                  )}
                </dd>
              </div>
            </dl>
            {onClose ? (
              <button
                type="button"
                disabled={closing}
                onClick={onClose}
                className="pointer-events-auto mt-2 min-h-11 w-full rounded-md border border-rose-400/50 bg-rose-500/15 px-2 text-sm font-semibold text-rose-200 hover:bg-rose-500/25 disabled:opacity-50 sm:min-h-9"
              >
                {closing ? "Closing…" : "Close position"}
              </button>
            ) : null}
            </>
            )}
          </div>
        )}
      </div>
      </div>
      {showTable ? (
        <ChartDataTable
          candles={rows}
          markers={view?.markers ?? NO_MARKERS}
          trades={allTrades ?? trades}
          snap={snapToBar}
          symbol={(past?.symbol ?? state?.symbol ?? "").toUpperCase()}
          barLabel={bar === 60 ? "1-hour" : `${bar}-minute`}
          barSeconds={bar * 60}
          source={past ? "Past view" : state?.mode === "REPLAY" ? "Replay" : state?.mode === "LIVE" ? "Live" : "Paper"}
        />
      ) : null}
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

function MeasureBar({
  measure,
  rows,
  snapClose,
  defaultQty,
  onSnap,
  onClear,
  onClose,
}: {
  measure: Measure;
  rows: Candle[];
  snapClose: boolean;
  defaultQty: number;
  onSnap: (on: boolean) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const [qtyText, setQtyText] = useState(String(defaultQty || 1));
  const qty = Math.max(0, Math.floor(Number(qtyText) || 0));
  const { a, b } = measure;
  let body: ReactNode;
  if (!a) {
    body = <span className="text-amber-200">Click the start candle on the chart.</span>;
  } else if (!b) {
    body = (
      <span>
        <span className="text-slate-400">Start</span> {istStamp(a.time)} · <span className="text-slate-100">{px(a.price)}</span>
        <span className="text-amber-200"> — now click the end candle.</span>
      </span>
    );
  } else {
    const points = b.price - a.price;
    const pct = a.price ? (points / a.price) * 100 : 0;
    const ia = rows.findIndex((c) => c.time === a.time);
    const ib = rows.findIndex((c) => c.time === b.time);
    const bars = ia >= 0 && ib >= 0 ? Math.abs(ib - ia) : null;
    const long = points * qty;
    const short = -points * qty;
    const tone = (v: number) => (v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-200");
    const sign = (v: number) => (v > 0 ? "+" : "");
    body = (
      <>
        <span className="whitespace-nowrap">
          <span className="text-slate-400">Start</span> {istStamp(a.time)} · <span className="text-slate-100">{px(a.price)}</span>
        </span>
        <span className="whitespace-nowrap">
          <span className="text-slate-400">End</span> {istStamp(b.time)} · <span className="text-slate-100">{px(b.price)}</span>
        </span>
        <span className={clsx("whitespace-nowrap font-semibold", tone(points))}>
          {sign(points)}
          {points.toFixed(2)} pts ({sign(pct)}
          {pct.toFixed(2)}%)
        </span>
        <span className="whitespace-nowrap text-slate-300">
          {bars != null ? `${bars} bar${bars === 1 ? "" : "s"} · ` : ""}
          {spanText(b.time - a.time)}
        </span>
        <span className="whitespace-nowrap">
          <span className="text-slate-400">Long</span> <span className={clsx("font-semibold", tone(long))}>{sign(long)}{inr(long)}</span>
        </span>
        <span className="whitespace-nowrap">
          <span className="text-slate-400">Short</span> <span className={clsx("font-semibold", tone(short))}>{sign(short)}{inr(short)}</span>
        </span>
        <span className="whitespace-nowrap text-[11px] text-slate-400">gross, before charges</span>
      </>
    );
  }
  return (
    <div
      role="region"
      aria-label="Measure"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-amber-400/20 bg-amber-400/[0.04] px-3 py-2 font-mono text-xs sm:px-4"
    >
      <span className="flex items-center gap-1 font-sans font-semibold text-amber-200">
        <Ruler size={13} aria-hidden /> Measure
      </span>
      {body}
      <span className="ml-auto flex flex-wrap items-center gap-2 font-sans">
        <label className="flex items-center gap-1 text-slate-400">
          Qty
          <input
            inputMode="numeric"
            value={qtyText}
            onChange={(e) => setQtyText(e.target.value.replace(/[^0-9]/g, ""))}
            className="min-h-8 w-16 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-slate-100"
            aria-label="Quantity for the P&L"
          />
        </label>
        <label
          className="flex items-center gap-1 text-slate-300"
          title="Off: the exact price where you click. On: the clicked candle's close."
        >
          <input type="checkbox" checked={snapClose} onChange={(e) => onSnap(e.target.checked)} className="accent-amber-400" />
          Snap to close
        </label>
        <button
          type="button"
          onClick={onClear}
          disabled={!a}
          className="min-h-8 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-40"
        >
          Clear
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close measure"
          className="flex min-h-8 min-w-8 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
        >
          <X size={14} aria-hidden />
        </button>
      </span>
    </div>
  );
}

/** The candle under the mouse (else the latest): OHLC, change, and the SMA 9 / SMA 21
 *  readings with their gap % — the number the gap filter and gap mode read. */
function OhlcLine({
  ohlc,
  hovering,
  sma9,
  sma21,
  gap,
  vwap,
  rsi,
}: {
  ohlc: Ohlc | null;
  hovering: boolean;
  sma9: number | null;
  sma21: number | null;
  gap: number | null;
  vwap: number | null;
  rsi: number | null;
}) {
  if (!ohlc) return null;
  const change = ohlc.prevClose != null ? ohlc.close - ohlc.prevClose : ohlc.close - ohlc.open;
  const base = ohlc.prevClose ?? ohlc.open;
  const pct = base ? (change / base) * 100 : 0;
  const tone = ohlc.close >= ohlc.open ? "text-emerald-300" : "text-rose-300";
  const cell = (label: string, value: number) => (
    <span className="whitespace-nowrap">
      <span className="text-slate-400">{label}</span> <span className={tone}>{px(value)}</span>
    </span>
  );
  return (
    <div
      aria-live="off"
      className="flex flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-white/5 px-3 py-1.5 font-mono text-xs sm:px-4"
    >
      <span className="whitespace-nowrap text-slate-300">
        {istStamp(ohlc.time)}
        {hovering ? "" : " · latest"}
      </span>
      {cell("O", ohlc.open)}
      {cell("H", ohlc.high)}
      {cell("L", ohlc.low)}
      {cell("C", ohlc.close)}
      <span className={clsx("whitespace-nowrap", change >= 0 ? "text-emerald-300" : "text-rose-300")}>
        {change >= 0 ? "+" : ""}
        {change.toFixed(2)} ({pct >= 0 ? "+" : ""}
        {pct.toFixed(2)}%)
      </span>
      <span className="whitespace-nowrap">
        <span className="text-[#F43F5E]">SMA 9</span> <span className="text-slate-100">{px(sma9)}</span>
      </span>
      <span className="whitespace-nowrap">
        <span className="text-[#3B82F6]">SMA 21</span> <span className="text-slate-100">{px(sma21)}</span>
      </span>
      <span className="whitespace-nowrap">
        <span className="text-slate-400">Gap</span>{" "}
        <span className={clsx(gap == null ? "text-slate-400" : gap >= 0 ? "text-emerald-300" : "text-rose-300")}>
          {gap == null ? "—" : `${gap >= 0 ? "+" : ""}${gap.toFixed(3)}%`}
        </span>
      </span>
      <span className="whitespace-nowrap">
        <span className="text-[#22D3EE]">VWAP</span> <span className="text-slate-100">{px(vwap)}</span>
      </span>
      <span className="whitespace-nowrap">
        <span className="text-[#F472B6]">RSI</span>{" "}
        <span className="text-slate-100">{rsi == null ? "—" : rsi.toFixed(1)}</span>
      </span>
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
