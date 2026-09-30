"use client";

import { useEffect, useRef, useState } from "react";
import { ColorType, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi } from "lightweight-charts";
import { inr, px, type ChartPayload, type SmaState } from "@/lib/smaApi";

type Props = {
  chart: ChartPayload | null;
  state: SmaState | null;
  closing: boolean;
  onClose: () => void;
};

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

export function StrategyChart({ chart, state, closing, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const smaFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const smaSlowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const atrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slLine = useRef<IPriceLine | null>(null);
  const entryLine = useRef<IPriceLine | null>(null);
  const pnlGrossRef = useRef<number | null>(null);
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
      const look = entryLook(pnlGrossRef.current);
      entryLine.current = candleRef.current.createPriceLine({
        price: chart.entry_price,
        color: look.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: look.title,
      });
    }
  }, [chart]);

  const pnl = livePnl(state);
  const pos = state?.position;
  const pnlGross = pnl?.gross ?? null;
  pnlGrossRef.current = pnlGross;
  useEffect(() => {
    if (!entryLine.current) return;
    entryLine.current.applyOptions(entryLook(pnlGross));
  }, [pnlGross]);

  const latest = latestSma(chart);
  const sma9 = hover ? hover.sma9 : latest.sma9;
  const sma21 = hover ? hover.sma21 : latest.sma21;

  return (
    <section className="min-w-0 max-w-full overflow-hidden rounded-xl border border-white/5 bg-[#151921]">
      <div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-4">
        <h2 className="text-sm font-medium text-slate-200">1-minute · SMA 9 / SMA 21 · ATR 14</h2>
        <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px]">
          <span className="text-[#F43F5E]">SMA 9 {px(sma9)}</span>
          <span className="text-[#3B82F6]">SMA 21 {px(sma21)}</span>
          <span className="text-[#A78BFA]">ATR</span>
          <span className="text-[#F59E0B]">Stop</span>
        </div>
      </div>
      <div className="relative">
        <div ref={rootRef} className="h-[320px] w-full sm:h-[460px] lg:h-[520px]" />
        {pos && pnl && (
          <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[240px] rounded-lg border border-white/10 bg-[#0B0E14]/90 p-2.5 shadow-lg">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">
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
              className="pointer-events-auto mt-2 w-full rounded-md border border-[#F43F5E]/50 bg-[#F43F5E]/15 px-2 py-1.5 text-xs font-semibold text-[#fda4af] hover:bg-[#F43F5E]/25 disabled:opacity-50"
            >
              {closing ? "Closing…" : "Close position"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
