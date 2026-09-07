"use client";

/**
 * Lightweight inline visualisations.
 *
 * These exist so colour has something to *do*. A dashboard made colourful by
 * tinting its panels just adds noise; a dashboard made colourful by drawing
 * its equity curve, its win/loss split and its exposure is carrying more
 * information in the same space. All of these render as inline SVG — no chart
 * library, no layout cost, and they scale to their container.
 */

import clsx from "clsx";
import { useId } from "react";

/* ------------------------------------------------------------ Sparkline -- */

/**
 * Area sparkline with a gradient fade. Direction colours it: a series ending
 * above where it started is green, below is red. Passing an explicit
 * `stroke` overrides that for series where up/down carries no P&L meaning.
 */
export function AreaSpark({
  values,
  className,
  stroke,
  height = 48,
  showDot = true,
}: {
  values: number[];
  className?: string;
  stroke?: string;
  height?: number;
  showDot?: boolean;
}) {
  const id = useId();
  if (values.length < 2) {
    return <div className={clsx("w-full", className)} style={{ height }} />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  // A flat series would divide by zero and collapse onto the baseline; give it
  // a nominal range so it renders as a centred straight line instead.
  const span = max - min || Math.abs(max) || 1;
  const W = 100;
  const H = 100;

  const pt = (v: number, i: number) => {
    const x = (i / (values.length - 1)) * W;
    const y = H - ((v - min) / span) * H;
    return [x, Math.max(2, Math.min(H - 2, y))] as const;
  };

  const pts = values.map(pt);
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const rising = values[values.length - 1] >= values[0];
  const color = stroke ?? (rising ? "rgb(var(--c-profit))" : "rgb(var(--c-loss))");
  const [lastX, lastY] = pts[pts.length - 1];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={clsx("w-full overflow-visible", className)}
      style={{ height }}
      aria-hidden
    >
      <defs>
        <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-${id})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {showDot && (
        <circle cx={lastX} cy={lastY} r="2.5" fill={color} vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

/* ------------------------------------------------------------- MiniBars -- */

/** Per-trade P&L as bars — the shape of a run, win/loss coloured. */
export function MiniBars({ values, height = 32 }: { values: number[]; height?: number }) {
  if (values.length === 0) return <div style={{ height }} />;
  const peak = Math.max(...values.map(Math.abs)) || 1;

  return (
    <div className="flex items-center gap-[2px]" style={{ height }}>
      {values.map((v, i) => {
        const pctHeight = Math.max(8, (Math.abs(v) / peak) * 100);
        return (
          <div key={i} className="relative flex-1 self-stretch">
            <div
              className={clsx(
                "absolute w-full rounded-[1px]",
                v >= 0 ? "bg-profit/70" : "bg-loss/70"
              )}
              style={
                v >= 0
                  ? { bottom: "50%", height: `${pctHeight / 2}%` }
                  : { top: "50%", height: `${pctHeight / 2}%` }
              }
            />
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------- Progress -- */

/** A labelled meter. `tone` is a CSS colour, so callers pass an accent token. */
export function Meter({
  value,
  max = 100,
  tone = "rgb(var(--c-bot))",
  className,
}: {
  value: number;
  max?: number;
  tone?: string;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return (
    <div className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]", className)}>
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${tone}99, ${tone})` }}
      />
    </div>
  );
}

/** Win/loss split as a single two-tone bar — denser than two separate meters. */
export function SplitBar({ wins, losses }: { wins: number; losses: number }) {
  const total = wins + losses;
  if (total === 0) {
    return <div className="h-1.5 w-full rounded-full bg-white/[0.07]" />;
  }
  const winPct = (wins / total) * 100;
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
      <div className="h-full bg-profit transition-[width] duration-500" style={{ width: `${winPct}%` }} />
      <div className="h-full bg-loss/70 transition-[width] duration-500" style={{ width: `${100 - winPct}%` }} />
    </div>
  );
}

/* ----------------------------------------------------------------- Ring -- */

export function Ring({
  value,
  max = 100,
  size = 44,
  tone = "rgb(var(--c-bot))",
  label,
}: {
  value: number;
  max?: number;
  size?: number;
  tone?: string;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(1, value / (max || 1)));
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth="4" stroke="rgb(var(--c-border))" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth="4"
          stroke={tone}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          style={{ transition: "stroke-dashoffset 600ms ease" }}
        />
      </svg>
      {label && (
        <span className="absolute inset-0 grid place-items-center font-mono text-[10px] tabular-nums text-slate-200">
          {label}
        </span>
      )}
    </div>
  );
}
