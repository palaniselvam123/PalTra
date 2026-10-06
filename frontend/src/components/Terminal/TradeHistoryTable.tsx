"use client";

import { Fragment, memo, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, FileDown, Loader2, Sigma } from "lucide-react";
import clsx from "clsx";
import { istStamp, parseClock } from "@/lib/format";
import { inr, pnlAtPrice, px, smaApi, type SmaState, type TradeBook, type TradeRow } from "@/lib/smaApi";
import { Badge, Skeleton, pnlTone } from "./ui";
import { BacktestRuns, RunDetail, filtersShort, stopShort, strategyLabel, type Settings } from "./BacktestRuns";
import { strategiesUsed, summarizeBook } from "@/lib/bookSummary";

const REASON: Record<string, string> = {
  MA_CROSS: "MA CROSS",
  MA_APPROACH: "SOLD BEFORE CROSS",
  ATR_SL_HIT: "ATR SL HIT",
  GAP_SL_HIT: "MOVING SL HIT",
  TSL_HIT: "TRAILING SL HIT",
  TARGET_HIT: "TARGET HIT",
  REPLAY_STOPPED: "REPLAY STOPPED",
  EOD_SQUARE_OFF: "EOD SQUARE-OFF",
  KILL_SWITCH: "KILL SWITCH",
  NOT_ON_GROWW: "NOT ON GROWW",
  SL_REJECTED: "STOP REFUSED",
  MANUAL_CLOSE: "MANUAL CLOSE",
};

type Book = "PAPER" | "LIVE" | "REPLAY";

const BOOKS: { id: Book; title: string; note: string }[] = [
  {
    id: "PAPER",
    title: "Simulation",
    note: "Paper fills only. After the close this tape walks forward from the last NSE price. Buy and sell both wait for the next SMA cross.",
  },
  {
    id: "LIVE",
    title: "NSE live",
    note: "Fills that were sent to Groww on the NSE tape.",
  },
  {
    id: "REPLAY",
    title: "Replay",
    note: "Practice trades on a replayed past day (Groww candles). No order was ever sent; dates are the replayed day.",
  },
];

function bookOf(trade: TradeRow): Book {
  const mode = (trade.mode || "PAPER").toUpperCase();
  return mode === "LIVE" ? "LIVE" : mode === "REPLAY" ? "REPLAY" : "PAPER";
}

type PnlSide = "all" | "profit" | "loss" | "open";
type SideFilter = "ALL" | "LONG" | "SHORT";

/** What a row is sorted on. `null` keeps the API order (newest first). */
type SortKey =
  | "id"
  | "stock"
  | "side"
  | "entry_time"
  | "entry"
  | "exit_time"
  | "exit"
  | "high"
  | "low"
  | "points"
  | "reason"
  | "strategy"
  | "charges"
  | "net";
type Sort = { key: SortKey; dir: "asc" | "desc" } | null;

const SORT_CHOICES: { key: SortKey; label: string }[] = [
  { key: "entry_time", label: "Entry time" },
  { key: "exit_time", label: "Exit time" },
  { key: "net", label: "Net P&L" },
  { key: "points", label: "Points" },
  { key: "charges", label: "Charges" },
  { key: "stock", label: "Stock" },
  { key: "side", label: "Side" },
  { key: "reason", label: "Exit reason" },
  { key: "strategy", label: "Strategy" },
  { key: "entry", label: "Entry price" },
  { key: "exit", label: "Exit price" },
  { key: "high", label: "Max high" },
  { key: "low", label: "Max low" },
  { key: "id", label: "Trade #" },
];

/** Sorts that keep a day's trades together, so day subtotals still make sense. */
const BY_TIME: (SortKey | undefined)[] = [undefined, "id", "entry_time", "exit_time"];

const NOT_RECORDED = "__none__";

type RunSettings = Map<number, Settings>;

/** Settings the trade ran with: its own record, else its replay run's snapshot. */
function settingsOf(trade: TradeRow, runs: RunSettings): Settings | null {
  if (trade.strategy && Object.keys(trade.strategy).length) return trade.strategy;
  if (trade.run_id != null) return runs.get(trade.run_id) ?? null;
  return null;
}

// Thousands of rows share a few settings objects; build each label once.
const LABELS = new WeakMap<Settings, string>();

function strategyOf(trade: TradeRow, runs: RunSettings): string | null {
  const settings = settingsOf(trade, runs);
  if (!settings) return null;
  let label = LABELS.get(settings);
  if (label === undefined) {
    label = strategyLabel(settings);
    LABELS.set(settings, label);
  }
  return label;
}

function epoch(value: string | null | undefined): number | null {
  if (!value) return null;
  const date = parseClock(value);
  return date ? date.getTime() : null;
}

function sortValue(trade: TradeRow, key: SortKey, state: SmaState | null, runs: RunSettings): number | string | null {
  switch (key) {
    case "id":
      return trade.id;
    case "stock":
      return trade.symbol.toUpperCase();
    case "side":
      return trade.direction;
    case "entry_time":
      return epoch(trade.entry_time);
    case "entry":
      return trade.entry_price;
    case "exit_time":
      return epoch(trade.exit_time);
    case "exit":
      return marketPrice(trade, state);
    case "high":
      return pnlAtPrice(trade, trade.max_high);
    case "low":
      return pnlAtPrice(trade, trade.max_low);
    case "points":
      return rowFigures(trade, state).points;
    case "reason":
      return trade.exit_price == null ? "Open" : REASON_SHORT[trade.exit_reason || ""] ?? (trade.exit_reason || "");
    case "strategy":
      return strategyOf(trade, runs);
    case "charges":
      return trade.brokerage_and_taxes;
    case "net":
      return rowFigures(trade, state).net;
  }
}

/** Sorted copy; rows without a value always go last. */
function sortRows(rows: TradeRow[], sort: Sort, state: SmaState | null, runs: RunSettings): TradeRow[] {
  if (!sort) return rows;
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((trade, i) => ({ trade, i, v: sortValue(trade, sort.key, state, runs) }))
    .sort((a, b) => {
      if (a.v == null || b.v == null) return a.v == null ? (b.v == null ? a.i - b.i : 1) : -1;
      const diff = typeof a.v === "number" && typeof b.v === "number" ? a.v - b.v : String(a.v).localeCompare(String(b.v));
      return diff === 0 ? a.i - b.i : sign * diff;
    })
    .map((row) => row.trade);
}

/** How often the whole book is read again; live changes come from the page's poll meanwhile. */
const BOOK_REFRESH_MS = 60_000;

/**
 * The selected book in full (up to 20,000 trades), with the page's newest-200
 * poll laid over it so open trades and fresh fills show without a reload.
 */
function useTradeBook(book: Book, polled: TradeRow[], active: boolean) {
  const [books, setBooks] = useState<Partial<Record<Book, TradeBook>>>({});
  const [counts, setCounts] = useState<Partial<Record<Book, number>>>({});
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!active) return;
    let live = true;
    const load = () => {
      setLoading(true);
      Promise.allSettled([smaApi.tradeBook(book), smaApi.tradeCounts()])
        .then(([full, perBook]) => {
          if (!live) return;
          if (full.status === "fulfilled") setBooks((prev) => ({ ...prev, [book]: full.value }));
          if (perBook.status === "fulfilled") setCounts(perBook.value);
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    };
    load();
    const timer = setInterval(load, BOOK_REFRESH_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [book, active]);

  const loaded = books[book];
  const rows = useMemo(() => {
    const fresh = polled.filter((trade) => bookOf(trade) === book);
    if (!loaded) return fresh;
    const byId = new Map(loaded.rows.map((trade) => [trade.id, trade]));
    let added = 0;
    for (const trade of fresh) {
      if (!byId.has(trade.id)) added += 1;
      byId.set(trade.id, trade);
    }
    const merged = Array.from(byId.values());
    if (added) merged.sort((a, b) => b.id - a.id);
    return merged;
  }, [loaded, polled, book]);
  const total = loaded ? Math.max(rows.length, loaded.total + (rows.length - loaded.rows.length)) : rows.length;
  const countOf = (id: Book) =>
    Math.max(id === book ? total : counts[id] ?? 0, polled.filter((trade) => bookOf(trade) === id).length);
  return { rows, total, countOf, loading: loading && !loaded };
}

/** Replay-run settings for the run ids in this list, fetched once per new run. */
function useRunSettings(trades: TradeRow[]): RunSettings {
  const [runs, setRuns] = useState<RunSettings>(() => new Map());
  const wanted = Array.from(
    new Set(trades.filter((t) => t.run_id != null && !t.strategy).map((t) => t.run_id as number))
  )
    .sort((a, b) => a - b)
    .join(",");
  useEffect(() => {
    if (!wanted) return;
    const missing = wanted.split(",").some((id) => !runs.has(Number(id)));
    if (!missing) return;
    let live = true;
    smaApi
      .replayRuns()
      .then((rows) => {
        if (live) setRuns(new Map(rows.map((run) => [run.id, run.settings])));
      })
      .catch(() => {
        /* the column shows "—" until the next try */
      });
    return () => {
      live = false;
    };
    // `runs` is read only to skip a fetch we do not need.
  }, [wanted]);
  return runs;
}

function tradeDay(trade: TradeRow): string {
  if (trade.date && /^\d{4}-\d{2}-\d{2}/.test(trade.date)) return trade.date.slice(0, 10);
  if (!trade.entry_time) return "";
  const date = parseClock(trade.entry_time);
  if (!date) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function shownPnl(trade: TradeRow, state: SmaState | null): number | null {
  if (trade.exit_price == null) {
    const live = markPnl(trade, marketPrice(trade, state));
    return live ? live.pnl : null;
  }
  return trade.gross_pnl;
}

export function TradeHistoryTable({
  trades,
  state,
  closingSymbol,
  onClose,
  loading = false,
}: {
  loading?: boolean;
  trades: TradeRow[];
  state: SmaState | null;
  closingSymbol: string | null;
  onClose: (trade: TradeRow) => void;
}) {
  const [book, setBook] = useState<Book>("PAPER");
  // The Backtests tab shows replay runs instead of a trade book.
  const [backtests, setBacktests] = useState(false);
  const [picked, setPicked] = useState(false);
  const [stock, setStock] = useState("ALL");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [pnlSide, setPnlSide] = useState<PnlSide>("all");
  const [minPnl, setMinPnl] = useState("");
  const [maxPnl, setMaxPnl] = useState("");
  const [side, setSide] = useState<SideFilter>("ALL");
  const [reason, setReason] = useState("ALL");
  const [strategy, setStrategy] = useState("ALL");
  const [sort, setSort] = useState<Sort>(null);
  const full = useTradeBook(book, trades, !backtests);
  const runs = useRunSettings(full.rows);
  const [closedFolded, setClosedFolded] = useState(false);
  useEffect(() => {
    try {
      setClosedFolded(localStorage.getItem("sma.blotter.closed") === "1");
    } catch {
      /* private mode */
    }
  }, []);
  const toggleClosed = () => {
    setClosedFolded((current) => {
      const next = !current;
      try {
        localStorage.setItem("sma.blotter.closed", next ? "1" : "0");
      } catch {
        /* private mode */
      }
      return next;
    });
  };
  useEffect(() => {
    if (picked || !state?.mode) return;
    setBook(state.mode === "LIVE" ? "LIVE" : state.mode === "REPLAY" ? "REPLAY" : "PAPER");
  }, [picked, state?.mode]);
  const inBook = full.rows;
  // The lists below cover the whole book (up to 20,000 trades), so they are
  // rebuilt only when the book changes, not on every price tick.
  const { closedInBook, openInBook, symbols, reasons, strategies } = useMemo(() => {
    const closed: TradeRow[] = [];
    const open: TradeRow[] = [];
    const names = new Set<string>();
    const why = new Set<string>();
    const labels = new Set<string>();
    for (const trade of inBook) {
      (trade.exit_price == null ? open : closed).push(trade);
      names.add(trade.symbol.toUpperCase());
      if (trade.exit_price != null) why.add(trade.exit_reason || "");
      labels.add(strategyOf(trade, runs) ?? NOT_RECORDED);
    }
    return {
      closedInBook: closed,
      openInBook: open,
      symbols: Array.from(names).sort(),
      reasons: Array.from(why).sort((a, b) => (REASON_SHORT[a] ?? a).localeCompare(REASON_SHORT[b] ?? b)),
      strategies: Array.from(labels).sort((a, b) =>
        a === NOT_RECORDED ? 1 : b === NOT_RECORDED ? -1 : a.localeCompare(b)
      ),
    };
  }, [inBook, runs]);
  const stockFilter = symbols.includes(stock) ? stock : "ALL";
  const reasonFilter = reasons.includes(reason) ? reason : "ALL";
  const strategyFilter = strategies.includes(strategy) ? strategy : "ALL";
  const min = minPnl.trim() === "" ? null : Number(minPnl);
  const max = maxPnl.trim() === "" ? null : Number(maxPnl);
  const keep = (trade: TradeRow, pnl: number | null): boolean => {
    if (stockFilter !== "ALL" && trade.symbol.toUpperCase() !== stockFilter) return false;
    if (side !== "ALL" && trade.direction !== side) return false;
    if (reasonFilter !== "ALL" && (trade.exit_price == null || (trade.exit_reason || "") !== reasonFilter)) return false;
    if (strategyFilter !== "ALL" && (strategyOf(trade, runs) ?? NOT_RECORDED) !== strategyFilter) return false;
    const day = tradeDay(trade);
    if (from && (!day || day < from)) return false;
    if (to && (!day || day > to)) return false;
    const open = trade.exit_price == null;
    if (pnlSide === "open" && !open) return false;
    if (pnlSide === "profit" && !(pnl != null && pnl > 0)) return false;
    if (pnlSide === "loss" && !(pnl != null && pnl < 0)) return false;
    if (min != null && Number.isFinite(min) && (pnl == null || pnl < min)) return false;
    if (max != null && Number.isFinite(max) && (pnl == null || pnl > max)) return false;
    return true;
  };
  // Closed trades do not move with the price: filter and sort them once per change.
  const completedRows = useMemo(
    () => sortRows(closedInBook.filter((trade) => keep(trade, trade.gross_pnl)), sort, null, runs),
    // `keep` reads exactly these values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [closedInBook, runs, sort, stockFilter, side, reasonFilter, strategyFilter, from, to, pnlSide, min, max]
  );
  // Open trades are few and are marked at the live price on every tick.
  const openRows = sortRows(
    openInBook.filter((trade) => keep(trade, shownPnl(trade, state))),
    sort,
    state,
    runs
  );
  const rows = openRows.length ? [...openRows, ...completedRows] : completedRows;
  // Click a header: high to low first for numbers, A to Z for words; a third click resets.
  const sortBy = (key: SortKey) => {
    const first: "asc" | "desc" = ["stock", "side", "reason", "strategy"].includes(key) ? "asc" : "desc";
    setSort((current) =>
      current?.key !== key ? { key, dir: first } : current.dir === first ? { key, dir: first === "asc" ? "desc" : "asc" } : null
    );
  };
  const closedTotals = useMemo(() => {
    let gross = 0;
    let net = 0;
    for (const trade of completedRows) {
      gross += trade.gross_pnl ?? 0;
      net += trade.net_pnl ?? 0;
    }
    return { gross, net };
  }, [completedRows]);
  const filteredPnl = openRows.reduce((sum, trade) => {
    const pnl = shownPnl(trade, state);
    return pnl == null ? sum : sum + pnl;
  }, closedTotals.gross);
  const filteredNet = openRows.reduce((sum, trade) => (trade.net_pnl == null ? sum : sum + trade.net_pnl), closedTotals.net);
  // Closed trades split into winners and losers by gross P&L, e.g. +₹15 and −₹20 → −₹5.
  const summary = useMemo(() => completedRows.reduce(
    (acc, trade) => {
      const gross = trade.gross_pnl ?? 0;
      if (gross > 0) {
        acc.wins += 1;
        acc.profit += gross;
      } else if (gross < 0) {
        acc.losses += 1;
        acc.loss += gross;
      } else {
        acc.flat += 1;
      }
      acc.charges += trade.brokerage_and_taxes ?? 0;
      acc.net += trade.net_pnl ?? gross;
      return acc;
    },
    { wins: 0, losses: 0, flat: 0, profit: 0, loss: 0, charges: 0, net: 0 }
  ), [completedRows]);
  const filtersOn =
    stockFilter !== "ALL" ||
    side !== "ALL" ||
    reasonFilter !== "ALL" ||
    strategyFilter !== "ALL" ||
    from !== "" ||
    to !== "" ||
    pnlSide !== "all" ||
    minPnl !== "" ||
    maxPnl !== "";
  const selected = BOOKS.find((item) => item.id === book) ?? BOOKS[0];
  const resetKey = [book, stockFilter, side, reasonFilter, strategyFilter, from, to, pnlSide, minPnl, maxPnl, sort?.key, sort?.dir].join("|");
  const simulation = book === "PAPER";

  const clearFilters = () => {
    setStock("ALL");
    setSide("ALL");
    setReason("ALL");
    setStrategy("ALL");
    setFrom("");
    setTo("");
    setPnlSide("all");
    setMinPnl("");
    setMaxPnl("");
  };

  // Backtest-style summary of the closed trades the filters show, and its PDF.
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const bookSummary = useMemo(() => (summaryOpen ? summarizeBook(completedRows) : null), [summaryOpen, completedRows]);
  const usedStrategies = useMemo(
    () => (summaryOpen ? strategiesUsed(completedRows, (s) => strategyLabel(s as Settings)) : []),
    [summaryOpen, completedRows]
  );
  const filterWords = (): string => {
    const words: string[] = [];
    if (stockFilter !== "ALL") words.push(stockFilter);
    if (side !== "ALL") words.push(side === "LONG" ? "long only" : "short only");
    if (reasonFilter !== "ALL") words.push(`exit: ${REASON_SHORT[reasonFilter] ?? reasonFilter}`);
    if (strategyFilter !== "ALL") words.push(`strategy: ${strategyFilter}`);
    if (from || to) words.push(`dates ${from || "start"} to ${to || "today"}`);
    if (pnlSide === "profit") words.push("profit only");
    if (pnlSide === "loss") words.push("loss only");
    if (minPnl !== "") words.push(`P&L >= ${minPnl}`);
    if (maxPnl !== "") words.push(`P&L <= ${maxPnl}`);
    return words.length ? words.join(", ") : "all closed trades";
  };
  const summaryTitle = `${selected.title} summary`;
  const downloadSummaryPdf = async () => {
    setPrinting(true);
    setPdfError(null);
    try {
      const run = summarizeBook(completedRows);
      const range = run.start_date ? (run.end_date !== run.start_date ? `${run.start_date} to ${run.end_date}` : run.start_date) : "no trades";
      const { downloadBacktestPdf } = await import("@/lib/backtestPdf");
      await downloadBacktestPdf({
        run,
        trades: completedRows,
        strategy: `Filters: ${filterWords()}`,
        settings: strategiesUsed(completedRows, (st) => strategyLabel(st as Settings)),
        settingsHead: ["Strategy used", "Trades"],
        reasons: REASON_SHORT,
        title: summaryTitle,
        subtitle: `${range} · ${run.symbols.length} stock${run.symbols.length === 1 ? "" : "s"}: ${run.symbols.join(", ") || "-"} · ${run.days_total} trading day${run.days_total === 1 ? "" : "s"}`,
        note:
          book === "LIVE"
            ? "Real orders sent to Groww (NSE live book). Net is after estimated Groww charges; check your Groww contract notes for the exact figures."
            : book === "REPLAY"
              ? "Practice replays on Groww 1-minute candles. No order was sent."
              : "Practice fills (Simulation book). No order was sent.",
        fileName: `${book === "LIVE" ? "nse-live" : book === "REPLAY" ? "replay" : "simulation"}-summary-${run.start_date || "empty"}${
          run.end_date && run.end_date !== run.start_date ? `_to_${run.end_date}` : ""
        }.pdf`,
      });
    } catch (err) {
      setPdfError(err instanceof Error ? `PDF failed: ${err.message}` : "PDF failed");
    } finally {
      setPrinting(false);
    }
  };

  const downloadFiltered = () => {
    const fields = [
      "id",
      "date",
      "symbol",
      "direction",
      "qty",
      "entry_time",
      "entry_price",
      "exit_time",
      "exit_price",
      "exit_reason",
      "max_high",
      "max_low",
      "gross_pnl",
      "brokerage_and_taxes",
      "net_pnl",
      "mode",
    ] as const;
    const lines = [[...fields, "pnl_at_max_high", "pnl_at_max_low", "strategy"].join(",")];
    const amount = (trade: TradeRow, price: number | null | undefined) => {
      const value = pnlAtPrice(trade, price);
      return value == null ? "" : value.toFixed(2);
    };
    for (const trade of [...openRows, ...completedRows]) {
      lines.push(
        [
          ...fields.map((key) => csvCell(trade[key])),
          amount(trade, trade.max_high),
          amount(trade, trade.max_low),
          csvCell(strategyOf(trade, runs)),
        ].join(",")
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${book.toLowerCase()}-blotter.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section
      className={clsx(
        "min-w-0 max-w-full rounded-xl border bg-[#151921]",
        simulation ? "border-[#F59E0B]/40" : book === "REPLAY" ? "border-violet-400/40" : "border-[#F43F5E]/40"
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.16em] text-slate-400">Trade blotter</div>
          <h2
            className={clsx(
              "text-sm font-medium",
              simulation ? "text-[#F59E0B]" : book === "REPLAY" ? "text-violet-300" : "text-[#F43F5E]"
            )}
          >
            {backtests ? "Backtests" : selected.title}
          </h2>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:items-center">
          {BOOKS.map((item) => {
            const count = full.countOf(item.id);
            const on = !backtests && item.id === book;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setPicked(true);
                  setBook(item.id);
                  setBacktests(false);
                }}
                className={clsx(
                  "min-h-11 whitespace-nowrap rounded-md px-3 text-xs font-semibold sm:min-h-9",
                  on && item.id === "PAPER" && "bg-[#F59E0B] text-[#1a1203]",
                  on && item.id === "LIVE" && "bg-[#F43F5E] text-white",
                  on && item.id === "REPLAY" && "bg-violet-500 text-white",
                  !on && "border border-white/10 text-slate-300 hover:bg-white/5"
                )}
              >
                {item.title} · {count.toLocaleString("en-IN")}
              </button>
            );
          })}
          <button
            type="button"
            aria-pressed={backtests}
            onClick={() => setBacktests((v) => !v)}
            className={clsx(
              "min-h-11 whitespace-nowrap rounded-md px-3 text-xs font-semibold sm:min-h-9",
              backtests ? "bg-sky-500 text-white" : "border border-white/10 text-slate-300 hover:bg-white/5"
            )}
          >
            Backtests
          </button>
          {backtests ? null : (
          <>
          <button
            type="button"
            onClick={toggleClosed}
            className="min-h-11 whitespace-nowrap rounded-md border border-white/15 px-3 text-xs font-semibold text-slate-200 hover:bg-white/5 sm:min-h-9"
          >
            {closedFolded ? `Show closed · ${completedRows.length}` : "Hide closed"}
          </button>
          <button
            type="button"
            onClick={downloadFiltered}
            className="min-h-11 whitespace-nowrap rounded-md border border-white/15 px-3 text-xs text-slate-200 hover:bg-white/5 sm:min-h-9"
          >
            Download CSV
          </button>
          <button
            type="button"
            aria-pressed={summaryOpen}
            onClick={() => setSummaryOpen((v) => !v)}
            title="Day-wise P&L, by stock and totals of the closed trades shown, like a backtest"
            className={clsx(
              "inline-flex min-h-11 items-center justify-center gap-1 whitespace-nowrap rounded-md px-3 text-xs font-semibold sm:min-h-9",
              summaryOpen ? "bg-sky-500 text-white" : "border border-sky-500/40 text-sky-300 hover:bg-sky-500/10"
            )}
          >
            <Sigma size={13} aria-hidden /> Summary
          </button>
          <button
            type="button"
            onClick={() => void downloadSummaryPdf()}
            disabled={printing}
            title="PDF of the summary and every closed trade shown"
            className="inline-flex min-h-11 items-center justify-center gap-1 whitespace-nowrap rounded-md border border-sky-500/40 px-3 text-xs font-semibold text-sky-300 hover:bg-sky-500/10 disabled:opacity-60 sm:min-h-9"
          >
            {printing ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <FileDown size={13} aria-hidden />} PDF
          </button>
          </>
          )}
        </div>
      </div>
      {backtests ? (
        <BacktestRuns />
      ) : (
      <>
      <div className="flex flex-wrap items-end gap-2 px-4 pb-3">
        <label className="flex min-w-[8.5rem] flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Stock
          <select
            value={stockFilter}
            onChange={(e) => setStock(e.target.value)}
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          >
            <option value="ALL">All stocks</option>
            {symbols.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[6.5rem] flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Side
          <select value={side} onChange={(e) => setSide(e.target.value as SideFilter)} className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9">
            <option value="ALL">Both</option>
            <option value="LONG">Long (buy)</option>
            <option value="SHORT">Short (sell)</option>
          </select>
        </label>
        <label className="flex min-w-[8.5rem] flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Exit reason
          <select value={reasonFilter} onChange={(e) => setReason(e.target.value)} className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9">
            <option value="ALL">Any reason</option>
            {reasons.map((code) => (
              <option key={code} value={code}>
                {REASON_SHORT[code] ?? (code || "Closed")}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[9rem] max-w-[16rem] flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Strategy
          <select value={strategyFilter} onChange={(e) => setStrategy(e.target.value)} className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9">
            <option value="ALL">Any strategy</option>
            {strategies.map((label) => (
              <option key={label} value={label}>
                {label === NOT_RECORDED ? "Not recorded" : label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          From
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          To
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          />
        </label>
        <label className="flex min-w-[7.5rem] flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          P&L
          <select
            value={pnlSide}
            onChange={(e) => setPnlSide(e.target.value as PnlSide)}
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          >
            <option value="all">Any</option>
            <option value="profit">Profit</option>
            <option value="loss">Loss</option>
            <option value="open">Open only</option>
          </select>
        </label>
        <label className="flex w-24 flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Min ₹
          <input
            inputMode="decimal"
            value={minPnl}
            onChange={(e) => setMinPnl(e.target.value)}
            placeholder="−500"
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          />
        </label>
        <label className="flex w-24 flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          Max ₹
          <input
            inputMode="decimal"
            value={maxPnl}
            onChange={(e) => setMaxPnl(e.target.value)}
            placeholder="500"
            className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
          />
        </label>
        <div className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
          <label htmlFor="blotter-sort">Sort</label>
          <div className="flex gap-1">
            <select
              id="blotter-sort"
              value={sort?.key ?? ""}
              onChange={(e) => setSort(e.target.value ? { key: e.target.value as SortKey, dir: sort?.dir ?? "desc" } : null)}
              className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-sm normal-case tracking-normal text-slate-100 sm:min-h-9"
            >
              <option value="">Newest first</option>
              {SORT_CHOICES.map((choice) => (
                <option key={choice.key} value={choice.key}>
                  {choice.label}
                </option>
              ))}
            </select>
            {sort ? (
              <button
                type="button"
                aria-label={sort.dir === "asc" ? "Ascending, switch to descending" : "Descending, switch to ascending"}
                onClick={() => setSort({ key: sort.key, dir: sort.dir === "asc" ? "desc" : "asc" })}
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border border-white/15 text-slate-200 hover:bg-white/5 sm:min-h-9 sm:min-w-9"
              >
                {sort.dir === "asc" ? <ArrowUp size={14} aria-hidden /> : <ArrowDown size={14} aria-hidden />}
              </button>
            ) : null}
          </div>
        </div>
        {filtersOn && (
          <button
            type="button"
            onClick={clearFilters}
            className="min-h-11 rounded-md border border-white/15 px-3 text-xs text-slate-200 hover:bg-white/5 sm:min-h-9"
          >
            Clear
          </button>
        )}
        <p className="pb-1 text-xs text-slate-400">
          {rows.length.toLocaleString("en-IN")} of {full.total.toLocaleString("en-IN")}
          {full.total > inBook.length ? ` (newest ${inBook.length.toLocaleString("en-IN")} loaded)` : ""}
          {rows.length > 0 && (
            <>
              {" "}
              · P&L {inr(filteredPnl)} · net {inr(filteredNet)}
            </>
          )}
        </p>
      </div>
      <p className="px-4 pb-3 text-xs text-slate-400">{selected.note}</p>
      {pdfError ? (
        <p role="alert" className="px-4 pb-2 text-xs text-rose-300">
          {pdfError}
        </p>
      ) : null}
      {summaryOpen && bookSummary ? (
        <div className="mx-2 mb-3 rounded-lg border border-sky-500/30 bg-sky-500/[0.03] sm:mx-4">
          <RunDetail
            run={bookSummary}
            heading={`${summaryTitle} · day-wise P&L`}
            subheading={`${filterWords()}${full.total > inBook.length ? ` · newest ${inBook.length.toLocaleString("en-IN")} trades loaded` : ""}`}
            settingsTitle="Strategies used"
            settingsRows={usedStrategies}
          />
        </div>
      ) : null}
      <BookSummary
        closed={completedRows.length}
        open={openRows.length}
        {...summary}
        filtered={filtersOn}
      />
      {loading || full.loading ? (
        <div aria-busy="true" aria-label="Loading trades" className="space-y-2 border-t border-white/10 p-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : (
      <>
      <OrderTable
        title="Open"
        rows={openRows}
        state={state}
        closingSymbol={closingSymbol}
        onClose={onClose}
        sort={sort}
        onSort={sortBy}
        runs={runs}
        resetKey={resetKey}
        empty={
          inBook.length === 0
            ? simulation
              ? "No simulated trades yet. Start the bot and wait for the next SMA cross."
              : book === "REPLAY"
                ? "No replay trades yet. Use “Replay a past day” above."
                : "No NSE live trades on this page."
            : "No open orders."
        }
      />
      {inBook.length > 0 && !closedFolded && (
        <OrderTable
          title="Completed"
          subtotals
          rows={completedRows}
          closingSymbol={null}
          onClose={onClose}
          sort={sort}
          onSort={sortBy}
          runs={runs}
          resetKey={resetKey}
          state={null}
          empty={rows.length === 0 ? "No trades match these filters." : "No completed orders."}
        />
      )}
      </>
      )}
      </>
      )}
    </section>
  );
}

type Col = { key: SortKey | "action"; label: string; num?: boolean };
const COLUMNS: Col[] = [
  { key: "id", label: "#", num: true },
  { key: "stock", label: "Stock" },
  { key: "side", label: "Side" },
  { key: "entry_time", label: "Entry time" },
  { key: "entry", label: "Entry", num: true },
  { key: "exit_time", label: "Exit time" },
  { key: "exit", label: "Exit", num: true },
  { key: "high", label: "Max high", num: true },
  { key: "low", label: "Max low", num: true },
  { key: "points", label: "Points", num: true },
  { key: "reason", label: "Exit reason" },
  { key: "strategy", label: "Strategy" },
  { key: "charges", label: "Charges", num: true },
  { key: "net", label: "Net P&L", num: true },
  { key: "action", label: "" },
];

const REASON_COLOR: Record<string, "sky" | "amber" | "violet" | "red" | "blue" | "slate"> = {
  MA_CROSS: "sky",
  MA_APPROACH: "sky",
  ATR_SL_HIT: "amber",
  GAP_SL_HIT: "amber",
  TSL_HIT: "amber",
  TARGET_HIT: "sky",
  EOD_SQUARE_OFF: "violet",
  KILL_SWITCH: "red",
  SL_REJECTED: "red",
  MANUAL_CLOSE: "blue",
  NOT_ON_GROWW: "slate",
};

export const REASON_SHORT: Record<string, string> = {
  MA_CROSS: "MA cross",
  MA_APPROACH: "Before cross",
  ATR_SL_HIT: "ATR SL",
  GAP_SL_HIT: "Moving SL",
  TSL_HIT: "Trailing SL",
  TARGET_HIT: "Target",
  EOD_SQUARE_OFF: "EOD",
  KILL_SWITCH: "Kill switch",
  SL_REJECTED: "Stop refused",
  MANUAL_CLOSE: "Manual",
  NOT_ON_GROWW: "Not on Groww",
};

function BookSummary({
  closed,
  open,
  wins,
  losses,
  flat,
  profit,
  loss,
  charges,
  net,
  filtered,
}: {
  closed: number;
  open: number;
  wins: number;
  losses: number;
  flat: number;
  profit: number;
  loss: number;
  charges: number;
  net: number;
  filtered: boolean;
}) {
  const gross = profit + loss;
  const signed = (v: number) => `${v > 0 ? "+" : ""}${inr(v)}`;
  const tile = "min-w-0 rounded-lg bg-black/25 px-3 py-2 ring-1 ring-inset ring-white/10";
  const label = "text-[11px] uppercase tracking-wider text-slate-400";
  return (
    <section aria-label="Closed trades summary" className="border-t border-white/10 px-4 py-3">
      <div className="mb-2 text-[11px] text-slate-400">
        Closed trades{filtered ? " matching the filters" : " in this book"}
        {open ? ` · ${open} still open (not counted)` : ""}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <div className={tile}>
          <div className={label}>Trades</div>
          <div className="font-mono text-[16px] font-semibold text-slate-100">{closed}</div>
          <div className="text-[11px] text-slate-400">
            {wins} W · {losses} L{flat ? ` · ${flat} flat` : ""}
          </div>
        </div>
        <div className={tile}>
          <div className={label}>Total profit</div>
          <div className="font-mono text-[16px] font-semibold text-emerald-300">{signed(profit)}</div>
          <div className="text-[11px] text-slate-400">from {wins} winning</div>
        </div>
        <div className={tile}>
          <div className={label}>Total loss</div>
          <div className="font-mono text-[16px] font-semibold text-rose-300">{signed(loss)}</div>
          <div className="text-[11px] text-slate-400">from {losses} losing</div>
        </div>
        <div className={tile}>
          <div className={label}>Gross P&amp;L</div>
          <div className={clsx("font-mono text-[16px] font-semibold", pnlTone(gross))}>{signed(gross)}</div>
          <div className="text-[11px] text-slate-400">profit + loss</div>
        </div>
        <div className={tile}>
          <div className={label}>Charges</div>
          <div className="font-mono text-[16px] font-semibold text-amber-300">{inr(charges)}</div>
          <div className="text-[11px] text-slate-400">brokerage &amp; taxes</div>
        </div>
        <div className={tile}>
          <div className={label}>Net P&amp;L</div>
          <div className={clsx("font-mono text-[16px] font-semibold", pnlTone(net))}>{signed(net)}</div>
          <div className="text-[11px] text-slate-400">after charges</div>
        </div>
      </div>
    </section>
  );
}

function ReasonBadge({ trade }: { trade: TradeRow }) {
  if (trade.exit_price == null) return <Badge color="green">Open</Badge>;
  const reason = trade.exit_reason || "";
  return (
    <Badge color={REASON_COLOR[reason] ?? "slate"} title={REASON[reason] ?? reason}>
      {REASON_SHORT[reason] ?? (reason || "Closed")}
    </Badge>
  );
}

/** Long / Short in green when the trade made money, red when it lost, grey while open. */
function ResultSideBadge({ trade, net }: { trade: TradeRow; net: number | null }) {
  const word = trade.direction === "LONG" ? "Long" : trade.direction === "SHORT" ? "Short" : "Flat";
  const open = trade.exit_price == null;
  const color = open || net == null || net === 0 ? "slate" : net > 0 ? "green" : "red";
  const result = open ? "open" : net == null || net === 0 ? "flat" : net > 0 ? "profit" : "loss";
  return (
    <Badge color={color} title={`${trade.direction === "LONG" ? "Bought" : "Sold"} first · ${result}`}>
      {word}
    </Badge>
  );
}

/**
 * The trade's highest or lowest price, with its distance from the entry in
 * points: green when it was in the trade's favour (high for a long, low for a
 * short), red when against it.
 */
function Extreme({ trade, which, left }: { trade: TradeRow; which: "high" | "low"; left?: boolean }) {
  const price = which === "high" ? trade.max_high : trade.max_low;
  if (price == null) {
    return (
      <span className="font-mono text-slate-500" title="Recorded for trades closed after this was added.">
        —
      </span>
    );
  }
  const move = price - trade.entry_price;
  const amount = pnlAtPrice(trade, price) ?? 0;
  const tone = amount > 0 ? "text-emerald-300" : amount < 0 ? "text-rose-300" : "text-slate-400";
  const label =
    amount > 0
      ? `best moment: ${signedInr(amount)} if closed there`
      : amount < 0
        ? `worst moment: ${signedInr(amount)} if closed there`
        : "never moved past the entry";
  return (
    <span
      className={clsx("inline-flex flex-col leading-tight", left ? "items-start" : "items-end")}
      title={`${which === "high" ? "Highest" : "Lowest"} price while open (${label}, before charges)`}
    >
      <span className="font-mono">{px(price)}</span>
      <span className={clsx("font-mono text-[11px]", tone)}>
        {move > 0 ? "+" : ""}
        {move.toFixed(2)} pts
      </span>
      <span className={clsx("font-mono text-[11px] font-semibold", tone)}>{signedInr(amount)}</span>
    </span>
  );
}

function StrategyCell({ settings }: { settings: Settings | null }) {
  if (!settings) {
    return (
      <span className="text-slate-500" title="This trade was booked before the strategy was recorded on each trade.">
        —
      </span>
    );
  }
  return (
    <div className="min-w-0 leading-tight" title={strategyLabel(settings)}>
      <div className="truncate text-xs text-slate-200">
        SMA {settings.sma_fast ?? 9}/{settings.sma_slow ?? 21} · {stopShort(settings)}
      </div>
      <div className="truncate text-[11px] text-slate-400">{filtersShort(settings)}</div>
    </div>
  );
}

/** One row's numbers. An open row is marked at the live price and says so. */
function rowFigures(t: TradeRow, state: SmaState | null) {
  const open = t.exit_price == null;
  const market = marketPrice(t, state);
  const mark = open ? markPnl(t, market) : null;
  const points = open ? mark?.points ?? null : t.points;
  const net = open ? mark?.pnl ?? null : t.net_pnl;
  return { open, market, points, net };
}

/** `count`, `charges` and `net` cover the whole day, even when a page shows part of it. */
type DayGroup = { day: string; rows: TradeRow[]; count: number; charges: number; net: number };

function byDay(rows: TradeRow[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const row of rows) {
    const day = tradeDay(row) || "—";
    let group = groups[groups.length - 1];
    if (!group || group.day !== day) {
      group = { day, rows: [], count: 0, charges: 0, net: 0 };
      groups.push(group);
    }
    group.rows.push(row);
    group.count += 1;
    group.charges += row.brokerage_and_taxes ?? 0;
    group.net += row.net_pnl ?? 0;
  }
  return groups;
}

const PAGE_SIZES = [100, 250, 500];

function dayLabel(day: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", {
    timeZone: "UTC",
    weekday: "short",
    day: "2-digit",
    month: "short",
  });
}

function OrderTable({
  title,
  rows,
  state,
  empty,
  closingSymbol,
  onClose,
  subtotals,
  sort,
  onSort,
  runs,
  resetKey,
}: {
  title: string;
  rows: TradeRow[];
  state: SmaState | null;
  empty: string;
  closingSymbol: string | null;
  onClose: (trade: TradeRow) => void;
  subtotals?: boolean;
  sort: Sort;
  onSort: (key: SortKey) => void;
  runs: RunSettings;
  /** Changes when the book, filters or sort change: back to page 1. */
  resetKey: string;
}) {
  // Thousands of rows are drawn a page at a time; totals still cover them all.
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]);
  useEffect(() => setPage(0), [resetKey, pageSize]);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pages - 1);
  const pageRows = rows.length > pageSize ? rows.slice(current * pageSize, (current + 1) * pageSize) : rows;
  // Day subtotals only while the rows stay in date order.
  const byTime = BY_TIME.includes(sort?.key);
  const allDays = useMemo(() => (byTime ? byDay(rows) : []), [byTime, rows]);
  const groups: DayGroup[] = byTime
    ? byDay(pageRows).map((group) => {
        const whole = allDays.find((day) => day.day === group.day);
        return whole ? { ...group, count: whole.count, charges: whole.charges, net: whole.net } : group;
      })
    : [{ day: "all", rows: pageRows, count: pageRows.length, charges: 0, net: 0 }];
  const showSubtotals = Boolean(subtotals) && byTime && allDays.length > 1;
  const pager =
    rows.length > PAGE_SIZES[0] ? (
      <Pager
        page={current}
        pages={pages}
        size={pageSize}
        total={rows.length}
        onPage={setPage}
        onSize={setPageSize}
      />
    ) : null;
  return (
    <div className="border-t border-white/10">
      <h3 className="flex items-baseline gap-2 px-4 pt-3 text-xs font-semibold uppercase tracking-wider text-slate-300">
        {title}
        <span className="font-normal text-slate-400">{rows.length.toLocaleString("en-IN")}</span>
      </h3>
      {pager}

      {/* Phone: one stacked card per trade. */}
      <ul className="space-y-2 p-3 md:hidden">
        {rows.length === 0 && <li className="py-4 text-center text-sm text-slate-400">{empty}</li>}
        {groups.map((group) => (
          <li key={group.day} className="space-y-2">
            {group.rows.map((trade) => (
              <TradeCard
                key={trade.id}
                trade={trade}
                runs={runs}
                state={state}
                closing={closingSymbol === trade.symbol.toUpperCase()}
                onClose={onClose}
              />
            ))}
            {showSubtotals && <DaySubtotal group={group} as="card" />}
          </li>
        ))}
      </ul>

      {/* Tablet and desktop: a table with a sticky header. */}
      <div className="hidden max-h-[70vh] overflow-auto md:block">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 z-10 bg-[#1b2130] text-xs uppercase tracking-wider text-slate-300 shadow-[0_1px_0_rgba(255,255,255,0.1)]">
            <tr>
              {COLUMNS.map((col) => {
                if (col.key === "action") return <th key={col.key} scope="col" className="px-3 py-2" />;
                const key = col.key;
                const on = sort?.key === key;
                const Icon = !on ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
                return (
                  <th
                    key={key}
                    scope="col"
                    aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    className={clsx("whitespace-nowrap px-1.5 py-1 font-medium", col.num && "text-right")}
                  >
                    <button
                      type="button"
                      onClick={() => onSort(key)}
                      title={`Sort by ${col.label === "#" ? "trade number" : col.label.toLowerCase()}`}
                      className={clsx(
                        "inline-flex min-h-8 items-center gap-1 rounded px-1.5 uppercase tracking-wider hover:bg-white/5 hover:text-white",
                        col.num && "flex-row-reverse",
                        on ? "text-white" : "text-slate-300"
                      )}
                    >
                      {col.label}
                      <Icon size={12} aria-hidden className={on ? "text-sky-300" : "text-slate-500"} />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-3 py-6 text-center text-slate-400">
                  {empty}
                </td>
              </tr>
            )}
            {groups.map((group) => (
              <Fragment key={group.day}>
                {group.rows.map((trade) => (
                  <OrderRow
                    key={trade.id}
                    trade={trade}
                    runs={runs}
                    state={state}
                    closing={closingSymbol === trade.symbol.toUpperCase()}
                    onClose={onClose}
                  />
                ))}
                {showSubtotals && <DaySubtotal group={group} as="row" />}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {pager}
    </div>
  );
}

function Pager({
  page,
  pages,
  size,
  total,
  onPage,
  onSize,
}: {
  page: number;
  pages: number;
  size: number;
  total: number;
  onPage: (page: number) => void;
  onSize: (size: number) => void;
}) {
  const first = page * size + 1;
  const last = Math.min(total, (page + 1) * size);
  const button =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border border-white/15 px-2 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-40 sm:min-h-8 sm:min-w-8";
  return (
    <nav aria-label="Pages" className="flex flex-wrap items-center gap-2 px-4 py-2 text-xs text-slate-400">
      <span>
        {first.toLocaleString("en-IN")}–{last.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")}
      </span>
      <span className="flex items-center gap-1">
        <button type="button" className={button} disabled={page === 0} onClick={() => onPage(0)} aria-label="First page">
          «
        </button>
        <button type="button" className={button} disabled={page === 0} onClick={() => onPage(page - 1)} aria-label="Previous page">
          ‹
        </button>
        <span className="px-1 text-slate-300">
          Page {page + 1} of {pages}
        </span>
        <button type="button" className={button} disabled={page >= pages - 1} onClick={() => onPage(page + 1)} aria-label="Next page">
          ›
        </button>
        <button type="button" className={button} disabled={page >= pages - 1} onClick={() => onPage(pages - 1)} aria-label="Last page">
          »
        </button>
      </span>
      <label className="ml-auto flex items-center gap-1.5">
        Rows per page
        <select
          value={size}
          onChange={(e) => onSize(Number(e.target.value))}
          className="min-h-11 rounded-md border border-white/15 bg-black/40 px-2 text-xs text-slate-100 sm:min-h-8"
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
    </nav>
  );
}

function DaySubtotal({ group, as }: { group: DayGroup; as: "row" | "card" }) {
  const n = group.count;
  const label = `${dayLabel(group.day)} · ${n} trade${n === 1 ? "" : "s"}`;
  if (as === "card") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg bg-white/[0.06] px-3 py-2 text-sm">
        <span className="font-semibold text-slate-200">Subtotal · {label}</span>
        <span className="font-mono">
          <span className="text-amber-300">{inr(group.charges)}</span>
          <span className={clsx("ml-3 font-semibold", pnlTone(group.net))}>{signedInr(group.net)}</span>
        </span>
      </div>
    );
  }
  return (
    <tr className="border-y border-white/15 bg-white/[0.06] font-semibold">
      <td colSpan={12} className="px-3 py-2 text-slate-200">
        Subtotal · {label}
      </td>
      <td className="px-3 py-2 text-right font-mono text-amber-300">{inr(group.charges)}</td>
      <td className={clsx("px-3 py-2 text-right font-mono", pnlTone(group.net))}>{signedInr(group.net)}</td>
      <td />
    </tr>
  );
}

function signedInr(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${inr(value)}`;
}

function signedPts(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function CloseButton({ trade, closing, onClose }: { trade: TradeRow; closing: boolean; onClose: (t: TradeRow) => void }) {
  return (
    <button
      type="button"
      disabled={closing}
      onClick={() => onClose(trade)}
      className="min-h-11 rounded-md px-3 text-xs font-semibold text-rose-200 ring-1 ring-inset ring-rose-400/50 hover:bg-rose-500/15 disabled:opacity-50 md:min-h-8"
    >
      {closing ? "Closing…" : "Close"}
    </button>
  );
}

const OrderRow = memo(OrderRowView, sameRow);
const TradeCard = memo(TradeCardView, sameRow);

type RowProps = {
  trade: TradeRow;
  runs: RunSettings;
  state: SmaState | null;
  closing: boolean;
  onClose: (trade: TradeRow) => void;
};

/** A closed row only changes with its own data; the page passes it no live state. */
function sameRow(a: RowProps, b: RowProps): boolean {
  return a.trade === b.trade && a.runs === b.runs && a.state === b.state && a.closing === b.closing;
}

function OrderRowView({
  trade: t,
  runs,
  state,
  closing,
  onClose,
}: {
  trade: TradeRow;
  runs: RunSettings;
  state: SmaState | null;
  closing: boolean;
  onClose: (trade: TradeRow) => void;
}) {
  const { open, market, points, net } = rowFigures(t, state);
  return (
    <tr className="border-b border-white/5 text-slate-200 odd:bg-white/[0.025] hover:bg-white/[0.05]">
      <td className="px-3 py-2 text-right font-mono text-slate-400">{t.id}</td>
      <td className="whitespace-nowrap px-3 py-2 font-semibold text-amber-300">{t.symbol}</td>
      <td className="whitespace-nowrap px-3 py-2">
        <ResultSideBadge trade={t} net={net} />
        <span className="ml-1.5 font-mono text-xs text-slate-400">{t.qty}</span>
      </td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-300">{istStamp(t.entry_time)}</td>
      <td className="px-3 py-2 text-right font-mono">{px(t.entry_price)}</td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-300">{open ? "—" : istStamp(t.exit_time)}</td>
      <td className="px-3 py-2 text-right font-mono">
        {open ? <span className="text-slate-400" title="Live price">{market == null ? "—" : `${px(market)}`}</span> : px(t.exit_price)}
      </td>
      <td className="px-3 py-2 text-right">
        <Extreme trade={t} which="high" />
      </td>
      <td className="px-3 py-2 text-right">
        <Extreme trade={t} which="low" />
      </td>
      <td className={clsx("px-3 py-2 text-right font-mono", pnlTone(points))}>{signedPts(points)}</td>
      <td className="px-3 py-2">
        <ReasonBadge trade={t} />
      </td>
      <td className="max-w-[13rem] px-3 py-2">
        <StrategyCell settings={settingsOf(t, runs)} />
      </td>
      <td className="px-3 py-2 text-right font-mono text-amber-300">
        {t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)}
      </td>
      <td className={clsx("px-3 py-2 text-right font-mono font-semibold", pnlTone(net))} title={open ? "Open: marked at the live price, before charges" : undefined}>
        {signedInr(net)}
      </td>
      <td className="px-3 py-1.5 text-right">{open ? <CloseButton trade={t} closing={closing} onClose={onClose} /> : null}</td>
    </tr>
  );
}

function TradeCardView({
  trade: t,
  runs,
  state,
  closing,
  onClose,
}: {
  trade: TradeRow;
  runs: RunSettings;
  state: SmaState | null;
  closing: boolean;
  onClose: (trade: TradeRow) => void;
}) {
  const { open, market, points, net } = rowFigures(t, state);
  return (
    <article className="rounded-lg bg-white/[0.03] p-3 ring-1 ring-inset ring-white/10">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-semibold text-amber-300">{t.symbol}</span>
          <ResultSideBadge trade={t} net={net} />
          <span className="font-mono text-xs text-slate-400">{t.qty}</span>
        </div>
        <span className={clsx("shrink-0 font-mono text-[17px] font-semibold", pnlTone(net))}>{signedInr(net)}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <Field k="Entry" v={px(t.entry_price)} sub={istStamp(t.entry_time)} />
        <Field k={open ? "Live price" : "Exit"} v={open ? px(market) : px(t.exit_price)} sub={open ? "open" : istStamp(t.exit_time)} />
        <Field k="Points" v={signedPts(points)} tone={pnlTone(points)} />
        <Field k="Charges" v={t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)} tone="text-amber-300" />
        <div className="min-w-0">
          <dt className="text-[11px] uppercase tracking-wider text-slate-400">Max high</dt>
          <dd className="text-sm text-slate-200">
            <Extreme trade={t} which="high" left />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[11px] uppercase tracking-wider text-slate-400">Max low</dt>
          <dd className="text-sm text-slate-200">
            <Extreme trade={t} which="low" left />
          </dd>
        </div>
      </dl>
      <div className="mt-2 text-xs">
        <div className="text-[11px] uppercase tracking-wider text-slate-400">Strategy</div>
        <StrategyCell settings={settingsOf(t, runs)} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-xs text-slate-400">
          #{t.id} <ReasonBadge trade={t} />
        </span>
        {open ? <CloseButton trade={t} closing={closing} onClose={onClose} /> : null}
      </div>
    </article>
  );
}

function Field({ k, v, sub, tone: color }: { k: string; v: string; sub?: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wider text-slate-400">{k}</dt>
      <dd className={clsx("truncate font-mono text-sm text-slate-200", color)}>{v}</dd>
      {sub ? <dd className="truncate font-mono text-[11px] text-slate-400">{sub}</dd> : null}
    </div>
  );
}

function csvCell(value: string | number | null | undefined): string {
  if (value == null) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function marketPrice(trade: TradeRow, state: SmaState | null): number | null {
  if (trade.exit_price != null && trade.exit_price > 0) return trade.exit_price;
  const symbol = trade.symbol?.toUpperCase();
  const book = state?.books?.find((row) => row.symbol.toUpperCase() === symbol);
  if (book?.ltp != null && book.ltp > 0) return book.ltp;
  if (state && state.symbol?.toUpperCase() === symbol && state.ltp > 0) return state.ltp;
  if (trade.market_price != null && trade.market_price > 0) return trade.market_price;
  return null;
}

/** Long P&L is market minus the fill. Short P&L is the fill minus market. */
export function markPnl(trade: TradeRow, market: number | null): { points: number; pnl: number } | null {
  if (market == null || !Number.isFinite(market) || !Number.isFinite(trade.entry_price)) return null;
  const points = trade.direction === "LONG" ? market - trade.entry_price : trade.entry_price - market;
  return { points, pnl: points * trade.qty };
}

