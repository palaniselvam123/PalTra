"use client";

import { inr, px, type SmaState } from "@/lib/smaApi";
import clsx from "clsx";

export function LivePositionCard({ state, pending }: { state: SmaState | null; pending?: boolean }) {
  const pos = state?.position;
  const direction = pos?.direction;
  const tone = direction === "LONG" ? "text-[#10B981]" : direction === "SHORT" ? "text-[#F43F5E]" : "text-slate-400";
  const mult = state?.atr_multiplier ?? 1.5;
  // The open position decides. With no position, the setting for the next entry does.
  const stopOff = pos ? pos.stop_active === false : state?.stop_enabled === false;

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="text-[11px] uppercase tracking-[0.14em] text-slate-400">
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
      <div className={clsx("mt-1 font-mono text-[17px] font-semibold leading-snug sm:text-lg", tone)}>
        {state == null
          ? pending
            ? "Position did not load"
            : "Loading position…"
          : pos
            ? `${pos.direction} ${pos.qty.toLocaleString("en-IN")} QTY`
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
        <Row label={`${mult}× ATR stop`} value={stopOff ? "OFF" : px(state?.active_sl_trigger)} />
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
        <Meter label="Unrealized gross" value={state?.unrealized_gross_pnl ?? 0} />
        <Meter label="Unrealized net" value={state?.unrealized_net_pnl ?? 0} hint={`est. charges ${inr(state?.estimated_charges ?? 0)}`} />
      </div>
      {state?.last_signal && <p className="mt-3 text-xs text-slate-400">{state.last_signal}</p>}
      {(state?.books ?? []).length > 0 && (
        <div className="mt-3 space-y-1 border-t border-white/5 pt-3">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Armed stocks</div>
          {(state?.books ?? []).map((book) => (
            <div key={book.symbol} className="flex items-center justify-between gap-2 font-mono text-xs">
              <span className="text-slate-300">{book.symbol}</span>
              <span
                className={
                  book.direction === "LONG"
                    ? "text-[#10B981]"
                    : book.direction === "SHORT"
                      ? "text-[#F43F5E]"
                      : "text-slate-400"
                }
              >
                {book.direction === "FLAT"
                  ? "FLAT"
                  : `${book.direction} ${book.qty}${book.stop_active === false ? " · NO STOP" : ""}`}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="font-mono text-slate-100">{value}</dd>
    </div>
  );
}

function Meter({ label, value, hint }: { label: string; value: number; hint?: string }) {
  const up = value >= 0;
  return (
    <div className="rounded-lg bg-black/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={clsx("font-mono text-[17px] font-semibold", up ? "text-[#10B981]" : "text-[#F43F5E]")}>{inr(value)}</div>
      {hint && <div className="text-[11px] text-slate-400">{hint}</div>}
    </div>
  );
}
