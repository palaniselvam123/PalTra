"use client";

import { useState } from "react";
import { DESKS, smaApi, type BotSummary, type Desk } from "@/lib/smaApi";

function deskName(d: Desk, bots: BotSummary[]): string {
  if (d === "research") return "Research";
  const n = d === "live" ? 1 : Number(d.slice(3));
  return bots.find((b) => b.bot === n)?.name ?? `Bot ${n}`;
}

/**
 * Copy another bot's strategy settings onto the bot shown (entry, filters, exits, stop,
 * limits). Its Trade list, PAPER/LIVE switch and name stay; per-stock settings only when ticked.
 */
export function CopySettings({ to, bots, onCopied }: { to: Desk; bots: BotSummary[]; onCopied: () => void }) {
  const sources = DESKS.filter((d) => d !== to);
  const [from, setFrom] = useState<Desk>(sources[0]);
  const [withStocks, setWithStocks] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const source = sources.includes(from) ? from : sources[0];

  const copy = async () => {
    const target = deskName(to, bots);
    const n = to === "live" ? 1 : to === "research" ? null : Number(to.slice(3));
    const live = n !== null && bots.find((b) => b.bot === n)?.mode === "LIVE";
    const what = withStocks ? "strategy and per-stock settings" : "strategy settings";
    if (!window.confirm(`Replace ${target}'s ${what} with ${deskName(source, bots)}'s? Its Trade list, mode and name stay.`)) return;
    if (live && !window.confirm(`${target} is LIVE and trades real money. Its new settings apply to its next orders. Copy anyway?`)) return;
    setBusy(true);
    try {
      const res = await smaApi.copySettings(source, to, withStocks, live);
      setNote(res.changed.length ? `Copied: ${res.changed.length} setting${res.changed.length === 1 ? "" : "s"} changed.` : "Copied: nothing was different.");
      onCopied();
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not copy the settings");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Copy settings from
        <select
          value={source}
          onChange={(e) => setFrom(e.target.value as Desk)}
          className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
        >
          {sources.map((d) => (
            <option key={d} value={d}>
              {deskName(d, bots)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex min-h-9 items-center gap-1.5 text-xs text-slate-300">
        <input type="checkbox" checked={withStocks} onChange={(e) => setWithStocks(e.target.checked)} />
        Per-stock settings too
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={copy}
        className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
      >
        Copy to {deskName(to, bots)}
      </button>
      {note ? <span className="text-xs text-slate-400">{note}</span> : null}
    </div>
  );
}
