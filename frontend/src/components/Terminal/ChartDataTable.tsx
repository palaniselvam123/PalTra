"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, Columns3, FileDown, Filter, RotateCcw, Search, X } from "lucide-react";
import clsx from "clsx";
import {
  COLUMNS,
  COLUMN_BY_ID,
  QUICK,
  buildTableRows,
  cellText,
  istStampOf,
  matchesFilter,
  quickMatch,
  saveFile,
  sortRows,
  toCsv,
  toPdf,
  type Column,
  type Quick,
  type Sort,
  type TableRow,
} from "@/lib/chartTable";
import type { Candle, ChartPayload, TradeRow } from "@/lib/smaApi";

const LAYOUT_KEY = "sma.table.columns";
const PAGE_KEY = "sma.table.page";
const PAGE_SIZES = [50, 100, 250, 0] as const; // 0 = all

type Layout = { order: string[]; hidden: string[] };

function defaultLayout(): Layout {
  return { order: COLUMNS.map((c) => c.id), hidden: COLUMNS.filter((c) => !c.visible).map((c) => c.id) };
}

/** Saved order plus any column added since, in its default place. */
function readLayout(): Layout {
  const base = defaultLayout();
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (!raw) return base;
    const saved = JSON.parse(raw) as Partial<Layout>;
    const known = (saved.order ?? []).filter((id) => COLUMN_BY_ID.has(id));
    const missing = base.order.filter((id) => !known.includes(id));
    return {
      order: [...known, ...missing],
      hidden: [...(saved.hidden ?? []).filter((id) => COLUMN_BY_ID.has(id)), ...missing.filter((id) => base.hidden.includes(id))],
    };
  } catch {
    return base;
  }
}

function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the layout still applies until the page closes */
  }
}

function tone(col: Column, r: TableRow): string {
  const v = col.get(r);
  if (col.id === "pattern") return r.bias === "bullish" ? "text-emerald-300" : r.bias === "bearish" ? "text-rose-300" : "text-slate-300";
  if (col.id === "cross") return v === "Bull cross" ? "font-semibold text-emerald-300" : "font-semibold text-rose-300";
  if (col.id === "side") return v === "Long" ? "text-emerald-300" : "text-rose-300";
  if (!(col.money || col.signed) || typeof v !== "number" || v === 0) return "text-slate-200";
  return v > 0 ? "text-emerald-300" : "text-rose-300";
}

type Props = {
  candles: Candle[];
  markers: ChartPayload["markers"];
  trades: TradeRow[];
  snap: (sec: number) => number;
  symbol: string;
  barLabel: string;
  /** "Live", "Past" or "Replay": which book the trades come from, for the file title. */
  source: string;
};

export function ChartDataTable({ candles, markers, trades, snap, symbol, barLabel, source }: Props) {
  const [layout, setLayout] = useState<Layout>(defaultLayout);
  const [pageSize, setPageSize] = useState<number>(100);
  useEffect(() => {
    setLayout(readLayout());
    try {
      const raw = localStorage.getItem(PAGE_KEY);
      const saved = raw == null ? NaN : Number(raw);
      if ((PAGE_SIZES as readonly number[]).includes(saved)) setPageSize(saved);
    } catch {
      /* private mode */
    }
  }, []);
  const saveLayout = (next: Layout) => {
    setLayout(next);
    remember(LAYOUT_KEY, JSON.stringify(next));
  };

  const [quick, setQuick] = useState<Quick>("all");
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [showFilters, setShowFilters] = useState(false);
  const [showColumns, setShowColumns] = useState(false);
  const [sort, setSort] = useState<Sort>({ id: "time", dir: "desc" });
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState<"pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const all = useMemo(() => buildTableRows(candles, markers, trades, snap), [candles, markers, trades, snap]);
  const cols = useMemo(
    () => layout.order.filter((id) => !layout.hidden.includes(id)).map((id) => COLUMN_BY_ID.get(id)!).filter(Boolean),
    [layout]
  );
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const active = Object.entries(filters).filter(([, v]) => v.trim());
    const kept = all.filter((r) => {
      if (!quickMatch(quick, r)) return false;
      for (const [id, raw] of active) {
        const col = COLUMN_BY_ID.get(id);
        if (col && !matchesFilter(col, r, raw)) return false;
      }
      if (needle && !cols.some((c) => cellText(c, r).toLowerCase().includes(needle))) return false;
      return true;
    });
    return sortRows(kept, sort);
  }, [all, quick, filters, search, sort, cols]);

  useEffect(() => setPage(0), [quick, filters, search, sort, pageSize]);
  const pages = pageSize ? Math.max(1, Math.ceil(shown.length / pageSize)) : 1;
  const current = Math.min(page, pages - 1);
  const visible = pageSize ? shown.slice(current * pageSize, current * pageSize + pageSize) : shown;

  const cycleSort = (id: string) =>
    setSort((s) => (!s || s.id !== id ? { id, dir: "asc" } : s.dir === "asc" ? { id, dir: "desc" } : null));

  const move = (id: string, by: -1 | 1) => {
    const order = [...layout.order];
    const i = order.indexOf(id);
    if (i < 0) return;
    // Jump over hidden columns so the move shows in the table; the next one if all are hidden.
    let j = i + by;
    while (j >= 0 && j < order.length && layout.hidden.includes(order[j]) && !layout.hidden.includes(id)) j += by;
    if (j < 0 || j >= order.length) j = i + by;
    if (j < 0 || j >= order.length) return;
    order.splice(i, 1);
    order.splice(j, 0, id);
    saveLayout({ ...layout, order });
    return;
  };
  const toggleCol = (id: string) =>
    saveLayout({
      ...layout,
      hidden: layout.hidden.includes(id) ? layout.hidden.filter((h) => h !== id) : [...layout.hidden, id],
    });

  const span = candles.length ? `${istStampOf(candles[0].time)} to ${istStampOf(candles[candles.length - 1].time)}` : "";
  const fileBase = `${symbol || "chart"}-${barLabel}-${candles.length ? new Date(candles[0].time * 1000).toISOString().slice(0, 10) : "data"}`;
  const filtered = shown.length !== all.length;
  const subtitle = `${barLabel} candles · ${span} IST · ${source} trades · ${shown.length}${filtered ? ` of ${all.length}` : ""} rows · ${
    sort ? `sorted by ${COLUMN_BY_ID.get(sort.id)?.label} ${sort.dir === "asc" ? "up" : "down"}` : "chart order"
  }. P&L is before charges.`;

  const downloadCsv = () => saveFile(`${fileBase}.csv`, toCsv(cols, shown), "text/csv;charset=utf-8");
  const downloadPdf = () => {
    setBusy("pdf");
    setError(null);
    toPdf(`${symbol} ${barLabel} candle data`, subtitle, cols, shown, `${fileBase}.pdf`)
      .catch((err: unknown) => setError(err instanceof Error ? `PDF failed: ${err.message}` : "PDF failed"))
      .finally(() => setBusy(null));
  };

  const activeFilters = Object.values(filters).filter((v) => v.trim()).length;

  return (
    <div aria-label="Candle data table" className="border-t border-white/10">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
        <label className="flex items-center gap-1 text-xs text-slate-400">
          <span className="sr-only">Rows</span>
          <select
            value={quick}
            onChange={(e) => setQuick(e.target.value as Quick)}
            className="min-h-8 rounded-md bg-[#0B0E14] px-2 text-xs text-slate-200 ring-1 ring-inset ring-white/10"
          >
            {QUICK.map((q) => (
              <option key={q.id} value={q.id}>
                {q.label}
              </option>
            ))}
          </select>
        </label>
        <label className="relative flex min-w-0 flex-1 items-center sm:max-w-[220px]">
          <Search size={13} aria-hidden className="pointer-events-none absolute left-2 text-slate-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search rows"
            aria-label="Search rows"
            className="min-h-8 w-full rounded-md bg-[#0B0E14] pl-7 pr-2 text-xs text-slate-200 ring-1 ring-inset ring-white/10 placeholder:text-slate-500"
          />
        </label>
        <button
          type="button"
          onClick={() => setShowFilters((v) => !v)}
          aria-pressed={showFilters}
          title="Filter by column: >0.05, <=30, 10..20, =Exit, !=Holding, or text"
          className={clsx(
            "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
            showFilters || activeFilters ? "bg-sky-400/15 text-sky-100 ring-sky-400/40" : "text-slate-300 ring-white/10 hover:bg-white/5"
          )}
        >
          <Filter size={13} aria-hidden />
          Filters{activeFilters ? ` (${activeFilters})` : ""}
        </button>
        <button
          type="button"
          onClick={() => setShowColumns((v) => !v)}
          aria-pressed={showColumns}
          aria-controls="table-columns"
          className={clsx(
            "flex min-h-8 items-center gap-1 rounded-md px-2 text-xs ring-1 ring-inset",
            showColumns ? "bg-sky-400/15 text-sky-100 ring-sky-400/40" : "text-slate-300 ring-white/10 hover:bg-white/5"
          )}
        >
          <Columns3 size={13} aria-hidden />
          Columns ({cols.length})
        </button>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={downloadCsv}
            disabled={!shown.length}
            title="The rows and columns shown (all pages), raw numbers"
            className="flex min-h-8 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-40"
          >
            <FileDown size={13} aria-hidden />
            CSV
          </button>
          <button
            type="button"
            onClick={downloadPdf}
            disabled={!shown.length || busy === "pdf"}
            title="The rows and columns shown (all pages)"
            className="flex min-h-8 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-40"
          >
            <FileDown size={13} aria-hidden />
            {busy === "pdf" ? "PDF…" : "PDF"}
          </button>
        </span>
        <span className="ml-auto text-xs text-slate-400">
          {shown.length.toLocaleString("en-IN")}
          {filtered ? ` of ${all.length.toLocaleString("en-IN")}` : ""} rows
        </span>
      </div>
      {error ? (
        <p role="alert" className="px-3 pb-2 text-xs text-rose-300 sm:px-4">
          {error}
        </p>
      ) : null}

      {showColumns ? (
        <div id="table-columns" className="mx-3 mb-2 rounded-lg border border-white/10 bg-[#0B0E14] p-2 sm:mx-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span>Tick to show, arrows to change the order. Saved in this browser.</span>
            <span className="flex gap-1">
              <button
                type="button"
                onClick={() => saveLayout({ ...layout, hidden: [] })}
                className="min-h-8 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
              >
                Show all
              </button>
              <button
                type="button"
                onClick={() => saveLayout(defaultLayout())}
                className="flex min-h-8 items-center gap-1 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5"
              >
                <RotateCcw size={12} aria-hidden />
                Reset
              </button>
            </span>
          </div>
          <ol className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {layout.order.map((id, i) => {
              const col = COLUMN_BY_ID.get(id);
              if (!col) return null;
              const on = !layout.hidden.includes(id);
              return (
                <li key={id} className="flex items-center gap-1 rounded-md bg-white/[0.03] px-2 py-0.5 text-xs">
                  <label className="flex min-h-8 min-w-0 flex-1 cursor-pointer items-center gap-2 text-slate-200">
                    <input type="checkbox" checked={on} onChange={() => toggleCol(id)} className="h-4 w-4 accent-sky-400" />
                    <span className="truncate">{col.label}</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => move(id, -1)}
                    disabled={i === 0}
                    aria-label={`Move ${col.label} earlier`}
                    className="flex h-8 w-7 items-center justify-center rounded text-slate-400 hover:bg-white/5 disabled:opacity-30"
                  >
                    <ChevronUp size={14} aria-hidden />
                  </button>
                  <button
                    type="button"
                    onClick={() => move(id, 1)}
                    disabled={i === layout.order.length - 1}
                    aria-label={`Move ${col.label} later`}
                    className="flex h-8 w-7 items-center justify-center rounded text-slate-400 hover:bg-white/5 disabled:opacity-30"
                  >
                    <ChevronDown size={14} aria-hidden />
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}

      <div className="max-h-[560px] overflow-auto">
        <table className="w-full min-w-max border-collapse text-xs">
          <thead className="sticky top-0 z-[1] bg-[#1A1F29]">
            <tr>
              {cols.map((c) => {
                const on = sort?.id === c.id;
                return (
                  <th
                    key={c.id}
                    scope="col"
                    aria-sort={on ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                    className={clsx(
                      "whitespace-nowrap px-2 py-1.5 font-semibold uppercase tracking-wide text-slate-400",
                      c.kind === "num" ? "text-right" : "text-left"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => cycleSort(c.id)}
                      title={c.title ? `${c.title}. Click to sort.` : "Click to sort"}
                      className={clsx("inline-flex min-h-7 items-center gap-1 hover:text-slate-100", on && "text-sky-200")}
                    >
                      {c.label}
                      {on ? sort!.dir === "asc" ? <ArrowUp size={11} aria-hidden /> : <ArrowDown size={11} aria-hidden /> : null}
                    </button>
                  </th>
                );
              })}
            </tr>
            {showFilters ? (
              <tr>
                {cols.map((c) => (
                  <th key={c.id} className="px-1 pb-1.5 font-normal">
                    <span className="relative flex items-center">
                      <input
                        value={filters[c.id] ?? ""}
                        onChange={(e) => setFilters((f) => ({ ...f, [c.id]: e.target.value }))}
                        placeholder={c.kind === "text" ? "contains" : ">, <, a..b"}
                        aria-label={`Filter ${c.label}`}
                        className="min-h-7 w-full min-w-[64px] rounded bg-[#0B0E14] px-1.5 pr-5 text-[11px] text-slate-200 ring-1 ring-inset ring-white/10 placeholder:text-slate-600"
                      />
                      {filters[c.id] ? (
                        <button
                          type="button"
                          onClick={() => setFilters((f) => ({ ...f, [c.id]: "" }))}
                          aria-label={`Clear the ${c.label} filter`}
                          className="absolute right-0.5 text-slate-500 hover:text-slate-200"
                        >
                          <X size={11} aria-hidden />
                        </button>
                      ) : null}
                    </span>
                  </th>
                ))}
              </tr>
            ) : null}
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={Math.max(cols.length, 1)} className="px-3 py-6 text-center text-slate-400">
                  {all.length ? "No rows match the filters." : "No candles yet."}
                </td>
              </tr>
            ) : (
              visible.map((r) => (
                <tr
                  key={r.key}
                  className={clsx(
                    "border-b border-white/5 hover:bg-white/[0.05]",
                    r.stage.includes("Entry")
                      ? "bg-sky-500/[0.08]"
                      : r.stage === "Exit"
                        ? "bg-amber-500/[0.08]"
                        : r.tradeRef
                          ? "bg-white/[0.025]"
                          : ""
                  )}
                >
                  {cols.map((c) => (
                    <td
                      key={c.id}
                      className={clsx(
                        "whitespace-nowrap px-2 py-1",
                        c.kind === "num" ? "text-right font-mono" : c.kind === "time" ? "font-mono text-slate-300" : "",
                        tone(c, r)
                      )}
                    >
                      {cellText(c, r)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-slate-400 sm:px-4">
        <label className="flex items-center gap-1">
          Rows per page
          <select
            value={pageSize}
            onChange={(e) => {
              const next = Number(e.target.value);
              setPageSize(next);
              remember(PAGE_KEY, String(next));
            }}
            className="min-h-8 rounded-md bg-[#0B0E14] px-2 text-slate-200 ring-1 ring-inset ring-white/10"
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n || "All"}
              </option>
            ))}
          </select>
        </label>
        {pages > 1 ? (
          <span className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage(Math.max(0, current - 1))}
              disabled={current === 0}
              className="min-h-8 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-40"
            >
              Prev
            </button>
            <span className="px-1">
              Page {current + 1} of {pages}
            </span>
            <button
              type="button"
              onClick={() => setPage(Math.min(pages - 1, current + 1))}
              disabled={current >= pages - 1}
              className="min-h-8 rounded-md px-2 text-slate-300 ring-1 ring-inset ring-white/10 hover:bg-white/5 disabled:opacity-40"
            >
              Next
            </button>
          </span>
        ) : null}
        <span>P&amp;L is from the entry fill, before charges. The exit candle uses the exit price.</span>
      </div>
    </div>
  );
}
