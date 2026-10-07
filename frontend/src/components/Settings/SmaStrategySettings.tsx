"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { StrategyConfigPanel } from "@/components/Terminal/StrategyConfigPanel";
import { DESKS, setDesk, smaApi, type BotSummary, type Desk, type SmaConfig } from "@/lib/smaApi";

/**
 * The SMA terminal's strategy settings (entry, filters, gap mode, exits,
 * stop, flip, limits), moved here from the terminal so its screen shows the
 * strategy only. Live desk or research desk, each with its own settings.
 */
export function SmaStrategySettings() {
  const [desk, pickDesk] = useState<Desk>("live");
  const [config, setConfig] = useState<SmaConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bots, setBots] = useState<BotSummary[]>([]);
  const [name, setName] = useState("");
  const [nameNote, setNameNote] = useState<string | null>(null);
  useEffect(() => {
    smaApi.bots().then(setBots).catch(() => undefined);
  }, []);
  useEffect(() => {
    setName(config?.bot_name ?? "");
    setNameNote(null);
  }, [config]);

  const load = useCallback(() => {
    smaApi
      .config()
      .then((cfg) => {
        setConfig(cfg);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The SMA terminal did not answer."));
  }, []);

  useEffect(() => {
    const asked = new URLSearchParams(window.location.search).get("desk");
    const chosen: Desk = (DESKS as string[]).includes(asked ?? "") ? (asked as Desk) : "live";
    setDesk(chosen);
    pickDesk(chosen);
    load();
    const target = window.location.hash.slice(1);
    if (target) {
      // Wait a frame so the sections below this one have rendered too.
      requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView());
    }
  }, [load]);

  const choose = (next: Desk) => {
    if (next === desk) return;
    setDesk(next);
    pickDesk(next);
    setConfig(null);
    const url = new URL(window.location.href);
    if (next !== "live") url.searchParams.set("desk", next);
    else url.searchParams.delete("desk");
    window.history.replaceState(null, "", url.toString());
    load();
  };

  return (
    <section id="sma-strategy" aria-label="SMA terminal strategy" className="terminal-dark scroll-mt-20 rounded-xl">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-slate-100">SMA terminal strategy</h2>
          <p className="text-xs text-slate-400">
            Entry, filters, gap mode, exits, stop and daily limits. The terminal shows a summary.
          </p>
        </div>
        <div role="group" aria-label="Desk" className="inline-flex rounded-md ring-1 ring-inset ring-white/15">
          {DESKS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={desk === d}
              onClick={() => choose(d)}
              className={clsx(
                "min-h-9 px-3 text-xs first:rounded-l-md last:rounded-r-md",
                desk === d ? "bg-sky-500/25 font-semibold text-sky-100" : "text-slate-300 hover:bg-white/5"
              )}
            >
              {d === "research"
                ? "Research"
                : bots.find((b) => b.bot === (d === "live" ? 1 : Number(d.slice(3))))?.name ??
                  (d === "live" ? "Bot 1" : `Bot ${d.slice(3)}`)}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p role="alert" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}
      {config && desk !== "research" ? (
        <form
          className="mb-2 flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const next = name.trim();
            if (!next) return;
            try {
              await smaApi.saveConfig({ bot_name: next });
              setNameNote("Saved.");
              smaApi.bots().then(setBots).catch(() => undefined);
              load();
            } catch (err: unknown) {
              setNameNote(err instanceof Error ? err.message : "Could not save the name");
            }
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Bot name
            <input
              value={name}
              maxLength={24}
              placeholder="e.g. Scalper"
              onChange={(e) => setName(e.target.value)}
              className="min-h-9 w-48 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            />
          </label>
          <button type="submit" className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10">
            Save name
          </button>
          {nameNote ? <span className="text-xs text-slate-400">{nameNote}</span> : null}
        </form>
      ) : null}
      {config ? (
        <StrategyConfigPanel key={desk} config={config} onChanged={load} wide />
      ) : error ? null : (
        <p className="text-sm text-slate-400">Loading the strategy settings…</p>
      )}
      <p className="mt-2 text-xs text-slate-400">
        <a href={desk === "research" ? "/terminal/?desk=research" : "/terminal/"} className="text-sky-300 underline-offset-2 hover:underline">
          Back to the terminal
        </a>
      </p>
    </section>
  );
}
