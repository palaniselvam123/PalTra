"use client";

/**
 * Compare page, Stock by stock: one candle chart of the picked stock and day with every compared run's entries and
 * exits on it, one colour per run (CSS `--s1`…`--s8`, the same as the rest of the page). Display only: candles come
 * from GET /api/history (read-only), the trades from the comparison already loaded. Nothing is changed or ordered.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { Loader2 } from "lucide-react";
import { ColorType, LineStyle, createChart, type IChartApi, type ISeriesApi, type SeriesMarker, type Time } from "lightweight-charts";
import { NewTag } from "@/components/ui/NewTag";
import { heikinAshi, shown, useChartStyle } from "@/lib/chartStyle";
import { runName } from "@/lib/liveBook";
import { smaApi, type Candle, type CmpTrade } from "@/lib/smaApi";

export const CANDLE_SIZES = [1, 3, 5, 15] as const;

const LOOK = {
  dark: { background: "#151921", text: "#94a3b8", grid: "#1c2230" },
  light: { background: "#ffffff", text: "#475569", grid: "#e5eaf1" },
} as const;

const OPEN_MIN = 9 * 60 + 15;

function theme(): "dark" | "light" {
  return typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function clockOf(sec: number): string {
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(sec * 1000));
}

/** Start of the `bar`-minute candle holding `sec`, counted from the 09:15 IST open (as the chart groups them). */
export function candleStart(sec: number, bar: number): number {
  const minute = sec - (sec % 60);
  if (bar <= 1) return minute;
  const ist = Math.floor(((minute + 19_800) % 86_400) / 60);
  const offset = (((ist - OPEN_MIN) % bar) + bar) % bar;
  return minute - offset * 60;
}

export type CompareRun = { id: number; place: number };

/** The candle size a run traded on: the colour / pattern candles in those modes, else the bot's own candle. */
export function runCandle(settings: Record<string, unknown> | undefined): number {
  const mode = String(settings?.entry_mode ?? "SMA");
  const size = Number(mode === "COLOUR" || mode === "PATTERN" ? settings?.pattern_tf : settings?.candle_minutes);
  return CANDLE_SIZES.reduce((best, s) => (Math.abs(s - size) < Math.abs(best - size) ? s : best), 5 as number);
}

type Marker = { time: number; trade: CmpTrade; kind: "in" | "out" };

/** Markers of the ticked runs' trades, snapped to the candle that holds each entry / exit, sorted by time. */
export function compareMarkers(trades: CmpTrade[], hidden: Set<number>, bar: number): Marker[] {
  const out: Marker[] = [];
  for (const t of trades) {
    if (hidden.has(t.run_id)) continue;
    if (t.entry_ts) out.push({ time: candleStart(t.entry_ts, bar), trade: t, kind: "in" });
    if (t.exit_ts) out.push({ time: candleStart(t.exit_ts, bar), trade: t, kind: "out" });
  }
  const kind = (m: Marker) => (m.kind === "in" ? 0 : 1);
  return out.sort((a, b) => a.time - b.time || kind(a) - kind(b) || a.trade.run_id - b.trade.run_id);
}

const money = (v: number) => `${v < 0 ? "−" : "+"}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

export function CompareCandles({
  symbol,
  day,
  runs,
  trades,
  defaultSize,
  exitWord,
  entryWord,
}: {
  symbol: string;
  day: string;
  runs: CompareRun[];
  trades: CmpTrade[];
  defaultSize: number;
  exitWord: (code: string) => string;
  entryWord: (code: string) => string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const smaFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const smaSlowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapRef = useRef<ISeriesApi<"Line"> | null>(null);
  // The candles the view was last fitted to: a new stock, day or size fits again; a style change keeps the zoom.
  const fittedRef = useRef<Candle[] | null>(null);
  const [style] = useChartStyle();
  const [size, setSize] = useState<number>(defaultSize);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [colours, setColours] = useState<string[]>([]);
  const [mode, setMode] = useState<"dark" | "light">("dark");

  useEffect(() => setSize(defaultSize), [defaultSize]);

  // Page theme and the run colours (the canvas cannot read CSS variables, so they are read once from the page).
  useEffect(() => {
    const read = () => {
      setMode(theme());
      const host = hostRef.current;
      if (!host) return;
      const css = getComputedStyle(host);
      setColours(Array.from({ length: 8 }, (_, i) => css.getPropertyValue(`--s${i + 1}`).trim() || "#94a3b8"));
    };
    read();
    const watch = new MutationObserver(read);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => watch.disconnect();
  }, []);

  // Candles for the day (the first backtest of the comparison names the run; its trades are drawn by us, not by it).
  useEffect(() => {
    if (!symbol || !day) return;
    let stop = false;
    setBusy(true);
    setError("");
    const runId = runs.find((r) => r.id > 0)?.id ?? null;
    smaApi
      .history(symbol, `${day}T09:15`, `${day}T15:30`, size, runId)
      .then((d) => {
        if (stop) return;
        const lo = Date.parse(`${day}T09:15:00+05:30`) / 1000;
        const hi = Date.parse(`${day}T15:30:00+05:30`) / 1000;
        setCandles((d.candles ?? []).filter((c) => c.time >= lo && c.time < hi));
      })
      .catch((e) => !stop && (setCandles([]), setError(e instanceof Error ? e.message : "Could not load the candles.")))
      .finally(() => !stop && setBusy(false));
    return () => {
      stop = true;
    };
    // runs only names a run for the candle payload; the candles are the same for any of them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, day, size]);

  // The chart itself, made once per theme.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const look = LOOK[mode];
    const chart = createChart(host, {
      layout: { background: { type: ColorType.Solid, color: look.background }, textColor: look.text, fontSize: 12 },
      grid: { vertLines: { color: look.grid }, horzLines: { color: look.grid } },
      rightPriceScale: { borderColor: look.grid },
      timeScale: { borderColor: look.grid, timeVisible: true, secondsVisible: false, tickMarkFormatter: (t: unknown) => clockOf(Number(t)) },
      localization: { timeFormatter: (t: unknown) => `${clockOf(Number(t))} IST` },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    candleRef.current = chart.addCandlestickSeries({});
    smaFastRef.current = chart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    smaSlowRef.current = chart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    vwapRef.current = chart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    chart.subscribeCrosshairMove((p) => setHover(p.time == null ? null : Number(p.time)));
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      fittedRef.current = null;
      candleRef.current = null;
    };
  }, [mode]);

  const markers = useMemo(() => compareMarkers(trades, hidden, size), [trades, hidden, size]);
  const placeOf = useMemo(() => new Map(runs.map((r) => [r.id, r.place])), [runs]);

  // Candles, lines and markers.
  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    const rows = style.heikinAshi ? heikinAshi(candles) : candles;
    series.applyOptions({
      upColor: style.candleUp,
      downColor: style.candleDown,
      borderUpColor: style.candleUp,
      borderDownColor: style.candleDown,
      wickUpColor: style.candleUp,
      wickDownColor: style.candleDown,
    });
    series.setData(rows.map((c) => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close })));
    const line = (ref: ISeriesApi<"Line"> | null, on: boolean, look: { color: string; width: number; style: number }, pick: (c: Candle) => number | null | undefined) => {
      if (!ref) return;
      ref.applyOptions({ visible: on, color: look.color, lineWidth: look.width as 1 | 2 | 3 | 4, lineStyle: look.style as LineStyle });
      ref.setData(on ? candles.flatMap((c) => (pick(c) == null ? [] : [{ time: c.time as Time, value: pick(c) as number }])) : []);
    };
    line(smaFastRef.current, shown(style.sma9, true), style.sma9, (c) => c.sma9);
    line(smaSlowRef.current, shown(style.sma21, true), style.sma21, (c) => c.sma21);
    line(vwapRef.current, shown(style.vwap, false), style.vwap, (c) => c.vwap);
    const times = new Set(candles.map((c) => c.time));
    const marks: SeriesMarker<Time>[] = markers
      .filter((m) => times.has(m.time))
      .map((m) => {
        const colour = colours[(placeOf.get(m.trade.run_id) ?? 0) % 8] ?? "#94a3b8";
        const long = m.trade.side === "LONG";
        if (m.kind === "in")
          return { time: m.time as Time, position: long ? "belowBar" : "aboveBar", shape: long ? "arrowUp" : "arrowDown", color: colour, text: runName(m.trade.run_id) };
        return { time: m.time as Time, position: long ? "aboveBar" : "belowBar", shape: "circle", color: colour, text: money(m.trade.pnl) };
      });
    series.setMarkers(marks);
    if (fittedRef.current !== candles) {
      fittedRef.current = candles;
      chartRef.current?.timeScale().fitContent();
    }
  }, [candles, markers, colours, style, placeOf, mode]);

  const hovered = useMemo(() => (hover == null ? [] : markers.filter((m) => m.time === hover)), [hover, markers]);
  const bar = hover == null ? null : candles.find((c) => c.time === hover);
  const offChart = markers.filter((m) => candles.length && !candles.some((c) => c.time === m.time)).length;

  return (
    <div className="mb-3">
      <div className="cmp-noprint mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-slate-200">
          Candles with every run&apos;s trades <NewTag id="cmp-candles" />
        </span>
        <span role="group" aria-label="Candle size" className="inline-flex gap-1">
          {CANDLE_SIZES.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={s === size}
              onClick={() => setSize(s)}
              className={clsx("min-h-8 rounded-md border px-2", s === size ? "border-sky-400/60 bg-sky-500/10 font-semibold text-slate-100" : "border-white/10 text-slate-300 hover:bg-white/5")}
            >
              {s} min
            </button>
          ))}
        </span>
        <span role="group" aria-label="Runs shown" className="flex flex-wrap gap-x-3 gap-y-1">
          {runs.map((r) => (
            <label key={r.id} className="inline-flex min-h-8 items-center gap-1.5 text-slate-300">
              <input
                type="checkbox"
                checked={!hidden.has(r.id)}
                onChange={(e) =>
                  setHidden((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.delete(r.id);
                    else next.add(r.id);
                    return next;
                  })
                }
              />
              <span aria-hidden className="h-2.5 w-2.5 rounded-sm" style={{ background: `var(--s${(r.place % 8) + 1})` }} />
              {runName(r.id)}
            </label>
          ))}
        </span>
        {busy ? <Loader2 size={14} className="animate-spin text-slate-400" aria-label="Loading candles" /> : null}
      </div>
      <div ref={hostRef} className="h-[360px] w-full overflow-hidden rounded-lg border border-white/10" aria-label={`${symbol} candles on ${day} with each run's entries and exits`} />
      {error ? <p className="mt-1 text-xs text-rose-300">Candles not loaded: {error}</p> : null}
      <div className="mt-1 min-h-[2.5rem] text-xs text-slate-400" aria-live="polite">
        {hovered.length || bar ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {bar ? (
              <span className="font-mono text-slate-300">
                {clockOf(bar.time)} O {bar.open.toFixed(2)} H {bar.high.toFixed(2)} L {bar.low.toFixed(2)} C {bar.close.toFixed(2)}
              </span>
            ) : null}
            {hovered.map((m) => (
              <span key={`${m.kind}-${m.trade.id}`} className="inline-flex items-center gap-1">
                <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: `var(--s${((placeOf.get(m.trade.run_id) ?? 0) % 8) + 1})` }} />
                <span className="text-slate-200">{runName(m.trade.run_id)}</span>
                {m.kind === "in" ? (
                  <span>
                    {m.trade.side === "LONG" ? "bought" : "sold"} {m.trade.entry_at.slice(0, 5)} at ₹{m.trade.entry_price} · {entryWord(m.trade.entry_reason)}
                  </span>
                ) : (
                  <span>
                    exit {m.trade.exit_at.slice(0, 5)} at ₹{m.trade.exit_price} · {exitWord(m.trade.exit_reason)} ·{" "}
                    <span className={m.trade.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>{money(m.trade.pnl)}</span>
                  </span>
                )}
              </span>
            ))}
          </div>
        ) : (
          <span>
            ▲ buy / ▼ sell entry, labelled with the run · ● exit with its P&amp;L · one colour per run · hover a candle for the details ·
            the SMA / VWAP lines and Heikin Ashi follow your chart&apos;s Indicators settings
            {offChart ? ` · ${offChart} mark(s) fall outside the candles loaded` : ""}
          </span>
        )}
      </div>
    </div>
  );
}
