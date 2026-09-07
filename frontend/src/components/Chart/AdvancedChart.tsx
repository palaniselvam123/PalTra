"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api, type CandlePattern, type ChartMarker, type ChartPayload, type IndicatorSeries } from "@/lib/api";
import type { Tick } from "@/hooks/useTradingState";
import { useTheme } from "@/hooks/useTheme";

const INTERVAL_SECONDS: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400 };
const INTERVAL_LIST = Object.keys(INTERVAL_SECONDS);

/** Overlays draw on the price pane; oscillators need their own pane because
 *  their scale (0-100, or a MACD delta near zero) has nothing to do with the
 *  price axis — forcing them onto it would flatten the candles into a line. */
const OVERLAYS = [
  { id: "ema_fast", label: "EMA 9", color: "#38bdf8" },
  { id: "ema_slow", label: "EMA 21", color: "#a78bfa" },
  { id: "vwap", label: "VWAP", color: "#fbbf24" },
  { id: "bollinger", label: "Bollinger", color: "#64748b" },
  { id: "supertrend", label: "Supertrend", color: "#22d3ee" },
];

const OSCILLATORS = [
  { id: "rsi", label: "RSI" },
  { id: "macd", label: "MACD" },
  { id: "adx", label: "ADX" },
];

/** lightweight-charts paints onto its own canvas, so CSS variables cannot
 *  reach it — axis, grid and label colours have to be passed in explicitly and
 *  re-applied when the theme changes. */
function baseOptions(theme: "dark" | "light") {
  const light = theme === "light";
  return {
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: light ? "#5b6b80" : "#94a3b8",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: light ? "#e8edf4" : "#1e293b55" },
      horzLines: { color: light ? "#e8edf4" : "#1e293b55" },
    },
    rightPriceScale: { borderColor: light ? "#dbe2ec" : "#1e293b" },
    timeScale: { borderColor: light ? "#dbe2ec" : "#1e293b", timeVisible: true, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
  };
}

type Legend = { o: number; h: number; l: number; c: number; v: number; change: number } | null;

/** Pairs an indicator series with candle times, dropping the nulls that mark
 *  its warm-up. Feeding zeros instead would draw a fake line into the past. */
function toLine(times: number[], values?: IndicatorSeries) {
  if (!values) return [];
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (let i = 0; i < values.length && i < times.length; i++) {
    const v = values[i];
    if (v !== null && v !== undefined && Number.isFinite(v)) {
      out.push({ time: times[i] as UTCTimestamp, value: v });
    }
  }
  return out;
}

export function AdvancedChart({
  symbol,
  symbols,
  onSymbolChange,
  tick,
  stopLoss,
  target,
  height = 420,
}: {
  symbol: string;
  symbols: string[];
  onSymbolChange: (s: string) => void;
  tick?: Tick;
  stopLoss?: number;
  target?: number;
  height?: number;
}) {
  const { theme } = useTheme();
  const priceRef = useRef<HTMLDivElement>(null);
  const oscRef = useRef<HTMLDivElement>(null);
  const priceChart = useRef<IChartApi | null>(null);
  const oscChart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlaySeries = useRef<Record<string, ISeriesApi<"Line">>>({});
  const oscSeriesRef = useRef<Record<string, ISeriesApi<"Line"> | ISeriesApi<"Histogram">>>({});
  const priceLines = useRef<any[]>([]);
  const lastBar = useRef<{ time: number; open: number; high: number; low: number; close: number } | null>(null);

  const [interval, setIntervalValue] = useState("5m");
  const [active, setActive] = useState<string[]>(["ema_fast", "vwap"]);
  const [osc, setOsc] = useState<string | null>("rsi");
  const [payload, setPayload] = useState<ChartPayload | null>(null);
  const [markers, setMarkers] = useState<ChartMarker[]>([]);
  const [patterns, setPatterns] = useState<CandlePattern[]>([]);
  const [showPatterns, setShowPatterns] = useState(false);
  const [loading, setLoading] = useState(true);
  const [legend, setLegend] = useState<Legend>(null);

  const activeKey = useMemo(() => [...active].sort().join(","), [active]);

  // ---- build the price chart once ---------------------------------------
  useEffect(() => {
    if (!priceRef.current) return;
    const chart = createChart(priceRef.current, {
      ...baseOptions(theme),
      width: priceRef.current.clientWidth,
      height,
    });
    const candles = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });
    // Volume shares the price pane but gets its own scale pinned to the
    // bottom fifth, so it reads as a footer rather than competing with price.
    const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    priceChart.current = chart;
    candleSeries.current = candles;
    volumeSeries.current = volume;

    chart.subscribeCrosshairMove((param) => {
      const bar = param.seriesData.get(candles) as any;
      const vol = param.seriesData.get(volume) as any;
      if (!bar) {
        setLegend(null);
        return;
      }
      setLegend({
        o: bar.open,
        h: bar.high,
        l: bar.low,
        c: bar.close,
        v: vol?.value ?? 0,
        change: bar.open ? ((bar.close - bar.open) / bar.open) * 100 : 0,
      });
    });

    const resize = () => {
      if (priceRef.current) chart.applyOptions({ width: priceRef.current.clientWidth });
      if (oscRef.current && oscChart.current) oscChart.current.applyOptions({ width: oscRef.current.clientWidth });
    };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      priceChart.current = null;
      candleSeries.current = null;
      volumeSeries.current = null;
      overlaySeries.current = {};
      priceLines.current = [];
    };
  }, [height, theme]);

  // ---- oscillator pane, created and destroyed with the selection --------
  useEffect(() => {
    if (!osc || !oscRef.current) return;

    const opts = baseOptions(theme);
    const chart = createChart(oscRef.current, {
      ...opts,
      width: oscRef.current.clientWidth,
      height: 130,
      timeScale: { ...opts.timeScale, visible: false },
    });
    oscChart.current = chart;
    oscSeriesRef.current = {};

    // Two panes only feel like one chart if their time axes move together.
    const main = priceChart.current;
    let syncing = false;
    const fromMain = (range: any) => {
      if (syncing || !range) return;
      syncing = true;
      chart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    };
    const fromOsc = (range: any) => {
      if (syncing || !range || !main) return;
      syncing = true;
      main.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    };
    main?.timeScale().subscribeVisibleLogicalRangeChange(fromMain);
    chart.timeScale().subscribeVisibleLogicalRangeChange(fromOsc);

    return () => {
      main?.timeScale().unsubscribeVisibleLogicalRangeChange(fromMain);
      chart.remove();
      oscChart.current = null;
      oscSeriesRef.current = {};
    };
  }, [osc, theme]);

  // ---- load candles, indicators and markers -----------------------------
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const names = [...active];
      if (osc) names.push(osc);
      const [data, marks, pats] = await Promise.all([
        api.chartCandles(symbol, interval, names, 500),
        api.chartMarkers(symbol).catch(() => [] as ChartMarker[]),
        showPatterns
          ? api.chartPatterns(symbol, interval).then((r) => r.patterns).catch(() => [] as CandlePattern[])
          : Promise.resolve([] as CandlePattern[]),
      ]);
      setPayload(data);
      setMarkers(marks);
      setPatterns(pats);
    } catch {
      setPayload(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, interval, activeKey, osc, showPatterns]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load();
  }, [load]);

  // ---- draw price data ---------------------------------------------------
  useEffect(() => {
    const candles = candleSeries.current;
    const volume = volumeSeries.current;
    const chart = priceChart.current;
    if (!candles || !volume || !chart || !payload) return;

    const times = payload.candles.map((c) => c.time);
    candles.setData(
      payload.candles.map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    volume.setData(
      payload.candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "#10b98133" : "#f43f5e33",
      }))
    );
    const last = payload.candles[payload.candles.length - 1];
    lastBar.current = last
      ? { time: last.time, open: last.open, high: last.high, low: last.low, close: last.close }
      : null;

    // Rebuild overlays from scratch — cheaper and far less error-prone than
    // diffing which indicator was toggled off.
    Object.values(overlaySeries.current).forEach((s) => chart.removeSeries(s));
    overlaySeries.current = {};

    const addLine = (key: string, data: any[], color: string, width = 2, dashed = false) => {
      if (!data.length) return;
      const s = chart.addLineSeries({
        color,
        lineWidth: width as any,
        priceLineVisible: false,
        lastValueVisible: false,
        lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
        crosshairMarkerVisible: false,
      });
      s.setData(data);
      overlaySeries.current[key] = s;
    };

    const ind = payload.indicators;
    if (active.includes("ema_fast")) addLine("ema_fast", toLine(times, ind.ema_fast), "#38bdf8");
    if (active.includes("ema_slow")) addLine("ema_slow", toLine(times, ind.ema_slow), "#a78bfa");
    if (active.includes("vwap")) addLine("vwap", toLine(times, ind.vwap), "#fbbf24", 2, true);
    if (active.includes("bollinger") && ind.bollinger) {
      addLine("bb_u", toLine(times, ind.bollinger.upper), "#64748b", 1);
      addLine("bb_m", toLine(times, ind.bollinger.middle), "#475569", 1, true);
      addLine("bb_l", toLine(times, ind.bollinger.lower), "#64748b", 1);
    }
    if (active.includes("supertrend") && ind.supertrend) {
      addLine("st", toLine(times, ind.supertrend.value), "#22d3ee", 2);
    }

    // Markers snap to the bar that contains them; a timestamp falling between
    // bars is silently dropped by the library.
    const step = INTERVAL_SECONDS[interval];
    const first = times[0] ?? 0;
    const lastT = times[times.length - 1] ?? 0;
    const snapped: SeriesMarker<UTCTimestamp>[] = markers
      .map((m) => ({ m, bucket: Math.floor(m.time / step) * step }))
      .filter(({ bucket }) => bucket >= first && bucket <= lastT)
      .map(({ m, bucket }) => ({
        time: bucket as UTCTimestamp,
        position: (m.side === "BUY" ? "belowBar" : "aboveBar") as any,
        shape: (m.side === "BUY" ? "arrowUp" : "arrowDown") as any,
        color: m.kind === "ENTRY" ? (m.side === "BUY" ? "#10b981" : "#f43f5e") : "#94a3b8",
        text:
          m.kind === "ENTRY"
            ? `${m.side} ${m.quantity} @${m.price}`
            : `EXIT ${m.pnl !== null ? (m.pnl >= 0 ? "+" : "") + m.pnl.toFixed(0) : ""}`,
      }));

    const patternMarkers: SeriesMarker<UTCTimestamp>[] = patterns
      .map((p) => ({ p, bucket: Math.floor(p.time / step) * step }))
      .filter(({ bucket }) => bucket >= first && bucket <= lastT)
      .map(({ p, bucket }) => ({
        time: bucket as UTCTimestamp,
        position: (p.bias === "BULLISH" ? "belowBar" : "aboveBar") as any,
        shape: "circle" as any,
        color: p.bias === "BULLISH" ? "#22d3ee" : p.bias === "BEARISH" ? "#f472b6" : "#64748b",
        text: p.label,
      }));

    // The library requires markers in ascending time order; merging two
    // independently-sorted lists without re-sorting silently drops some.
    candles.setMarkers(
      [...snapped, ...patternMarkers].sort((a, b) => (a.time as number) - (b.time as number))
    );

    priceLines.current.forEach((l) => candles.removePriceLine(l));
    priceLines.current = [];
    if (stopLoss) {
      priceLines.current.push(
        candles.createPriceLine({
          price: stopLoss,
          color: "#f43f5e",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: "SL",
          axisLabelVisible: true,
        })
      );
    }
    if (target) {
      priceLines.current.push(
        candles.createPriceLine({
          price: target,
          color: "#10b981",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: "TGT",
          axisLabelVisible: true,
        })
      );
    }

    chart.timeScale().fitContent();
  }, [payload, markers, patterns, active, interval, stopLoss, target]);

  // ---- draw oscillator data ----------------------------------------------
  useEffect(() => {
    const chart = oscChart.current;
    if (!chart || !payload || !osc) return;
    Object.values(oscSeriesRef.current).forEach((s) => chart.removeSeries(s as any));
    oscSeriesRef.current = {};

    const times = payload.candles.map((c) => c.time);
    const line = (key: string, data: any[], color: string, width = 2) => {
      if (!data.length) return;
      const s = chart.addLineSeries({
        color,
        lineWidth: width as any,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      s.setData(data);
      oscSeriesRef.current[key] = s;
    };

    const ind = payload.indicators;
    if (osc === "rsi" && ind.rsi) {
      line("rsi", toLine(times, ind.rsi), "#38bdf8");
      const s = oscSeriesRef.current["rsi"] as ISeriesApi<"Line"> | undefined;
      s?.createPriceLine({ price: 70, color: "#f43f5e66", lineWidth: 1, lineStyle: LineStyle.Dashed, title: "70" });
      s?.createPriceLine({ price: 30, color: "#10b98166", lineWidth: 1, lineStyle: LineStyle.Dashed, title: "30" });
    }
    if (osc === "macd" && ind.macd) {
      const hist = chart.addHistogramSeries({ priceLineVisible: false });
      hist.setData(
        toLine(times, ind.macd.histogram).map((p) => ({
          time: p.time,
          value: p.value,
          color: p.value >= 0 ? "#10b98188" : "#f43f5e88",
        }))
      );
      oscSeriesRef.current["hist"] = hist;
      line("macd", toLine(times, ind.macd.macd), "#38bdf8");
      line("signal", toLine(times, ind.macd.signal), "#fbbf24", 1);
    }
    if (osc === "adx" && ind.adx) {
      line("adx", toLine(times, ind.adx.adx), "#e2e8f0");
      line("plus_di", toLine(times, ind.adx.plus_di), "#10b981", 1);
      line("minus_di", toLine(times, ind.adx.minus_di), "#f43f5e", 1);
      const s = oscSeriesRef.current["adx"] as ISeriesApi<"Line"> | undefined;
      // 20 is the bot's own trend threshold — drawing it makes the ADX entry
      // filter legible instead of an invisible rule.
      s?.createPriceLine({ price: 20, color: "#fbbf2466", lineWidth: 1, lineStyle: LineStyle.Dashed, title: "20" });
    }
  }, [payload, osc]);

  // ---- live tick folds into the forming bar ------------------------------
  useEffect(() => {
    const candles = candleSeries.current;
    if (!candles || !tick || !payload || tick.symbol !== symbol) return;
    const step = INTERVAL_SECONDS[interval];
    const bucket = Math.floor(Date.now() / 1000 / step) * step;
    const cur = lastBar.current;

    if (!cur || bucket > cur.time) {
      lastBar.current = { time: bucket, open: tick.ltp, high: tick.ltp, low: tick.ltp, close: tick.ltp };
      candles.update({
        time: bucket as UTCTimestamp,
        open: tick.ltp,
        high: tick.ltp,
        low: tick.ltp,
        close: tick.ltp,
      });
    } else if (bucket === cur.time) {
      cur.high = Math.max(cur.high, tick.ltp);
      cur.low = Math.min(cur.low, tick.ltp);
      cur.close = tick.ltp;
      candles.update({
        time: cur.time as UTCTimestamp,
        open: cur.open,
        high: cur.high,
        low: cur.low,
        close: cur.close,
      });
    }
  }, [tick, symbol, interval, payload]);

  const toggle = (id: string) => setActive((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const bars = payload?.candles.length ?? 0;

  return (
    <div className="overflow-hidden rounded-card border border-border bg-surface">
      {/* Toolbar: four labelled groups separated by rules, on a single
          non-wrapping scroll line. Previously every control sat in one
          flex-wrap row at three different font sizes, so timeframes, overlays
          and the oscillator picker ran together into an undifferentiated
          strip that reflowed unpredictably as the window narrowed. */}
      <div className="flex items-center gap-2.5 overflow-x-auto border-b border-border px-3 py-2">
        <select
          value={symbol}
          onChange={(e) => onSymbolChange(e.target.value)}
          className="shrink-0 rounded-md border border-border bg-base px-2 py-1 text-body font-semibold text-slate-100 focus:border-bot/60 focus:outline-none"
        >
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <span className="h-5 w-px shrink-0 bg-border" />

        <div className="flex shrink-0 overflow-hidden rounded-md border border-border">
          {INTERVAL_LIST.map((iv) => (
            <button
              key={iv}
              onClick={() => setIntervalValue(iv)}
              className={clsx(
                "px-2 py-1 text-caption transition-colors",
                interval === iv ? "bg-bot/15 font-medium text-bot" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {iv}
            </button>
          ))}
        </div>

        <span className="h-5 w-px shrink-0 bg-border" />

        <div className="flex shrink-0 items-center gap-1.5">
          <span className="text-caption uppercase text-slate-500">Overlay</span>
          <div className="flex items-center gap-1">
            {OVERLAYS.map((o) => (
              <button
                key={o.id}
                onClick={() => toggle(o.id)}
                className={clsx(
                  "rounded border px-1.5 py-0.5 text-caption transition-colors",
                  active.includes(o.id)
                    ? "border-transparent font-medium text-slate-900"
                    : "border-border text-slate-500 hover:text-slate-300"
                )}
                style={active.includes(o.id) ? { backgroundColor: o.color } : undefined}
              >
                {o.label}
              </button>
            ))}
            <button
              onClick={() => setShowPatterns((v) => !v)}
              title="Mark candlestick patterns (hammer, engulfing, doji…) detected on closed bars"
              className={clsx(
                "rounded border px-1.5 py-0.5 text-caption transition-colors",
                showPatterns
                  ? "border-transparent bg-violet-400 font-medium text-slate-900"
                  : "border-border text-slate-500 hover:text-slate-300"
              )}
            >
              Patterns
            </button>
          </div>
        </div>

        <span className="h-5 w-px shrink-0 bg-border" />

        <div className="ml-auto flex shrink-0 items-center gap-1.5 pl-1">
          <span className="text-caption uppercase text-slate-500">Lower</span>
          <div className="flex overflow-hidden rounded-md border border-border">
            <button
              onClick={() => setOsc(null)}
              className={clsx(
                "px-2 py-1 text-caption transition-colors",
                osc === null ? "bg-bot/15 font-medium text-bot" : "text-slate-500 hover:text-slate-300"
              )}
            >
              None
            </button>
            {OSCILLATORS.map((o) => (
              <button
                key={o.id}
                onClick={() => setOsc(o.id)}
                className={clsx(
                  "px-2 py-1 text-caption transition-colors",
                  osc === o.id ? "bg-bot/15 font-medium text-bot" : "text-slate-500 hover:text-slate-300"
                )}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 px-3 py-1.5 text-[11px] font-mono border-b border-border/60 min-h-[28px]">
        <span className="text-slate-200 font-sans font-medium">{symbol}</span>
        <span className="text-slate-600">{interval}</span>
        {legend ? (
          <>
            <span className="text-slate-500">
              O<span className="text-slate-300 ml-0.5">{legend.o.toFixed(2)}</span>
            </span>
            <span className="text-slate-500">
              H<span className="text-slate-300 ml-0.5">{legend.h.toFixed(2)}</span>
            </span>
            <span className="text-slate-500">
              L<span className="text-slate-300 ml-0.5">{legend.l.toFixed(2)}</span>
            </span>
            <span className="text-slate-500">
              C<span className="text-slate-300 ml-0.5">{legend.c.toFixed(2)}</span>
            </span>
            <span className={legend.change >= 0 ? "text-profit" : "text-loss"}>
              {legend.change >= 0 ? "+" : ""}
              {legend.change.toFixed(2)}%
            </span>
            <span className="text-slate-600">Vol {legend.v.toLocaleString("en-IN")}</span>
          </>
        ) : (
          <span className="text-slate-600">hover for OHLC · {bars} bars</span>
        )}
        {loading && <Loader2 size={11} className="animate-spin text-slate-600 ml-auto" />}
      </div>

      {payload?.note && (
        <div className="flex items-start gap-1.5 px-3 py-1.5 bg-amber-500/10 border-b border-amber-500/25 text-[11px] text-amber-300">
          <AlertTriangle size={12} className="shrink-0 mt-0.5" />
          <span>{payload.note}</span>
        </div>
      )}

      <div ref={priceRef} />
      {osc && <div ref={oscRef} className="border-t border-border/60" />}
    </div>
  );
}
