"use client";

import { inr, px, type SmaState } from "@/lib/smaApi";
import clsx from "clsx";

export function LivePositionCard({ state, pending }: { state: SmaState | null; pending?: boolean }) {
  const pos = state?.position;
  const direction = pos?.direction;
  const tone = direction === "LONG" ? "text-sky-300" : direction === "SHORT" ? "text-violet-300" : "text-slate-400";
  const mult = state?.atr_multiplier ?? 1.5;
  // The open position decides. With no position, the setting for the next entry does.
  const stopOff = pos ? pos.stop_active === false : state?.stop_enabled === false;

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-slate-400">
        Live position{state?.symbol ? ` · ${state.symbol}` : ""}
      </div>
      {state != null && stopOff && (
        <div
          role="alert"
          className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs font-semibold text-amber-300"
        >
          {pos
            ? "STOP-LOSS OFF — this position has no stop order. Only an opposite cross, square-off, or Kill closes it."
            : "STOP-LOSS OFF — new entries will be sent with no stop order."}
        </div>
      )}
      <div className={clsx("mt-1 font-mono text-base font-semibold leading-snug sm:text-xl", tone)}>
        {state == null
          ? pending
            ? "Position did not load"
            : "Loading position…"
          : pos
            ? `${pos.direction} ${pos.qty.toLocaleString("en-IN")} QTY${pos.flipped ? " · FLIPPED" : ""}`
            : "FLAT — waiting for the next SMA cross"}
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3 text-sm">
        <Row label="Entry" value={px(pos?.entry_price)} />
        <Row label="LTP" value={px(state?.ltp)} />
        <Row label="SMA 9" value={px(state?.sma9)} />
        <Row label="SMA 21" value={px(state?.sma21)} />
        <Row
          label="SMA gap %"
          value={state?.sma_gap_pct == null ? "—" : `${state.sma_gap_pct >= 0 ? "+" : ""}${state.sma_gap_pct.toFixed(3)}%`}
        />
        <Row label="VWAP" value={px(state?.vwap)} />
        <Row
          label="Volume ratio"
          value={state?.volume_ratio == null ? "—" : `${state.volume_ratio.toFixed(2)}×`}
        />
        <Row label="RSI 14" value={state?.rsi14 == null ? "—" : state.rsi14.toFixed(1)} />
        <Row label="ATR 14" value={px(state?.atr14)} />
        <Row
          label={
            pos?.trailing
              ? "Moving stop (SMA gap)"
              : pos?.tsl_step
                ? `Trailing stop (every ₹${pos.tsl_step})`
                : `${mult}× ATR stop`
          }
          value={stopOff ? "OFF" : px(state?.active_sl_trigger)}
        />
        {pos?.trailing ? <Row label="Target (SMA gap)" value={px(pos.target)} /> : null}
        {pos?.tsl_step ? <Row label="Target" value={pos.target == null ? "none" : px(pos.target)} /> : null}
        <Row
          label="Distance to SL"
          value={
            stopOff
              ? "No stop"
              : state?.sl_room == null
                ? "—"
                : `${state.sl_room >= 0 ? "" : "−"}₹${Math.abs(state.sl_room).toFixed(2)} (${state.sl_room_pct?.toFixed(2)}%)`
          }
        />
        <Row label="ADX 14" value={state?.adx14 == null ? "—" : state.adx14.toFixed(1)} />
      </dl>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Meter label="Unrealized P&L" value={state?.unrealized_gross_pnl ?? 0} hint="before charges" />
        <Meter label="Est. charges" value={-(state?.estimated_charges ?? 0)} hint="not taken off the P&L" />
      </div>
      {state?.last_signal && <p className="mt-3 text-xs text-slate-400">{state.last_signal}</p>}
      {(state?.books ?? []).length > 0 && (
        <div className="mt-3 space-y-1 border-t border-white/5 pt-3">
          <div className="flex items-baseline justify-between gap-2 text-xs uppercase tracking-wider text-slate-400">
            <span>Armed stocks</span>
            <span>Today P&amp;L</span>
          </div>
          {(state?.books ?? []).map((book) => (
            <div key={book.symbol} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 font-mono text-xs">
              <span className="min-w-0 truncate text-slate-300">{book.symbol}</span>
              <span
                className={
                  book.direction === "LONG"
                    ? "text-sky-300"
                    : book.direction === "SHORT"
                      ? "text-violet-300"
                      : "text-slate-400"
                }
              >
                {book.direction === "FLAT"
                  ? "FLAT"
                  : `${book.direction} ${book.qty}${book.stop_active === false ? " · NO STOP" : ""}`}
              </span>
              <span
                className={clsx(
                  "min-w-[5.5rem] text-right",
                  dayPnl(book) > 0 ? "text-[#10B981]" : dayPnl(book) < 0 ? "text-[#F43F5E]" : "text-slate-500"
                )}
                title={
                  openPnl(book) != null
                    ? `open ${inr(openPnl(book))} · closed ${inr(closedPnl(book))} (${book.closed_trades ?? 0}), before charges`
                    : `${book.closed_trades ?? 0} closed trade(s), before charges`
                }
              >
                {inr(dayPnl(book))}
              </span>
            </div>
          ))}
          <AllStocksTotal state={state} />
        </div>
      )}
    </section>
  );
}

type Book = NonNullable<SmaState["books"]>[number];
// Before charges (the screens' basis); the older net fields stand in if the server has no gross yet.
const openPnl = (b: Book) => (b.open_gross !== undefined ? b.open_gross : b.open_net) ?? null;
const closedPnl = (b: Book) => b.closed_gross ?? b.closed_net ?? 0;
const dayPnl = (b: Book) => b.day_gross ?? b.day_net ?? 0;

/** Every armed or held stock together: today's closed P&L plus all open P&L, before charges. */
function AllStocksTotal({ state }: { state: SmaState | null }) {
  const books = state?.books ?? [];
  if (books.length < 2) return null;
  const open = books.reduce((sum, b) => sum + (openPnl(b) ?? 0), 0);
  const closed = state?.kpis?.actual_gross ?? books.reduce((sum, b) => sum + closedPnl(b), 0);
  const total = closed + open;
  return (
    <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-3 border-t border-white/5 pt-1 font-mono text-xs">
      <span className="text-slate-400">
        All stocks · open {inr(open)} · closed {inr(closed)}
      </span>
      <span className={clsx("min-w-[5.5rem] text-right font-semibold", total >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
        {inr(total)}
      </span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="font-mono text-slate-100">{value}</dd>
    </div>
  );
}

function Meter({ label, value, hint }: { label: string; value: number; hint?: string }) {
  const up = value >= 0;
  return (
    <div className="rounded-lg bg-black/20 px-3 py-2">
      <div className="text-xs uppercase tracking-wider text-slate-400">{label}</div>
      <div className={clsx("font-mono text-base font-semibold", up ? "text-[#10B981]" : "text-[#F43F5E]")}>{inr(value)}</div>
      {hint && <div className="text-xs text-slate-400">{hint}</div>}
    </div>
  );
}
