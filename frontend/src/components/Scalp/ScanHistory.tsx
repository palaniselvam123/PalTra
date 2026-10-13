"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Download, FileText, History, Loader2, RefreshCw, Target } from "lucide-react";
import {
  smaApi,
  type ChopScanRow,
  type CrossScanRow,
  type ScanCheckGroup,
  type ScanCheckRow,
  type ScanHistory as ScanHistoryData,
  type ScanKind,
  type ScanRunDetail,
} from "@/lib/smaApi";
import { saveFile } from "@/lib/chartTable";
import { pdfText } from "@/lib/backtestPdf";
import { SortTh, useSort } from "@/components/Terminal/sortable";

const POLL_MS = 3_000;

type Saved = Partial<CrossScanRow> & Partial<ChopScanRow> & { symbol: string };
type Merged = Saved & { check?: ScanCheckRow };
type Col = {
  id: string;
  label: string;
  title?: string;
  num?: boolean;
  /** Colour + / − (a move with or against the scan's call). */
  tone?: boolean;
  get: (r: Merged) => number | string | null | undefined;
  show?: (r: Merged) => string;
};

/** "10:42" in IST from an ISO time. */
function clock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  return at.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

function dayText(day: string): string {
  const at = new Date(`${day}T12:00:00+05:30`);
  return at.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

const pct = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
const fixed = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));
const sideText = (v: string | null | undefined) => (v === "BULLISH" ? "Up ↑" : v === "BEARISH" ? "Down ↓" : "—");
const mins = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v)} min`);

const BASE: Col[] = [
  { id: "symbol", label: "Stock", get: (r) => r.symbol },
  { id: "ltp", label: "LTP", num: true, title: "Price when the scan ran", get: (r) => r.ltp, show: (r) => fixed(r.ltp) },
];

const CHOP_COLS: Col[] = [
  { id: "score", label: "Score", get: (r) => r.score },
  {
    id: "trend",
    label: "Trend",
    get: (r) => (r.trend === "STRONG_BULLISH" ? "Strong up" : r.trend === "STRONG_BEARISH" ? "Strong down" : null),
    show: (r) => (r.trend === "STRONG_BULLISH" ? "Strong up ↑" : r.trend === "STRONG_BEARISH" ? "Strong down ↓" : "—"),
  },
  {
    id: "quiet",
    label: "Quiet runner",
    get: (r) => (r.quiet_runner ? r.minutes_since_last_cross ?? 0 : null),
    show: (r) => (r.quiet_runner ? `Quiet ${Math.round(r.minutes_since_last_cross ?? 0)}m` : "—"),
  },
  {
    id: "first",
    label: "First alert",
    title: "When a scan first read the stock as quiet that day (📨 = its Telegram went)",
    get: (r) => r.first_quiet_at ?? null,
    show: (r) => (r.first_quiet_at ? `${clock(r.first_quiet_at)}${r.alerted_at ? " 📨" : ""}` : "—"),
  },
  { id: "crosses", label: "Crosses", num: true, title: "SMA crosses that day up to the scan", get: (r) => r.crosses_today },
  { id: "lastSide", label: "Last cross", get: (r) => r.last_cross_direction, show: (r) => sideText(r.last_cross_direction) },
  { id: "dayMove", label: "Day %", num: true, tone: true, get: (r) => r.day_move_pct, show: (r) => pct(r.day_move_pct) },
];

const CROSS_COLS: Col[] = [
  { id: "state", label: "State", get: (r) => (r.state === "APPROACHING" ? "About to cross" : r.state === "CROSSED" ? "Just crossed" : r.state) },
  { id: "side", label: "Side", get: (r) => r.side, show: (r) => sideText(r.side) },
  {
    id: "in",
    label: "In",
    num: true,
    title: "Estimated minutes to the cross (about to cross), or candles since it (just crossed)",
    get: (r) => (r.state === "APPROACHING" ? r.minutes_to_cross : r.crossed_candles_ago),
    show: (r) =>
      r.state === "APPROACHING" ? mins(r.minutes_to_cross) : r.crossed_candles_ago == null ? "—" : `${r.crossed_candles_ago} candle(s) ago`,
  },
  { id: "gap", label: "Gap %", num: true, get: (r) => r.gap_pct, show: (r) => (r.gap_pct == null ? "—" : `${r.gap_pct.toFixed(3)}%`) },
  { id: "speed", label: "Speed", num: true, title: "% per minute over the last 10 minutes", get: (r) => r.speed_pct_per_min, show: (r) => (r.speed_pct_per_min == null ? "—" : `${r.speed_pct_per_min.toFixed(3)}%/m`) },
];

const CHECK_COLS: Col[] = [
  { id: "call", label: "Call", title: "The way the scan pointed", get: (r) => r.check?.side, show: (r) => sideText(r.check?.side) },
  { id: "a15", label: "15 min", num: true, tone: true, title: "Move 15 minutes after the scan, with the call", get: (r) => r.check?.after_15_pct, show: (r) => pct(r.check?.after_15_pct) },
  { id: "a30", label: "30 min", num: true, tone: true, get: (r) => r.check?.after_30_pct, show: (r) => pct(r.check?.after_30_pct) },
  { id: "a60", label: "1 hour", num: true, tone: true, get: (r) => r.check?.after_60_pct, show: (r) => pct(r.check?.after_60_pct) },
  { id: "close", label: "At close", num: true, tone: true, get: (r) => r.check?.close_pct, show: (r) => pct(r.check?.close_pct) },
  { id: "best", label: "Best", num: true, tone: true, title: "Best move on the way to the close", get: (r) => r.check?.best_pct, show: (r) => pct(r.check?.best_pct) },
  { id: "worst", label: "Worst", num: true, tone: true, title: "Worst move on the way to the close", get: (r) => r.check?.worst_pct, show: (r) => pct(r.check?.worst_pct) },
];

const CHOP_CHECK: Col[] = [
  { id: "held", label: "Trend held", num: true, title: "Minutes until the next SMA cross after the scan (else to the close)", get: (r) => r.check?.held_min, show: (r) => mins(r.check?.held_min) },
];

const CROSS_CHECK: Col[] = [
  {
    id: "crossed",
    label: "Crossed?",
    title: "About to cross rows: did the SMAs cross the predicted way, and within double the estimate",
    get: (r) => (r.check?.crossed == null ? null : r.check.crossed ? (r.check.on_time ? 2 : 1) : 0),
    show: (r) =>
      r.check?.crossed == null ? "—" : r.check.crossed ? (r.check.on_time ? "Yes, on time" : "Yes, late") : "No",
  },
  { id: "after", label: "Next cross", num: true, title: "Minutes from the scan to the next SMA cross", get: (r) => r.check?.next_cross_after_min, show: (r) => mins(r.check?.next_cross_after_min) },
];

function columns(kind: ScanKind, checked: boolean): Col[] {
  const own = kind === "cross" ? CROSS_COLS : CHOP_COLS;
  if (!checked) return [...BASE, ...own];
  return [...BASE, ...own, ...CHECK_COLS, ...(kind === "cross" ? CROSS_CHECK : CHOP_CHECK)];
}

const cellText = (c: Col, r: Merged) => (c.show ? c.show(r) : String(c.get(r) ?? "—"));

function csv(cols: Col[], rows: Merged[]): string {
  const cell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const head = cols.map((c) => cell(c.label)).join(",");
  const body = rows.map((r) =>
    cols
      .map((c) => {
        const v = c.get(r);
        if (v == null) return "";
        return typeof v === "number" ? String(Math.round(v * 10000) / 10000) : cell(c.show ? c.show(r) : v);
      })
      .join(",")
  );
  return [head, ...body].join("\n");
}

function runTitle(run: ScanRunDetail): string {
  return `${run.label} · ${clock(run.as_of)} IST · ${dayText(run.day)}${run.minutes ? ` · ${run.minutes}-min candle` : ""}`;
}

function groupLine(g: ScanCheckGroup, kind: ScanKind): string[] {
  const out = [
    `${g.right_after_30_pct ?? "—"}% went the right way 30 min later`,
    `${g.right_close_pct ?? "—"}% by the close`,
    `average at close ${pct(g.avg_close_pct)}`,
    `best ${pct(g.avg_best_pct)} · worst ${pct(g.avg_worst_pct)} on average`,
  ];
  if (kind === "cross" && g.crossed_pct != null) out.unshift(`${g.crossed_pct}% crossed as predicted (${g.on_time_pct ?? 0}% on time)`);
  if (kind === "chop" && g.avg_held_min != null) out.push(`trend held ${Math.round(g.avg_held_min)} min on average`);
  return out;
}

async function pdf(run: ScanRunDetail, cols: Col[], rows: Merged[]): Promise<void> {
  const [{ jsPDF }, { autoTable }] = await Promise.all([import("jspdf"), import("jspdf-autotable")]);
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: cols.length > 14 ? "a3" : "a4" });
  doc.setFontSize(14);
  doc.text(pdfText(runTitle(run)), 32, 36);
  doc.setFontSize(9);
  const m = run.meta;
  const facts = [
    `${run.row_count} stocks listed of ${run.total ?? run.row_count} scanned`,
    m.sma_fast && m.sma_slow ? `SMA ${m.sma_fast}/${m.sma_slow}` : "",
    run.kind === "chop" && m.quiet_min ? `quiet after ${m.quiet_min} min` : "",
    m.source === "auto" ? "automatic re-scan" : m.source === "manual" ? "Scan now" : "",
  ].filter(Boolean);
  doc.text(pdfText(facts.join(" · ")), 32, 52);
  let y = 66;
  if (run.check) {
    for (const g of run.check.summary) {
      doc.text(pdfText(`${g.label} (${g.count}): ${groupLine(g, run.kind).join(" · ")}`), 32, y);
      y += 13;
    }
  }
  autoTable(doc, {
    startY: y + 4,
    head: [cols.map((c) => pdfText(c.label))],
    body: rows.map((r) => cols.map((c) => pdfText(cellText(c, r)).replace("📨", "(sent)"))),
    styles: { fontSize: 7, cellPadding: 2 },
    headStyles: { fillColor: [30, 41, 59] },
  });
  doc.save(`${run.kind === "cross" ? "cross-scan" : "trend-quality"}-${run.day}-${clock(run.as_of).replace(":", "")}.pdf`);
}

export function ScanHistory() {
  const [hist, setHist] = useState<ScanHistoryData | null>(null);
  const [day, setDay] = useState<string>("");
  const [kind, setKind] = useState<ScanKind | "">("");
  const [runId, setRunId] = useState<number | null>(null);
  const [run, setRun] = useState<ScanRunDetail | null>(null);
  const [show, setShow] = useState<"all" | "quiet" | "strong" | "approaching">("all");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const h = await smaApi.scanHistory(day || undefined, kind || undefined);
      setHist(h);
      if (!day && h.day) setDay(h.day);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [day, kind]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const loadRun = useCallback(async (id: number) => {
    try {
      setRun(await smaApi.scanHistoryRun(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (runId == null) {
      setRun(null);
      return;
    }
    void loadRun(runId);
  }, [runId, loadRun]);

  // While this run's check is running, follow it.
  const checking = run?.checking.running && run.checking.run_id === run.id;
  useEffect(() => {
    if (!checking || runId == null) return;
    const t = setInterval(() => void loadRun(runId), POLL_MS);
    return () => clearInterval(t);
  }, [checking, runId, loadRun]);
  useEffect(() => {
    // A check that just finished: refresh the list's "Checked" marks.
    if (!checking && run?.check) void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking]);

  const startCheck = async () => {
    if (!run) return;
    try {
      await smaApi.scanHistoryCheck(run.id);
      await loadRun(run.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const merged: Merged[] = useMemo(() => {
    if (!run) return [];
    const byStock = new Map((run.check?.rows ?? []).map((c) => [c.symbol, c]));
    return (run.rows as Saved[]).map((r) => ({ ...r, check: byStock.get(r.symbol) }));
  }, [run]);
  const shown = useMemo(
    () =>
      merged.filter((r) =>
        show === "quiet" ? r.quiet_runner : show === "strong" ? !!r.trend : show === "approaching" ? r.state === "APPROACHING" : true
      ),
    [merged, show]
  );
  const cols = run ? columns(run.kind, !!run.check) : [];
  const { sorted, sort, onSort } = useSort<Merged, string>(shown, (r, k) => cols.find((c) => c.id === k)?.get(r) ?? null);

  const fileBase = run ? `${run.kind === "cross" ? "cross-scan" : "trend-quality"}-${run.day}-${clock(run.as_of).replace(":", "")}` : "scan";

  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <History size={15} className="text-accentSky" /> Scan history
      </h2>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        Every finished Cross scan and Trend quality scan is saved here with its time, kept for {hist?.keep_days ?? 90} days.
        Pick a day and a time to see exactly what that scan showed, download it, or press Check accuracy after the market
        closes to see what each stock did afterwards. Information only: nothing here places an order.
      </p>

      <div className="mb-3 flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Day
          <select
            value={day}
            onChange={(e) => {
              setDay(e.target.value);
              setRunId(null);
            }}
            className="min-h-8 rounded-md border border-slate-700 bg-transparent px-2 text-slate-200"
          >
            {(hist?.days ?? []).length === 0 && <option value="">No saved scans yet</option>}
            {(hist?.days ?? []).map((d) => (
              <option key={d.day} value={d.day}>
                {dayText(d.day)} · {d.runs} scan{d.runs === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Scanner
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as ScanKind | "");
              setRunId(null);
            }}
            className="min-h-8 rounded-md border border-slate-700 bg-transparent px-2 text-slate-200"
          >
            <option value="">Both</option>
            <option value="chop">Trend quality</option>
            <option value="cross">Cross scan</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void loadList()}
          className="flex min-h-8 items-center gap-1 rounded-md px-2.5 ring-1 ring-inset ring-slate-700 hover:bg-white/5"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Refresh
        </button>
      </div>

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}

      {(hist?.runs ?? []).length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          No saved scans{day ? " on this day" : " yet"}. Scans are saved from now on, each time one finishes.
        </p>
      ) : (
        <div className="mb-4 max-h-64 overflow-auto rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card text-xs uppercase text-slate-500">
              <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium">
                <th>Time</th>
                <th>Scanner</th>
                <th>Candle</th>
                <th>Stocks</th>
                <th>How</th>
                <th>Accuracy</th>
              </tr>
            </thead>
            <tbody>
              {hist!.runs.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setRunId(r.id)}
                  aria-selected={r.id === runId}
                  className={clsx(
                    "cursor-pointer border-t border-slate-800/60 [&_td]:px-2 [&_td]:py-1.5",
                    r.id === runId ? "bg-accentSky/10" : "hover:bg-white/5"
                  )}
                >
                  <td className="tabular-nums">
                    {clock(r.as_of)}
                    {r.day !== day ? <span className="ml-1 text-xs text-slate-500">{r.day}</span> : null}
                  </td>
                  <td>{r.label}</td>
                  <td>{r.minutes ? `${r.minutes} min` : "—"}</td>
                  <td className="tabular-nums">
                    {r.row_count}
                    {r.total ? <span className="text-slate-500"> of {r.total}</span> : null}
                  </td>
                  <td className="text-xs text-slate-400">{r.meta.source === "auto" ? "Auto" : r.meta.source === "manual" ? "Scan now" : "—"}</td>
                  <td className="text-xs">{r.checked_at ? <span className="text-profit">Checked ✓</span> : <span className="text-slate-500">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {run && (
        <div className="rounded-lg border border-slate-800 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="mr-auto text-sm font-semibold">{runTitle(run)}</h3>
            <button
              type="button"
              onClick={() => saveFile(`${fileBase}.csv`, csv(cols, sorted), "text/csv;charset=utf-8")}
              className="flex min-h-8 items-center gap-1 rounded-md px-2.5 text-xs ring-1 ring-inset ring-slate-700 hover:bg-white/5"
            >
              <Download size={13} /> CSV
            </button>
            <button
              type="button"
              onClick={() => void pdf(run, cols, sorted)}
              className="flex min-h-8 items-center gap-1 rounded-md px-2.5 text-xs ring-1 ring-inset ring-slate-700 hover:bg-white/5"
            >
              <FileText size={13} /> PDF
            </button>
            <button
              type="button"
              disabled={!run.can_check || !!checking || run.checking.running}
              onClick={() => void startCheck()}
              title={run.can_check ? "Download that day's candles again and see what each stock did after the scan" : "Available after 15:30 on the scan's day"}
              className="flex min-h-8 items-center gap-1 rounded-md bg-accentSky/15 px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/50 hover:bg-accentSky/25 disabled:opacity-50"
            >
              {checking ? <Loader2 size={13} className="animate-spin" /> : <Target size={13} />}
              {checking ? `Checking ${run.checking.done}/${run.checking.total}` : run.check ? "Check again" : "Check accuracy"}
            </button>
          </div>
          <p className="mb-2 text-xs text-slate-500">
            {run.row_count} stocks listed of {run.total ?? run.row_count} scanned
            {run.meta.sma_fast && run.meta.sma_slow ? ` · SMA ${run.meta.sma_fast}/${run.meta.sma_slow}` : ""}
            {run.kind === "chop" && run.meta.quiet_min ? ` · quiet after ${run.meta.quiet_min} min` : ""}
            {run.finished_at ? ` · finished ${clock(run.finished_at)}` : ""}
            {run.error ? ` · ${run.error}` : ""}
            {!run.can_check ? " · Check accuracy opens after 15:30 that day" : ""}
          </p>
          {!checking && run.checking.run_id === run.id && run.checking.error && (
            <p className="mb-2 text-xs text-amber-300">{run.checking.error}</p>
          )}

          {run.check && (
            <div className="mb-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {run.check.summary.map((g) => (
                <div key={g.key} className="rounded-lg border border-slate-800 bg-white/[0.02] p-2.5 text-xs">
                  <div className="mb-1 font-semibold text-slate-200">
                    {g.label} <span className="font-normal text-slate-500">· {g.count} stocks</span>
                  </div>
                  <ul className="space-y-0.5 text-slate-400">
                    {groupLine(g, run.kind).map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          {run.check && (
            <p className="mb-2 text-[11px] text-slate-500">
              Moves are % from the price at the scan, counted with the scan&apos;s call: a fall is + when the call was down.
              Checked {run.check.checked} stocks{run.check.error ? ` · ${run.check.error}` : ""}.
            </p>
          )}

          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            Show
            <select
              value={show}
              onChange={(e) => setShow(e.target.value as typeof show)}
              className="min-h-8 rounded-md border border-slate-700 bg-transparent px-2 text-slate-200"
            >
              <option value="all">All stocks</option>
              {run.kind === "chop" ? (
                <>
                  <option value="quiet">Quiet runners</option>
                  <option value="strong">Strong trend</option>
                </>
              ) : (
                <option value="approaching">About to cross</option>
              )}
            </select>
            <span>{sorted.length} shown</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
                <tr>
                  {cols.map((c) => (
                    <SortTh key={c.id} label={c.label} k={c.id} sort={sort} onSort={onSort} num={c.num} text={!c.num} title={c.title} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.symbol} className="border-t border-slate-800/60">
                    {cols.map((c) => {
                      const v = c.get(r);
                      return (
                        <td
                          key={c.id}
                          className={clsx(
                            "whitespace-nowrap px-2 py-1.5 text-xs",
                            c.num && "text-right tabular-nums",
                            c.id === "symbol" && "font-semibold text-slate-200",
                            c.tone && typeof v === "number" && (v > 0 ? "text-profit" : v < 0 ? "text-loss" : "")
                          )}
                        >
                          {cellText(c, r)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
