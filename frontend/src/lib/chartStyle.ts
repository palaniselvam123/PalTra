"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * The SMA chart's look, like Groww's indicator settings: which lines show and each
 * line's colour, thickness and pattern. Display only, kept in this browser
 * (localStorage), shared by the terminal's chart and the chart pages.
 *
 * `show`: "auto" draws the line only while the strategy uses it (the old behaviour,
 * e.g. VWAP while the VWAP check is ticked); "on" always; "off" never.
 */
export type Show = "auto" | "on" | "off";

/** lightweight-charts LineStyle values. */
export const LINE_PATTERNS = [
  { value: 0, label: "Solid" },
  { value: 2, label: "Dashed" },
  { value: 3, label: "Long dash" },
  { value: 1, label: "Dotted" },
  { value: 4, label: "Sparse dots" },
] as const;
export type Pattern = (typeof LINE_PATTERNS)[number]["value"];
export const LINE_WIDTHS = [1, 2, 3, 4] as const;
export type Width = (typeof LINE_WIDTHS)[number];

export type LineLook = {
  show: Show;
  color: string;
  /** The second colour of a two-line indicator (EMA slow, Supertrend down). */
  color2?: string;
  width: Width;
  style: Pattern;
};

export type LineKey = "sma9" | "sma21" | "vwap" | "ema" | "bb" | "st" | "atr" | "rsi" | "cvd" | "pressure" | "pressureSell" | "flow" | "flowSell" | "pivots";

export type ChartStyle = Record<LineKey, LineLook> & {
  candleUp: string;
  candleDown: string;
  /** Draw Heikin Ashi candles instead of the real ones (display only; the bot reads real prices). */
  heikinAshi: boolean;
  /** Mark on the buyers / sellers strip, at each trade's entry and exit, the executed % the bot read then. */
  tradeReads: boolean;
  /** Draw the buyers / sellers lines over the candles (like the SMA lines, 0-100 % across the candle area) instead of
   * on their own strip under them. Display only. */
  pressureOnCandles: boolean;
};

export const LINE_NAMES: Record<LineKey, { name: string; auto: string; color2?: string }> = {
  sma9: { name: "SMA fast (9)", auto: "always" },
  sma21: { name: "SMA slow (21)", auto: "always" },
  vwap: { name: "VWAP", auto: "while the VWAP check is ticked" },
  ema: { name: "EMA fast / slow", auto: "while the EMA check or exit is on", color2: "slow" },
  bb: { name: "Bollinger bands", auto: "while the Bollinger check or exit is on" },
  st: { name: "Supertrend", auto: "while the Supertrend check or exit is on", color2: "down" },
  atr: { name: "ATR 14 (strip)", auto: "while the stop type is ATR" },
  rsi: { name: "RSI 14 (strip)", auto: "while the RSI check is ticked" },
  cvd: { name: "Cumulative delta (strip)", auto: "while the cumulative delta check or divergence exit is on" },
  pressure: { name: "Buyers waiting % (strip)", auto: "while a Most active scan has read the stock" },
  pressureSell: { name: "Sellers waiting % (strip)", auto: "while a Most active scan has read the stock" },
  flow: { name: "Buyers executed % (strip, estimated)", auto: "with the waiting lines" },
  flowSell: { name: "Sellers executed % (strip, estimated)", auto: "with the waiting lines" },
  pivots: { name: "Pivot points (P, R1/S1, R2/S2)", auto: "while the pivot target is on", color2: "P" },
};

export const DEFAULT_CHART_STYLE: ChartStyle = {
  candleUp: "#10B981",
  candleDown: "#F43F5E",
  heikinAshi: false,
  tradeReads: true,
  pressureOnCandles: false,
  sma9: { show: "on", color: "#F43F5E", width: 2, style: 0 },
  sma21: { show: "on", color: "#3B82F6", width: 2, style: 0 },
  vwap: { show: "auto", color: "#22D3EE", width: 2, style: 1 },
  ema: { show: "auto", color: "#F59E0B", color2: "#10B981", width: 2, style: 1 },
  bb: { show: "auto", color: "#C4B5FD", width: 1, style: 2 },
  st: { show: "auto", color: "#16A34A", color2: "#DC2626", width: 2, style: 0 },
  atr: { show: "auto", color: "#A78BFA", width: 2, style: 0 },
  rsi: { show: "auto", color: "#F472B6", width: 2, style: 0 },
  cvd: { show: "auto", color: "#2DD4BF", width: 2, style: 0 },
  pressure: { show: "auto", color: "#10B981", width: 2, style: 0 },
  pressureSell: { show: "auto", color: "#F43F5E", width: 2, style: 0 },
  flow: { show: "auto", color: "#6EE7B7", width: 2, style: 2 },
  flowSell: { show: "auto", color: "#FDA4AF", width: 2, style: 2 },
  pivots: { show: "auto", color: "#EAB308", color2: "#94A3B8", width: 1, style: 2 },
};

const KEY = "sma.chart.style.v1";
const EVENT = "sma-chart-style";
const HEX = /^#[0-9a-fA-F]{6}$/;

function clean(raw: unknown): ChartStyle {
  const out: ChartStyle = JSON.parse(JSON.stringify(DEFAULT_CHART_STYLE));
  if (!raw || typeof raw !== "object") return out;
  const src = raw as Record<string, unknown>;
  for (const k of ["candleUp", "candleDown"] as const) {
    if (typeof src[k] === "string" && HEX.test(src[k] as string)) out[k] = src[k] as string;
  }
  out.heikinAshi = src.heikinAshi === true;
  out.tradeReads = src.tradeReads !== false;
  out.pressureOnCandles = src.pressureOnCandles === true;
  for (const key of Object.keys(LINE_NAMES) as LineKey[]) {
    const v = src[key] as Partial<LineLook> | undefined;
    if (!v || typeof v !== "object") continue;
    const line = out[key];
    if (v.show === "auto" || v.show === "on" || v.show === "off") line.show = v.show;
    if (typeof v.color === "string" && HEX.test(v.color)) line.color = v.color;
    if (typeof v.color2 === "string" && HEX.test(v.color2) && line.color2) line.color2 = v.color2;
    if (LINE_WIDTHS.includes(v.width as Width)) line.width = v.width as Width;
    if (LINE_PATTERNS.some((p) => p.value === v.style)) line.style = v.style as Pattern;
  }
  return out;
}

function load(): ChartStyle {
  try {
    return clean(JSON.parse(localStorage.getItem(KEY) || "null"));
  } catch {
    return clean(null);
  }
}

/** Whether a line is drawn: its own switch, or on "auto" whether the strategy uses it. */
export function shown(look: LineLook, auto: boolean): boolean {
  return look.show === "on" || (look.show === "auto" && auto);
}

export function useChartStyle(): [ChartStyle, (next: ChartStyle) => void, () => void] {
  const [style, setStyle] = useState<ChartStyle>(DEFAULT_CHART_STYLE);
  useEffect(() => {
    setStyle(load());
    const sync = () => setStyle(load());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const save = useCallback((next: ChartStyle) => {
    setStyle(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
      window.dispatchEvent(new Event(EVENT));
    } catch {
      /* private window: this page only */
    }
  }, []);
  const reset = useCallback(() => save(clean(null)), [save]);
  return [style, save, reset];
}

type Candle = { open: number; high: number; low: number; close: number };

/** Heikin Ashi candles (as backend/heikin_ashi.py): close = (O + H + L + C) / 4, open = the middle of the
 *  Heikin Ashi candle before (the first: (O + C) / 2), high / low stretched to cover both. Display only. */
export function heikinAshi<T extends Candle>(rows: T[]): T[] {
  let po: number | null = null;
  let pc: number | null = null;
  return rows.map((r) => {
    const close = (r.open + r.high + r.low + r.close) / 4;
    const open = po == null || pc == null ? (r.open + r.close) / 2 : (po + pc) / 2;
    if (Number.isFinite(open) && Number.isFinite(close)) {
      po = open;
      pc = close;
    }
    return { ...r, open, close, high: Math.max(r.high, open, close), low: Math.min(r.low, open, close) };
  });
}

const IST_OFFSET_SEC = 19_800;

/**
 * Cumulative delta, the same estimate as the bot (cum_delta.py): each candle's volume × (close − open) ÷ (high − low),
 * added up from the first candle of each IST day. A candle with no volume or no range adds 0.
 */
export function cvdValues(rows: { time: number; open: number; high: number; low: number; close: number; volume?: number | null }[]): number[] {
  let day = -1;
  let sum = 0;
  return rows.map((c) => {
    const d = Math.floor((c.time + IST_OFFSET_SEC) / 86_400);
    if (d !== day) {
      day = d;
      sum = 0;
    }
    const span = c.high - c.low;
    const vol = c.volume != null && Number.isFinite(c.volume) && c.volume > 0 ? c.volume : 0;
    if (span > 0) sum += (vol * Math.max(-1, Math.min(1, (c.close - c.open) / span)));
    return sum;
  });
}

/**
 * The buyers' share of the waiting orders (the Most active scan's reads, pressure_exit.series) put on the candles:
 * each candle carries the latest read taken before it closed (before the next candle's start), held until a new read,
 * never into the next IST day. Null before the day's first read. Buyers % = 100 − sellers %.
 */
export function pressureValues(rows: { time: number }[], points: { time: number; buy_pct: number }[] | null | undefined): (number | null)[] {
  return pressureReads(rows, points).map((r) => (r ? r.buy_pct : null));
}

/** The read behind each candle's buyers %: the latest point before the candle closed, held within the IST day
 * (`time` = when the Most active scan read that stock's order book, so the chart can say which scan it came from). */
export function pressureReads<P extends { time: number; buy_pct: number }>(
  rows: { time: number }[],
  points: P[] | null | undefined
): (P | null)[] {
  if (!points?.length) return rows.map(() => null);
  const dayOf = (t: number) => Math.floor((t + IST_OFFSET_SEC) / 86_400);
  let j = 0;
  let last: P | null = null;
  return rows.map((c, i) => {
    const end = i + 1 < rows.length ? rows[i + 1].time : Number.POSITIVE_INFINITY;
    while (j < points.length && points[j].time < end) last = points[j++];
    return last && dayOf(last.time) === dayOf(c.time) ? last : null;
  });
}

/** One minute's executed volume split, estimated by the server (trade_flow.py): "T" from the recorded seconds, "C" from
 * the candle's shape. */
export type FlowPoint = { time: number; buy: number; sell: number; src: "T" | "C" };

export type FlowRead = { buyPct: number; buy: number; sell: number; src: "ticks" | "candles" | "ticks + candles" };

/** Each candle's executed buy % (shares bought at market ÷ all traded in it, estimated): the minutes inside it added
 * up. Null for a candle with no volume known. Sell % = 100 − it. */
export function flowReads(rows: { time: number }[], points: FlowPoint[] | null | undefined, minutes = 1): (FlowRead | null)[] {
  if (!points?.length) return rows.map(() => null);
  let j = 0;
  return rows.map((c, i) => {
    const end = i + 1 < rows.length ? Math.min(rows[i + 1].time, c.time + minutes * 60) : c.time + minutes * 60;
    while (j < points.length && points[j].time < c.time) j += 1;
    let buy = 0;
    let sell = 0;
    const kinds = new Set<string>();
    for (let k = j; k < points.length && points[k].time < end; k += 1) {
      buy += points[k].buy;
      sell += points[k].sell;
      kinds.add(points[k].src);
    }
    if (buy + sell <= 0) return null;
    const src = kinds.size > 1 ? "ticks + candles" : kinds.has("T") ? "ticks" : "candles";
    return { buyPct: (100 * buy) / (buy + sell), buy, sell, src };
  });
}

/** EMA of the closes (`ewm(adjust=False)`, as the bot); null until `n` candles. */
export function emaValues(closes: number[], n: number): (number | null)[] {
  const k = 2 / (n + 1);
  let e: number | null = null;
  return closes.map((c, i) => {
    e = e == null ? c : e + k * (c - e);
    return i + 1 >= n ? e : null;
  });
}
