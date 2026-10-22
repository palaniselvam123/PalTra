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

export type LineKey = "sma9" | "sma21" | "vwap" | "ema" | "bb" | "st" | "atr" | "rsi";

export type ChartStyle = Record<LineKey, LineLook> & { candleUp: string; candleDown: string };

export const LINE_NAMES: Record<LineKey, { name: string; auto: string; color2?: string }> = {
  sma9: { name: "SMA fast (9)", auto: "always" },
  sma21: { name: "SMA slow (21)", auto: "always" },
  vwap: { name: "VWAP", auto: "while the VWAP check is ticked" },
  ema: { name: "EMA fast / slow", auto: "while the EMA check or exit is on", color2: "slow" },
  bb: { name: "Bollinger bands", auto: "while the Bollinger check or exit is on" },
  st: { name: "Supertrend", auto: "while the Supertrend check or exit is on", color2: "down" },
  atr: { name: "ATR 14 (strip)", auto: "while the stop type is ATR" },
  rsi: { name: "RSI 14 (strip)", auto: "while the RSI check is ticked" },
};

export const DEFAULT_CHART_STYLE: ChartStyle = {
  candleUp: "#10B981",
  candleDown: "#F43F5E",
  sma9: { show: "on", color: "#F43F5E", width: 2, style: 0 },
  sma21: { show: "on", color: "#3B82F6", width: 2, style: 0 },
  vwap: { show: "auto", color: "#22D3EE", width: 2, style: 1 },
  ema: { show: "auto", color: "#F59E0B", color2: "#10B981", width: 2, style: 1 },
  bb: { show: "auto", color: "#C4B5FD", width: 1, style: 2 },
  st: { show: "auto", color: "#16A34A", color2: "#DC2626", width: 2, style: 0 },
  atr: { show: "auto", color: "#A78BFA", width: 2, style: 0 },
  rsi: { show: "auto", color: "#F472B6", width: 2, style: 0 },
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

/** EMA of the closes (`ewm(adjust=False)`, as the bot); null until `n` candles. */
export function emaValues(closes: number[], n: number): (number | null)[] {
  const k = 2 / (n + 1);
  let e: number | null = null;
  return closes.map((c, i) => {
    e = e == null ? c : e + k * (c - e);
    return i + 1 >= n ? e : null;
  });
}
