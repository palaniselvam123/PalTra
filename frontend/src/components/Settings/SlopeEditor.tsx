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
const SIDE_KEY = "sma.slope.side";
const DEG45_DEFAULT = 0.05;

type Num =
  | "slope_turn_pct"
  | "vturn_move_pct"
  | "vturn_turn_pct"
  | "vturn_within"
  | "uturn_run"
  | "uturn_min_pct"
  | "slope_lookback"
  | "slope_bend_pct"
  | "decel_lookback"
  | "decel_arm_atr"
  | "decel_drop_pct"
  | "decel_min_atr"
  | "decel_candles";

const SLOPE_CANDLES = [0, 1, 2, 3, 5, 10, 15] as const;

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
  // Which trade the picture is drawn for: a buy (the V points up then down) or a short (its mirror image).
  // The bot reads the same numbers for both; it handles the sign.
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  useEffect(() => {
    try {
      const v = Number(localStorage.getItem(DEG45_KEY));
      if (v > 0) setDeg45(v);
      if (localStorage.getItem(SIDE_KEY) === "SHORT") setSide("SHORT");
    } catch {
      /* private window */
    }
  }, []);
  const chooseSide = (next: "LONG" | "SHORT") => {
    setSide(next);
    try {
      localStorage.setItem(SIDE_KEY, next);
    } catch {
      /* private window */
    }
  };
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
  const lookback = Math.min(10, Math.max(1, Math.round(num("slope_lookback", 2))));
  const bend = Math.min(100, Math.max(0, num("slope_bend_pct", 60)));
  const slopeMinutes = Number(form.slope_minutes ?? 0) || 0;
  const dLook = Math.min(10, Math.max(1, Math.round(num("decel_lookback", 2))));
  const dArm = num("decel_arm_atr", 0.1);
  const dDrop = Math.min(100, Math.max(0, num("decel_drop_pct", 50)));
  const dMin = num("decel_min_atr", 0.02);
  const dCandles = Math.min(5, Math.max(1, Math.round(num("decel_candles", 2))));
  // Which rules read these numbers (the bot ignores a rule's numbers while its tick box is off).
  const turnExitOn = Boolean(form.slope_exit) && form.slope_turn !== false;
  const bendOn = Boolean(form.slope_exit) && bend > 0;
  const vturnOn = Boolean(form.vturn_exit);
  const decelOn = Boolean(form.decel_exit);
  const turnEntryOn = Boolean(form.turn_entry);
  const uOn = Boolean(form.uturn_on) && (turnEntryOn || Boolean(form.turn_alert));

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
  // Bend exit: the run eased to (100 − bend)% of its steepest; drawn as a flatter leg on from the peak.
  const bendSlope = move * (1 - bend / 100);
  const bendY = Math.max(10, peakY - rise(bendSlope) * 3);
  // U-turn, on the right: down uRun candles (capped on screen) at uMin, a flat rounding, up again.
  const uCandles = Math.min(8, uRun);
  const ux0 = endX + 70;
  const uDrop = Math.min(60, rise(uMin) * uCandles);
  const uTop = base - 50;
  const uPath = `M ${ux0} ${uTop} L ${ux0 + uCandles * CW} ${uTop + uDrop} Q ${ux0 + uCandles * CW + 1.5 * CW} ${uTop + uDrop + 10} ${
    ux0 + uCandles * CW + 3 * CW
  } ${uTop + uDrop} L ${ux0 + 2 * uCandles * CW + 3 * CW} ${uTop}`;
  const ref45 = { x: W - 150, y: H - 24, len: 36 };
  // The picture is worked out for a buy; a short is its mirror image (flipped top to bottom).
  const my = (y: number) => (side === "SHORT" ? H - y : y);
  /** A label's baseline: `off` px from the point in the buy picture (negative = above), flipped for a short. */
  const ty = (y: number, off: number) => (side === "SHORT" ? H - y - off + (off < 0 ? 8 : -4) : y + off);
  const mirrorPath = (d: string) => (side === "SHORT" ? d.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g, (_m, x, y) => `${x} ${H - Number(y)}`) : d);
  const up = side === "LONG" ? "up" : "down";
  const down = side === "LONG" ? "down" : "up";

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
    const raw = toSvg(e);
    const p = { x: raw.x, y: my(raw.y) };
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

  type Show = "angle" | "count" | "pct" | "atr";
  const slider = (key: Num, text: string, value: number, min: number, max: number, step: number, show: Show = "angle", dim = false) => (
    <label className={clsx("block text-xs text-slate-300", dim && "opacity-60")}>
      <span className="flex items-center justify-between gap-2">
        <span>{text}</span>
        <span className="flex items-center gap-1.5 font-mono text-slate-100">
          {show === "angle" ? (
            <>
              {clean(value)}%/c ·
              <input
                type="number"
                min={0}
                max={85}
                step={1}
                value={Math.round(slopeAngle(value, deg45))}
                onChange={(e) => set(key, clean(Math.min(5, slopeFromAngle(Math.min(85, Math.max(0, Number(e.target.value) || 0)), deg45))))}
                aria-label={`${text}, in degrees`}
                title="Type any angle; it is turned into % per candle with the picture scale below"
                className="h-6 w-12 rounded border border-white/15 bg-black/30 px-1 text-right text-xs text-slate-100"
              />
              °
            </>
          ) : show === "pct" ? (
            `${clean(value)}%`
          ) : show === "atr" ? (
            `${clean(value)} ATR/c`
          ) : (
            String(value)
          )}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        onChange={(e) => set(key, show === "count" ? String(Math.round(Number(e.target.value))) : clean(Number(e.target.value)))}
        className="mt-1 w-full accent-sky-400"
        aria-label={text}
      />
    </label>
  );
  // The rule strip: each rule's tick box, so the picture says which numbers the bot reads.
  const rule = (on: boolean, text: string, when: string, toggle: (next: boolean) => void, disabled = false) => (
    <label
      className={clsx(
        "flex min-h-9 items-center gap-2 rounded-md px-2 py-1 text-xs ring-1 ring-inset",
        on ? "bg-emerald-500/10 text-slate-100 ring-emerald-400/40" : "text-slate-400 ring-white/10",
        disabled && "opacity-50"
      )}
      title={when}
    >
      <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => toggle(e.target.checked)} className="accent-emerald-400" />
      <span className="font-semibold">{text}</span>
      <span className={clsx("ml-auto font-mono", on ? "text-emerald-300" : "text-slate-500")}>{on ? "ON" : "off"}</span>
    </label>
  );
  // Deceleration guard picture: a run at the arm slope, then `dCandles` legs each a bend flatter, down to the drop level.
  const D = { w: 300, h: 120, cw: 26, base: 95, x0: 16 };
  const dRise = (atr: number) => atr * 220; // picture only: 0.1 ATR/candle rises 22 px per candle
  const dPeakX = D.x0 + 4 * D.cw;
  const dPeakY = D.base - Math.min(70, dRise(dArm) * 4);
  const dLegs: string[] = [`${D.x0},${D.base}`, `${dPeakX},${dPeakY}`];
  let dx = dPeakX;
  let dy = dPeakY;
  let dSlope = dArm;
  for (let k = 0; k < dCandles; k += 1) {
    dSlope = Math.max(dArm * (1 - dDrop / 100), dSlope - dMin);
    dx += D.cw;
    dy -= dRise(dSlope);
    dLegs.push(`${dx},${dy}`);
  }
  const dEndSlope = dArm * (1 - dDrop / 100);

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
        its % per candle and its angle; the angle follows the convention at the bottom right.{" "}
        The picture is drawn for a buy or a short (switch below): a short is the mirror image with the same numbers.{" "}
        <span className="text-slate-300">Every level is a minimum, not an exact value</span>: &quot;turn back ≥ 0.015%/c&quot; fires on
        the first closed candle whose slope is at least that against the trade. The chart shades where that is in price
        (&quot;Exit zone&quot; / &quot;Buy zone&quot;): the next candle&apos;s close at which the slope reaches the level.
      </p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3" role="group" aria-label="Which slope rules are on">
        {rule(turnExitOn, "Turn exit", "Closes when the SMA 9 slope is at least the turn level against the trade (SMA9_TURN).", (on) => {
          set("slope_turn", on);
          if (on) set("slope_exit", true);
        })}
        {rule(bendOn, "Bend exit", "Closes when the slope has eased by the bend % from its steepest since the entry (SMA9_BEND). 0% = off.", (on) => {
          if (on) {
            set("slope_exit", true);
            if (bend <= 0) set("slope_bend_pct", "60");
          } else set("slope_bend_pct", "0");
        })}
        {rule(vturnOn, "V-turn exit", "Closes on a steep run then a steep turn back within the window (SMA9_VTURN).", (on) => set("vturn_exit", on))}
        {rule(decelOn, "Deceleration guard", "Closes when the line is still bending flatter, before it turns (SMA9_DECEL); read in ATR per candle.", (on) =>
          set("decel_exit", on)
        )}
        {rule(turnEntryOn, "Turn entry (reverse on the V)", "Opens a trade on the V (the same steep run / turn back numbers): a short's V-turn up buys. Without it a slope exit only closes.", (on) =>
          set("turn_entry", on)
        )}
        {rule(uOn, "U-turn (turn entry / alert)", "The turn entry and alert also fire on a rounded U: a run one way, then the first candle the other way.", (on) => set("uturn_on", on), !turnEntryOn && !Boolean(form.turn_alert))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-300" role="radiogroup" aria-label="Draw the picture for">
        <span className="text-slate-400">Draw it for:</span>
        {(["LONG", "SHORT"] as const).map((s) => (
          <label key={s} className={clsx("flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 ring-1 ring-inset", side === s ? "bg-sky-400/15 text-sky-100 ring-sky-400/50" : "ring-white/15 hover:bg-white/5")}>
            <input type="radio" name="slope-side" value={s} checked={side === s} onChange={() => chooseSide(s)} className="accent-sky-400" />
            {s === "LONG" ? "a buy (bullish): SMA 9 rises, then turns down" : "a short (bearish): SMA 9 falls, then turns up"}
          </label>
        ))}
        <span className="text-slate-500">Same numbers both ways; the bot flips the sign for a short.</span>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 w-full touch-none select-none rounded-md bg-black/30"
        onPointerMove={onMove}
        onPointerUp={stop}
        onPointerCancel={stop}
        onPointerLeave={stop}
        role="img"
        aria-label={`SMA ${fast} slope picture for a ${side === "LONG" ? "buy" : "short"}: steep run ${label(move)}, turn back ${label(turn)} within ${within} candles, turn exit at ${label(turnExit)}, U-turn ${uRun} candles at ${label(uMin)}`}
      >
        {/* candle grid */}
        {Array.from({ length: Math.floor(W / CW) }, (_, i) => (
          <line key={i} x1={i * CW} x2={i * CW} y1={0} y2={H} stroke="rgba(148,163,184,0.08)" />
        ))}
        <line x1={0} x2={W} y1={my(base)} y2={my(base)} stroke="rgba(148,163,184,0.25)" strokeDasharray="4 4" />
        {/* V-turn: the run with the trade (green), the turn back against it (red); a short is the mirror image */}
        <polyline points={`${vx0},${my(base)} ${peakX},${my(peakY)}`} fill="none" stroke="#34d399" strokeWidth={3} strokeLinecap="round" />
        <polyline points={`${peakX},${my(peakY)} ${endX},${my(endY)}`} fill="none" stroke="#fb7185" strokeWidth={3} strokeLinecap="round" />
        <line x1={peakX} y1={my(peakY)} x2={peakX + 3 * CW} y2={my(texY)} stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="5 4" />
        {bendOn ? (
          <>
            <line x1={peakX} y1={my(peakY)} x2={peakX + 3 * CW} y2={my(bendY)} stroke="#38bdf8" strokeWidth={1.5} strokeDasharray="2 3" />
            <text x={vx0} y={side === "LONG" ? Math.max(12, peakY - 24) : Math.min(H - 4, H - peakY + 32)} fill="#38bdf8" fontSize={11} fontFamily="monospace">
              bend exit ≤ {label(bendSlope)} ({bend}% off the steepest)
            </text>
          </>
        ) : null}
        <text x={vx0} y={ty(peakY, -10)} fill="#34d399" fontSize={11} fontFamily="monospace">
          steep {side === "LONG" ? "rise" : "fall"} ≥ {label(move)} (with the {side === "LONG" ? "buy" : "short"})
        </text>
        <text x={peakX + 6} y={ty(endY, 16)} fill="#fb7185" fontSize={11} fontFamily="monospace">
          turn back {down} ≥ {label(turn)} within {within} candle{within === 1 ? "" : "s"} → exit{turnEntryOn ? ` + ${side === "LONG" ? "sell" : "buy"}` : ""}
        </text>
        <text x={peakX + 3 * CW + 6} y={ty(Math.min(texY, endY), -8)} fill="#f59e0b" fontSize={11} fontFamily="monospace">
          turn exit at {label(turnExit)} {down}
        </text>
        <circle cx={peakX} cy={my(peakY)} r={8} fill="#0f172a" stroke="#34d399" strokeWidth={2} className="cursor-ns-resize" onPointerDown={start("peak")} />
        <circle cx={endX} cy={my(endY)} r={8} fill="#0f172a" stroke="#fb7185" strokeWidth={2} className="cursor-move" onPointerDown={start("end")} />
        <text x={vx0} y={side === "LONG" ? H - 10 : 14} fill="#94a3b8" fontSize={10} fontFamily="monospace">
          {side === "LONG" ? "Buy" : "Short"} · V-turn · {RUN} candles {up}, {within} back {down} · one grid column = one candle
        </text>
        {/* U-turn */}
        <path d={mirrorPath(uPath)} fill="none" stroke={form.uturn_on ? "#a78bfa" : "rgba(167,139,250,0.35)"} strokeWidth={3} strokeLinecap="round" />
        <text x={ux0} y={ty(uTop, -10)} fill={form.uturn_on ? "#a78bfa" : "rgba(167,139,250,0.5)"} fontSize={11} fontFamily="monospace">
          U-turn · {uRun} candles {down} ≥ {label(uMin)}, then {up}{form.uturn_on ? "" : " (off)"}
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
        {slider("vturn_within", "Turn back within (candles)", within, 1, 10, 1, "count")}
        {slider("slope_turn_pct", "Turn exit at (against the trade)", turnExit, 0, 0.2, 0.001)}
        {slider("uturn_run", "U-turn: run before (candles)", uRun, 2, 30, 1, "count", !uOn)}
        {slider("uturn_min_pct", "U-turn: slope at least", uMin, 0, 0.05, 0.0005, "angle", !uOn)}
        {slider("slope_lookback", "Slope over (candles): SMA 9 now vs this many candles ago", lookback, 1, 10, 1, "count")}
        {slider("slope_bend_pct", "Bend exit: % off the steepest slope since the entry (0 = off)", bend, 0, 100, 5, "pct", !bendOn)}
        <label className="block text-xs text-slate-300">
          <span className="flex items-center justify-between gap-2">
            <span>Slope candle: read the slope on</span>
            <span className="font-mono text-slate-100">{slopeMinutes ? `${slopeMinutes} min` : `the bot's ${form.candle_minutes ?? 1}-min candle`}</span>
          </span>
          <select
            value={String(SLOPE_CANDLES.includes(slopeMinutes as (typeof SLOPE_CANDLES)[number]) ? slopeMinutes : 0)}
            onChange={(e) => set("slope_minutes", e.target.value)}
            aria-label="Slope candle"
            className="mt-1 block h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-xs text-slate-100"
          >
            {SLOPE_CANDLES.map((m) => (
              <option key={m} value={String(m)}>
                {m === 0 ? `The bot's own candle (${form.candle_minutes ?? 1} min)` : `${m}-minute candles (a V shows ${m < Number(form.candle_minutes ?? 1) ? "sooner" : "later"})`}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-slate-300 sm:col-span-2">
          <span className="flex items-center justify-between gap-2">
            <span>
              Picture scale: 45° on this picture = this many % per candle{" "}
              <span className="text-slate-500">
                (any angle can be typed in the ° boxes; this only sets how steep the same numbers are drawn, because a chart&apos;s angle
                depends on its zoom. The bot reads only % per candle.)
              </span>
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
      <div className={clsx("mt-3 rounded-md border border-white/10 p-2", !decelOn && "opacity-60")}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h5 className="text-xs font-semibold text-slate-200">Deceleration guard, as a picture {decelOn ? "" : "(off)"}</h5>
          <span className="text-[11px] text-slate-500">In ATR per candle, so one set of numbers fits every stock; no angle</span>
        </div>
        <div className="mt-2 grid gap-3">
          <svg viewBox={`0 0 ${D.w} ${D.h}`} className="w-full max-w-[420px] rounded-md bg-black/30" role="img" aria-label={`Deceleration: run at ${clean(dArm)} ATR per candle, then ${dCandles} candles each ${clean(dMin)} ATR flatter, exit once ${dDrop}% off the peak`}>
            <line x1={0} x2={D.w} y1={D.base} y2={D.base} stroke="rgba(148,163,184,0.25)" strokeDasharray="4 4" />
            <polyline points={dLegs.slice(0, 2).join(" ")} fill="none" stroke="#34d399" strokeWidth={3} strokeLinecap="round" />
            <polyline points={dLegs.slice(1).join(" ")} fill="none" stroke="#f59e0b" strokeWidth={3} strokeLinecap="round" />
            <text x={D.x0} y={dPeakY - 8} fill="#34d399" fontSize={10} fontFamily="monospace">
              arms at ≥ {clean(dArm)} ATR/c
            </text>
            <text x={Math.min(dx + 4, D.w - 150)} y={Math.max(12, dy - 6)} fill="#f59e0b" fontSize={10} fontFamily="monospace">
              {dCandles} bending candle{dCandles === 1 ? "" : "s"} → exit
            </text>
            <text x={D.x0} y={D.h - 6} fill="#94a3b8" fontSize={10} fontFamily="monospace">
              exit once slope ≤ {clean(dEndSlope)} ATR/c ({dDrop}% off the peak)
            </text>
          </svg>
          <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
            {slider("decel_lookback", "Slope over (candles)", dLook, 1, 10, 1, "count", !decelOn)}
            {slider("decel_arm_atr", "Arms at (the run was real)", dArm, 0, 1, 0.01, "atr", !decelOn)}
            {slider("decel_drop_pct", "Down from the peak slope", dDrop, 0, 100, 5, "pct", !decelOn)}
            {slider("decel_min_atr", "Bend per candle", dMin, 0, 0.2, 0.005, "atr", !decelOn)}
            {slider("decel_candles", "Bending candles in a row", dCandles, 1, 5, 1, "count", !decelOn)}
          </div>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        The bot reads slopes in % per candle over {form.slope_lookback ?? 2} candle{Number(form.slope_lookback ?? 2) === 1 ? "" : "s"}
        {form.slope_minutes ? ` on ${form.slope_minutes}-minute candles` : ""}; the degrees are this picture&apos;s way of showing
        the same numbers. Press Save to keep a change.
      </p>
    </section>
  );
}
