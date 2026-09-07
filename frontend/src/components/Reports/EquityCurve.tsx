"use client";

import { useMemo, useState } from "react";
import type { EquityPoint } from "@/lib/api";
import { money, timestamp } from "@/lib/format";

const W = 900;
const H = 240;
const PAD = { top: 16, right: 16, bottom: 24, left: 64 };

/** Cumulative realised P&L, trade by trade, with the drawdown envelope
 *  underneath. Plotted against trade sequence rather than clock time so a
 *  quiet week does not stretch into a flat void. */
export function EquityCurve({ points }: { points: EquityPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    if (points.length === 0) return null;
    const values = points.map((p) => p.cumulative);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = max - min || 1;

    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (i: number) => PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
    const y = (v: number) => PAD.top + innerH - ((v - min) / span) * innerH;

    const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(" ");
    const area = `${line} L${x(points.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`;

    // Running peak: the ceiling the drawdown is measured from.
    let peak = -Infinity;
    const peakLine = points
      .map((p, i) => {
        peak = Math.max(peak, p.cumulative);
        return `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(peak).toFixed(1)}`;
      })
      .join(" ");

    const ticks = [max, (max + min) / 2, min].filter((v, i, arr) => arr.indexOf(v) === i);
    return { line, area, peakLine, x, y, min, max, ticks, zeroY: y(0) };
  }, [points]);

  if (!geometry) {
    return (
      <div className="rounded-card border border-border bg-surface p-6 text-center text-sm text-slate-500">
        No closed trades in this filter — nothing to plot.
      </div>
    );
  }

  const last = points[points.length - 1];
  const active = hover !== null ? points[hover] : null;

  return (
    <div className="rounded-card border border-border bg-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2 flex-wrap">
        <div className="text-sm font-medium text-slate-200">Equity Curve</div>
        <div className="text-xs text-slate-500">
          {points.length} closed trades · ending{" "}
          <span className={last.cumulative >= 0 ? "text-profit" : "text-loss"}>{money(last.cumulative, true)}</span>
        </div>
      </div>

      <div className="p-2 overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[560px]" role="img" aria-label="Cumulative profit and loss">
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.30" />
              <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {geometry.ticks.map((value) => (
            <g key={value}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={geometry.y(value)}
                y2={geometry.y(value)}
                stroke="#1e293b"
                strokeWidth="1"
              />
              <text x={PAD.left - 8} y={geometry.y(value) + 4} textAnchor="end" fontSize="11" fill="#64748b">
                {money(value)}
              </text>
            </g>
          ))}

          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={geometry.zeroY}
            y2={geometry.zeroY}
            stroke="#475569"
            strokeWidth="1"
            strokeDasharray="4 4"
          />

          <path d={geometry.area} fill="url(#equityFill)" />
          <path d={geometry.peakLine} fill="none" stroke="#334155" strokeWidth="1.5" strokeDasharray="3 3" />
          <path d={geometry.line} fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinejoin="round" />

          {points.map((p, i) => (
            <circle
              key={p.trade_id}
              cx={geometry.x(i)}
              cy={geometry.y(p.cumulative)}
              r={hover === i ? 5 : 3}
              fill={(p.pnl ?? 0) >= 0 ? "#10b981" : "#f43f5e"}
              stroke="#0b0f19"
              strokeWidth="1"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              className="cursor-pointer"
            />
          ))}
        </svg>
      </div>

      <div className="px-4 py-2 border-t border-border text-[11px] text-slate-500 flex flex-wrap gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 bg-bot" /> cumulative P&amp;L
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 bg-slate-600" /> running peak (gap = drawdown)
        </span>
        {active ? (
          <span className="text-slate-300">
            Trade #{active.trade_id} · {timestamp(active.at)} · {money(active.pnl, true)} → cumulative{" "}
            {money(active.cumulative, true)}
            {active.drawdown > 0 && ` · ${money(-active.drawdown)} below peak`}
          </span>
        ) : (
          <span>Hover a point for that trade.</span>
        )}
      </div>
    </div>
  );
}
