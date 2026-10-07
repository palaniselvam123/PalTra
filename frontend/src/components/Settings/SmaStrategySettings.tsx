"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { StrategyConfigPanel } from "@/components/Terminal/StrategyConfigPanel";
import { setDesk, smaApi, type Desk, type SmaConfig } from "@/lib/smaApi";

/**
 * The SMA terminal's strategy settings (entry, filters, gap mode, exits,
 * stop, flip, limits), moved here from the terminal so its screen shows the
 * strategy only. Live desk or research desk, each with its own settings.
 */
export function SmaStrategySettings() {
  const [desk, pickDesk] = useState<Desk>("live");
  const [config, setConfig] = useState<SmaConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    const chosen: Desk = asked === "research" ? "research" : "live";
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
    if (next === "research") url.searchParams.set("desk", "research");
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
          {(["live", "research"] as const).map((d) => (
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
              {d === "live" ? "Live desk" : "Research"}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p role="alert" className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
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
