"use client";

import { useEffect, useRef } from "react";
import clsx from "clsx";
import { X } from "lucide-react";
import type { ArmTarget, BotSummary } from "@/lib/smaApi";

export type ArmDesk = {
  target: ArmTarget;
  name: string;
  mode: "PAPER" | "LIVE";
  armed: string[];
};

/** The SMA bots (main desk first) and the research desk, as places a stock can be armed. */
export function armDesks(bots: BotSummary[], researchArmed: string[] | null): ArmDesk[] {
  const out: ArmDesk[] = bots
    .slice()
    .sort((a, b) => a.bot - b.bot)
    .map((b) => ({
      target: b.bot as ArmTarget,
      name: b.name || `Bot ${b.bot}`,
      mode: b.mode === "LIVE" ? "LIVE" : "PAPER",
      armed: (b.armed ?? []).map((s) => s.toUpperCase()),
    }));
  if (researchArmed) out.push({ target: "research", name: "Research", mode: "PAPER", armed: researchArmed.map((s) => s.toUpperCase()) });
  return out;
}

/** Short names of the desks a stock is armed on, e.g. ["Bot 1", "Research"]. */
export function armedOn(desks: ArmDesk[], symbol: string): string[] {
  const name = symbol.toUpperCase();
  return desks.filter((d) => d.armed.includes(name)).map((d) => d.name);
}

/**
 * Asks which desk a stock should be armed on. A LIVE bot is marked in red and
 * still asks for confirmation before it is armed (the caller does that); a
 * LIVE bot is not offered when another LIVE bot already has the stock.
 */
export function ArmPicker({
  symbol,
  desks,
  onPick,
  onClose,
}: {
  symbol: string;
  desks: ArmDesk[];
  onPick: (desk: ArmDesk) => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    box.current?.querySelector<HTMLButtonElement>("button[data-pick]:not(:disabled)")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const name = symbol.toUpperCase();
  const liveOwner = desks.find((d) => d.mode === "LIVE" && d.armed.includes(name));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={`Arm ${name} on which bot`}
        className="w-full max-w-sm rounded-xl border border-border bg-surface p-4 shadow-xl"
      >
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">Arm {name} on which bot?</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          It goes on that bot&apos;s Trade list and orders on its next SMA cross, with that bot&apos;s settings.
        </p>
        {desks.length === 0 ? (
          <p className="text-xs text-amber-300">The SMA terminal is not answering, so no bot can be armed right now.</p>
        ) : (
          <ul className="space-y-1.5">
            {desks.map((d) => {
              const already = d.armed.includes(name);
              const blocked = !already && d.mode === "LIVE" && liveOwner != null && liveOwner.target !== d.target;
              return (
                <li key={String(d.target)}>
                  <button
                    type="button"
                    data-pick
                    disabled={already || blocked}
                    onClick={() => onPick(d)}
                    className={clsx(
                      "flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ring-1 ring-inset disabled:cursor-not-allowed disabled:opacity-55",
                      d.mode === "LIVE" ? "ring-loss/50 hover:bg-loss/10" : "ring-slate-500/30 hover:bg-slate-500/10"
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block font-semibold text-slate-100">{d.name}</span>
                      <span className="block text-[11px] text-slate-400">
                        {already
                          ? "already armed here"
                          : blocked
                            ? `${liveOwner?.name} trades it LIVE — one LIVE bot per stock`
                            : d.target === "research"
                              ? "practice money only"
                              : d.mode === "LIVE"
                                ? "real money — asks you to confirm"
                                : "practice money"}
                      </span>
                    </span>
                    <span
                      className={clsx(
                        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold",
                        d.mode === "LIVE" ? "bg-loss/20 text-loss" : "bg-profit/15 text-profit"
                      )}
                    >
                      {d.target === "research" ? "RESEARCH" : d.mode}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
