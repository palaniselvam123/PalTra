"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Download, Loader2, RefreshCw, X } from "lucide-react";
import {
  smaApi,
  type OpenPnlLog as LiveLog,
  type OpenPnlPoint,
  type OpenPnlReplayLog,
  type OpenPnlReplayRun,
  type OpenPnlStockPoint,
} from "@/lib/smaApi";

type LogData = Pick<LiveLog, "day" | "days" | "points" | "summary" | "keep_days" | "by_stock">;
import { saveFile } from "@/lib/chartTable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { SortTh, useSort } from "@/components/Terminal/sortable";

type SortKey = "time" | "total" | "closed" | "gross" | "net" | "held";

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

// The day's P&L at that minute: closed + open (an older point without closed P&L counts as open only).
const totalOf = (p: OpenPnlPoint) => p.total_gross ?? p.gross;
const closedOf = (p: OpenPnlPoint) => p.closed_gross ?? 0;
const totalNetOf = (p: OpenPnlPoint) => p.total_net ?? p.net;

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
  const values = points.flatMap((p) => [totalOf(p), p.gross]);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const x = (iso: string) => PAD.l + (Math.min(SESSION_MIN, Math.max(0, minuteOf(iso))) / SESSION_MIN) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((top - v) / span) * (H - PAD.t - PAD.b);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(totalOf(p)).toFixed(1)}`).join("");
  const openLine = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.gross).toFixed(1)}`).join("");
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
        aria-label="Day P&L (closed + open) and open P&L before charges through the day"
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
        <path d={openLine} fill="none" className="stroke-slate-400" strokeWidth={1.25} strokeDasharray="3 3" strokeLinejoin="round" />
        <path d={line} fill="none" className="stroke-accentSky" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {h && (
          <g>
            <line x1={x(h.t)} x2={x(h.t)} y1={PAD.t} y2={H - PAD.b} className="stroke-slate-400" strokeWidth={1} />
            <circle cx={x(h.t)} cy={y(totalOf(h))} r={4} className="fill-accentSky stroke-surface" strokeWidth={2} />
          </g>
        )}
      </svg>
      {h && (
        <div
          className="pointer-events-none absolute top-1 max-w-[70%] rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-lg"
          style={{ left: `${Math.min(70, (x(h.t) / W) * 100)}%` }}
        >
          <div className="font-semibold text-slate-100">
            {clock(h.t)} · Day {rupees(totalOf(h))}
          </div>
          <div className="text-slate-400">
            Closed {rupees(closedOf(h))} · Open {rupees(h.gross)}
          </div>
          <div className="text-slate-400">{h.held ? stocksText(h) : "No open position"}</div>
        </div>
      )}
    </div>
  );
}

const COLORS = ["#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#60a5fa", "#fb923c", "#2dd4bf", "#e879f9"];

/** The picked stocks' P&L at each minute: the combined total and each stock's own (closed + open, before charges). */
type StockRow = { t: string; total: number; closed: number; open: number; parts: Record<string, number> };

function stockRows(byStock: Record<string, OpenPnlStockPoint[]>, picked: string[]): StockRow[] {
  const first = byStock[picked[0]] ?? [];
  return first.map((_, i) => {
    const row: StockRow = { t: first[i].t, total: 0, closed: 0, open: 0, parts: {} };
    for (const name of picked) {
      const p = byStock[name]?.[i];
      if (!p) continue;
      row.total += p.total;
      row.closed += p.closed;
      row.open += p.open;
      row.parts[name] = p.total;
    }
    return row;
  });
}

function rowSummary(rows: StockRow[]) {
  if (!rows.length) return null;
  let hi = rows[0];
  let lo = rows[0];
  for (const r of rows) {
    if (r.total > hi.total) hi = r;
    if (r.total < lo.total) lo = r;
  }
  const last = rows[rows.length - 1];
  return { high: hi.total, high_at: hi.t, low: lo.total, low_at: lo.t, last: last.total, last_at: last.t, basis: "gross" };
}

/** Each picked stock's day P&L as its own line, plus their total (blue) when more than one is picked. */
function StockChart({ rows, picked }: { rows: StockRow[]; picked: string[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  const values = rows.flatMap((r) => [r.total, ...Object.values(r.parts)]);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const x = (iso: string) => PAD.l + (Math.min(SESSION_MIN, Math.max(0, minuteOf(iso))) / SESSION_MIN) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((top - v) / span) * (H - PAD.t - PAD.b);
  const path = (get: (r: StockRow) => number) => rows.map((r, i) => `${i ? "L" : "M"}${x(r.t).toFixed(1)},${y(get(r)).toFixed(1)}`).join("");
  const ticks = Array.from(new Set([top, 0, bottom]));
  const hours = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"];
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || !rows.length) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    for (let i = 1; i < rows.length; i++) if (Math.abs(x(rows[i].t) - px) < Math.abs(x(rows[best].t) - px)) best = i;
    setHover(best);
  };
  const h = hover == null ? null : rows[hover];
  return (
    <div className="relative">
      <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
        {picked.length > 1 && (
          <span className="flex items-center gap-1 text-slate-300">
            <span className="h-0.5 w-4 bg-accentSky" /> Total of the {picked.length} stocks
          </span>
        )}
        {picked.map((name, i) => (
          <span key={name} className="flex items-center gap-1 text-slate-300">
            <span className="h-0.5 w-4" style={{ background: COLORS[i % COLORS.length] }} /> {name}
          </span>
        ))}
      </div>
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full touch-none select-none"
        role="img"
        aria-label="Each picked stock's day P&L before charges through the day"
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
        <line x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} className="stroke-slate-400" strokeWidth={1} strokeDasharray="4 3" />
        {hours.map((t) => (
          <text key={t} x={x(`0000-00-00T${t}`)} y={H - 8} textAnchor="middle" className="fill-slate-400 text-[10px]">
            {t}
          </text>
        ))}
        {picked.map((name, i) => (
          <path
            key={name}
            d={path((r) => r.parts[name] ?? 0)}
            fill="none"
            stroke={COLORS[i % COLORS.length]}
            strokeWidth={picked.length > 1 ? 1.25 : 2}
            strokeLinejoin="round"
          />
        ))}
        {picked.length > 1 && (
          <path d={path((r) => r.total)} fill="none" className="stroke-accentSky" strokeWidth={2} strokeLinejoin="round" />
        )}
        {h && <line x1={x(h.t)} x2={x(h.t)} y1={PAD.t} y2={H - PAD.b} className="stroke-slate-400" strokeWidth={1} />}
      </svg>
      {h && (
        <div
          className="pointer-events-none absolute top-6 max-w-[70%] rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-lg"
          style={{ left: `${Math.min(70, (x(h.t) / W) * 100)}%` }}
        >
          <div className="font-semibold text-slate-100">
            {clock(h.t)} · {picked.length > 1 ? `Total ${rupees(h.total)}` : `${picked[0]} ${rupees(h.total)}`}
          </div>
          <div className="text-slate-400">
            Closed {rupees(h.closed)} · Open {rupees(h.open)}
          </div>
          {picked.length > 1 && (
            <div className="text-slate-400">{picked.map((n) => `${n} ${rupees(h.parts[n])}`).join(" · ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

/** The desk's "Open" P&L, one point a minute, for any recorded day. */
export function OpenPnlLog({
  desk,
  deskName,
  onClose,
  startOnReplay = false,
}: {
  desk: string;
  deskName?: string;
  onClose: () => void;
  /** Open on the Replays view (the desk is showing a replay). */
  startOnReplay?: boolean;
}) {
  // Bots 1-4 replay; the research desk does not.
  const bot = /^[1-5]$/.test(desk) ? Number(desk) : null;
  const [source, setSource] = useState<"live" | "replay">(startOnReplay && bot ? "replay" : "live");
  const [runs, setRuns] = useState<OpenPnlReplayRun[]>([]);
  const [runId, setRunId] = useState<number | undefined>(undefined);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [data, setData] = useState<LogData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (source === "replay" && bot) {
        const list = (await smaApi.openPnlReplays(bot)).runs;
        setRuns(list);
        const pick = runId ?? list[0]?.run_id;
        setData(pick ? ((await smaApi.openPnlReplay(pick, day)) as OpenPnlReplayLog) : { day: null, days: [], points: [], summary: null, keep_days: 90 });
      } else {
        setData(await smaApi.openPnl(desk, day));
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [desk, day, source, bot, runId]);

  const choose = (next: "live" | "replay") => {
    setSource(next);
    setDay(undefined);
    setRunId(undefined);
    setData(null);
  };
  const runLabel = (r: OpenPnlReplayRun) =>
    `Run #${r.run_id} · ${dayText(r.first)}${r.last !== r.first ? ` – ${dayText(r.last)}` : ""} (${r.days} day${r.days === 1 ? "" : "s"})`;
  const shownRun = runId ?? runs[0]?.run_id;

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const points = data?.points ?? [];
  // Stock filter: none picked = the whole desk; one or more = those stocks' own P&L.
  const [picked, setPicked] = useState<string[]>([]);
  const byStock = useMemo(() => data?.by_stock ?? {}, [data]);
  const stockNames = useMemo(
    () =>
      Object.keys(byStock).sort((a, b) => {
        const last = (n: string) => Math.abs(byStock[n]?.[byStock[n].length - 1]?.total ?? 0);
        return last(b) - last(a) || a.localeCompare(b);
      }),
    [byStock]
  );
  const pickedHere = picked.filter((n) => byStock[n]);
  const rows = useMemo(() => (pickedHere.length ? stockRows(byStock, pickedHere) : []), [byStock, pickedHere.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (name: string) => setPicked((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name]));
  const newestFirst = useMemo(() => [...points].reverse(), [points]);
  // Newest first until a column is clicked; the third click on a column comes back here.
  const { sorted, sort, onSort } = useSort<OpenPnlPoint, SortKey>(newestFirst, (p, k) =>
    k === "time" ? p.t : k === "total" ? totalOf(p) : k === "closed" ? closedOf(p) : k === "gross" ? p.gross : k === "net" ? totalNetOf(p) : p.held
  );
  const paged = usePaged(sorted, "terminal.openPnl.pageSize");
  const rowsNewest = useMemo(() => [...rows].reverse(), [rows]);
  const pagedRows = usePaged(rowsNewest, "terminal.openPnl.stockPageSize");
  const s = pickedHere.length ? rowSummary(rows) : data?.summary;

  const csv = () => {
    if (pickedHere.length) {
      const head = ["Time", "Total of picked stocks before charges", "Closed", "Open", ...pickedHere].join(",");
      const lines = rows.map((r) => [clock(r.t), r.total.toFixed(2), r.closed.toFixed(2), r.open.toFixed(2), ...pickedHere.map((n) => (r.parts[n] ?? 0).toFixed(2))].join(","));
      saveFile(`pnl-by-stock-${source === "replay" ? `replay${shownRun ?? ""}` : desk}-${data?.day ?? "day"}.csv`, [head, ...lines].join("\n"), "text/csv;charset=utf-8");
      return;
    }
    const head = "Time,Day P&L before charges (closed + open),Closed P&L before charges,Open P&L before charges,Day P&L after charges,Open P&L after charges,Stocks held,Stocks";
    const lines = points.map((p) => [clock(p.t), totalOf(p), closedOf(p), p.gross, totalNetOf(p), p.net, p.held, `"${stocksText(p).replace(/₹/g, "Rs ").replace(/−/g, "-")}"`].join(","));
    saveFile(`open-pnl-${source === "replay" ? `replay${shownRun ?? ""}` : desk}-${data?.day ?? "day"}.csv`, [head, ...lines].join("\n"), "text/csv;charset=utf-8");
  };

  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="P&L through the day" className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl">
        <div className="mb-1 flex items-center gap-2">
          <h2 className="mr-auto font-semibold text-slate-100">
            P&amp;L through the day{deskName ? <span className="font-normal text-slate-400"> · {deskName}</span> : null}
          </h2>
          <button type="button" aria-label="Refresh" onClick={() => void load()} className="rounded p-1 text-slate-400 hover:bg-white/5">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
          </button>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          The day&apos;s P&amp;L before charges: today&apos;s closed trades plus the open ones (the blue line; the dashed line is
          open trades only),{" "}
          {source === "replay"
            ? "saved for every replayed minute of each replay run"
            : "saved once a minute while the market is open"}{" "}
          and kept {data?.keep_days ?? 90} days. Point at the chart to see the stocks held at that minute.
        </p>

        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          {bot ? (
            <div className="flex overflow-hidden rounded-md border border-border" role="group" aria-label="What to show">
              {(["live", "replay"] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  aria-pressed={source === k}
                  onClick={() => source !== k && choose(k)}
                  className={clsx("h-8 px-2.5", source === k ? "bg-accentSky/15 font-semibold text-accentSky" : "text-slate-300 hover:bg-white/5")}
                >
                  {k === "live" ? "Market days" : "Replays"}
                </button>
              ))}
            </div>
          ) : null}
          {source === "replay" && (
            <label className="flex items-center gap-1.5">
              Run
              <select
                value={shownRun ?? ""}
                onChange={(e) => {
                  setRunId(Number(e.target.value) || undefined);
                  setDay(undefined);
                }}
                className="h-8 max-w-[18rem] rounded-md border border-border bg-surface px-1.5 text-xs text-slate-200"
              >
                {runs.length === 0 && <option value="">No replay recorded yet</option>}
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {runLabel(r)}
                  </option>
                ))}
              </select>
            </label>
          )}
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

        {stockNames.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="Stocks to show">
            <span className="mr-1 text-slate-400">Stocks</span>
            <button
              type="button"
              aria-pressed={!pickedHere.length}
              onClick={() => setPicked([])}
              className={clsx(
                "h-7 rounded-md px-2 ring-1 ring-inset",
                !pickedHere.length ? "bg-accentSky/15 font-semibold text-accentSky ring-accentSky/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
              )}
            >
              All (desk total)
            </button>
            {stockNames.map((name) => {
              const on = pickedHere.includes(name);
              const last = byStock[name]?.[byStock[name].length - 1]?.total ?? 0;
              const color = on ? COLORS[pickedHere.indexOf(name) % COLORS.length] : undefined;
              return (
                <button
                  key={name}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(name)}
                  title={`${name}: ${rupees(last)} by the last logged minute. Tap to ${on ? "remove" : "add"}.`}
                  className={clsx("h-7 rounded-md px-2 ring-1 ring-inset", on ? "font-semibold text-slate-100" : "text-slate-300 ring-white/10 hover:bg-white/5")}
                  style={on ? { boxShadow: `inset 0 0 0 1px ${color}`, background: `${color}22` } : undefined}
                >
                  {name} <span className={last >= 0 ? "text-emerald-300" : "text-rose-300"}>{rupees(last)}</span>
                </button>
              );
            })}
          </div>
        )}

        {!points.length ? (
          <p className="py-8 text-center text-xs text-slate-400">
            {source === "replay"
              ? "No replay recorded yet. Play a past day with Replay and its P&L is saved minute by minute."
              : `Nothing recorded ${data?.day ? "on this day" : "yet"}. A point is saved every minute from 09:15 to 15:30 on market days.`}
          </p>
        ) : (
          <>
            {s && (
              <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
                {[
                  [s.basis === "gross" ? "Latest" : "Day P&L now", s.last, s.last_at],
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
            {pickedHere.length ? (
              <>
                <StockChart rows={rows} picked={pickedHere} />
                <div ref={pagedRows.anchor} className="mt-3">
                  <Pager p={pagedRows} noun="minutes" className="mb-2" />
                </div>
                <table className="w-full text-xs">
                  <thead className="text-left text-slate-400">
                    <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                      <th>Time</th>
                      <th className="text-right">{pickedHere.length > 1 ? "Total" : pickedHere[0]}</th>
                      <th className="text-right">Closed</th>
                      <th className="text-right">Open</th>
                      {pickedHere.length > 1 && <th>Each stock</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {pagedRows.shown.map((r) => (
                      <tr key={r.t} className="border-t border-white/5 [&_td]:px-2 [&_td]:py-1.5">
                        <td className="font-mono text-slate-200">{clock(r.t)}</td>
                        <td className={clsx("text-right font-mono font-semibold", r.total >= 0 ? "text-emerald-300" : "text-rose-300")}>{rupees(r.total)}</td>
                        <td className="text-right font-mono text-slate-300">{rupees(r.closed)}</td>
                        <td className={clsx("text-right font-mono", r.open >= 0 ? "text-emerald-300/80" : "text-rose-300/80")}>{rupees(r.open)}</td>
                        {pickedHere.length > 1 && (
                          <td className="text-slate-400">{pickedHere.map((n) => `${n} ${rupees(r.parts[n])}`).join(" · ")}</td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Pager p={pagedRows} noun="minutes" className="mt-2" />
              </>
            ) : (
            <>
            <Chart points={points} />
            <div ref={paged.anchor} className="mt-3">
              <Pager p={paged} noun="minutes" className="mb-2" />
            </div>
            <table className="w-full text-xs">
              <thead className="text-left text-slate-400">
                <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                  <SortTh label="Time" k="time" text sort={sort} onSort={onSort} title="Sort by time (earliest first, then latest first)" />
                  <SortTh label="Day (closed + open)" k="total" num sort={sort} onSort={onSort} title="The day's P&L before charges at that minute: closed trades plus open ones" />
                  <SortTh label="Closed" k="closed" num sort={sort} onSort={onSort} title="Today's closed trades before charges, at that minute" />
                  <SortTh label="Open" k="gross" num sort={sort} onSort={onSort} title="Open trades before charges, at that minute" />
                  <SortTh label="Day after charges" k="net" num sort={sort} onSort={onSort} title="The day's P&L after charges (closed + open)" />
                  <SortTh label="Stocks held" k="held" sort={sort} onSort={onSort} title="Sort by how many stocks were held (most first)" />
                </tr>
              </thead>
              <tbody>
                {paged.shown.map((p) => (
                  <tr key={p.t} className="border-t border-white/5 [&_td]:px-2 [&_td]:py-1.5">
                    <td className="font-mono text-slate-200">{clock(p.t)}</td>
                    <td className={clsx("text-right font-mono font-semibold", totalOf(p) >= 0 ? "text-emerald-300" : "text-rose-300")}>{rupees(totalOf(p))}</td>
                    <td className="text-right font-mono text-slate-300">{rupees(closedOf(p))}</td>
                    <td className={clsx("text-right font-mono", p.gross >= 0 ? "text-emerald-300/80" : "text-rose-300/80")}>{rupees(p.gross)}</td>
                    <td className="text-right font-mono text-slate-300">{rupees(totalNetOf(p))}</td>
                    <td className="text-slate-400">{p.held ? stocksText(p) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager p={paged} noun="minutes" className="mt-2" />
            </>
            )}
          </>
        )}
      </div>
    </div>,
    document.body
  );
}
