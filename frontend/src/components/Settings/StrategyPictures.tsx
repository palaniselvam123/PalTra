"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * The strategy's candle numbers as pictures, like the SMA 9 slope editor: the ATR stop, the ADX trend
 * check, the candle size, and the candle rules (direction check, counter candles, tall candle).
 *
 * Each picture is drawn on a fixed, made-up run of candles so the shapes never change; only the setting
 * being edited moves. The sliders write the same fields the number boxes edit. Display only: the numbers
 * saved are the ones the bot reads.
 */
type Props = { form: SmaConfig; set: (key: keyof SmaConfig, value: string | boolean) => void };

type Candle = { o: number; h: number; l: number; c: number };

// A made-up but steady run of candles (price units): a pull-back, a climb, a wide bar, a quiet stretch.
const BARS: Candle[] = [
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
}: {
  bars: Candle[];
  x0: number;
  cw: number;
  y: (p: number) => number;
  colourOf?: (bar: Candle, i: number) => string;
  faded?: boolean;
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
          <g key={i} opacity={faded ? 0.35 : 1}>
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
  const atr = atrOf(bars, period);
  const fill = bars[bars.length - 1].c;
  const stop = side === "LONG" ? fill - mult * atr : fill + mult * atr;
  const W = 560;
  const H = 230;
  const cw = 26;
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
        <Candles bars={bars} x0={x0} cw={cw} y={y} />
        {/* one ATR, next to the last candle */}
        <line x1={atrX} x2={atrX} y1={y(last.c)} y2={y(last.c + atr)} stroke="#FACC15" strokeWidth={3} strokeLinecap="round" />
        <text x={atrX + 6} y={y(last.c + atr / 2) + 4} fill="#FACC15" fontSize={11} fontFamily={MONO}>
          1 ATR = {clean(atr)} ({period} candles)
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

// Candles used by the rule pictures: a run up, then three candles against it, then one tall bar.
const RULE_BARS: Candle[] = [
  { o: 100.0, h: 100.5, l: 99.8, c: 100.4 },
  { o: 100.4, h: 100.9, l: 100.2, c: 100.8 },
  { o: 100.8, h: 101.3, l: 100.6, c: 101.2 },
  { o: 101.2, h: 101.5, l: 100.9, c: 101.0 },
  { o: 101.0, h: 101.2, l: 100.5, c: 100.6 },
  { o: 100.6, h: 100.8, l: 100.1, c: 100.2 },
  { o: 100.2, h: 101.9, l: 100.1, c: 101.8 },
];

/**
 * Candle rules: the direction check before an entry (N candles the trade's way), counter candles against an
 * open trade (N candles against it, each with a body of at least x ATR), and the tall candle (a signal candle
 * taller than x ATR waits for a pull-back instead of ordering at once).
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
  const atr = 0.5; // the example's ATR, so bodies and heights read in ATR
  const W = 560;
  const H = 210;
  const cw = 30;
  const x0 = 16;
  // A short is the mirror image: flip the candles around the middle price.
  const mid = 101.0;
  const flip = (b: Candle): Candle => (side === "LONG" ? b : { o: 2 * mid - b.o, c: 2 * mid - b.c, h: 2 * mid - b.l, l: 2 * mid - b.h });
  const bars = RULE_BARS.map(flip);
  const lo = Math.min(...bars.map((b) => b.l)) - 0.2;
  const hi = Math.max(...bars.map((b) => b.h)) + 0.2;
  const y = (p: number) => 18 + ((hi - p) / (hi - lo)) * (H - 46);
  const withTrade = (b: Candle) => (side === "LONG" ? b.c > b.o : b.c < b.o);
  const bodyAtr = (b: Candle) => Math.abs(b.c - b.o) / atr;
  const label = (i: number, text: string, colour: string, above = true) => (
    <text key={`t${i}`} x={x0 + i * cw + cw / 2} y={above ? y(bars[i].h) - 6 : y(bars[i].l) + 14} fill={colour} fontSize={10} fontFamily={MONO} textAnchor="middle">
      {text}
    </text>
  );
  const dirText = dirRule === "COLOUR" ? "green candles" : dirRule === "BOTH" ? "green candles, each closing higher" : "each close higher than the last";
  const counterText = counterRule === "CLOSES" ? "each close lower than the last" : counterRule === "BOTH" ? "red candles, each closing lower" : "red candles";
  const want = side === "LONG" ? "buy" : "short";
  return (
    <section className="mt-3 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3" aria-label="Candle rules picture">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">
          {parts.includes("direction") ? "Candle direction check" : parts.includes("counter") ? "Counter candles" : "Tall candle"}, as a picture
        </h4>
        <SideSwitch side={side} onChange={setSide} />
      </div>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
        {parts.includes("direction") ? <Rule on={dirOn} text="Direction check (entry)" when={`A ${want} needs the last ${dirN} closed candles to go its way: ${dirText}.`} toggle={(next) => set("use_candle_dir", next)} /> : null}
        {parts.includes("counter") ? <Rule on={counterN > 0} text="Counter candles (exit)" when={`Closes the trade once ${counterN || "N"} closed candles in a row go against it (${counterText}).`} toggle={(next) => set("counter_exit_count", next ? "3" : "0")} /> : null}
        {parts.includes("tall") ? <Rule on={tallOn} text="Tall candle (wait for a pull-back)" when={`A signal candle taller than ${clean(tallMult, 1)} ATR does not order at once; the entry waits at half its height or the fast SMA.`} toggle={(next) => set("tall_pullback", next)} /> : null}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full select-none rounded-md bg-black/30" role="img" aria-label="Candle rules picture">
        <Candles bars={bars} x0={x0} cw={cw} y={y} />
        {/* direction: the first dirN candles go the trade's way */}
        {parts.includes("direction")
          ? bars.slice(0, Math.min(3, dirN)).map((b, i) => label(i, withTrade(b) ? "✓ with" : "✗", withTrade(b) ? "#34d399" : "#fb7185"))
          : null}
        {parts.includes("direction") ? (
          <text x={x0} y={H - 6} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
            direction check: the last {dirN} closed candle{dirN === 1 ? "" : "s"} before a {want} must be {dirText}
            {dirN > 3 ? ` (${dirN} needed; 3 drawn)` : ""}
          </text>
        ) : null}
        {/* counter: candles 3..5 go against the trade; their bodies in ATR */}
        {parts.includes("counter")
          ? bars.slice(3, 6).map((b, k) => {
              const i = k + 3;
              const big = bodyAtr(b) >= minBody;
              const counts = k < (counterN || 3) && !withTrade(b) && big;
              return label(i, `${counts ? "✓" : "·"} ${clean(bodyAtr(b), 1)} ATR`, counts ? "#fb7185" : "#64748b", false);
            })
          : null}
        {parts.includes("counter") ? (
          <text x={x0} y={H - 6} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
            {counterN ? `exit after ${counterN} candle${counterN === 1 ? "" : "s"} against the ${want}` : "counter exit off (N = 0)"}
            {minBody ? `, each body ≥ ${clean(minBody, 1)} ATR` : ", any body size"} · {counterText}
          </text>
        ) : null}
        {/* tall: the last bar vs tall_atr_mult × ATR */}
        {parts.includes("tall") ? (
          <>
            {(() => {
              const b = bars[6];
              const height = (b.h - b.l) / atr;
              const tall = height >= tallMult;
              const x = x0 + 6 * cw + cw / 2;
              const xr = x0 + 7 * cw + 10; // the room to the right of the tall candle
              const pull = side === "LONG" ? b.l + (b.h - b.l) / 2 : b.h - (b.h - b.l) / 2;
              return (
                <>
                  <line x1={xr} x2={xr} y1={y(b.h)} y2={y(b.l)} stroke="#FACC15" strokeWidth={3} />
                  <text x={xr + 8} y={y(side === "LONG" ? b.h : b.l) + (side === "LONG" ? 10 : -4)} fill="#FACC15" fontSize={10} fontFamily={MONO}>
                    height {clean(height, 1)} ATR {tall ? `≥ ${clean(tallMult, 1)}: tall` : `< ${clean(tallMult, 1)}: normal`}
                  </text>
                  {tall && tallOn ? (
                    <>
                      <line x1={x - cw} x2={W - 8} y1={y(pull)} y2={y(pull)} stroke="#f59e0b" strokeDasharray="5 4" strokeWidth={1.5} />
                      <text x={xr + 8} y={y(pull) - 5} fill="#f59e0b" fontSize={10} fontFamily={MONO}>
                        ← entry waits here (half-way back, or the fast SMA)
                      </text>
                    </>
                  ) : null}
                </>
              );
            })()}
            <text x={x0} y={H - 6} fill="#94a3b8" fontSize={10} fontFamily={MONO}>
              a signal candle this tall usually snaps back: {tallOn ? "the order waits for the pull-back" : "with the rule off it orders at once"}
            </text>
          </>
        ) : null}
      </svg>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {parts.includes("direction") ? <Slider text="Direction: last candles to check" value={dirN} min={1} max={10} step={1} onChange={(v) => set("candle_dir_count", String(Math.round(v)))} fmt={(v) => `${v} candle${v === 1 ? "" : "s"}`} dim={!dirOn} /> : null}
        {parts.includes("counter") ? <Slider text="Counter candles before the exit (0 = off)" value={counterN} min={0} max={10} step={1} onChange={(v) => set("counter_exit_count", String(Math.round(v)))} fmt={(v) => (v ? `${v} candle${v === 1 ? "" : "s"}` : "off")} /> : null}
        {parts.includes("counter") ? <Slider text="Each counter candle's body at least (× ATR)" value={minBody} min={0} max={5} step={0.1} onChange={(v) => set("counter_min_body", clean(v, 1))} fmt={(v) => (v ? `${clean(v, 1)} ATR` : "any size")} dim={counterN === 0} /> : null}
        {parts.includes("tall") ? <Slider text="Tall when the candle's height is at least (× ATR)" value={tallMult} min={0.5} max={5} step={0.1} onChange={(v) => set("tall_atr_mult", clean(v, 1))} fmt={(v) => `${clean(v, 1)} ATR`} dim={!tallOn} /> : null}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Press Save to keep a change. The candles are a made-up example with an ATR of {atr}; the bot reads each stock&apos;s own.</p>
    </section>
  );
}
