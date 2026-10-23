"use client";

import { ColorType, createChart, type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { UsChart as Chart } from "@/lib/api";

/** Candles with SMA fast / slow and the desk's practice trades. Times arrive as New York wall clock. */
export function UsChart({ chart }: { chart: Chart | null }) {
  const root = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; candles: ISeriesApi<"Candlestick">; fast: ISeriesApi<"Line">; slow: ISeriesApi<"Line"> } | null>(null);
  const fitted = useRef<string | null>(null);

  useEffect(() => {
    if (!root.current) return;
    const instance = createChart(root.current, {
      height: 360,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(255,255,255,0.1)" },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      autoSize: true,
    });
    const candles = instance.addCandlestickSeries({
      upColor: "#10B981",
      downColor: "#EF4444",
      borderVisible: false,
      wickUpColor: "#10B981",
      wickDownColor: "#EF4444",
    });
    const fast = instance.addLineSeries({ color: "#38BDF8", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const slow = instance.addLineSeries({ color: "#F59E0B", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    api.current = { chart: instance, candles, fast, slow };
    return () => {
      instance.remove();
      api.current = null;
      fitted.current = null;
    };
  }, []);

  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const rows = chart?.candles ?? [];
    a.candles.setData(rows.map((r) => ({ time: r.time as UTCTimestamp, open: r.open, high: r.high, low: r.low, close: r.close })));
    a.fast.setData(rows.filter((r) => r.fast != null).map((r) => ({ time: r.time as UTCTimestamp, value: r.fast as number })));
    a.slow.setData(rows.filter((r) => r.slow != null).map((r) => ({ time: r.time as UTCTimestamp, value: r.slow as number })));
    const span = (chart?.candle_minutes ?? 1) * 60;
    const first = rows[0]?.time ?? 0;
    const markers: SeriesMarker<Time>[] = (chart?.markers ?? [])
      .filter((m) => m.time >= first)
      .map((m) => {
        const buy = (m.kind === "entry") === (m.side === "LONG");
        return {
          time: (m.time - (m.time % span)) as UTCTimestamp,
          position: buy ? "belowBar" : "aboveBar",
          color: m.kind === "entry" ? (buy ? "#10B981" : "#EF4444") : "#CBD5E1",
          shape: buy ? "arrowUp" : "arrowDown",
          text:
            m.kind === "entry"
              ? `${m.side === "LONG" ? "Buy" : "Short"} ${m.price ?? ""}`
              : `${m.reason ?? "Exit"}${m.pnl != null ? ` ${m.pnl >= 0 ? "+" : "−"}$${Math.abs(m.pnl).toFixed(2)}` : ""}`,
        } as SeriesMarker<Time>;
      })
      .sort((x, y) => (x.time as number) - (y.time as number));
    a.candles.setMarkers(markers);
    const key = `${chart?.symbol}-${chart?.candle_minutes}`;
    if (rows.length && fitted.current !== key) {
      a.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 120), to: rows.length + 3 });
      fitted.current = key;
    }
  }, [chart]);

  return <div ref={root} className="h-[360px] w-full" aria-label={chart ? `${chart.symbol} chart` : "Chart"} />;
}
