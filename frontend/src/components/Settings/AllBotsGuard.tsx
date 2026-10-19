"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { inr, smaApi, type AllBotsGuard as Guard } from "@/lib/smaApi";

/**
 * All-bots day target / stop loss / square-off time: when today's P&L of bots 1-4 together
 * (closed + open, before charges) reaches the target or falls to minus the stop, or the
 * square-off time comes, every bot is panic squared off and stays stopped today
 * (all_bots_guard.py). 0 / empty = off.
 */
export function AllBotsGuard() {
  const [guard, setGuard] = useState<Guard | null>(null);
  const [target, setTarget] = useState("");
  const [stop, setStop] = useState("");
  const [squareoff, setSquareoff] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .allBotsGuard()
      .then((g) => {
        setGuard(g);
        if (fill) {
          setTarget(String(g.target || 0));
          setStop(String(g.stop || 0));
          setSquareoff(g.squareoff || "");
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), 10000);
    return () => clearInterval(poll);
  }, [load]);

  const save = async () => {
    const t = Math.max(0, Number(target) || 0);
    const s = Math.max(0, Number(stop) || 0);
    const live = await smaApi.bots().then((b) => b.some((x) => x.mode === "LIVE")).catch(() => false);
    if (
      live &&
      !window.confirm("A bot is LIVE. When this target, stop loss or square-off time is hit, every bot's real Groww positions are squared off. Save?")
    )
      return;
    setSaving(true);
    try {
      const g = await smaApi.saveAllBotsGuard(t, s, squareoff.trim());
      setGuard(g);
      setTarget(String(g.target));
      setStop(String(g.stop));
      setSquareoff(g.squareoff || "");
      setNote(t || s || g.squareoff ? "Saved. Watched every 5 s while NSE is open." : "Saved: off.");
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const total = guard?.total ?? 0;
  return (
    <section id="all-bots-guard" aria-label="All bots day target and stop loss" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="text-base font-semibold text-slate-100">All bots: day target, stop loss and square-off time</h2>
      <p className="mt-1 text-xs text-slate-400">
        Today&apos;s P&amp;L of Bots 1–4 together (closed trades plus open positions, before charges, each bot&apos;s own PAPER or
        LIVE book; not the research desk or replays). When it reaches the target, or falls to minus the stop loss, every bot is
        panic squared off and stays stopped for the rest of the day. The square-off time does the same at that time of day
        (IST, 09:15–15:30), whatever the P&amp;L; each bot&apos;s own square-off time still applies too. To start again today,
        change the settings so they are no longer hit. Backtests use them too: each replayed day stops when that bot&apos;s own
        P&amp;L for the day hits the target or stop, or the replay clock reaches the square-off time. 0 or empty = off.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Day target (₹)
          <input
            inputMode="decimal"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="min-h-9 w-36 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Day stop loss (₹)
          <input
            inputMode="decimal"
            value={stop}
            onChange={(e) => setStop(e.target.value)}
            className="min-h-9 w-36 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Square off all at (IST)
          <span className="flex items-center gap-1">
            <input
              type="time"
              min="09:15"
              max="15:30"
              value={squareoff}
              onChange={(e) => setSquareoff(e.target.value)}
              className="min-h-9 w-32 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            />
            {squareoff ? (
              <button type="button" onClick={() => setSquareoff("")} className="text-xs text-slate-500 underline-offset-2 hover:underline">
                clear
              </button>
            ) : null}
          </span>
        </label>
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Save
        </button>
        {guard ? (
          <span className="text-sm text-slate-300">
            Today, all bots:{" "}
            <span className={clsx("font-mono font-semibold", total > 0 ? "text-emerald-300" : total < 0 ? "text-rose-300" : "text-slate-200")}>
              {inr(total)}
            </span>
          </span>
        ) : null}
        {note ? <span className="text-xs text-slate-400">{note}</span> : null}
      </div>
      {guard && guard.halted_bots.length > 0 ? (
        <p role="status" className="mt-2 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
          {guard.reason || "The all-bots limit was hit"} (Bots {guard.halted_bots.join(", ")}).
        </p>
      ) : null}
    </section>
  );
}
