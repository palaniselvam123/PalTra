"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Radio } from "lucide-react";
import clsx from "clsx";
import { smaApi, px, type SmaConfig, type SmaState } from "@/lib/smaApi";

const QUICK = ["KIRLOSFER", "ANTELOPUS"];

type Props = {
  state: SmaState | null;
  config: SmaConfig | null;
  connected: boolean;
  onChanged: () => void;
};

export function Header({ state, config, connected, onChanged }: Props) {
  const [symbol, setSymbol] = useState(config?.symbol ?? "KIRLOSFER");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prevClose, setPrevClose] = useState<number | null>(null);

  useEffect(() => {
    if (config?.symbol) setSymbol(config.symbol);
  }, [config?.symbol]);

  // Day-change baseline: first LTP we see for this symbol in the session.
  useEffect(() => {
    setPrevClose(null);
  }, [state?.symbol]);
  useEffect(() => {
    if (state?.ltp && prevClose === null) setPrevClose(state.ltp);
  }, [state?.ltp, prevClose]);

  const ltp = state?.ltp ?? 0;
  const changePct = prevClose && prevClose > 0 ? ((ltp - prevClose) / prevClose) * 100 : 0;
  const live = (state?.mode ?? config?.trading_mode) === "LIVE";
  const running = state?.bot_status === "RUNNING";

  const applySymbol = async (next: string) => {
    const cleaned = next.trim().toUpperCase();
    if (!cleaned) return;
    setBusy(true);
    setError(null);
    try {
      await smaApi.saveConfig({ symbol: cleaned });
      setSymbol(cleaned);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not change symbol");
    } finally {
      setBusy(false);
    }
  };

  const switchMode = async (mode: "PAPER" | "LIVE", confirmLive = false) => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.setMode(mode, confirmLive);
      setConfirm(false);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Mode change refused");
    } finally {
      setBusy(false);
    }
  };

  const toggleBot = async () => {
    setBusy(true);
    setError(null);
    try {
      if (running) await smaApi.pause();
      else await smaApi.start();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Bot command failed");
    } finally {
      setBusy(false);
    }
  };

  const panic = async () => {
    setBusy(true);
    setError(null);
    try {
      await smaApi.kill();
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Kill switch failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-[#0B0E14]/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-3">
        <a href="/" className="text-[11px] uppercase tracking-[0.16em] text-slate-500 hover:text-slate-300">
          Desk
        </a>
        <div className="text-sm font-semibold tracking-tight text-slate-100">SMA × ATR Terminal</div>

        <div className="flex items-center gap-1 rounded-full border border-white/10 bg-[#151921] px-2 py-1">
          <span className="px-1 text-[10px] uppercase tracking-wider text-slate-500">NSE</span>
          {QUICK.map((s) => (
            <button
              key={s}
              disabled={busy}
              onClick={() => applySymbol(s)}
              className={clsx(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                symbol === s ? "bg-white/10 text-slate-100" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {s}
            </button>
          ))}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              applySymbol(symbol);
            }}
          >
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="w-28 bg-transparent px-2 text-xs uppercase text-slate-100 outline-none"
              aria-label="NSE symbol"
            />
          </form>
        </div>

        <div className="font-mono text-sm">
          <span className="text-slate-100">{px(ltp)}</span>
          <span className={clsx("ml-2 text-xs", changePct >= 0 ? "text-[#10B981]" : "text-[#F43F5E]")}>
            {changePct >= 0 ? "+" : ""}
            {changePct.toFixed(2)}%
          </span>
        </div>

        <button
          disabled={busy}
          onClick={() => (live ? switchMode("PAPER") : setConfirm(true))}
          className={clsx(
            "rounded-full px-3 py-1 text-xs font-semibold",
            live
              ? "animate-pulse bg-[#F43F5E]/20 text-[#F43F5E] ring-1 ring-[#F43F5E]/50"
              : "bg-[#F59E0B]/15 text-[#F59E0B] ring-1 ring-[#F59E0B]/40"
          )}
        >
          {live ? "LIVE REAL MONEY" : "PAPER SIMULATOR (SAFE)"}
        </button>

        <button
          disabled={busy}
          onClick={toggleBot}
          className={clsx(
            "rounded-md px-3 py-1.5 text-xs font-semibold",
            running ? "bg-white/10 text-slate-100" : "bg-[#10B981] text-[#04140d]"
          )}
        >
          {running ? "PAUSE BOT" : "START BOT"}
        </button>

        <span className="flex items-center gap-1.5 text-[11px] text-slate-400" title={state?.data_source}>
          <Radio size={12} className={connected ? "text-[#10B981]" : "text-[#F43F5E]"} />
          {connected ? state?.bot_status ?? "LIVE" : "OFFLINE"}
          {state?.data_source ? ` · ${state.data_source}` : ""}
        </span>

        <button
          disabled={busy}
          onClick={panic}
          className="ml-auto rounded-md bg-[#F43F5E] px-3 py-1.5 text-xs font-bold tracking-wide text-white shadow-[0_0_24px_rgba(244,63,94,0.35)]"
        >
          PANIC SQUARE-OFF ALL
        </button>
      </div>
      {(error || state?.halt_reason || state?.last_error) && (
        <div className="border-t border-[#F43F5E]/30 bg-[#F43F5E]/10 px-4 py-1.5 text-xs text-[#F43F5E]">
          {error || state?.halt_reason || state?.last_error}
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#F43F5E]/40 bg-[#151921] p-5">
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 text-[#F43F5E]" size={20} />
              <div>
                <h2 className="text-base font-semibold text-slate-100">Enable LIVE REAL MONEY?</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  Orders will be sent to Groww as NSE MIS limit orders with a 0.20% protection buffer,
                  plus an exchange stop-loss. This needs <span className="text-slate-200">GROWW_ACCESS_TOKEN</span> in
                  the backend environment. Paper mode stays the default until you confirm.
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5"
                onClick={() => setConfirm(false)}
              >
                Cancel
              </button>
              <button
                disabled={busy}
                className="rounded-md bg-[#F43F5E] px-3 py-1.5 text-sm font-semibold text-white"
                onClick={() => switchMode("LIVE", true)}
              >
                Confirm LIVE
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
