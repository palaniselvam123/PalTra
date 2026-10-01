"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { istDateTime, parseClock } from "@/lib/format";
import { inr, px, type SmaState, type TradeRow } from "@/lib/smaApi";

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
        <div className="flex items-center gap-2">
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
                  "rounded-md px-3 py-1.5 text-xs font-semibold",
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
            className="rounded-md border border-white/10 px-2 py-1 text-xs font-semibold text-slate-200 hover:bg-white/5"
          >
            {closedFolded ? `Show closed orders · ${completedRows.length}` : "Shrink closed orders"}
          </button>
          <button
            type="button"
            onClick={downloadFiltered}
            className="rounded-md border border-white/10 px-2 py-1 text-xs text-slate-300 hover:bg-white/5"
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

const COLUMNS = ["#", "Stock", "Side", "Executed", "Market", "P&L", "ATR", "SL", "Exit", "Trigger", "Charges", "Net", ""];

function OrderTable({
  title,
  rows,
  state,
  empty,
  closingSymbol,
  onClose,
}: {
  title: string;
  rows: TradeRow[];
  state: SmaState | null;
  empty: string;
  closingSymbol: string | null;
  onClose: (trade: TradeRow) => void;
}) {
  return (
    <div className="border-t border-white/5">
      <h3 className="px-4 pt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        {title}
        <span className="ml-2 font-normal text-slate-500">{rows.length}</span>
      </h3>
      <div className="max-w-full overflow-x-auto">
        <table className="w-full min-w-[1040px] text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500">
            <tr className="border-y border-white/5">
              {COLUMNS.map((heading) => (
                <th key={heading} className="px-3 py-2 font-medium">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-3 py-6 text-center text-slate-500">
                  {empty}
                </td>
              </tr>
            )}
            {rows.map((trade) => (
              <OrderRow
                key={trade.id}
                trade={trade}
                state={state}
                closing={closingSymbol === trade.symbol.toUpperCase()}
                onClose={onClose}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
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
  const market = marketPrice(t, state);
  const live = markPnl(t, market);
  const pnl = t.exit_price == null ? live?.pnl ?? null : t.gross_pnl;
  return (
    <tr className="border-b border-white/5 text-slate-200">
      <td className="px-3 py-2 font-mono text-slate-500">{t.id}</td>
      <td className="px-3 py-2">
        <div className="font-medium text-slate-100">{t.symbol}</div>
        <div className={clsx("font-mono", pnl == null ? "text-slate-500" : pnl >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
          {pnl == null ? "P&L —" : inr(pnl)}
        </div>
      </td>
      <td className="px-3 py-2">
        <span
          className={clsx(
            "rounded-full px-2 py-0.5 text-[10px] font-semibold",
            t.direction === "LONG" ? "bg-[#10B981]/15 text-[#10B981]" : "bg-[#F43F5E]/15 text-[#F43F5E]"
          )}
        >
          {t.direction}
        </span>
      </td>
      <td className="px-3 py-2 font-mono">
        {px(t.entry_price)}
        <div className="text-slate-500">{t.entry_time ? `${istDateTime(t.entry_time)} IST` : "—"}</div>
      </td>
      <td className="px-3 py-2 font-mono">
        {market == null ? "—" : px(market)}
        <div className="text-slate-500">{t.exit_price == null ? "live" : "exit"}</div>
      </td>
      <td className={clsx("px-3 py-2 font-mono", pnl == null ? "text-slate-500" : pnl >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
        {pnl == null ? "—" : inr(pnl)}
        {live && (
          <div className="text-slate-500">
            {live.points >= 0 ? "+" : ""}
            {live.points.toFixed(2)} pts
          </div>
        )}
      </td>
      <td className="px-3 py-2 font-mono">{px(t.atr_at_entry)}</td>
      <td className="px-3 py-2 font-mono">{px(t.sl_trigger_price)}</td>
      <td className="px-3 py-2 font-mono">
        {t.exit_time ? `${istDateTime(t.exit_time)} IST` : "—"}
        <div className="text-slate-400">{t.exit_price == null ? "open" : px(t.exit_price)}</div>
      </td>
      <td className="px-3 py-2">
        {t.exit_reason ? (
          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300">
            {REASON[t.exit_reason] ?? t.exit_reason}
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-2 font-mono text-[#F59E0B]">
        {t.brokerage_and_taxes == null ? "—" : inr(t.brokerage_and_taxes)}
      </td>
      <td className={clsx("px-3 py-2 font-mono", (t.net_pnl ?? 0) >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
        {t.net_pnl == null ? "—" : inr(t.net_pnl)}
      </td>
      <td className="px-3 py-2">
        {t.exit_price == null ? (
          <button
            type="button"
            disabled={closing}
            onClick={() => onClose(t)}
            className="rounded-md border border-[#F43F5E]/50 bg-[#F43F5E]/15 px-2 py-1 text-[11px] font-semibold text-[#fda4af] hover:bg-[#F43F5E]/25 disabled:opacity-50"
          >
            {closing ? "Closing…" : "Close"}
          </button>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
    </tr>
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

