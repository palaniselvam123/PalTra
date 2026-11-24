"use client";

import { useEffect, useState } from "react";
import { smaApi, type BotSummary } from "@/lib/smaApi";
import { NewTag } from "@/components/ui/NewTag";

type Target = "1" | "2" | "3" | "4" | "5" | "research";
const TARGETS: Target[] = ["1", "2", "3", "4", "5", "research"];

function name(t: Target, bots: BotSummary[]): string {
  if (t === "research") return "Research";
  return bots.find((b) => b.bot === Number(t))?.name ?? `Bot ${t}`;
}

/**
 * "Use this strategy on": write the settings a backtest run played with onto a bot (replay.py saves them on the
 * run). The bot's Trade list, PAPER/LIVE switch and name stay. "Reset the bot's other settings" puts every setting
 * the run did not save back to its default first; per-stock settings come from the run when ticked (else, with the
 * reset, they are cleared). No order is placed.
 */
export function ApplyRunStrategy({ runId, runBot, hasBot5 = false }: { runId: number; runBot?: number | null; hasBot5?: boolean }) {
  const [bots, setBots] = useState<BotSummary[]>([]);
  const [to, setTo] = useState<Target>(runBot && runBot >= 1 && runBot <= 5 ? (String(runBot) as Target) : "1");
  // Which settings of the run: the replayed bot's strategy, or the Bot 5 settings its fast-mover hand-off played with.
  const [source, setSource] = useState<"run" | "bot5">("run");
  const from = source === "bot5" ? `run #${runId}'s Bot 5 settings` : `run #${runId}'s strategy`;
  const [reset, setReset] = useState(false);
  const [withStocks, setWithStocks] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    smaApi.bots().then(setBots).catch(() => setBots([]));
  }, []);

  const apply = async () => {
    const target = name(to, bots);
    const live = to !== "research" && bots.find((b) => b.bot === Number(to))?.mode === "LIVE";
    const parts = [
      `Put ${from} on ${target}?`,
      reset ? "Settings the run did not save go back to their defaults." : "Settings the run did not save keep the bot's own value.",
      withStocks
        ? "The run's per-stock settings replace the bot's."
        : reset
          ? "The bot's per-stock settings are cleared."
          : "The bot's per-stock settings stay.",
      "Its Trade list, mode and name stay.",
    ];
    if (!window.confirm(parts.join("\n"))) return;
    if (live && !window.confirm(`${target} is LIVE and trades real money. The new settings apply to its next orders. Go ahead?`)) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await smaApi.applyRunStrategy(runId, to, reset, withStocks, live, source);
      const n = res.changed.length;
      setNote(
        (n ? `Done: ${n} setting${n === 1 ? "" : "s"} changed on ${target}.` : `Done: ${target} already had this strategy.`) +
          (res.notes.length ? ` ${res.notes.join(" ")}` : "")
      );
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not put the strategy on the bot");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-sky-400/25 bg-sky-500/5 px-3 py-2">
      <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-sky-100">
        Use this strategy on a bot <NewTag id="backtest-apply-strategy" />
      </div>
      <p className="mb-2 text-xs text-slate-400">
        Copies the settings this run played with onto the bot you pick
        {hasBot5 ? ": the replayed bot's strategy, or the Bot 5 settings its fast-mover hand-off used (the \"Bot 5 · …\" rows)" : ""}.
        The bot&apos;s Trade list, PAPER / LIVE and name stay. No order is placed.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {hasBot5 ? (
          <label className="flex items-center gap-1.5 text-xs text-slate-300">
            Copy
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as "run" | "bot5")}
              className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            >
              <option value="run">Bot {runBot ?? 1}&apos;s strategy (the replayed bot)</option>
              <option value="bot5">Bot 5&apos;s settings (fast-mover hand-off)</option>
            </select>
            <NewTag id="backtest-apply-bot5" />
          </label>
        ) : null}
        <label className="flex items-center gap-1.5 text-xs text-slate-300">
          Bot
          <select
            value={to}
            onChange={(e) => setTo(e.target.value as Target)}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          >
            {TARGETS.map((t) => {
              const mode = t === "research" ? null : bots.find((b) => b.bot === Number(t))?.mode;
              return (
                <option key={t} value={t}>
                  {name(t, bots)}
                  {mode === "LIVE" ? " (LIVE)" : ""}
                </option>
              );
            })}
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-1.5 text-xs text-slate-300" title="Every setting the run did not save goes back to its default first">
          <input type="checkbox" checked={reset} onChange={(e) => setReset(e.target.checked)} />
          Reset the bot&apos;s other settings first
        </label>
        <label className="flex min-h-9 items-center gap-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={withStocks} onChange={(e) => setWithStocks(e.target.checked)} />
          Per-stock settings too
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={apply}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          {busy ? "Working…" : `Use on ${name(to, bots)}`}
        </button>
      </div>
      {note ? <p className="mt-2 text-xs text-slate-300">{note}</p> : null}
    </div>
  );
}
