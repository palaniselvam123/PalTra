"use client";

import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type OptionTrade } from "@/lib/api";
import { money, num } from "@/lib/format";
import type { PickedLeg } from "./OptionChainTable";

/** Buy or sell a picked option in the practice book. Never a Groww order. */
export function OptionTicket({
  underlying,
  expiry,
  lotSize,
  picked,
  feed,
  onClose,
  onFilled,
}: {
  underlying: string;
  expiry: string;
  lotSize: number | null;
  picked: PickedLeg;
  feed: "groww" | "simulated";
  onClose: () => void;
  onFilled: (row: OptionTrade) => void;
}) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [lots, setLots] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setError(null), [picked.strike, picked.type, side, lots]);

  const qty = lotSize ? lots * lotSize : null;
  const price = picked.leg.ltp ?? 0;
  const value = qty != null ? qty * price : null;

  const place = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await api.optionOrder({
        underlying,
        expiry,
        strike: picked.strike,
        option_type: picked.type,
        side,
        lots,
      });
      onFilled(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Practice order"
      className="rounded-card border border-accentSky/40 bg-surface p-4 shadow-lg shadow-black/20"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">
            {underlying} {picked.strike.toLocaleString("en-IN")} {picked.type}
          </h2>
          <p className="text-xs text-slate-400">
            Expiry {expiry} · LTP <span className="font-mono text-slate-200">{num(picked.leg.ltp)}</span>
            {picked.leg.iv != null ? ` · IV ${num(picked.leg.iv, 1)}%` : ""}
            {picked.leg.delta != null ? ` · Δ ${num(picked.leg.delta)}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close order ticket"
          className="flex min-h-9 min-w-9 items-center justify-center rounded-md text-slate-400 ring-1 ring-inset ring-white/10 hover:bg-white/5"
        >
          <X size={14} aria-hidden />
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5" role="group" aria-label="Buy or sell">
        {(["BUY", "SELL"] as const).map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={side === s}
            onClick={() => setSide(s)}
            className={clsx(
              "min-h-10 rounded-md text-sm font-semibold ring-1 ring-inset",
              side === s
                ? s === "BUY"
                  ? "bg-profit text-white ring-profit"
                  : "bg-loss text-white ring-loss"
                : "text-slate-300 ring-white/15 hover:bg-white/5"
            )}
          >
            {s === "BUY" ? "Buy" : "Sell"}
          </button>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <label className="flex flex-col gap-1 text-slate-400">
          Lots
          <input
            type="number"
            min={1}
            max={100}
            value={lots}
            onChange={(e) => setLots(Math.max(1, Math.min(100, Math.floor(Number(e.target.value) || 1))))}
            className="min-h-10 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          />
        </label>
        <div className="flex flex-col gap-1 text-slate-400">
          Quantity
          <span className="flex min-h-10 items-center font-mono text-sm text-slate-100">
            {qty != null ? `${qty.toLocaleString("en-IN")} (lot ${lotSize})` : "lot size loads with the order"}
          </span>
        </div>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        Premium {side === "BUY" ? "paid" : "received"}:{" "}
        <span className="font-mono text-slate-100">{value != null ? money(value) : "—"}</span>
        {side === "SELL" ? " · selling options needs real margin on Groww; the practice book does not check it." : ""}
      </p>

      {error && (
        <p role="alert" className="mt-2 rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={place}
        disabled={busy || !price}
        className={clsx(
          "mt-3 flex min-h-10 w-full items-center justify-center gap-2 rounded-md text-sm font-semibold text-white disabled:opacity-50",
          side === "BUY" ? "bg-profit hover:bg-profit/85" : "bg-loss hover:bg-loss/85"
        )}
      >
        {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
        Place practice {side === "BUY" ? "buy" : "sell"}
      </button>
      <p className="mt-2 text-[11px] text-slate-500">
        Practice only: filled at the last price on {feed === "groww" ? "Groww's chain" : "the simulated chain"}. Nothing
        is sent to Groww.
      </p>
    </section>
  );
}
