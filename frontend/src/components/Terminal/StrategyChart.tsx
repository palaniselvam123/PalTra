"use client";

import { useEffect, useRef, useState } from "react";
import { ColorType, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi } from "lightweight-charts";
import { px, type ChartPayload } from "@/lib/smaApi";

type Props = { chart: ChartPayload | null };

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

function lineValue(row: unknown): number | null {
  if (row && typeof row === "object" && "value" in row && typeof row.value === "number") return row.value;
  return null;
}

export function StrategyChart({ chart }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const smaFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const smaSlowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const atrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slLine = useRef<IPriceLine | null>(null);
  const entryLine = useRef<IPriceLine | null>(null);
  const [hover, setHover] = useState<SmaHover | null>(null);

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
        tickMarkFormatter: (time: unknown) => istClock(time),
      },
      localization: {
        timeFormatter: (time: unknown) => {
          const clock = istClock(time);
          return clock ? `${clock} IST` : "";
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
    if (!chart || !candleRef.current || !smaFastRef.current || !smaSlowRef.current || !atrRef.current) return;
    const rows = chart.candles;
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
    candleRef.current.setMarkers(
      chart.markers.map((m) => ({
        time: m.time as never,
        position: m.direction === "LONG" ? "belowBar" : "aboveBar",
        color: m.direction === "LONG" ? "#10B981" : "#F43F5E",
        shape: m.direction === "LONG" ? "arrowUp" : "arrowDown",
        text: m.direction === "LONG" ? "BUY" : "SELL",
      }))
    );

    if (slLine.current) {
      candleRef.current.removePriceLine(slLine.current);
      slLine.current = null;
    }
    if (entryLine.current) {
      candleRef.current.removePriceLine(entryLine.current);
      entryLine.current = null;
    }
    if (chart.sl_trigger) {
      slLine.current = candleRef.current.createPriceLine({
        price: chart.sl_trigger,
        color: "#F59E0B",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: "1.5× ATR SL",
      });
    }
    if (chart.entry_price) {
      entryLine.current = candleRef.current.createPriceLine({
        price: chart.entry_price,
        color: "#94a3b8",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: "Entry",
      });
    }
  }, [chart]);

  const latest = latestSma(chart);
  const sma9 = hover ? hover.sma9 : latest.sma9;
  const sma21 = hover ? hover.sma21 : latest.sma21;

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921]">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <h2 className="text-sm font-medium text-slate-200">1-minute · SMA 9 / SMA 21 · ATR 14</h2>
        <div className="flex flex-wrap justify-end gap-3 font-mono text-[11px]">
          <span className="text-[#F43F5E]">SMA 9 {px(sma9)}</span>
          <span className="text-[#3B82F6]">SMA 21 {px(sma21)}</span>
          <span className="text-[#A78BFA]">ATR</span>
          <span className="text-[#F59E0B]">Stop</span>
        </div>
      </div>
      <div ref={rootRef} className="h-[520px] w-full" />
    </section>
  );
}
