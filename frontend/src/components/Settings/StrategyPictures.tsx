"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * The strategy's candle numbers as pictures, like the SMA 9 slope editor: the ATR stop, the ADX trend
 * check, the candle size, the candle rules (direction check, counter candles, tall candle) and SMA gap mode.
 *
 * Each picture is drawn on a fixed, made-up run of candles so the shapes never change; only the setting
 * being edited moves. The sliders write the same fields the number boxes edit. Display only: the numbers
 * saved are the ones the bot reads.
 */
type Props = { form: SmaConfig; set: (key: keyof SmaConfig, value: string | boolean) => void };

type Candle = { o: number; h: number; l: number; c: number };

// A made-up but steady run of candles (price units): a pull-back, a climb, a wide bar, a quiet stretch.
const BARS: Candle[] = [
  { o: 99.2, h: 99.9, l: 98.9, c: 99.6 },
  { o: 99.6, h: 100.2, l: 99.3, c: 99.4 },
  { o: 99.4, h: 99.8, l: 98.8, c: 99.1 },
  { o: 99.1, h: 99.7, l: 98.9, c: 99.5 },
  { o: 99.5, h: 100.4, l: 99.3, c: 100.2 },
  { o: 100.2, h: 100.5, l: 99.7, c: 99.9 },
  { o: 99.9, h: 100.3, l: 99.5, c: 100.1 },
  { o: 100.1, h: 100.4, l: 99.8, c: 100.0 },
  { o: 100.0, h: 100.8, l: 99.6, c: 100.5 },
  { o: 100.5, h: 100.9, l: 100.0, c: 100.2 },
  { o: 100.2, h: 100.6, l: 99.4, c: 99.6 },
  { o: 99.6, h: 100.3, l: 99.3, c: 100.1 },
  { o: 100.1, h: 101.2, l: 100.0, c: 101.0 },
  { o: 101.0, h: 101.5, l: 100.6, c: 101.3 },
  { o: 101.3, h: 102.6, l: 101.1, c: 102.4 },
  { o: 102.4, h: 102.7, l: 101.9, c: 102.1 },
  { o: 102.1, h: 102.5, l: 101.8, c: 102.3 },
  { o: 102.3, h: 102.6, l: 102.0, c: 102.5 },
  { o: 102.5, h: 102.9, l: 102.2, c: 102.7 },
  { o: 102.7, h: 103.0, l: 102.4, c: 102.6 },
];

function trueRange(bar: Candle, prev: Candle | undefined): number {
  if (!prev) return bar.h - bar.l;
  return Math.max(bar.h - bar.l, Math.abs(bar.h - prev.c), Math.abs(bar.l - prev.c));
}

/** The plain average of the last `period` true ranges (Wilder's smoothing gives much the same picture). */
function atrOf(bars: Candle[], period: number): number {
  const n = Math.max(1, Math.min(bars.length, period));
  const trs = bars.map((b, i) => trueRange(b, bars[i - 1])).slice(-n);
  return trs.reduce((a, b) => a + b, 0) / trs.length;
}

function num(form: SmaConfig, key: keyof SmaConfig, fallback: number): number {
  const v = Number(form[key]);
  return Number.isFinite(v) ? v : fallback;
}

function clean(v: number, digits = 2): string {
  return String(Number(v.toFixed(digits)));
}

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** A slider with the value shown; `fmt` names the unit. */
function Slider({
  text,
  value,
  min,
  max,
  step,
  onChange,
  fmt = (v) => String(v),
  dim = false,
}: {
  text: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  fmt?: (v: number) => string;
  dim?: boolean;
}) {
  return (
    <label className={clsx("block text-xs text-slate-300", dim && "opacity-60")}>
      <span className="flex items-center justify-between gap-2">
        <span>{text}</span>
        <span className="font-mono text-slate-100">{fmt(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full accent-sky-400"
        aria-label={text}
      />
    </label>
  );
}

function Rule({ on, text, when, toggle }: { on: boolean; text: string; when: string; toggle?: (next: boolean) => void }) {
  return (
    <label
      className={clsx(
        "flex min-h-8 items-center gap-2 rounded-md px-2 py-1 text-xs ring-1 ring-inset",
        on ? "bg-emerald-500/10 text-slate-100 ring-emerald-400/40" : "text-slate-400 ring-white/10"
      )}
      title={when}
    >
      {toggle ? <input type="checkbox" checked={on} onChange={(e) => toggle(e.target.checked)} className="accent-emerald-400" /> : null}
      <span className="font-semibold">{text}</span>
      <span className={clsx("ml-auto font-mono", on ? "text-emerald-300" : "text-slate-500")}>{on ? "ON" : "off"}</span>
    </label>
  );
}

/** Candles drawn into a box: `scale` maps price to y, bars `cw` px apart. */
function Candles({
  bars,
  x0,
  cw,
  y,
  colourOf,
  faded = false,
  opacityOf,
}: {
  bars: Candle[];
  x0: number;
  cw: number;
  y: (p: number) => number;
  colourOf?: (bar: Candle, i: number) => string;
  faded?: boolean;
  /** Per-candle opacity (1 = normal); overrides `faded`. */
  opacityOf?: (bar: Candle, i: number) => number;
}) {
  return (
    <>
      {bars.map((b, i) => {
        const x = x0 + i * cw + cw / 2;
        const up = b.c >= b.o;
        const colour = colourOf ? colourOf(b, i) : up ? "#10B981" : "#F43F5E";
        const top = y(Math.max(b.o, b.c));
        const bottom = y(Math.min(b.o, b.c));
        return (
          <g key={i} opacity={opacityOf ? opacityOf(b, i) : faded ? 0.35 : 1}>
            <line x1={x} x2={x} y1={y(b.h)} y2={y(b.l)} stroke={colour} strokeWidth={1.5} />
            <rect x={x - cw * 0.3} y={top} width={cw * 0.6} height={Math.max(1.5, bottom - top)} fill={colour} />
          </g>
        );
      })}
    </>
  );
}

const SIDE_KEY = "sma.pictures.side";

function useSide(): ["LONG" | "SHORT", (s: "LONG" | "SHORT") => void] {
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  useEffect(() => {
    try {
      if (localStorage.getItem(SIDE_KEY) === "SHORT") setSide("SHORT");
    } catch {
      /* private window */
    }
  }, []);
  return [
    side,
    (s) => {
      setSide(s);
      try {
        localStorage.setItem(SIDE_KEY, s);
      } catch {
        /* private window */
      }
    },
  ];
}

function SideSwitch({ side, onChange }: { side: "LONG" | "SHORT"; onChange: (s: "LONG" | "SHORT") => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300" role="radiogroup" aria-label="Draw the picture for">
      <span className="text-slate-400">Draw it for:</span>
      {(["LONG", "SHORT"] as const).map((s) => (
        <label key={s} className={clsx("flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 ring-1 ring-inset", side === s ? "bg-sky-400/15 text-sky-100 ring-sky-400/50" : "ring-white/15 hover:bg-white/5")}>
          <input type="radio" name="pictures-side" value={s} checked={side === s} onChange={() => onChange(s)} className="accent-sky-400" />
          {s === "LONG" ? "a buy" : "a short"}
        </label>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ ATR */

/** ATR = how far a candle usually travels. The stop sits that far × the multiplier from the fill. */
export function AtrPicture({ form, set }: Props) {
  const period = Math.min(50, Math.max(1, Math.round(num(form, "atr_period", 14))));
  const mult = num(form, "atr_multiplier", 1.5);
  const stopOn = form.use_stop !== false;
  const type = (form.stop_type as string | undefined) ?? "ATR";
  const [side, setSide] = useSide();
  const bars = BARS;
  const used = Math.min(period, bars.length); // the candles the average reads (drawn bright; the rest dim)
  const atr = atrOf(bars, used);
  const fill = bars[bars.length - 1].c;
  const stop = side === "LONG" ? fill - mult * atr : fill + mult * atr;
  const W = 560;
  const H = 230;
  const cw = 20;
  const x0 = 16;
  const lo = Math.min(...bars.map((b) => b.l), stop) - 0.3;
  const hi = Math.max(...bars.map((b) => b.h), stop) + 0.3;
  const y = (p: number) => 16 + ((hi - p) / (hi - lo)) * (H - 40);
  const xEnd = x0 + bars.length * cw;
  const atrX = xEnd + 14;
  const last = bars[bars.length - 1];
  const active = stopOn && type === "ATR";
  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="ATR picture">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">ATR stop, as a picture</h4>
        <SideSwitch side={side} onChange={setSide} />
      </div>
      <p className="mt-1 text-xs text-slate-400">
        <span className="text-slate-300">ATR (average true range)</span> is how far one candle usually travels from its low to its
        high, averaged over the last {period} candles (the yellow bracket). The stop is placed {clean(mult)} × that distance
        {side === "LONG" ? " below" : " above"} the fill (the red line): a wild stock gets a wider stop, a quiet one a tighter stop, with
        the same numbers. A tick past the stop closes the trade.
      </p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <Rule on={active} text="ATR stop" when={stopOn ? (type === "ATR" ? "The stop is this far from the fill." : `The stop type is ${type}; the ATR numbers are not used for the stop.`) : "The stop is off."} toggle={type === "ATR" ? (on) => set("use_stop", on) : undefined} />
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label={`ATR over ${period} candles is ${clean(atr)}; the stop sits ${clean(mult)} ATR ${side === "LONG" ? "below" : "above"} the fill`}>
        <Candles bars={bars} x0={x0} cw={cw} y={y} opacityOf={(_b, i) => (i >= bars.length - used ? 1 : 0.22)} />
        <rect x={x0 + (bars.length - used) * cw} y={8} width={used * cw} height={H - 24} fill="rgba(250,204,21,0.06)" stroke="rgba(250,204,21,0.35)" strokeDasharray="3 3" />
        <text x={x0 + (bars.length - used) * cw + 4} y={H - 8} fill="#FACC15" fontSize={10} fontFamily={MONO}>
          last {used} candle{used === 1 ? "" : "s"} averaged{period > bars.length ? ` (${period} set, ${bars.length} drawn)` : ""}
        </text>
        {/* one ATR, next to the last candle */}
        <line x1={atrX} x2={atrX} y1={y(last.c)} y2={y(last.c + atr)} stroke="#FACC15" strokeWidth={3} strokeLinecap="round" />
        <text x={atrX + 6} y={y(last.c + atr / 2) + 4} fill="#FACC15" fontSize={11} fontFamily={MONO}>
          1 ATR = {clean(atr)}
        </text>
        {/* the fill and the stop */}
        <line x1={x0} x2={W - 8} y1={y(fill)} y2={y(fill)} stroke="rgba(226,232,240,0.5)" strokeDasharray="4 3" />
        <text x={x0} y={y(fill) - 4} fill="#94a3b8" fontSize={11} fontFamily={MONO}>
          fill {clean(fill)} ({side === "LONG" ? "buy" : "short"})
        </text>
        <line x1={x0} x2={W - 8} y1={y(stop)} y2={y(stop)} stroke={active ? "#F43F5E" : "rgba(244,63,94,0.4)"} strokeWidth={2} strokeDasharray="6 4" />
        <text x={x0} y={side === "LONG" ? y(stop) + 14 : y(stop) - 5} fill={active ? "#F43F5E" : "rgba(244,63,94,0.6)"} fontSize={11} fontFamily={MONO}>
          stop {clean(stop)} = fill {side === "LONG" ? "−" : "+"} {clean(mult)} × ATR{active ? "" : " (not in use)"}
        </text>
        {/* the multiplier as a stack of ATR brackets */}
        {Array.from({ length: Math.ceil(mult) }, (_, k) => {
          const a = side === "LONG" ? fill - k * atr : fill + k * atr;
          const bEnd = side === "LONG" ? Math.max(stop, fill - (k + 1) * atr) : Math.min(stop, fill + (k + 1) * atr);
          return <line key={k} x1={xEnd + 40} x2={xEnd + 40} y1={y(a)} y2={y(bEnd)} stroke={k % 2 ? "#fb923c" : "#FACC15"} strokeWidth={3} strokeLinecap="butt" />;
        })}
        <text x={xEnd + 46} y={y(side === "LONG" ? fill - (mult * atr) / 2 : fill + (mult * atr) / 2) + 4} fill="#fb923c" fontSize={11} fontFamily={MONO}>
          {clean(mult)} ATR
        </text>
      </svg>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Slider text="ATR period (candles averaged)" value={period} min={2} max={50} step={1} onChange={(v) => set("atr_period", String(Math.round(v)))} fmt={(v) => `${v} candles`} />
        <Slider text="Stop distance (× ATR)" value={mult} min={0.5} max={5} step={0.1} onChange={(v) => set("atr_multiplier", clean(v, 1))} fmt={(v) => `${clean(v, 1)} × ATR = ${clean(v * atr)} here`} dim={!active} />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Press Save to keep a change. The candles are a made-up example; the bot reads the real ATR of each stock.</p>
    </section>
  );
}

/* ------------------------------------------------------------------ ADX */

// A made-up ADX line: sideways, then a trend builds, then it fades.
const ADX_LINE = [12, 11, 13, 14, 16, 19, 23, 27, 31, 34, 37, 38, 36, 33, 29, 26, 22, 19, 17, 15];

/** ADX measures how strong a trend is (0-100), never which way it points. */
export function AdxPicture({ form, set }: Props) {
  const on = Boolean(form.use_adx_filter);
  const threshold = Math.min(60, Math.max(5, num(form, "adx_threshold", 20)));
  const W = 560;
  const H = 180;
  const x0 = 36;
  const cw = (W - x0 - 12) / ADX_LINE.length;
  const y = (v: number) => 14 + ((60 - v) / 60) * (H - 40);
  const pts = ADX_LINE.map((v, i) => `${x0 + i * cw + cw / 2},${y(v)}`).join(" ");
  const strong = ADX_LINE.filter((v) => v >= threshold).length;
  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="ADX picture">
      <h4 className="text-sm font-semibold text-slate-100">ADX trend check, as a picture</h4>
      <p className="mt-1 text-xs text-slate-400">
        <span className="text-slate-300">ADX (average directional index)</span> is a number from 0 to 100 that says how strongly the price is
        trending, up or down alike. Low = sideways chop, high = a clear run. With the check on, a cross is skipped while ADX is under{" "}
        {clean(threshold, 0)}: the shaded part. Only entries are checked; it never closes a trade.
      </p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <Rule on={on} text="ADX check" when="A cross is skipped while ADX is under the threshold." toggle={(next) => set("use_adx_filter", next)} />
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label={`ADX line with the threshold at ${clean(threshold, 0)}: ${strong} of ${ADX_LINE.length} candles would allow an entry`}>
        <rect x={x0} y={y(threshold)} width={W - x0 - 12} height={y(0) - y(threshold)} fill={on ? "rgba(244,63,94,0.12)" : "rgba(148,163,184,0.08)"} />
        {[0, 20, 40, 60].map((v) => (
          <g key={v}>
            <line x1={x0} x2={W - 12} y1={y(v)} y2={y(v)} stroke="rgba(148,163,184,0.15)" />
            <text x={4} y={y(v) + 4} fill="#64748b" fontSize={10} fontFamily={MONO}>
              {v}
            </text>
          </g>
        ))}
        <polyline points={pts} fill="none" stroke="#a78bfa" strokeWidth={2.5} strokeLinejoin="round" />
        {ADX_LINE.map((v, i) => (
          <circle key={i} cx={x0 + i * cw + cw / 2} cy={y(v)} r={3} fill={v >= threshold ? "#34d399" : on ? "#fb7185" : "#94a3b8"} />
        ))}
        <line x1={x0} x2={W - 12} y1={y(threshold)} y2={y(threshold)} stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="6 4" />
        <text x={x0 + 4} y={y(threshold) - 5} fill="#f59e0b" fontSize={11} fontFamily={MONO}>
          threshold {clean(threshold, 0)} · above: entries allowed
        </text>
        <text x={x0 + 4} y={y(threshold) + 13} fill={on ? "#fb7185" : "#94a3b8"} fontSize={11} fontFamily={MONO}>
          below: {on ? "no entry (sideways)" : "would be skipped with the check on"}
        </text>
      </svg>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Slider text="ADX threshold" value={threshold} min={5} max={60} step={1} onChange={(v) => set("adx_threshold", String(Math.round(v)))} fmt={(v) => `${clean(v, 0)} (${strong} of ${ADX_LINE.length} example candles pass)`} dim={!on} />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Typical: under 20 = no trend, 20–25 = a trend starting, over 25 = a strong trend. Press Save to keep a change.</p>
    </section>
  );
}

/* ------------------------------------------------------------- candle size */

// Thirty made-up 1-minute candles (a slow climb with pull-backs).
const ONE_MIN: Candle[] = Array.from({ length: 30 }, (_, i) => {
  const base = 100 + i * 0.08 + Math.sin(i / 2.3) * 0.25;
  const o = base;
  const c = base + (i % 3 === 1 ? -0.12 : 0.14) + Math.cos(i / 1.7) * 0.05;
  return { o, c, h: Math.max(o, c) + 0.08, l: Math.min(o, c) - 0.08 };
});

function group(bars: Candle[], n: number): Candle[] {
  const out: Candle[] = [];
  for (let i = 0; i < bars.length; i += n) {
    const part = bars.slice(i, i + n);
    out.push({ o: part[0].o, c: part[part.length - 1].c, h: Math.max(...part.map((b) => b.h)), l: Math.min(...part.map((b) => b.l)) });
  }
  return out;
}

const SIZES = [1, 2, 3, 5, 10, 15] as const;

/** The candle size: 1-minute candles grouped into longer ones; the bot judges each one once it closes. */
export function CandleSizePicture({ form, set }: Props) {
  const minutes = (SIZES as readonly number[]).includes(Number(form.candle_minutes ?? 1)) ? Number(form.candle_minutes ?? 1) : 1;
  const fast = num(form, "sma_fast", 9);
  const slow = num(form, "sma_slow", 21);
  const W = 560;
  const H = 200;
  const left = ONE_MIN;
  const right = group(ONE_MIN, minutes);
  const lo = Math.min(...left.map((b) => b.l)) - 0.1;
  const hi = Math.max(...left.map((b) => b.h)) + 0.1;
  const y = (p: number) => 20 + ((hi - p) / (hi - lo)) * (H - 44);
  const cwL = 8;
  const x0L = 10;
  const x0R = x0L + left.length * cwL + 30;
  const cwR = Math.min(26, (W - x0R - 10) / right.length);
  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="Candle size picture">
      <h4 className="text-sm font-semibold text-slate-100">Candle size, as a picture</h4>
      <p className="mt-1 text-xs text-slate-400">
        Groww sends 1-minute candles (left). The bot glues every {minutes} of them into one candle (right) and reads the SMAs, the ATR and
        the checks on those. A signal is judged only when a {minutes}-minute candle closes, so a bigger candle means fewer, slower signals
        and less noise; SMA {fast} then needs {fast * minutes} minutes of candles and SMA {slow} needs {slow * minutes}.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label={`${left.length} one-minute candles become ${right.length} candles of ${minutes} minutes`}>
        <text x={x0L} y={12} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
          {left.length} × 1-minute candles
        </text>
        <Candles bars={left} x0={x0L} cw={cwL} y={y} />
        {minutes > 1
          ? Array.from({ length: Math.ceil(left.length / minutes) + 1 }, (_, k) => (
              <line key={k} x1={x0L + k * minutes * cwL} x2={x0L + k * minutes * cwL} y1={16} y2={H - 20} stroke="rgba(56,189,248,0.35)" strokeDasharray="2 3" />
            ))
          : null}
        <text x={x0L + left.length * cwL + 6} y={H / 2 + 4} fill="#38bdf8" fontSize={14}>
          →
        </text>
        <text x={x0R} y={12} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
          {right.length} × {minutes}-minute candle{right.length === 1 ? "" : "s"} (what the bot reads)
        </text>
        <Candles bars={right} x0={x0R} cw={cwR} y={y} />
        <text x={x0L} y={H - 6} fill="#64748b" fontSize={10} fontFamily={MONO}>
          one {minutes}-minute candle = {minutes} minute{minutes === 1 ? "" : "s"} of waiting before the bot can act on it
        </text>
      </svg>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-300" role="radiogroup" aria-label="Candle size">
        {SIZES.map((m) => (
          <label key={m} className={clsx("flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 ring-1 ring-inset", minutes === m ? "bg-sky-400/15 text-sky-100 ring-sky-400/50" : "ring-white/15 hover:bg-white/5")}>
            <input type="radio" name="candle-size-picture" value={m} checked={minutes === m} onChange={() => set("candle_minutes", String(m))} className="accent-sky-400" />
            {m} min
          </label>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Press Save to keep a change. The chart's own candle-size menu only changes the drawing, never the bot.</p>
    </section>
  );
}

/* ------------------------------------------------------------ candle rules */

type RulePart = "direction" | "counter" | "tall";

const ATR_EX = 0.5; // the example's ATR in price units, so bodies and heights read in ATR

/** One candle whose body is `body` (price units, signed: + = up) with small wicks. */
function bar(open: number, body: number, wick = 0.06): Candle {
  const close = open + body;
  return { o: open, c: close, h: Math.max(open, close) + wick, l: Math.min(open, close) - wick };
}

/** A run of `n` candles each moving `body` (signed), one after the other. */
function run(start: number, n: number, body: number, wick?: number): Candle[] {
  const out: Candle[] = [];
  let price = start;
  for (let i = 0; i < n; i += 1) {
    out.push(bar(price, body, wick));
    price += body;
  }
  return out;
}

/**
 * Candle rules, drawn from the settings: the direction check before an entry (exactly N candles the trade's way),
 * counter candles against an open trade (exactly N candles against it, each body the chosen size in ATR), and the
 * tall candle (a signal candle exactly the chosen height in ATR). Every slider redraws the candles.
 */
export function CandleRulesPicture({ form, set, parts }: Props & { parts: RulePart[] }) {
  const [side, setSide] = useSide();
  const dirOn = Boolean(form.use_candle_dir);
  const dirN = Math.min(10, Math.max(1, Math.round(num(form, "candle_dir_count", 2))));
  const dirRule = (form.candle_dir_rule as string | undefined) ?? "CLOSES";
  const counterN = Math.min(10, Math.max(0, Math.round(num(form, "counter_exit_count", 0))));
  const counterRule = (form.counter_rule as string | undefined) ?? "COLOUR";
  const minBody = Math.min(5, Math.max(0, num(form, "counter_min_body", 0)));
  const tallOn = Boolean(form.tall_pullback);
  const tallMult = Math.min(5, Math.max(0.5, num(form, "tall_atr_mult", 1.5)));
  const dir = side === "LONG" ? 1 : -1; // + = the trade's way
  const want = side === "LONG" ? "buy" : "short";
  const W = 560;
  const H = 230;
  const x0 = 16;

  // ---- build the candles for the part shown
  let bars: Candle[] = [];
  let caption = "";
  let labels: { i: number; text: string; colour: string; above: boolean; right?: boolean }[] = [];
  let extras: { kind: "tall"; i: number; height: number; pull: number } | null = null;
  if (parts.includes("direction")) {
    // two mixed candles, then exactly dirN candles the trade's way (each close beyond the last)
    const lead = [bar(100, -0.15 * dir), bar(100 - 0.15 * dir, 0.1 * dir)];
    const last = lead[lead.length - 1].c;
    const good = run(last, dirN, 0.22 * dir, 0.05);
    bars = [...lead, ...good];
    labels = good.map((_b, k) => ({ i: lead.length + k, text: "✓", colour: "#34d399", above: dir > 0 }));
    const words = dirRule === "COLOUR" ? `${dir > 0 ? "green" : "red"} candles` : dirRule === "BOTH" ? `${dir > 0 ? "green" : "red"} candles, each closing ${dir > 0 ? "higher" : "lower"}` : `each close ${dir > 0 ? "higher" : "lower"} than the last`;
    caption = `${dirOn ? "entry allowed" : "with the check on, an entry would need"}: the last ${dirN} closed candle${dirN === 1 ? "" : "s"} ${words}${dirOn ? "" : " (check off)"}`;
  } else if (parts.includes("counter")) {
    // three candles with the trade, then exactly N against it with bodies of max(minBody, 0.3) ATR
    const withTrade = run(100, 3, 0.3 * dir, 0.05);
    const n = counterN || 3;
    const bodyAtr = Math.max(minBody, 0.3);
    const against = run(withTrade[withTrade.length - 1].c, n, -bodyAtr * ATR_EX * dir, 0.05);
    bars = [...withTrade, ...against];
    labels = against.map((_b, k) => ({ i: withTrade.length + k, text: `${clean(bodyAtr, 1)} ATR`, colour: counterN ? "#fb7185" : "#64748b", above: dir < 0 }));
    const last = withTrade.length + n - 1;
    labels.push({ i: last, text: counterN ? "← exit here" : "← (off)", colour: counterN ? "#fb7185" : "#64748b", above: dir > 0, right: true });
    const words = counterRule === "CLOSES" ? `each close ${dir > 0 ? "lower" : "higher"} than the last` : counterRule === "BOTH" ? `${dir > 0 ? "red" : "green"} candles, each closing ${dir > 0 ? "lower" : "higher"}` : `${dir > 0 ? "red" : "green"} candles`;
    caption = counterN
      ? `exit after ${counterN} candle${counterN === 1 ? "" : "s"} against the ${want} (${words})${minBody ? `, each body ≥ ${clean(minBody, 1)} ATR` : ", any body size"}`
      : "counter exit off (N = 0): the candles against the trade are ignored";
  } else {
    // four normal candles (0.8 ATR tall), then the signal candle exactly tallMult ATR tall the trade's way
    const normal: Candle[] = [];
    let price = 100;
    for (let i = 0; i < 4; i += 1) {
      const body = (i % 2 ? -0.25 : 0.35) * dir;
      normal.push({ o: price, c: price + body, h: Math.max(price, price + body) + (0.8 * ATR_EX - Math.abs(body)) / 2, l: Math.min(price, price + body) - (0.8 * ATR_EX - Math.abs(body)) / 2 });
      price += body;
    }
    const height = tallMult * ATR_EX;
    const tall: Candle = dir > 0 ? { o: price, c: price + height * 0.85, h: price + height, l: price } : { o: price, c: price - height * 0.85, h: price, l: price - height };
    bars = [...normal, tall];
    const pull = dir > 0 ? tall.l + height / 2 : tall.h - height / 2;
    extras = { kind: "tall", i: normal.length, height: tallMult, pull };
    caption = tallOn ? `a signal candle ≥ ${clean(tallMult, 1)} ATR tall does not order at once: the entry waits for the pull-back` : `with the rule on, a candle ≥ ${clean(tallMult, 1)} ATR tall would wait for a pull-back (rule off: orders at once)`;
  }
  const cw = Math.min(30, Math.floor((W - x0 - 150) / Math.max(bars.length, 1)));
  const lo = Math.min(...bars.map((b) => b.l)) - 0.15;
  const hi = Math.max(...bars.map((b) => b.h)) + 0.15;
  const y = (p: number) => 18 + ((hi - p) / (hi - lo)) * (H - 46);
  const dim = parts.includes("direction") ? !dirOn : parts.includes("counter") ? counterN === 0 : !tallOn;

  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="Candle rules picture">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">
          {parts.includes("direction") ? "Candle direction check" : parts.includes("counter") ? "Counter candles" : "Tall candle"}, as a picture
        </h4>
        <SideSwitch side={side} onChange={setSide} />
      </div>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
        {parts.includes("direction") ? <Rule on={dirOn} text="Direction check (entry)" when={`A ${want} needs the last ${dirN} closed candles to go its way.`} toggle={(next) => set("use_candle_dir", next)} /> : null}
        {parts.includes("counter") ? <Rule on={counterN > 0} text="Counter candles (exit)" when={`Closes the trade once ${counterN || "N"} closed candles in a row go against it.`} toggle={(next) => set("counter_exit_count", next ? "3" : "0")} /> : null}
        {parts.includes("tall") ? <Rule on={tallOn} text="Tall candle (wait for a pull-back)" when={`A signal candle taller than ${clean(tallMult, 1)} ATR does not order at once; the entry waits at half its height or the fast SMA.`} toggle={(next) => set("tall_pullback", next)} /> : null}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label={caption}>
        <Candles bars={bars} x0={x0} cw={cw} y={y} opacityOf={(_b, i) => (dim && i >= (parts.includes("tall") ? 4 : parts.includes("direction") ? 2 : 3) ? 0.45 : 1)} />
        {labels.map((l, k) =>
          l.right ? (
            <text key={k} x={x0 + (l.i + 1) * cw + 4} y={y(bars[l.i].c) + 4} fill={l.colour} fontSize={11} fontFamily={MONO}>
              {l.text}
            </text>
          ) : (
            <text key={k} x={x0 + l.i * cw + cw / 2} y={l.above ? y(bars[l.i].h) - 6 : y(bars[l.i].l) + 14} fill={l.colour} fontSize={10} fontFamily={MONO} textAnchor="middle">
              {l.text}
            </text>
          )
        )}
        {extras ? (
          (() => {
            const b = bars[extras.i];
            const xr = x0 + (extras.i + 1) * cw + 10;
            return (
              <>
                <line x1={xr} x2={xr} y1={y(b.h)} y2={y(b.l)} stroke="#FACC15" strokeWidth={3} />
                <text x={xr + 8} y={y(dir > 0 ? b.h : b.l) + (dir > 0 ? 10 : -4)} fill="#FACC15" fontSize={10} fontFamily={MONO}>
                  height = {clean(extras.height, 1)} ATR (your setting)
                </text>
                <text x={x0} y={y(dir > 0 ? bars[0].l : bars[0].h) + (dir > 0 ? 14 : -6)} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
                  normal candles ≈ 0.8 ATR
                </text>
                {tallOn ? (
                  <>
                    <line x1={x0 + (extras.i - 1) * cw} x2={W - 8} y1={y(extras.pull)} y2={y(extras.pull)} stroke="#f59e0b" strokeDasharray="5 4" strokeWidth={1.5} />
                    <text x={xr + 8} y={y(extras.pull) - 5} fill="#f59e0b" fontSize={10} fontFamily={MONO}>
                      ← entry waits here (half-way back, or the fast SMA)
                    </text>
                  </>
                ) : null}
              </>
            );
          })()
        ) : null}
        <text x={x0} y={H - 6} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
          {caption}
        </text>
      </svg>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {parts.includes("direction") ? <Slider text="Direction: last candles to check" value={dirN} min={1} max={10} step={1} onChange={(v) => set("candle_dir_count", String(Math.round(v)))} fmt={(v) => `${v} candle${v === 1 ? "" : "s"}`} dim={!dirOn} /> : null}
        {parts.includes("counter") ? <Slider text="Counter candles before the exit (0 = off)" value={counterN} min={0} max={10} step={1} onChange={(v) => set("counter_exit_count", String(Math.round(v)))} fmt={(v) => (v ? `${v} candle${v === 1 ? "" : "s"}` : "off")} /> : null}
        {parts.includes("counter") ? <Slider text="Each counter candle's body at least (× ATR)" value={minBody} min={0} max={5} step={0.1} onChange={(v) => set("counter_min_body", clean(v, 1))} fmt={(v) => (v ? `${clean(v, 1)} ATR` : "any size")} dim={counterN === 0} /> : null}
        {parts.includes("tall") ? <Slider text="Tall when the candle's height is at least (× ATR)" value={tallMult} min={0.5} max={5} step={0.1} onChange={(v) => set("tall_atr_mult", clean(v, 1))} fmt={(v) => `${clean(v, 1)} ATR`} dim={!tallOn} /> : null}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        The candles are drawn from your numbers (an example ATR of {ATR_EX}): move a slider and they redraw. Press Save to keep a change.
      </p>
    </section>
  );
}


/* ------------------------------------------------------------------ SMA gap mode */

// A made-up gap % line (SMA fast − SMA slow, in % of the price) over 26 one-minute candles: below the slow
// SMA, a cross, the gap widens to a peak, then it fades back and crosses the other way. Drawn for a buy;
// a short is the mirror image.
const GAP_LINE = [
  -0.06, -0.045, -0.03, -0.015, -0.004, 0.008, 0.02, 0.034, 0.048, 0.062, 0.076, 0.088, 0.097, 0.103, 0.106, 0.104,
  0.097, 0.086, 0.072, 0.058, 0.044, 0.03, 0.018, 0.008, -0.004, -0.016,
];
// The slow SMA's own gentle drift (price units on a ₹1,000 stock), so the top strip looks like a chart.
const SLOW_LINE = GAP_LINE.map((_, i) => 1000 + Math.sin(i / 6) * 0.6 + i * 0.05);

type GapPlay = { cross: number; entry: number | null; exit: number | null; peak: number; why: string; exitWhy: string };

/** Where the bot would enter and exit on the example, with the settings as the bot reads them (gap_mode.py). */
function playGap(gap: number[], entryLevel: number, exitLevel: number, giveback: number, delay: number, window: number, fadeCandles: number): GapPlay {
  const cross = gap.findIndex((g, i) => i > 0 && gap[i - 1] <= 0 && g > 0);
  let entry: number | null = null;
  let why = "";
  for (let i = cross; i >= 0 && i < gap.length; i += 1) {
    if (window > 0 && i - cross > window) {
      why = `given up: ${window} min after the cross with no entry`;
      break;
    }
    if (gap[i] >= entryLevel) {
      const at = i + delay;
      if (at >= gap.length) break;
      if (gap[at] >= entryLevel) {
        entry = at;
        why = delay ? `gap reached ${clean(entryLevel, 3)}% at candle ${i + 1}, still there after the ${delay}-min wait` : `first closed candle with the gap ≥ ${clean(entryLevel, 3)}%`;
        break;
      }
    }
  }
  if (entry === null && !why) why = `the gap never reached ${clean(entryLevel, 3)}% before it crossed back`;
  let exit: number | null = null;
  let exitWhy = "";
  let peak = entry === null ? 0 : gap[entry];
  if (entry !== null) {
    const lockIn = exitLevel > entryLevel; // an exit beyond the entry arms only once the gap has been that wide
    let armed = !lockIn;
    for (let i = entry + 1; i < gap.length; i += 1) {
      peak = Math.max(peak, gap[i]);
      if (!armed && gap[i] >= exitLevel) armed = true;
      const narrowing = fadeCandles > 0 ? Array.from({ length: fadeCandles }, (_, k) => i - k).every((j) => j > entry && gap[j] < gap[j - 1]) : true;
      if (armed && gap[i] <= exitLevel && narrowing) {
        exit = i;
        exitWhy = `faded back to ${clean(exitLevel, 3)}%${fadeCandles ? ` after ${fadeCandles} narrowing candles` : ""}`;
        break;
      }
      if (giveback > 0 && peak > 0 && gap[i] <= peak * (1 - giveback / 100) && narrowing) {
        exit = i;
        exitWhy = `gave back ${giveback}% of its widest (${clean(peak, 3)}%)`;
        break;
      }
      if (gap[i] <= 0) {
        exit = i;
        exitWhy = "opposite cross: closed at once";
        break;
      }
    }
    if (exit === null) exitWhy = "still held at the end of the example";
  }
  return { cross, entry, exit, peak, why, exitWhy };
}

/** SMA gap mode: enter when the gap between the SMAs widens past a level, exit when it fades back. */
export function GapModePicture({ form, set }: Props) {
  const [side, setSide] = useSide();
  const on = Boolean(form.use_gap_mode);
  const long = side === "LONG";
  const entryKey = long ? "gap_entry_long" : "gap_entry_short";
  const exitKey = long ? "gap_exit_long" : "gap_exit_short";
  // Buy levels are positive, sell levels negative; the picture works in the trade's own direction (+).
  const entryLevel = Math.abs(num(form, entryKey, long ? 0.05 : -0.05));
  const exitLevel = Math.abs(num(form, exitKey, long ? 0.02 : -0.02));
  const giveback = Math.max(0, num(form, "gap_giveback_pct", 0));
  const delay = Math.max(0, Math.round(num(form, "gap_entry_delay_min", 0)));
  const window = Math.max(0, Math.round(num(form, "gap_entry_window_min", 0)));
  const fadeCandles = Math.max(0, Math.round(num(form, "gap_fade_min_candles", 0)));
  const gap = GAP_LINE;
  const play = playGap(gap, entryLevel, exitLevel, giveback, delay, window, fadeCandles);
  const sign = long ? 1 : -1;
  const W = 560;
  const topH = 90;
  const H = 300;
  const x0 = 16;
  const cw = (W - 40) / gap.length;
  const x = (i: number) => x0 + i * cw + cw / 2;
  // Top strip: the two SMAs in price (the fast one = slow + gap % of the price).
  const fast = SLOW_LINE.map((p, i) => p * (1 + (sign * gap[i]) / 100));
  const pLo = Math.min(...SLOW_LINE, ...fast) - 0.2;
  const pHi = Math.max(...SLOW_LINE, ...fast) + 0.2;
  const yp = (p: number) => 10 + ((pHi - p) / (pHi - pLo)) * (topH - 16);
  // Bottom: the signed gap in %.
  const gLo = -0.12;
  const gHi = 0.14;
  const yg = (g: number) => topH + 18 + ((gHi - g) / (gHi - gLo)) * (H - topH - 44);
  const path = (ys: number[]) => ys.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${v.toFixed(1)}`).join(" ");
  const giveLevel = play.entry !== null && giveback > 0 ? play.peak * (1 - giveback / 100) : null;
  const levelText = (v: number) => `${sign * v > 0 ? "+" : ""}${clean(sign * v, 3)}%`;
  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="SMA gap mode picture">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">SMA gap mode, as a picture</h4>
        <SideSwitch side={side} onChange={setSide} />
      </div>
      <p className="mt-1 text-xs text-slate-400">
        The gap is how far SMA {form.sma_fast ?? 9} sits from SMA {form.sma_slow ?? 21}, in % of the price (bottom line). A cross only
        arms the trade; the order goes on the first closed candle whose gap reaches the <span className="text-emerald-300">entry level</span>
        {delay ? `, and is still there ${delay} min later` : ""}. The trade is held while the gap widens and closes when it fades back to the{" "}
        <span className="text-rose-300">exit level</span>
        {giveback ? `, or gives back ${giveback}% of its widest` : ""}
        {fadeCandles ? `, once it has narrowed ${fadeCandles} candles in a row` : ""}. An opposite cross closes at once.
      </p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <Rule on={on} text="SMA gap mode" when={on ? "Entries wait for the gap level; exits on the fade." : "Off: a cross orders at once and the gap levels are not read."} toggle={(next) => set("use_gap_mode", next)} />
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label={`SMA gap mode example: ${play.entry !== null ? `enters at candle ${play.entry + 1}` : "no entry"}; ${play.exitWhy}`}>
        {/* top strip: the SMAs */}
        <path d={path(SLOW_LINE.map(yp))} fill="none" stroke="#f59e0b" strokeWidth={1.5} />
        <path d={path(fast.map(yp))} fill="none" stroke="#38bdf8" strokeWidth={1.5} />
        <text x={x0} y={12} fill="#38bdf8" fontSize={10} fontFamily={MONO}>SMA {form.sma_fast ?? 9}</text>
        <text x={x0 + 52} y={12} fill="#f59e0b" fontSize={10} fontFamily={MONO}>SMA {form.sma_slow ?? 21}</text>
        <line x1={x0} x2={W - 8} y1={topH + 6} y2={topH + 6} stroke="rgba(255,255,255,0.1)" />
        {/* bottom: the gap in % */}
        <text x={x0} y={topH + 16} fill="#94a3b8" fontSize={10} fontFamily={MONO}>gap % = (SMA {form.sma_fast ?? 9} − SMA {form.sma_slow ?? 21}) ÷ SMA {form.sma_slow ?? 21}{long ? "" : " (a short reads it the other way up)"}</text>
        <line x1={x0} x2={W - 8} y1={yg(0)} y2={yg(0)} stroke="rgba(226,232,240,0.35)" />
        <text x={W - 10} y={yg(0) - 3} fill="#94a3b8" fontSize={10} fontFamily={MONO} textAnchor="end">0 (cross)</text>
        {/* the zone the trade is held in */}
        {play.entry !== null ? (
          <rect x={x(play.entry) - cw / 2} y={10} width={x(play.exit ?? gap.length - 1) - x(play.entry) + (play.exit === null ? cw / 2 : 0)} height={H - 24} fill={long ? "rgba(16,185,129,0.07)" : "rgba(244,63,94,0.07)"} />
        ) : null}
        <path d={path(gap.map(yg))} fill="none" stroke="#e2e8f0" strokeWidth={2} />
        {/* levels */}
        <line x1={x0} x2={W - 8} y1={yg(entryLevel)} y2={yg(entryLevel)} stroke="#10B981" strokeWidth={1.5} strokeDasharray="6 4" />
        <text x={x0} y={yg(entryLevel) - 4} fill="#10B981" fontSize={11} fontFamily={MONO}>enter at gap {long ? "≥" : "≤"} {levelText(entryLevel)}</text>
        <line x1={x0} x2={W - 8} y1={yg(exitLevel)} y2={yg(exitLevel)} stroke="#F43F5E" strokeWidth={1.5} strokeDasharray="6 4" />
        <text x={x0} y={yg(exitLevel) + 12} fill="#F43F5E" fontSize={11} fontFamily={MONO}>exit at gap {long ? "≤" : "≥"} {levelText(exitLevel)}{exitLevel > entryLevel ? " (lock-in: arms once the gap has been this wide)" : ""}</text>
        {giveLevel !== null ? (
          <>
            <line x1={x(play.entry!)} x2={W - 8} y1={yg(giveLevel)} y2={yg(giveLevel)} stroke="#fb923c" strokeWidth={1} strokeDasharray="2 3" />
            <text x={W - 10} y={yg(giveLevel) + 12} fill="#fb923c" fontSize={10} fontFamily={MONO} textAnchor="end">give-back {giveback}% of the widest {clean(play.peak, 3)}% → {clean(giveLevel, 3)}%</text>
          </>
        ) : null}
        {/* the cross, entry and exit candles */}
        <line x1={x(play.cross)} x2={x(play.cross)} y1={10} y2={H - 14} stroke="rgba(226,232,240,0.3)" strokeDasharray="2 3" />
        <text x={x(play.cross) + 3} y={H - 4} fill="#94a3b8" fontSize={10} fontFamily={MONO}>cross</text>
        {window > 0 ? (
          <>
            <line x1={x(play.cross + window)} x2={x(play.cross + window)} y1={10} y2={H - 14} stroke="rgba(251,146,60,0.5)" strokeDasharray="2 3" />
            <text x={x(play.cross + window) + 3} y={H - 4} fill="#fb923c" fontSize={10} fontFamily={MONO}>give up ({window} min)</text>
          </>
        ) : null}
        {play.entry !== null ? (
          <>
            <circle cx={x(play.entry)} cy={yg(gap[play.entry])} r={5} fill={long ? "#10B981" : "#F43F5E"} />
            <text x={x(play.entry) + 7} y={yg(gap[play.entry]) - 6} fill={long ? "#10B981" : "#F43F5E"} fontSize={11} fontFamily={MONO}>{long ? "BUY" : "SELL"}</text>
          </>
        ) : null}
        {play.exit !== null ? (
          <>
            <circle cx={x(play.exit)} cy={yg(gap[play.exit])} r={5} fill="#F43F5E" stroke="#fff" strokeWidth={1} />
            <text x={x(play.exit) + 7} y={yg(gap[play.exit]) - 7} fill="#F43F5E" fontSize={11} fontFamily={MONO}>exit</text>
          </>
        ) : null}
      </svg>
      <p className="mt-1 text-xs text-slate-300">
        <span className="font-semibold">On this example:</span> {play.entry !== null ? `enters at candle ${play.entry + 1} (${play.why}); ${play.exitWhy}${play.exit !== null ? ` at candle ${play.exit + 1}` : ""}.` : `no entry: ${play.why}.`}
      </p>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <Slider text={`${long ? "Buy" : "Sell"}: enter at gap ${long ? "≥" : "≤"}`} value={entryLevel} min={0.005} max={0.5} step={0.005} onChange={(v) => set(entryKey, clean(sign * v, 3))} fmt={(v) => levelText(v)} dim={!on} />
        <Slider text={`${long ? "Buy" : "Sell"}: exit at gap ${long ? "≤" : "≥"}`} value={exitLevel} min={0} max={0.5} step={0.005} onChange={(v) => set(exitKey, clean(sign * v, 3))} fmt={(v) => levelText(v)} dim={!on} />
        <Slider text="Also exit after giving back % of the widest gap (0 = off)" value={giveback} min={0} max={90} step={5} onChange={(v) => set("gap_giveback_pct", String(Math.round(v)))} fmt={(v) => (v ? `${v}%` : "off")} dim={!on} />
        <Slider text="Wait after the level is met" value={delay} min={0} max={15} step={1} onChange={(v) => set("gap_entry_delay_min", String(Math.round(v)))} fmt={(v) => (v ? `${v} min` : "no wait")} dim={!on} />
        <Slider text="Give up after the cross (0 = never)" value={window} min={0} max={60} step={1} onChange={(v) => set("gap_entry_window_min", String(Math.round(v)))} fmt={(v) => (v ? `${v} min` : "never")} dim={!on} />
        <Slider text="Fade exit: gap narrowing N candles in a row (0 = off)" value={fadeCandles} min={0} max={10} step={1} onChange={(v) => set("gap_fade_min_candles", String(Math.round(v)))} fmt={(v) => (v ? `${v} candles` : "off")} dim={!on} />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Press Save to keep a change. The gap line is a made-up example on a 1-minute candle; the bot reads each stock&apos;s own SMAs. The sliders edit the same numbers as the boxes above ({long ? "buy" : "sell"} side).</p>
    </section>
  );
}
