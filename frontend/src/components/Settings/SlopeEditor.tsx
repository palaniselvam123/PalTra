"use client";

import clsx from "clsx";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * The SMA 9 slope settings as a picture. The bot reads slopes in % per candle, which has no
 * angle of its own (a chart's angle depends on its zoom), so the picture uses one stated
 * convention, "45° = X % per candle" (kept in this browser), and labels every leg with both
 * its % per candle and the angle that gives. Dragging a handle or moving a slider writes the
 * same fields the number boxes edit, and typing a number moves the curve. Display only: the
 * numbers saved are the ones the bot reads.
 */
const DEG45_KEY = "sma.slope.deg45";
const DEG45_DEFAULT = 0.05;

type Num = "slope_turn_pct" | "vturn_move_pct" | "vturn_turn_pct" | "vturn_within" | "uturn_run" | "uturn_min_pct";

export function slopeAngle(pctPerCandle: number, deg45: number): number {
  if (!deg45) return 0;
  return (Math.atan(pctPerCandle / deg45) * 180) / Math.PI;
}

/** A slope from an angle under the same convention. */
export function slopeFromAngle(deg: number, deg45: number): number {
  return Math.tan((deg * Math.PI) / 180) * deg45;
}

function clean(v: number, digits = 4): string {
  return String(Number(v.toFixed(digits)));
}

const PRESETS: { id: string; label: string; blurb: string; apply: (deg45: number) => Partial<Record<keyof SmaConfig, string | boolean>> }[] = [
  {
    id: "v",
    label: "V-turn (sharp)",
    blurb: "A steep run, then a sharp turn back within a few candles: the point of a V.",
    apply: () => ({ vturn_exit: true, vturn_move_pct: "0.02", vturn_turn_pct: "0.015", vturn_within: "6" }),
  },
  {
    id: "u",
    label: "U-turn (rounded)",
    blurb: "A gentle run one way, a flat top, then the other way: a rounded U, read by the turn alert / entry.",
    apply: () => ({ uturn_on: true, uturn_run: "5", uturn_min_pct: "0.005" }),
  },
  {
    id: "45",
    label: "45° slope",
    blurb: "Steep run and turn back both at 45° under the convention below.",
    apply: (deg45) => ({ vturn_exit: true, vturn_move_pct: clean(deg45), vturn_turn_pct: clean(deg45), vturn_within: "6", slope_turn_pct: clean(deg45) }),
  },
];

export function SlopeEditor({
  form,
  set,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
}) {
  const [deg45, setDeg45] = useState(DEG45_DEFAULT);
  useEffect(() => {
    try {
      const v = Number(localStorage.getItem(DEG45_KEY));
      if (v > 0) setDeg45(v);
    } catch {
      /* private window */
    }
  }, []);
  const saveDeg45 = (v: number) => {
    const next = Math.min(1, Math.max(0.005, v));
    setDeg45(next);
    try {
      localStorage.setItem(DEG45_KEY, String(next));
    } catch {
      /* private window */
    }
  };

  const num = (key: Num, fallback: number) => {
    const v = Number(form[key]);
    return Number.isFinite(v) ? v : fallback;
  };
  const move = num("vturn_move_pct", 0.02);
  const turn = num("vturn_turn_pct", 0.015);
  const within = Math.min(10, Math.max(1, Math.round(num("vturn_within", 6))));
  const turnExit = num("slope_turn_pct", 0.02);
  const uRun = Math.min(30, Math.max(2, Math.round(num("uturn_run", 5))));
  const uMin = num("uturn_min_pct", 0.005);
  const fast = form.sma_fast ?? 9;

  // Geometry: one candle = CW px wide; a slope of deg45 % per candle rises CW px per candle (45°).
  const W = 560;
  const H = 230;
  const CW = 26;
  const rise = (pct: number) => (pct / deg45) * CW;
  const RUN = 4; // candles drawn for the steep run
  const base = 150;
  const vx0 = 30;
  const peakX = vx0 + RUN * CW;
  const peakY = base - Math.min(120, rise(move) * RUN);
  const endX = peakX + within * CW;
  const endY = Math.min(H - 20, peakY + rise(turn) * within);
  // Turn-exit level: a dashed leg at that slope from the peak, as "this is the angle that counts as a turn".
  const texY = Math.min(H - 20, peakY + rise(turnExit) * 3);
  // U-turn, on the right: down uRun candles (capped on screen) at uMin, a flat rounding, up again.
  const uCandles = Math.min(8, uRun);
  const ux0 = endX + 70;
  const uDrop = Math.min(60, rise(uMin) * uCandles);
  const uTop = base - 50;
  const uPath = `M ${ux0} ${uTop} L ${ux0 + uCandles * CW} ${uTop + uDrop} Q ${ux0 + uCandles * CW + 1.5 * CW} ${uTop + uDrop + 10} ${
    ux0 + uCandles * CW + 3 * CW
  } ${uTop + uDrop} L ${ux0 + 2 * uCandles * CW + 3 * CW} ${uTop}`;
  const ref45 = { x: W - 150, y: H - 24, len: 36 };

  // Dragging the peak sets the steep run; dragging the turn-back end sets the turn back and the window.
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<"peak" | "end" | null>(null);
  const toSvg = (e: ReactPointerEvent | PointerEvent) => {
    const el = svgRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  };
  const onMove = (e: ReactPointerEvent) => {
    if (!drag.current) return;
    const p = toSvg(e);
    if (drag.current === "peak") {
      const dy = Math.max(0, base - p.y);
      set("vturn_move_pct", clean(Math.min(5, ((dy / RUN) * deg45) / CW)));
    } else {
      const dy = Math.max(0, p.y - peakY);
      const candles = Math.min(10, Math.max(1, Math.round((p.x - peakX) / CW)));
      set("vturn_within", String(candles));
      set("vturn_turn_pct", clean(Math.min(5, ((dy / candles) * deg45) / CW)));
    }
  };
  const start = (which: "peak" | "end") => (e: ReactPointerEvent) => {
    drag.current = which;
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const stop = () => {
    drag.current = null;
  };

  const deg = (pct: number) => `${slopeAngle(pct, deg45).toFixed(0)}°`;
  const label = (pct: number) => `${clean(pct)}%/c · ${deg(pct)}`;
  const matches = (p: (typeof PRESETS)[number]) => {
    const want = p.apply(deg45);
    return Object.entries(want).every(([k, v]) => (typeof v === "boolean" ? Boolean(form[k as keyof SmaConfig]) === v : Number(form[k as keyof SmaConfig]) === Number(v)));
  };
  const applyPreset = (p: (typeof PRESETS)[number]) => {
    for (const [k, v] of Object.entries(p.apply(deg45))) set(k as keyof SmaConfig, v as string | boolean);
  };

  const slider = (
    key: Num,
    text: string,
    value: number,
    min: number,
    max: number,
    step: number,
    angle = true
  ) => (
    <label className="block text-xs text-slate-300">
      <span className="flex items-center justify-between gap-2">
        <span>{text}</span>
        <span className="font-mono text-slate-100">
          {angle ? `${clean(value)}%/c · ${deg(value)}` : String(value)}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        onChange={(e) => set(key, angle ? clean(Number(e.target.value)) : String(Math.round(Number(e.target.value))))}
        className="mt-1 w-full accent-sky-400"
        aria-label={text}
      />
    </label>
  );

  return (
    <section id="sma-slope-editor" aria-label="Slope editor" className="mt-3 scroll-mt-24 rounded-lg border border-sky-400/20 bg-sky-500/[0.04] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">SMA {fast} slope, as a picture</h4>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Slope presets">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p)}
              aria-pressed={matches(p)}
              title={p.blurb}
              className={clsx(
                "min-h-8 rounded-full px-2.5 text-xs font-semibold ring-1 ring-inset",
                matches(p) ? "bg-sky-400/20 text-sky-100 ring-sky-400/50" : "text-slate-300 ring-white/15 hover:bg-white/5"
              )}
            >
              {p.label}
            </button>
          ))}
          <span className={clsx("min-h-8 rounded-full px-2.5 text-xs font-semibold leading-8 ring-1 ring-inset", PRESETS.some(matches) ? "text-slate-500 ring-white/10" : "bg-amber-400/15 text-amber-200 ring-amber-400/40")}>
            Custom
          </span>
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Drag the two handles, move the sliders, or type in the boxes above: all of them change the same settings. Each leg shows
        its % per candle and its angle; the angle follows the convention at the bottom right.
      </p>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 w-full touch-none select-none rounded-md bg-black/30"
        onPointerMove={onMove}
        onPointerUp={stop}
        onPointerCancel={stop}
        onPointerLeave={stop}
        role="img"
        aria-label={`SMA ${fast} slope picture: steep run ${label(move)}, turn back ${label(turn)} within ${within} candles, turn exit at ${label(turnExit)}, U-turn ${uRun} candles at ${label(uMin)}`}
      >
        {/* candle grid */}
        {Array.from({ length: Math.floor(W / CW) }, (_, i) => (
          <line key={i} x1={i * CW} x2={i * CW} y1={0} y2={H} stroke="rgba(148,163,184,0.08)" />
        ))}
        <line x1={0} x2={W} y1={base} y2={base} stroke="rgba(148,163,184,0.25)" strokeDasharray="4 4" />
        {/* V-turn */}
        <polyline points={`${vx0},${base} ${peakX},${peakY}`} fill="none" stroke="#34d399" strokeWidth={3} strokeLinecap="round" />
        <polyline points={`${peakX},${peakY} ${endX},${endY}`} fill="none" stroke="#fb7185" strokeWidth={3} strokeLinecap="round" />
        <line x1={peakX} y1={peakY} x2={peakX + 3 * CW} y2={texY} stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="5 4" />
        <text x={vx0} y={peakY - 10} fill="#34d399" fontSize={11} fontFamily="monospace">
          steep run ≥ {label(move)}
        </text>
        <text x={peakX + 6} y={endY + 16} fill="#fb7185" fontSize={11} fontFamily="monospace">
          turn back ≥ {label(turn)} within {within} candle{within === 1 ? "" : "s"}
        </text>
        <text x={peakX + 3 * CW + 6} y={Math.min(texY, endY) - 8} fill="#f59e0b" fontSize={11} fontFamily="monospace">
          turn exit at {label(turnExit)}
        </text>
        <circle cx={peakX} cy={peakY} r={8} fill="#0f172a" stroke="#34d399" strokeWidth={2} className="cursor-ns-resize" onPointerDown={start("peak")} />
        <circle cx={endX} cy={endY} r={8} fill="#0f172a" stroke="#fb7185" strokeWidth={2} className="cursor-move" onPointerDown={start("end")} />
        <text x={vx0} y={H - 10} fill="#94a3b8" fontSize={10} fontFamily="monospace">
          V-turn · {RUN} candles up, {within} back · one grid column = one candle
        </text>
        {/* U-turn */}
        <path d={uPath} fill="none" stroke={form.uturn_on ? "#a78bfa" : "rgba(167,139,250,0.35)"} strokeWidth={3} strokeLinecap="round" />
        <text x={ux0} y={uTop - 10} fill={form.uturn_on ? "#a78bfa" : "rgba(167,139,250,0.5)"} fontSize={11} fontFamily="monospace">
          U-turn · {uRun} candles ≥ {label(uMin)}{form.uturn_on ? "" : " (off)"}
        </text>
        {/* 45° reference */}
        <line x1={ref45.x} y1={ref45.y} x2={ref45.x + ref45.len} y2={ref45.y - ref45.len} stroke="#e2e8f0" strokeWidth={1.5} />
        <line x1={ref45.x} y1={ref45.y} x2={ref45.x + ref45.len} y2={ref45.y} stroke="rgba(226,232,240,0.5)" strokeWidth={1} />
        <text x={ref45.x - 4} y={ref45.y + 14} fill="#e2e8f0" fontSize={10} fontFamily="monospace">
          45° = {clean(deg45)}%/candle
        </text>
      </svg>
      <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {slider("vturn_move_pct", "Steep run (with the trade)", move, 0, 0.2, 0.001)}
        {slider("vturn_turn_pct", "Turn back (against the trade)", turn, 0, 0.2, 0.001)}
        {slider("vturn_within", "Turn back within (candles)", within, 1, 10, 1, false)}
        {slider("slope_turn_pct", "Turn exit at (against the trade)", turnExit, 0, 0.2, 0.001)}
        {slider("uturn_run", "U-turn: run before (candles)", uRun, 2, 30, 1, false)}
        {slider("uturn_min_pct", "U-turn: slope at least", uMin, 0, 0.05, 0.0005)}
        <label className="block text-xs text-slate-300 sm:col-span-2">
          <span className="flex items-center justify-between gap-2">
            <span>
              Angle convention: what counts as 45°{" "}
              <span className="text-slate-500">(a chart&apos;s angle depends on its zoom; this fixes the degrees shown here)</span>
            </span>
            <span className="font-mono text-slate-100">{clean(deg45)}%/candle = 45°</span>
          </span>
          <input
            type="range"
            min={0.005}
            max={0.5}
            step={0.005}
            value={deg45}
            onChange={(e) => saveDeg45(Number(e.target.value))}
            className="mt-1 w-full accent-slate-300"
            aria-label="Angle convention: % per candle that counts as 45 degrees"
          />
        </label>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        The bot reads slopes in % per candle over {form.slope_lookback ?? 2} candle{Number(form.slope_lookback ?? 2) === 1 ? "" : "s"}
        {form.slope_minutes ? ` on ${form.slope_minutes}-minute candles` : ""}; the degrees are this picture&apos;s way of showing
        the same numbers. Press Save to keep a change.
      </p>
    </section>
  );
}
