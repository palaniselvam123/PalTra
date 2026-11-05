"use client";

/**
 * The P&L dashboard's charts, drawn as plain SVG (no chart library). Each has a hover layer: a crosshair with a
 * tooltip on the line charts, a per-mark tooltip on the bars. Colours come from the app's tokens (globals.css), so
 * light and dark both work; green / red keep their money meaning (above / below zero).
 */

import clsx from "clsx";
import { useEffect, useRef, useState, type ReactNode } from "react";

export const C = {
  avg: "rgb(var(--c-sky))",
  median: "rgb(var(--c-violet))",
  close: "rgb(var(--c-amber))",
  profit: "rgb(var(--c-profit))",
  loss: "rgb(var(--c-loss))",
  band: "rgb(var(--c-sky) / 0.12)",
  faint: "rgb(148 163 184 / 0.35)",
  grid: "rgb(var(--c-border) / 0.7)",
  axis: "rgb(148 163 184)",
};

export const money = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

const short = (v: number) => {
  const a = Math.abs(v);
  const s = a >= 1e5 ? `${(a / 1e5).toFixed(1)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}k` : a.toFixed(0);
  return `${v < 0 ? "−" : ""}${s}`;
};

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(320);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** "Nice" round ticks covering lo..hi, always including 0. */
function ticks(lo: number, hi: number, n = 5): number[] {
  const a = Math.min(lo, 0);
  const b = Math.max(hi, 0, a + 1);
  const span = b - a || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.floor(a / step) * step; v <= b + step * 0.001; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

const PAD = { l: 52, r: 14, t: 12, b: 26 };

export type Series = { name: string; color: string; values: number[]; width?: number; dash?: string; faint?: boolean };

function Tip({ x, w, children }: { x: number; w: number; children: ReactNode }) {
  const left = x > w * 0.6;
  return (
    <div
      className="pointer-events-none absolute top-2 z-10 min-w-[170px] rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-slate-200 shadow-card"
      style={left ? { right: w - x + 10 } : { left: x + 10 }}
    >
      {children}
    </div>
  );
}

export function Legend({ items }: { items: { name: string; color: string; dash?: boolean; box?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
      {items.map((it) => (
        <span key={it.name} className="inline-flex items-center gap-1.5">
          {it.box ? (
            <span className="h-2.5 w-3.5 rounded-sm" style={{ background: it.color }} />
          ) : (
            <svg width="18" height="6" aria-hidden>
              <line x1="0" y1="3" x2="18" y2="3" stroke={it.color} strokeWidth="2" strokeDasharray={it.dash ? "4 3" : undefined} />
            </svg>
          )}
          {it.name}
        </span>
      ))}
    </div>
  );
}

/** Lines over the time of day, with an optional min-max band, a dashed reference level and a marked time. */
export function TimeChart({
  x,
  series,
  band,
  refY,
  markX,
  height = 280,
  tip,
  onPick,
}: {
  x: string[];
  series: Series[];
  band?: { lo: number[]; hi: number[] };
  refY?: { value: number; label: string; color: string };
  markX?: { at: string; label: string };
  height?: number;
  tip?: (i: number) => ReactNode;
  onPick?: (i: number) => void;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const all = [...series.flatMap((s) => s.values), ...(band ? [...band.lo, ...band.hi] : []), ...(refY ? [refY.value] : [])];
  if (!x.length || !all.length) return <div ref={ref} className="grid h-40 place-items-center text-sm text-slate-500">No data</div>;
  const ys = ticks(Math.min(...all), Math.max(...all));
  const lo = ys[0];
  const hi = ys[ys.length - 1];
  const iw = w - PAD.l - PAD.r;
  const ih = height - PAD.t - PAD.b;
  const px = (i: number) => PAD.l + (x.length === 1 ? iw / 2 : (i / (x.length - 1)) * iw);
  const py = (v: number) => PAD.t + ih - ((v - lo) / (hi - lo || 1)) * ih;
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join("");
  const every = Math.max(1, Math.ceil(x.length / Math.max(2, Math.floor(iw / 64))));
  const mi = markX ? x.indexOf(markX.at) : -1;
  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const rel = ((e.clientX - r.left) / r.width) * w - PAD.l;
    setHover(Math.max(0, Math.min(x.length - 1, Math.round((rel / iw) * (x.length - 1)))));
  };
  return (
    <div ref={ref} className="relative w-full min-w-0">
      <svg
        width={w}
        height={height}
        className="block touch-none select-none"
        onPointerMove={move}
        onPointerLeave={() => setHover(null)}
        onClick={() => hover != null && onPick?.(hover)}
        role="img"
      >
        {ys.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={w - PAD.r} y1={py(v)} y2={py(v)} stroke={C.grid} strokeWidth={v === 0 ? 1.5 : 1} />
            <text x={PAD.l - 6} y={py(v) + 4} textAnchor="end" fontSize="11" fill={C.axis}>
              {short(v)}
            </text>
          </g>
        ))}
        {x.map((t, i) =>
          (i % every === 0 && x.length - 1 - i >= every * 0.6) || i === x.length - 1 ? (
            <text key={t} x={px(i)} y={height - 8} textAnchor="middle" fontSize="11" fill={C.axis}>
              {t}
            </text>
          ) : null
        )}
        {band ? (
          <path
            d={`${path(band.hi)}${band.lo.map((v, i) => `L${px(band.lo.length - 1 - i).toFixed(1)},${py(band.lo[band.lo.length - 1 - i]).toFixed(1)}`).join("")}Z`}
            fill={C.band}
          />
        ) : null}
        {refY ? (
          <g>
            <line x1={PAD.l} x2={w - PAD.r} y1={py(refY.value)} y2={py(refY.value)} stroke={refY.color} strokeWidth="1.5" strokeDasharray="5 4" />
            <text x={w - PAD.r} y={py(refY.value) - 5} textAnchor="end" fontSize="11" fill={refY.color}>
              {refY.label}
            </text>
          </g>
        ) : null}
        {mi >= 0 && markX ? (
          <g>
            <line x1={px(mi)} x2={px(mi)} y1={PAD.t} y2={PAD.t + ih} stroke={C.profit} strokeWidth="1.5" strokeDasharray="3 3" />
            <text x={px(mi) + 4} y={PAD.t + 12} fontSize="11" fill={C.profit}>
              {markX.label}
            </text>
          </g>
        ) : null}
        {series
          .filter((s) => s.faint)
          .map((s) => (
            <path key={s.name} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={s.width ?? 1} strokeLinejoin="round" />
          ))}
        {series
          .filter((s) => !s.faint)
          .map((s) => (
            <path
              key={s.name}
              d={path(s.values)}
              fill="none"
              stroke={s.color}
              strokeWidth={s.width ?? 2}
              strokeDasharray={s.dash}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
        {hover != null ? (
          <g>
            <line x1={px(hover)} x2={px(hover)} y1={PAD.t} y2={PAD.t + ih} stroke={C.axis} strokeWidth="1" />
            {series
              .filter((s) => !s.faint)
              .map((s) => (
                <circle key={s.name} cx={px(hover)} cy={py(s.values[hover])} r="4" fill={s.color} stroke="rgb(var(--c-surface))" strokeWidth="2" />
              ))}
          </g>
        ) : null}
      </svg>
      {hover != null ? (
        <Tip x={px(hover)} w={w}>
          <div className="mb-0.5 font-semibold text-slate-100">{x[hover]}</div>
          {tip
            ? tip(hover)
            : series
                .filter((s) => !s.faint)
                .map((s) => (
                  <div key={s.name} className="flex justify-between gap-3">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
                      {s.name}
                    </span>
                    <span className="font-mono tabular-nums">{money(s.values[hover])}</span>
                  </div>
                ))}
        </Tip>
      ) : null}
    </div>
  );
}

/** One vertical range per day: low to high, with the close marked. Click a day to pick it. */
export function DayRanges({
  days,
  picked,
  onPick,
  height = 260,
}: {
  days: { label: string; high: number; high_at: string; low: number; low_at: string; close: number }[];
  picked?: string | null;
  onPick?: (label: string) => void;
  height?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (!days.length) return <div ref={ref} className="grid h-40 place-items-center text-sm text-slate-500">No days</div>;
  const ys = ticks(Math.min(...days.map((d) => d.low)), Math.max(...days.map((d) => d.high)));
  const lo = ys[0];
  const hi = ys[ys.length - 1];
  const iw = w - PAD.l - PAD.r;
  const ih = height - PAD.t - PAD.b;
  const slot = iw / days.length;
  const cx = (i: number) => PAD.l + slot * (i + 0.5);
  const py = (v: number) => PAD.t + ih - ((v - lo) / (hi - lo || 1)) * ih;
  const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(iw / 70))));
  const tick = Math.max(3, Math.min(10, slot * 0.35));
  return (
    <div ref={ref} className="relative w-full min-w-0">
      <svg width={w} height={height} className="block select-none" onPointerLeave={() => setHover(null)} role="img">
        {ys.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={w - PAD.r} y1={py(v)} y2={py(v)} stroke={C.grid} strokeWidth={v === 0 ? 1.5 : 1} />
            <text x={PAD.l - 6} y={py(v) + 4} textAnchor="end" fontSize="11" fill={C.axis}>
              {short(v)}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const on = picked === d.label || hover === i;
          return (
            <g key={d.label} opacity={picked && picked !== d.label && hover !== i ? 0.55 : 1}>
              <rect
                x={cx(i) - slot / 2}
                y={PAD.t}
                width={slot}
                height={ih}
                fill={on ? "rgb(var(--c-hover) / 0.05)" : "transparent"}
                onPointerEnter={() => setHover(i)}
                onClick={() => onPick?.(d.label)}
                className="cursor-pointer"
              />
              <line x1={cx(i)} x2={cx(i)} y1={py(d.high)} y2={py(d.low)} stroke={C.axis} strokeWidth="2" strokeLinecap="round" pointerEvents="none" />
              <circle cx={cx(i)} cy={py(d.high)} r="4" fill={C.profit} pointerEvents="none" />
              <circle cx={cx(i)} cy={py(d.low)} r="4" fill={C.loss} pointerEvents="none" />
              <line x1={cx(i) - tick} x2={cx(i) + tick} y1={py(d.close)} y2={py(d.close)} stroke={C.close} strokeWidth="3" strokeLinecap="round" pointerEvents="none" />
              {i % every === 0 ? (
                <text x={cx(i)} y={height - 8} textAnchor="middle" fontSize="11" fill={C.axis} pointerEvents="none">
                  {d.label.length > 10 ? d.label.slice(-5) : d.label.slice(5)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {hover != null ? (
        <Tip x={cx(hover)} w={w}>
          <div className="mb-0.5 font-semibold text-slate-100">{days[hover].label}</div>
          <Row color={C.profit} name={`High at ${days[hover].high_at}`} value={days[hover].high} />
          <Row color={C.loss} name={`Low at ${days[hover].low_at}`} value={days[hover].low} />
          <Row color={C.close} name="Close" value={days[hover].close} />
          <div className="mt-0.5 text-slate-400">Given back from the high: {money(days[hover].high - days[hover].close)}</div>
        </Tip>
      ) : null}
    </div>
  );
}

function Row({ color, name, value }: { color: string; name: string; value: number }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full" style={{ background: color }} />
        {name}
      </span>
      <span className="font-mono tabular-nums">{money(value)}</span>
    </div>
  );
}

/** Vertical bars over named buckets (counts or percentages). */
export function Bars({
  items,
  color,
  height = 180,
  fmt = (v: number) => String(v),
  tip,
  maxY,
}: {
  items: { key: string; value: number }[];
  color: string | ((v: number) => string);
  height?: number;
  fmt?: (v: number) => string;
  tip?: (i: number) => ReactNode;
  maxY?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (!items.length) return <div ref={ref} className="grid h-32 place-items-center text-sm text-slate-500">No data</div>;
  const top = maxY ?? Math.max(1, ...items.map((i) => i.value));
  const iw = w - PAD.l - PAD.r;
  const ih = height - PAD.t - PAD.b;
  const slot = iw / items.length;
  const bw = Math.max(2, slot - 2);
  const py = (v: number) => PAD.t + ih - (v / top) * ih;
  const every = Math.max(1, Math.ceil(items.length / Math.max(2, Math.floor(iw / 48))));
  const ys = ticks(0, top, 4).filter((v) => v <= top);
  return (
    <div ref={ref} className="relative w-full min-w-0">
      <svg width={w} height={height} className="block select-none" onPointerLeave={() => setHover(null)} role="img">
        {ys.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={w - PAD.r} y1={py(v)} y2={py(v)} stroke={C.grid} />
            <text x={PAD.l - 6} y={py(v) + 4} textAnchor="end" fontSize="11" fill={C.axis}>
              {fmt(v)}
            </text>
          </g>
        ))}
        {items.map((it, i) => {
          const x = PAD.l + slot * i + 1;
          const h = Math.max(0, PAD.t + ih - py(it.value));
          const fill = typeof color === "function" ? color(it.value) : color;
          return (
            <g key={it.key} onPointerEnter={() => setHover(i)}>
              <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={ih} fill="transparent" />
              <rect x={x} y={py(it.value)} width={bw} height={h} rx={Math.min(4, bw / 2)} fill={fill} opacity={hover == null || hover === i ? 1 : 0.6} />
              {i % every === 0 ? (
                <text x={x + bw / 2} y={height - 8} textAnchor="middle" fontSize="11" fill={C.axis}>
                  {it.key}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {hover != null ? (
        <Tip x={PAD.l + slot * (hover + 0.5)} w={w}>
          <div className="font-semibold text-slate-100">{items[hover].key}</div>
          {tip ? tip(hover) : <div className="font-mono tabular-nums">{fmt(items[hover].value)}</div>}
        </Tip>
      ) : null}
    </div>
  );
}

/** Horizontal bars around zero (one per stock), green above zero and red below. */
export function DivergingBars({
  items,
  onPick,
  picked,
}: {
  items: { key: string; value: number; note?: string }[];
  onPick?: (key: string) => void;
  picked?: string[];
}) {
  if (!items.length) return <div className="grid h-24 place-items-center text-sm text-slate-500">No stocks</div>;
  const max = Math.max(1, ...items.map((i) => Math.abs(i.value)));
  return (
    <div className="space-y-1">
      {items.map((it) => {
        const pct = (Math.abs(it.value) / max) * 50;
        const on = picked?.includes(it.key);
        return (
          <button
            key={it.key}
            type="button"
            onClick={() => onPick?.(it.key)}
            title={it.note}
            className={clsx(
              "grid w-full grid-cols-[88px_1fr_80px] items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-white/5",
              on && "bg-sky-500/10 ring-1 ring-inset ring-sky-400/40"
            )}
          >
            <span className="truncate font-semibold text-slate-200">{it.key}</span>
            <span className="relative h-3">
              <span className="absolute inset-y-0 left-1/2 w-px bg-slate-500/60" />
              <span
                className="absolute inset-y-0.5 rounded-sm"
                style={{
                  background: it.value >= 0 ? C.profit : C.loss,
                  width: `${pct}%`,
                  left: it.value >= 0 ? "50%" : `${50 - pct}%`,
                }}
              />
            </span>
            <span className={clsx("text-right font-mono tabular-nums", it.value >= 0 ? "text-profit" : "text-loss")}>{money(it.value)}</span>
          </button>
        );
      })}
    </div>
  );
}
