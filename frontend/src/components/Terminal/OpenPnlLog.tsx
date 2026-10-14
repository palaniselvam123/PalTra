"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Download, Loader2, RefreshCw, X } from "lucide-react";
import { smaApi, type OpenPnlLog as LogData, type OpenPnlPoint } from "@/lib/smaApi";
import { saveFile } from "@/lib/chartTable";
import { Pager, usePaged } from "@/components/ui/pagination";

const W = 760;
const H = 220;
const PAD = { l: 64, r: 12, t: 12, b: 26 };

const rupees = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const clock = (iso: string) => iso.slice(11, 16);

function dayText(day: string) {
  return new Date(`${day}T12:00:00+05:30`).toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "Asia/Kolkata",
  });
}

const stocksText = (p: OpenPnlPoint) =>
  p.stocks.map((s) => `${s.symbol} ${s.side === "LONG" ? "L" : "S"}×${s.qty} ${rupees(s.gross)}`).join(" · ");

/** Minutes since 09:15 IST, for the time axis. */
function minuteOf(iso: string) {
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  return h * 60 + m - (9 * 60 + 15);
}
const SESSION_MIN = 375; // 09:15 to 15:30

function Chart({ points }: { points: OpenPnlPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  const values = points.map((p) => p.gross);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const x = (iso: string) => PAD.l + (Math.min(SESSION_MIN, Math.max(0, minuteOf(iso))) / SESSION_MIN) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((top - v) / span) * (H - PAD.t - PAD.b);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.gross).toFixed(1)}`).join("");
  // The top, zero and bottom of the day (zero is the dashed line).
  const ticks = Array.from(new Set([top, 0, bottom]));
  const hours = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"];

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || !points.length) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(x(points[i].t) - px) < Math.abs(x(points[best].t) - px)) best = i;
    setHover(best);
  };
  const h = hover == null ? null : points[hover];

  return (
    <div className="relative">
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full touch-none select-none"
        role="img"
        aria-label="Open P&L before charges through the day"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className="stroke-slate-700" strokeWidth={1} strokeOpacity={0.35} />
            <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end" className="fill-slate-400 text-[10px]">
              {rupees(v)}
            </text>
          </g>
        ))}
        {/* Zero: above it the open trades are in profit, below in loss. */}
        <line x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} className="stroke-slate-400" strokeWidth={1} strokeDasharray="4 3" />
        {hours.map((t) => (
          <text key={t} x={x(`0000-00-00T${t}`)} y={H - 8} textAnchor="middle" className="fill-slate-400 text-[10px]">
            {t}
          </text>
        ))}
        <path d={line} fill="none" className="stroke-accentSky" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {h && (
          <g>
            <line x1={x(h.t)} x2={x(h.t)} y1={PAD.t} y2={H - PAD.b} className="stroke-slate-400" strokeWidth={1} />
            <circle cx={x(h.t)} cy={y(h.gross)} r={4} className="fill-accentSky stroke-surface" strokeWidth={2} />
          </g>
        )}
      </svg>
      {h && (
        <div
          className="pointer-events-none absolute top-1 max-w-[70%] rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-lg"
          style={{ left: `${Math.min(70, (x(h.t) / W) * 100)}%` }}
        >
          <div className="font-semibold text-slate-100">
            {clock(h.t)} · {rupees(h.gross)}
          </div>
          <div className="text-slate-400">{h.held ? stocksText(h) : "No open position"}</div>
        </div>
      )}
    </div>
  );
}

/** The desk's "Open" P&L, one point a minute, for any recorded day. */
export function OpenPnlLog({ desk, deskName, onClose }: { desk: string; deskName?: string; onClose: () => void }) {
  const [day, setDay] = useState<string | undefined>(undefined);
  const [data, setData] = useState<LogData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await smaApi.openPnl(desk, day));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [desk, day]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const points = data?.points ?? [];
  const newestFirst = useMemo(() => [...points].reverse(), [points]);
  const paged = usePaged(newestFirst, "terminal.openPnl.pageSize");
  const s = data?.summary;

  const csv = () => {
    const head = "Time,Open P&L before charges,Open P&L after charges,Stocks held,Stocks";
    const rows = points.map((p) => [clock(p.t), p.gross, p.net, p.held, `"${stocksText(p).replace(/₹/g, "Rs ").replace(/−/g, "-")}"`].join(","));
    saveFile(`open-pnl-${desk}-${data?.day ?? "day"}.csv`, [head, ...rows].join("\n"), "text/csv;charset=utf-8");
  };

  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Open P&L through the day" className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl">
        <div className="mb-1 flex items-center gap-2">
          <h2 className="mr-auto font-semibold text-slate-100">
            Open P&amp;L through the day{deskName ? <span className="font-normal text-slate-400"> · {deskName}</span> : null}
          </h2>
          <button type="button" aria-label="Refresh" onClick={() => void load()} className="rounded p-1 text-slate-400 hover:bg-white/5">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
          </button>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          The &quot;Open&quot; figure of the Stocks bar (open trades, before charges), saved once a minute while the market is open and kept
          {` ${data?.keep_days ?? 90} days`}. Point at the chart to see the stocks held at that minute.
        </p>

        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <label className="flex items-center gap-1.5">
            Day
            <select
              value={data?.day ?? ""}
              onChange={(e) => setDay(e.target.value || undefined)}
              className="h-8 rounded-md border border-border bg-surface px-1.5 text-xs text-slate-200"
            >
              {(data?.days ?? []).length === 0 && <option value="">Nothing recorded yet</option>}
              {(data?.days ?? []).map((d) => (
                <option key={d.day} value={d.day}>
                  {dayText(d.day)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={csv}
            disabled={!points.length}
            className="ml-auto flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-slate-300 hover:bg-white/5 disabled:opacity-40"
          >
            <Download size={13} /> CSV
          </button>
        </div>

        {error && <p className="mb-2 text-xs text-rose-300">{error}</p>}

        {!points.length ? (
          <p className="py-8 text-center text-xs text-slate-400">
            Nothing recorded {data?.day ? "on this day" : "yet"}. A point is saved every minute from 09:15 to 15:30 on market days.
          </p>
        ) : (
          <>
            {s && (
              <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
                {[
                  ["Latest", s.last, s.last_at],
                  ["Highest", s.high, s.high_at],
                  ["Lowest", s.low, s.low_at],
                ].map(([label, v, at]) => (
                  <div key={label as string} className="rounded-lg border border-border px-3 py-2">
                    <div className="text-slate-400">{label}</div>
                    <div className={clsx("font-mono text-base font-semibold", (v as number) >= 0 ? "text-emerald-300" : "text-rose-300")}>
                      {rupees(v as number)}
                    </div>
                    <div className="text-slate-500">at {clock(at as string)}</div>
                  </div>
                ))}
              </div>
            )}
            <Chart points={points} />
            <div ref={paged.anchor} className="mt-3">
              <Pager p={paged} noun="minutes" className="mb-2" />
            </div>
            <table className="w-full text-xs">
              <thead className="text-left text-slate-400">
                <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                  <th>Time</th>
                  <th className="text-right">Open (before charges)</th>
                  <th className="text-right">After charges</th>
                  <th>Stocks held</th>
                </tr>
              </thead>
              <tbody>
                {paged.shown.map((p) => (
                  <tr key={p.t} className="border-t border-white/5 [&_td]:px-2 [&_td]:py-1.5">
                    <td className="font-mono text-slate-200">{clock(p.t)}</td>
                    <td className={clsx("text-right font-mono", p.gross >= 0 ? "text-emerald-300" : "text-rose-300")}>{rupees(p.gross)}</td>
                    <td className="text-right font-mono text-slate-300">{rupees(p.net)}</td>
                    <td className="text-slate-400">{p.held ? stocksText(p) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager p={paged} noun="minutes" className="mt-2" />
          </>
        )}
      </div>
    </div>,
    document.body
  );
}
