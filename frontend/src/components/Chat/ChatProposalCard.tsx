"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import type { ChatProposal } from "@/lib/api";
import { smaApi, type BotSummary, type SmaConfig } from "@/lib/smaApi";

const LABELS: Record<string, string> = {
  use_stop: "Stop on",
  stop_type: "Stop type",
  atr_multiplier: "ATR multiple",
  tsl_mode: "Trailing units",
  tsl_sl_points: "Stop distance (₹)",
  tsl_trail_points: "Trail step (₹)",
  tsl_target_points: "Target (₹, 0 = none)",
  tsl_sl_pct: "Stop distance (%)",
  tsl_trail_pct: "Trail step (%)",
  tsl_target_pct: "Target (%, 0 = none)",
  gap_sl_mult: "Gap stop multiple",
  gap_tp_mult: "Gap target multiple",
  gap_min_pct: "Gap floor (%)",
};

function show(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  return String(value);
}

type Phase = "ready" | "confirm-live" | "saving" | "done" | "failed";

/** One proposed change from the assistant. Saves only when the person taps Apply. */
export function ChatProposalCard({ proposal }: { proposal: ChatProposal }) {
  const [bot, setBot] = useState<BotSummary | null>(null);
  const [current, setCurrent] = useState<Partial<SmaConfig> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("ready");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    smaApi
      .bots()
      .then((rows) => live && setBot(rows.find((r) => r.bot === proposal.bot) ?? null))
      .catch((e: Error) => live && setLoadError(e.message));
    if (proposal.kind === "stop") {
      const read = proposal.symbol ? smaApi.stockConfigOn(proposal.bot, proposal.symbol) : smaApi.configOn(proposal.bot);
      read.then((cfg) => live && setCurrent(cfg)).catch((e: Error) => live && setLoadError(e.message));
    }
    return () => {
      live = false;
    };
  }, [proposal]);

  const isLive = bot?.mode === "LIVE";
  const botName = bot?.name || `Bot ${proposal.bot}`;
  const stopOff = proposal.kind === "stop" && proposal.changes.use_stop === false;
  const armedNow = new Set(bot?.armed ?? []);

  const apply = async () => {
    if (isLive && phase !== "confirm-live") {
      setPhase("confirm-live");
      return;
    }
    setPhase("saving");
    setNote(null);
    try {
      if (proposal.kind === "trade_list") {
        const failed: string[] = [];
        for (const [symbol, armed] of [
          ...proposal.arm.map((s) => [s, true] as const),
          ...proposal.unarm.map((s) => [s, false] as const),
        ]) {
          try {
            await smaApi.setTradeSymbolOn(proposal.bot, symbol, armed, "chat");
          } catch (e: unknown) {
            failed.push(`${symbol}: ${e instanceof Error ? e.message : "refused"}`);
          }
        }
        if (failed.length) throw new Error(failed.join("; "));
      } else if (proposal.symbol) {
        await smaApi.saveStockConfigOn(proposal.bot, proposal.symbol, proposal.changes as Partial<SmaConfig>);
      } else {
        await smaApi.saveConfigOn(proposal.bot, proposal.changes as Partial<SmaConfig>);
      }
      setPhase("done");
      setNote("Saved.");
    } catch (e: unknown) {
      setPhase("failed");
      setNote(e instanceof Error ? e.message : "Not saved");
    }
  };

  return (
    <div
      className={clsx(
        "rounded-lg border px-3 py-2.5 text-xs space-y-2",
        isLive ? "border-loss/50 bg-loss/5" : "border-bot/40 bg-bot/5"
      )}
    >
      <div className="flex items-center gap-2">
        <span className="font-semibold text-slate-200">
          {proposal.kind === "trade_list" ? "Trade list" : "Stop-loss"} · {botName}
          {proposal.kind === "stop" && ` · ${proposal.symbol ?? "all stocks"}`}
        </span>
        <span
          className={clsx(
            "ml-auto rounded px-1.5 py-0.5 text-[10px] font-bold",
            isLive ? "bg-loss/20 text-loss" : "bg-white/10 text-slate-300"
          )}
        >
          {bot ? bot.mode : "…"}
        </span>
      </div>

      {proposal.kind === "trade_list" ? (
        <ul className="space-y-0.5 text-slate-300">
          {proposal.arm.map((s) => (
            <li key={`a-${s}`}>
              <span className="text-profit">Arm</span> {s}
              {armedNow.has(s) && <span className="text-slate-500"> (already armed)</span>}
            </li>
          ))}
          {proposal.unarm.map((s) => (
            <li key={`u-${s}`}>
              <span className="text-loss">Unarm</span> {s}
              {bot && !armedNow.has(s) && <span className="text-slate-500"> (not armed)</span>}
            </li>
          ))}
        </ul>
      ) : (
        <table className="w-full text-slate-300">
          <tbody>
            {Object.entries(proposal.changes).map(([key, value]) => {
              const before = current ? show(current[key as keyof SmaConfig]) : "…";
              const same = before === show(value);
              return (
                <tr key={key} className={clsx(same && "opacity-50")}>
                  <td className="py-0.5 pr-2 text-slate-400">{LABELS[key] ?? key}</td>
                  <td className="py-0.5 text-right font-mono">
                    {same ? (
                      <span className="text-slate-400">{show(value)} (no change)</span>
                    ) : (
                      <>
                        <span className="text-slate-500">{before}</span>
                        {" → "}
                        <span className="text-slate-100">{show(value)}</span>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {proposal.reason && <p className="text-slate-500">{proposal.reason}</p>}
      {stopOff && <p className="text-loss">This turns the stop-loss off. Trades will have no stop.</p>}
      {loadError && <p className="text-amber-300">Could not read the terminal: {loadError}</p>}

      {phase === "confirm-live" && (
        <p className="flex items-start gap-1.5 text-loss">
          <AlertTriangle size={13} className="mt-px shrink-0" />
          {botName} is LIVE and trades real money. Tap Confirm to change it.
        </p>
      )}

      {phase === "done" ? (
        <p className="flex items-center gap-1 text-profit">
          <Check size={13} /> {note}
        </p>
      ) : (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={apply}
            disabled={phase === "saving" || !bot}
            className={clsx(
              "inline-flex h-8 items-center gap-1 rounded-md px-3 font-semibold disabled:opacity-50",
              phase === "confirm-live" ? "bg-loss text-white" : "bg-bot text-slate-900"
            )}
          >
            {phase === "saving" && <Loader2 size={12} className="animate-spin" />}
            {phase === "confirm-live" ? "Confirm on LIVE" : "Apply"}
          </button>
          {phase === "confirm-live" && (
            <button type="button" onClick={() => setPhase("ready")} className="h-8 px-2 text-slate-400 hover:text-slate-200">
              Cancel
            </button>
          )}
          {phase === "failed" && <span className="text-loss">{note}</span>}
        </div>
      )}
    </div>
  );
}
