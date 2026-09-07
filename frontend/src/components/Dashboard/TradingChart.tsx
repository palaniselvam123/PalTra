"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Tick } from "@/hooks/useTradingState";

type Props = {
  symbol: string;
  tick?: Tick;
  stopLoss?: number;
  target?: number;
};

const CANDLE_INTERVAL_SEC = 5;

export function TradingChart({ symbol, tick, stopLoss, target }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const currentCandleRef = useRef<{ time: number; open: number; high: number; low: number; close: number } | null>(null);
  const slLineRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]> | null>(null);
  const targetLineRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      width: containerRef.current.clientWidth,
      height: 360,
      timeScale: { timeVisible: true, secondsVisible: true },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    currentCandleRef.current = null;

    const resize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [symbol]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !tick) return;

    const bucket = (Math.floor(Date.now() / 1000 / CANDLE_INTERVAL_SEC) * CANDLE_INTERVAL_SEC) as UTCTimestamp;
    const current = currentCandleRef.current;

    if (!current || current.time !== bucket) {
      const next = { time: bucket, open: tick.ltp, high: tick.ltp, low: tick.ltp, close: tick.ltp };
      currentCandleRef.current = next;
      series.update({ time: bucket, open: next.open, high: next.high, low: next.low, close: next.close });
    } else {
      current.high = Math.max(current.high, tick.ltp);
      current.low = Math.min(current.low, tick.ltp);
      current.close = tick.ltp;
      series.update({ time: current.time as UTCTimestamp, open: current.open, high: current.high, low: current.low, close: current.close });
    }
  }, [tick]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (slLineRef.current) series.removePriceLine(slLineRef.current);
    if (stopLoss) {
      slLineRef.current = series.createPriceLine({
        price: stopLoss,
        color: "#f43f5e",
        lineWidth: 1,
        lineStyle: 2,
        title: "SL",
      });
    }
  }, [stopLoss]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (targetLineRef.current) series.removePriceLine(targetLineRef.current);
    if (target) {
      targetLineRef.current = series.createPriceLine({
        price: target,
        color: "#10b981",
        lineWidth: 1,
        lineStyle: 2,
        title: "Target",
      });
    }
  }, [target]);

  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-slate-200">{symbol}</span>
        {tick && <span className="font-mono text-sm text-bot">{tick.ltp.toFixed(2)}</span>}
      </div>
      <div ref={containerRef} />
    </div>
  );
}
