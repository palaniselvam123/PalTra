"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Tick } from "@/hooks/useTradingState";
import type { FeedStatus } from "@/lib/api";

type Props = {
  symbols: string[];
  ticks: Record<string, Tick>;
  feed: FeedStatus | null;
  killSwitchActive: boolean;
  onOrderPlaced: () => void;
};

export function PlaceOrderForm({ symbols, ticks, feed, killSwitchActive, onOrderPlaced }: Props) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [entryPrice, setEntryPrice] = useState<number | "">("");
  const [stopLoss, setStopLoss] = useState<number | "">("");
  const [target, setTarget] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!symbol && symbols.length > 0) setSymbol(symbols[0]);
  }, [symbols, symbol]);

  useEffect(() => {
    const ltp = ticks[symbol]?.ltp;
    if (ltp) setEntryPrice(Number(ltp.toFixed(2)));
  }, [symbol, ticks]);

  const submit = async () => {
    if (!symbol || entryPrice === "" || stopLoss === "" || target === "") {
      setFeedback({ ok: false, message: "Fill in symbol, entry, stop-loss, and target first." });
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      const res: any = await api.placeOrder({
        symbol,
        side,
        entry_price: Number(entryPrice),
        stop_loss: Number(stopLoss),
        target: Number(target),
      });
      setFeedback({
        ok: true,
        message: `Filled ${res.fill.quantity} ${symbol} @ ₹${res.fill.filled_price} (charges ~₹${res.fill.charges})`,
      });
      onOrderPlaced();
    } catch (e: any) {
      setFeedback({ ok: false, message: e.message ?? "Order rejected" });
    } finally {
      setSubmitting(false);
    }
  };

  // On live data outside market hours the backend rejects entries (frozen
  // prices); disable here too so the reason is visible before clicking.
  const marketBlocked = feed?.source === "live" && (!feed.market_open || feed.stale);
  const disabled = killSwitchActive || marketBlocked;

  return (
    <div className="rounded-card border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">Place Order (Paper)</span>
        <div className="flex rounded-md border border-border overflow-hidden text-xs">
          <button
            onClick={() => setSide("BUY")}
            className={clsx("px-3 py-1", side === "BUY" ? "bg-profit/20 text-profit" : "text-slate-400")}
          >
            BUY
          </button>
          <button
            onClick={() => setSide("SELL")}
            className={clsx("px-3 py-1", side === "SELL" ? "bg-loss/20 text-loss" : "text-slate-400")}
          >
            SELL
          </button>
        </div>
      </div>

      {marketBlocked && (
        <div className="text-xs text-amber-400">
          {feed?.stale
            ? "Live feed is stale — entries blocked until prices update."
            : `Market is ${feed?.session.replace("_", "-").toLowerCase()} on live data — entries open at 09:15 IST.`}
        </div>
      )}
      {killSwitchActive && <div className="text-xs text-loss">Kill switch active — reset it to place orders.</div>}

      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-400 flex flex-col gap-1 col-span-2">
          Symbol
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200"
          >
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s} {ticks[s] ? `— ${ticks[s].ltp.toFixed(2)}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Entry Price
          <input
            type="number"
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
          />
        </label>
        <div />
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Stop Loss
          <input
            type="number"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
          />
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Target
          <input
            type="number"
            value={target}
            onChange={(e) => setTarget(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
          />
        </label>
      </div>

      <p className="text-[11px] text-slate-500">
        Quantity is computed server-side by the 1% risk rule from your entry/stop-loss distance — you don&apos;t set it directly.
      </p>

      <button
        onClick={submit}
        disabled={disabled || submitting}
        className={clsx(
          "w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-xs font-semibold transition",
          disabled ? "bg-slate-700 text-slate-500 cursor-not-allowed" : "bg-bot/20 text-bot hover:bg-bot/30"
        )}
      >
        {submitting && <Loader2 size={13} className="animate-spin" />}
        Submit {side} Order
      </button>

      {feedback && (
        <div className={clsx("text-xs", feedback.ok ? "text-profit" : "text-loss")}>{feedback.message}</div>
      )}
    </div>
  );
}
