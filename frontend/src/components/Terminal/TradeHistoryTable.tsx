"use client";

import { Fragment, useEffect, useState } from "react";
import clsx from "clsx";
import { istStamp, parseClock } from "@/lib/format";
import { inr, px, type SmaState, type TradeRow } from "@/lib/smaApi";
import { Badge, SideBadge, pnlTone } from "./ui";

const REASON: Record<string, string> = {
  MA_CROSS: "MA CROSS",
  MA_APPROACH: "SOLD BEFORE CROSS",
  ATR_SL_HIT: "ATR SL HIT",
  EOD_SQUARE_OFF: "EOD SQUARE-OFF",
  KILL_SWITCH: "KILL SWITCH",
  NOT_ON_GROWW: "NOT ON GROWW",
  SL_REJECTED: "STOP REFUSED",
  MANUAL_CLOSE: "MANUAL CLOSE",
};

type Book = "PAPER" | "LIVE";

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
];

function bookOf(trade: TradeRow): Book {
  return (trade.mode || "PAPER").toUpperCase() === "LIVE" ? "LIVE" : "PAPER";
}

type PnlSide = "all" | "profit" | "loss" | "open";

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
}: {
  trades: TradeRow[];
  state: SmaState | null;
  closingSymbol: string | null;
  onClose: (trade: TradeRow) => void;
}) {
  const [book, setBook] = useState<Book>("PAPER");
  const [picked, setPicked] = useState(false);
  const [stock, setStock] = useState("ALL");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [pnlSide, setPnlSide] = useState<PnlSide>("all");
  const [minPnl, setMinPnl] = useState("");
  const [maxPnl, setMaxPnl] = useState("");
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
    setBook(state.mode === "LIVE" ? "LIVE" : "PAPER");
  }, [picked, state?.mode]);
  const inBook = trades.filter((trade) => bookOf(trade) === book);
  const symbols = Array.from(new Set(inBook.map((trade) => trade.symbol.toUpperCase()))).sort();
  const stockFilter = symbols.includes(stock) ? stock : "ALL";
  const min = minPnl.trim() === "" ? null : Number(minPnl);
  const max = maxPnl.trim() === "" ? null : Number(maxPnl);
  const rows = inBook.filter((trade) => {
    if (stockFilter !== "ALL" && trade.symbol.toUpperCase() !== stockFilter) return false;
    const day = tradeDay(trade);
    if (from && (!day || day < from)) return false;
    if (to && (!day || day > to)) return false;
    const open = trade.exit_price == null;
    const pnl = shownPnl(trade, state);
    if (pnlSide === "open" && !open) return false;
    if (pnlSide === "profit" && !(pnl != null && pnl > 0)) return false;
    if (pnlSide === "loss" && !(pnl != null && pnl < 0)) return false;
    if (min != null && Number.isFinite(min) && (pnl == null || pnl < min)) return false;
    if (max != null && Number.isFinite(max) && (pnl == null || pnl > max)) return false;
    return true;
  });
  const openRows = rows.filter((trade) => trade.exit_price == null);
  const completedRows = rows.filter((trade) => trade.exit_price != null);
  const filteredPnl = rows.reduce((sum, trade) => {
    const pnl = shownPnl(trade, state);
    return pnl == null ? sum : sum + pnl;
  }, 0);
  const filteredNet = rows.reduce((sum, trade) => (trade.net_pnl == null ? sum : sum + trade.net_pnl), 0);
  const filtersOn = stockFilter !== "ALL" || from !== "" || to !== "" || pnlSide !== "all" || minPnl !== "" || maxPnl !== "";
  const selected = BOOKS.find((item) => item.id === book) ?? BOOKS[0];
  const simulation = book === "PAPER";

  const clearFilters = () => {
    setStock("ALL");
    setFrom("");
    setTo("");
    setPnlSide("all");
    setMinPnl("");
    setMaxPnl("");
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
      "gross_pnl",
      "brokerage_and_taxes",
      "net_pnl",
      "mode",
    ] as const;
    const lines = [fields.join(",")];
    for (const trade of rows) {
      lines.push(fields.map((key) => csvCell(trade[key])).join(","));
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
        simulation ? "border-[#F59E0B]/40" : "border-[#F43F5E]/40"
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Trade blotter</div>
          <h2 className={clsx("text-sm font-medium", simulation ? "text-[#F59E0B]" : "text-[#F43F5E]")}>
            {selected.title}
          </h2>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:items-center">
          {BOOKS.map((item) => {
            const count = trades.filter((trade) => bookOf(trade) === item.id).length;
            const on = item.id === book;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setPicked(true);
                  setBook(item.id);
                }}
                className={clsx(
                  "min-h-11 whitespace-nowrap rounded-md px-3 text-xs font-semibold sm:min-h-9",
                  on && item.id === "PAPER" && "bg-[#F59E0B] text-[#1a1203]",
                  on && item.id === "LIVE" && "bg-[#F43F5E] text-white",
                  !on && "border border-white/10 text-slate-300 hover:bg-white/5"
                )}
              >
                {item.title} · {count}
              </button>
            );
          })}
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
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2 px-4 pb-3">
        <label className="flex min-w-[8.5rem] flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          Stock
          <select
            value={stockFilter}
            onChange={(e) => setStock(e.target.value)}
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          >
            <option value="ALL">All stocks</option>
            {symbols.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          From
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          To
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          />
        </label>
        <label className="flex min-w-[7.5rem] flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          P&L
          <select
            value={pnlSide}
            onChange={(e) => setPnlSide(e.target.value as PnlSide)}
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          >
            <option value="all">Any</option>
            <option value="profit">Profit</option>
            <option value="loss">Loss</option>
            <option value="open">Open only</option>
          </select>
        </label>
        <label className="flex w-24 flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          Min ₹
          <input
            inputMode="decimal"
            value={minPnl}
            onChange={(e) => setMinPnl(e.target.value)}
            placeholder="−500"
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          />
        </label>
        <label className="flex w-24 flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
          Max ₹
          <input
            inputMode="decimal"
            value={maxPnl}
            onChange={(e) => setMaxPnl(e.target.value)}
            placeholder="500"
            className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs normal-case tracking-normal text-slate-100"
          />
        </label>
        {filtersOn && (
          <button
            type="button"
            onClick={clearFilters}
            className="rounded-md border border-white/10 px-2 py-1.5 text-xs text-slate-300 hover:bg-white/5"
          >
            Clear
          </button>
        )}
        <p className="pb-1 text-xs text-slate-400">
          {rows.length} of {inBook.length}
          {rows.length > 0 && (
            <>
              {" "}
              · P&L {inr(filteredPnl)} · net {inr(filteredNet)}
            </>
          )}
        </p>
      </div>
      <p className="px-4 pb-3 text-xs text-slate-400">{selected.note}</p>
      <OrderTable
        title="Open"
        rows={openRows}
        state={state}
        closingSymbol={closingSymbol}
        onClose={onClose}
        empty={
          inBook.length === 0
            ? simulation
              ? "No simulated trades yet. Start the bot and wait for the next SMA cross."
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
          state={state}
          empty={rows.length === 0 ? "No trades match these filters." : "No completed orders."}
        />
      )}
    </section>
  );
}

type Col = { key: string; label: string; num?: boolean };
const COLUMNS: Col[] = [
  { key: "id", label: "#", num: true },
  { key: "stock", label: "Stock" },
  { key: "side", label: "Side" },
  { key: "entry_time", label: "Entry time" },
  { key: "entry", label: "Entry", num: true },
  { key: "exit_time", label: "Exit time" },
  { key: "exit", label: "Exit", num: true },
  { key: "points", label: "Points", num: true },
  { key: "reason", label: "Exit reason" },
  { key: "charges", label: "Charges", num: true },
  { key: "net", label: "Net P&L", num: true },
  { key: "action", label: "" },
];

const REASON_COLOR: Record<string, "sky" | "amber" | "violet" | "red" | "blue" | "slate"> = {
  MA_CROSS: "sky",
  MA_APPROACH: "sky",
  ATR_SL_HIT: "amber",
  EOD_SQUARE_OFF: "violet",
  KILL_SWITCH: "red",
  SL_REJECTED: "red",
  MANUAL_CLOSE: "blue",
  NOT_ON_GROWW: "slate",
};

const REASON_SHORT: Record<string, string> = {
  MA_CROSS: "MA cross",
  MA_APPROACH: "Before cross",
  ATR_SL_HIT: "ATR SL",
  EOD_SQUARE_OFF: "EOD",
  KILL_SWITCH: "Kill switch",
  SL_REJECTED: "Stop refused",
  MANUAL_CLOSE: "Manual",
  NOT_ON_GROWW: "Not on Groww",
};

function ReasonBadge({ trade }: { trade: TradeRow }) {
  if (trade.exit_price == null) return <Badge color="green">Open</Badge>;
  const reason = trade.exit_reason || "";
  return (
    <Badge color={REASON_COLOR[reason] ?? "slate"} title={REASON[reason] ?? reason}>
      {REASON_SHORT[reason] ?? (reason || "Closed")}
    </Badge>
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

type DayGroup = { day: string; rows: TradeRow[]; charges: number; net: number };

function byDay(rows: TradeRow[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const row of rows) {
    const day = tradeDay(row) || "—";
    let group = groups[groups.length - 1];
    if (!group || group.day !== day) {
      group = { day, rows: [], charges: 0, net: 0 };
      groups.push(group);
    }
    group.rows.push(row);
    group.charges += row.brokerage_and_taxes ?? 0;
    group.net += row.net_pnl ?? 0;
  }
  return groups;
}

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
}: {
  title: string;
  rows: TradeRow[];
  state: SmaState | null;
  empty: string;
  closingSymbol: string | null;
  onClose: (trade: TradeRow) => void;
  subtotals?: boolean;
}) {
  const groups = byDay(rows);
  const showSubtotals = Boolean(subtotals) && groups.length > 1;
  return (
    <div className="border-t border-white/10">
      <h3 className="flex items-baseline gap-2 px-4 pt-3 text-xs font-semibold uppercase tracking-wider text-slate-300">
        {title}
        <span className="font-normal text-slate-400">{rows.length}</span>
      </h3>

      {/* Phone: one stacked card per trade. */}
      <ul className="space-y-2 p-3 md:hidden">
        {rows.length === 0 && <li className="py-4 text-center text-sm text-slate-400">{empty}</li>}
        {groups.map((group) => (
          <li key={group.day} className="space-y-2">
            {group.rows.map((trade) => (
              <TradeCard
                key={trade.id}
                trade={trade}
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
              {COLUMNS.map((col) => (
                <th key={col.key} scope="col" className={clsx("whitespace-nowrap px-3 py-2 font-medium", col.num && "text-right")}>
                  {col.label}
                </th>
              ))}
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
    </div>
  );
}

function DaySubtotal({ group, as }: { group: DayGroup; as: "row" | "card" }) {
  const n = group.rows.length;
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
      <td colSpan={9} className="px-3 py-2 text-slate-200">
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

function OrderRow({
  trade: t,
  state,
  closing,
  onClose,
}: {
  trade: TradeRow;
  state: SmaState | null;
  closing: boolean;
  onClose: (trade: TradeRow) => void;
}) {
  const { open, market, points, net } = rowFigures(t, state);
  return (
    <tr className="border-b border-white/5 text-slate-200 odd:bg-white/[0.025] hover:bg-white/[0.05]">
      <td className="px-3 py-2 text-right font-mono text-slate-400">{t.id}</td>
      <td className="whitespace-nowrap px-3 py-2 font-semibold text-amber-300">{t.symbol}</td>
      <td className="px-3 py-2">
        <SideBadge side={t.direction} />
        <span className="ml-1.5 font-mono text-xs text-slate-400">{t.qty}</span>
      </td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-300">{istStamp(t.entry_time)}</td>
      <td className="px-3 py-2 text-right font-mono">{px(t.entry_price)}</td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-300">{open ? "—" : istStamp(t.exit_time)}</td>
      <td className="px-3 py-2 text-right font-mono">
        {open ? <span className="text-slate-400" title="Live price">{market == null ? "—" : `${px(market)}`}</span> : px(t.exit_price)}
      </td>
      <td className={clsx("px-3 py-2 text-right font-mono", pnlTone(points))}>{signedPts(points)}</td>
      <td className="px-3 py-2">
        <ReasonBadge trade={t} />
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

function TradeCard({
  trade: t,
  state,
  closing,
  onClose,
}: {
  trade: TradeRow;
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
          <SideBadge side={t.direction} />
          <span className="font-mono text-xs text-slate-400">{t.qty}</span>
        </div>
        <span className={clsx("shrink-0 font-mono text-[17px] font-semibold", pnlTone(net))}>{signedInr(net)}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <Field k="Entry" v={px(t.entry_price)} sub={istStamp(t.entry_time)} />
        <Field k={open ? "Live price" : "Exit"} v={open ? px(market) : px(t.exit_price)} sub={open ? "open" : istStamp(t.exit_time)} />
        <Field k="Points" v={signedPts(points)} tone={pnlTone(points)} />
        <Field k="Charges" v={t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)} tone="text-amber-300" />
      </dl>
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

