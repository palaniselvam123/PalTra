"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import { api, type DeskAccount, type OrderPreview, type WatchRow } from "@/lib/api";
import { money, num, pct } from "@/lib/format";

/** A broker-style order ticket: you choose the quantity, and the cost of the
 *  order is shown before you commit to it. Unlike the strategy account — where
 *  size comes from the 1% rule — the only limit here is the wallet's margin. */
export function OrderTicket({
  row,
  account,
  onClose,
  onFilled,
}: {
  row: WatchRow;
  account: DeskAccount | null;
  onClose: () => void;
  onFilled: () => void;
}) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [quantity, setQuantity] = useState(1);
  const [useBracket, setUseBracket] = useState(false);
  const [stopLoss, setStopLoss] = useState("");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (quantity < 1) return;
    let cancelled = false;
    api
      .deskPreview(row.symbol, side, quantity)
      .then((p) => !cancelled && setPreview(p))
      .catch(() => !cancelled && setPreview(null));
    return () => {
      cancelled = true;
    };
  }, [row.symbol, side, quantity]);

  const affordable = account ? Math.floor(account.margin_available / (row.ltp || 1)) : 0;

  const submit = async () => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const res = await api.deskPlace({
        symbol: row.symbol,
        side,
        quantity,
        stop_loss: useBracket && stopLoss ? Number(stopLoss) : null,
        target: useBracket && target ? Number(target) : null,
      });
      setDone(`${side} ${res.quantity} ${res.symbol} filled at ₹${res.filled_price}`);
      onFilled();
    } catch (e: any) {
      setError(e.message ?? "Order rejected");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <div>
          <div className="text-sm font-medium text-slate-100">{row.symbol}</div>
          <div className="font-mono text-xs text-slate-400">
            ₹{num(row.ltp)}{" "}
            <span className="text-slate-600">
              bid {num(row.bid)} / ask {num(row.ask)}
            </span>
          </div>
        </div>
        <button onClick={onClose} className="ml-auto text-slate-500 hover:text-slate-300" aria-label="Close ticket">
          <X size={16} />
        </button>
      </div>

      <div className="p-4 space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {(["BUY", "SELL"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSide(s)}
              className={clsx(
                "py-2 rounded-md text-sm font-semibold transition border",
                side === s
                  ? s === "BUY"
                    ? "bg-profit/20 text-profit border-profit/40"
                    : "bg-loss/20 text-loss border-loss/40"
                  : "border-border text-slate-400 hover:text-slate-200"
              )}
            >
              {s}
            </button>
          ))}
        </div>

        <label className="text-xs text-slate-400 flex flex-col gap-1">
          <span className="flex items-baseline justify-between">
            Quantity
            <button
              onClick={() => setQuantity(Math.max(1, affordable))}
              className="text-[10px] text-bot hover:underline"
              title="Largest quantity the desk's free margin allows"
            >
              max {affordable.toLocaleString("en-IN")}
            </button>
          </span>
          <input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value || "1", 10)))}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
          />
        </label>

        <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={useBracket}
            onChange={(e) => setUseBracket(e.target.checked)}
            className="accent-cyan-500"
          />
          Add stop-loss / target (enforced on every tick)
        </label>

        {useBracket && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-400 flex flex-col gap-1">
              Stop Loss
              <input
                type="number"
                step="0.05"
                value={stopLoss}
                onChange={(e) => setStopLoss(e.target.value)}
                placeholder={side === "BUY" ? "below entry" : "above entry"}
                className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
              />
            </label>
            <label className="text-xs text-slate-400 flex flex-col gap-1">
              Target
              <input
                type="number"
                step="0.05"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder={side === "BUY" ? "above entry" : "below entry"}
                className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 font-mono"
              />
            </label>
          </div>
        )}

        {preview && (
          <div className="rounded-md border border-border bg-base px-3 py-2 space-y-1 text-[11px]">
            <Line label="Expected fill" value={`₹${num(preview.expected_fill)}`} hint="after slippage" />
            <Line label="Order value" value={money(preview.order_value)} />
            <Line label="Charges (this leg)" value={money(preview.estimated_charges)} />
            <Line label="Round-trip charges" value={money(preview.estimated_round_trip_charges)} />
            <Line
              label="Round-trip slippage"
              value={money(preview.slippage_round_trip)}
              hint="hidden inside your fill prices"
            />
            {/* Charges alone understate the hurdle badly — slippage is baked
                into the fill prices and never appears as a line item in P&L. */}
            <div className="border-t border-border/70 pt-1 mt-1">
              <Line
                label="Break-even move"
                value={`₹${num(preview.breakeven_move_per_share)}/share`}
                hint={`${money(preview.total_round_trip_cost)} total · ${pct(preview.breakeven_move_pct, 3)}`}
                tone="text-amber-400"
              />
            </div>
            {account && (
              <Line
                label="Margin left after"
                value={money(account.margin_available - preview.order_value)}
                tone={account.margin_available - preview.order_value < 0 ? "text-loss" : undefined}
              />
            )}
          </div>
        )}

        {error && <div className="text-xs text-loss">{error}</div>}
        {done && <div className="text-xs text-profit">{done}</div>}

        <button
          onClick={submit}
          disabled={busy || quantity < 1}
          className={clsx(
            "w-full py-2.5 rounded-md text-sm font-semibold transition flex items-center justify-center gap-2 disabled:opacity-40",
            side === "BUY" ? "bg-profit/20 text-profit hover:bg-profit/30" : "bg-loss/20 text-loss hover:bg-loss/30"
          )}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          {side} {quantity.toLocaleString("en-IN")} {row.symbol}
        </button>

        <p className="text-[10px] text-slate-600">
          Virtual money. This desk has its own wallet and its own reports — nothing here touches the strategy
          account or your broker.
        </p>
      </div>
    </div>
  );
}

function Line({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-slate-500">
        {label}
        {hint && <span className="text-slate-600"> · {hint}</span>}
      </span>
      <span className={clsx("font-mono", tone ?? "text-slate-200")}>{value}</span>
    </div>
  );
}
