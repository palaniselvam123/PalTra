"use client";

import { useEffect, useRef, useState } from "react";
import { Info, Pause, Play, Siren, Zap } from "lucide-react";
import clsx from "clsx";
import type { SmaState } from "@/lib/smaApi";
import { ConfirmDialog } from "./ConfirmDialog";
import { SideBadge } from "./ui";

type Props = {
  state: SmaState | null;
  live: boolean;
  running: boolean;
  busy: boolean;
  /** The chart stock, which Force order acts on. */
  symbol: string;
  symbolArmed: boolean;
  onToggleBot: () => void;
  onForce: () => void;
  onPanic: () => void;
};

/** Start/Pause, Force order and Panic in one group, with named confirmations. */
export function ControlBar({ state, live, running, busy, symbol, symbolArmed, onToggleBot, onForce, onPanic }: Props) {
  const [ask, setAsk] = useState<"force" | "panic" | null>(null);
  const open = (state?.books ?? []).filter((b) => b.direction === "LONG" || b.direction === "SHORT");
  const forceBlocked = !symbol || !symbolArmed;
  const sideNow =
    state && state.symbol?.toUpperCase() === symbol && state.sma9 != null && state.sma21 != null && state.sma9 !== state.sma21
      ? state.sma9 > state.sma21
        ? "BUY"
        : "SELL"
      : null;
  const money = live ? "real Groww" : "practice";

  return (
    <section aria-label="Bot controls" className="rounded-xl border border-white/10 bg-[#151921] p-2">
      <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
        <button
          type="button"
          disabled={busy || !state}
          onClick={onToggleBot}
          className={clsx(
            "flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold disabled:opacity-50",
            running ? "bg-white/10 text-slate-100 hover:bg-white/15" : "bg-emerald-500 text-[#04140d] hover:bg-emerald-400"
          )}
        >
          {running ? <Pause size={16} aria-hidden /> : <Play size={16} aria-hidden />}
          {running ? "Pause bot" : "Start bot"}
        </button>
        <button
          type="button"
          disabled={busy || forceBlocked}
          onClick={() => setAsk("force")}
          title={forceBlocked ? `${symbol || "The chart stock"} is not armed. Switch it on in the stock list first.` : undefined}
          className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-amber-400 px-4 text-sm font-semibold text-[#1a1203] hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Zap size={16} aria-hidden />
          Force order
        </button>
        <HowItWorks />
        <button
          type="button"
          disabled={busy}
          onClick={() => setAsk("panic")}
          className="col-span-2 flex min-h-11 items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 text-sm font-bold tracking-wide text-white shadow-[0_0_20px_rgba(225,29,72,0.35)] hover:bg-rose-500 sm:ml-auto"
        >
          <Siren size={16} aria-hidden />
          Panic square-off
        </button>
      </div>

      <ConfirmDialog
        open={ask === "force"}
        title={`Force a ${money} order on ${symbol}?`}
        confirmLabel={`Force ${symbol}`}
        requireTyping={live}
        danger={live}
        busy={busy}
        onCancel={() => setAsk(null)}
        onConfirm={() => {
          setAsk(null);
          onForce();
        }}
      >
        <p>
          Stock affected: <span className="font-semibold text-amber-300">{symbol}</span>
          {sideNow ? (
            <>
              {" "}
              — SMA 9 is {sideNow === "BUY" ? "above" : "below"} SMA 21 now, so this would be a{" "}
              <span className="font-semibold">{sideNow}</span>.
            </>
          ) : null}
        </p>
        <p>It does not wait for a cross. Checked VWAP, volume, density and RSI filters still apply. The bot will start.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={ask === "panic"}
        title={open.length ? `Close ${open.length} position${open.length > 1 ? "s" : ""} and halt the bot?` : "Halt the bot?"}
        confirmLabel="Square off and halt"
        requireTyping={live}
        busy={busy}
        onCancel={() => setAsk(null)}
        onConfirm={() => {
          setAsk(null);
          onPanic();
        }}
      >
        {open.length ? (
          <>
            <p>These {money} positions will be closed at the market now:</p>
            <ul className="divide-y divide-white/10 rounded-md bg-black/30 ring-1 ring-inset ring-white/10">
              {open.map((b) => (
                <li key={b.symbol} className="flex items-center justify-between gap-2 px-3 py-2">
                  <span className="font-semibold text-amber-300">{b.symbol}</span>
                  <span className="flex items-center gap-2 font-mono text-xs text-slate-300">
                    <SideBadge side={b.direction} />
                    {b.qty}
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>No stock is open right now. Nothing will be sold.</p>
        )}
        <p>The bot stops for the rest of the day. You can start it again after a manual panic.</p>
      </ConfirmDialog>
    </section>
  );
}

function HowItWorks() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative col-span-2 flex justify-center sm:col-span-1">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="how-it-works"
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-sm text-sky-300 hover:bg-white/5"
      >
        <Info size={16} aria-hidden />
        How it works
      </button>
      {open && (
        <div
          id="how-it-works"
          role="dialog"
          aria-label="How the bot works"
          className="absolute left-1/2 top-full z-40 mt-2 w-[min(22rem,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-white/15 bg-[#0B0E14] p-4 text-sm leading-relaxed text-slate-300 shadow-2xl sm:left-0 sm:translate-x-0"
        >
          <ul className="list-disc space-y-1.5 pl-4">
            <li>Switch a stock to <b className="text-emerald-300">Armed for trading</b> so the bot may order it, up to 24 at once. "Show on chart" only changes the chart.</li>
            <li>Buy and sell both wait for the next SMA 9 / SMA 21 cross on a closed 1-minute candle.</li>
            <li>Telegram warns about 3 minutes before a likely cross; the order still waits for the cross.</li>
            <li><b>Force order</b> orders the chart stock now from the current SMA side.</li>
            <li>Checked VWAP, volume, density and RSI filters apply to both.</li>
          </ul>
        </div>
      )}
    </div>
  );
}
