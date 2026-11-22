"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { SettingsCard } from "@/components/Settings/SettingsCard";
import { NewTag } from "@/components/ui/NewTag";
import { inr, smaApi, type AllBotsGuard as Guard } from "@/lib/smaApi";
import { LevelBox, UnitSwitch, type LevelUnit } from "@/components/Settings/CapitalLevel";

const input = "min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
const num = (v: string) => Math.max(0, Number(v) || 0);

/**
 * All bots: quit at a day loss, and a profit safety net that trails the day's best live P&L (MTM = closed + open,
 * before charges) of Bots 1-4 (all_bots_guard.trail_rule). Shares the guard's panic: every bot squared off and
 * stopped for the day. The loss here is the same setting as the day stop loss on the card above.
 */
export function ProfitSafetyNet() {
  const [guard, setGuard] = useState<Guard | null>(null);
  const [on, setOn] = useState(false);
  const [stop, setStop] = useState("");
  const [start, setStart] = useState("5000");
  const [keep, setKeep] = useState("70");
  const [start2, setStart2] = useState("10000");
  const [keep2, setKeep2] = useState("75");
  const [unit, setUnit] = useState<LevelUnit>("RS");
  const [stopPct, setStopPct] = useState("");
  const [startPct, setStartPct] = useState("");
  const [start2Pct, setStart2Pct] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .allBotsGuard()
      .then((g) => {
        setGuard(g);
        if (fill) {
          setOn(Boolean(g.trail));
          setStop(String(g.stop || 0));
          setStart(String(g.trail_start ?? 5000));
          setKeep(String(g.trail_keep ?? 70));
          setStart2(String(g.trail_start2 ?? 0));
          setKeep2(String(g.trail_keep2 ?? 75));
          setUnit(g.unit ?? "RS");
          setStopPct(String(g.stop_pct || 0));
          setStartPct(String(g.trail_start_pct || 0));
          setStart2Pct(String(g.trail_start2_pct || 0));
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), 10000);
    return () => clearInterval(poll);
  }, [load]);

  const useMine = () => {
    setOn(true);
    setUnit("RS");
    setStop("4000");
    setStart("5000");
    setKeep("70");
    setStart2("10000");
    setKeep2("75");
    setNote("Filled in: quit at -₹4,000; from +₹5,000 keep 70 %, from +₹10,000 keep 75 %. Press Save.");
  };

  const save = async () => {
    const pct = unit === "PCT";
    const s1 = pct ? num(startPct) : num(start);
    const s2 = pct ? num(start2Pct) : num(start2);
    if (on && s1 <= 0) return setNote("Set the profit that switches the safety net on.");
    if (s2 && s2 <= s1) return setNote("The second step must start above the first (or 0 for none).");
    const live = await smaApi.bots().then((b) => b.some((x) => x.mode === "LIVE")).catch(() => false);
    if (live && !window.confirm("A bot is LIVE. When the loss quit or the safety net is hit, every bot's real Groww positions are squared off. Save?"))
      return;
    setSaving(true);
    try {
      const g = await smaApi.saveProfitNet({
        stop: num(stop),
        trail: on,
        unit,
        stop_pct: num(stopPct),
        trail_start_pct: num(startPct),
        trail_start2_pct: num(start2Pct),
        trail_start: num(start) || 5000,
        trail_keep: Math.min(99, Math.max(1, Number(keep) || 70)),
        trail_start2: num(start2),
        trail_keep2: Math.min(99, Math.max(1, Number(keep2) || 75)),
      });
      setGuard(g);
      setNote(on ? "Saved. Watched every 5 s while NSE is open." : "Saved: safety net off.");
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const total = guard?.total ?? 0;
  const cap = guard?.capital ?? 0;
  const s1 = unit === "PCT" ? (num(startPct) * cap) / 100 : num(start);
  const k1 = Number(keep) || 70;
  const s2 = unit === "PCT" ? (num(start2Pct) * cap) / 100 : num(start2);
  const k2 = Number(keep2) || 75;
  const example = (best: number) => (s2 && best >= s2 ? (best * k2) / 100 : (best * k1) / 100);
  return (
    <SettingsCard
      id="all-bots-profit-net"
      title={
        <>
          All bots: loss quit and profit safety net <NewTag id="profit-safety-net" />
        </>
      }
      tier="start"
      status={guard ? { on: Boolean(guard.trail || guard.effective?.stop) } : null}
      summary="Live P&L (MTM) = today's closed trades plus open trades of Bots 1-4, before charges. Quit at a set loss; once the profit reaches a level, never let the market take back more than a set share of the day's best profit. Either one closes every bot and stops it for the day."
      help={
        <>
          The loss quit is the same setting as the day stop loss on the card above (saving here changes it there too). The safety
          net switches on once the day&apos;s live profit reaches the first level; it then sits at the kept % of the best profit so
          far and only moves up. From the second level (0 = none) it keeps the second, tighter %. When the live profit falls back to
          the net, every bot is panic squared off (LIVE stops cancelled first) and stays stopped today, like the day target. The best
          profit is kept for the day, also across a restart (read from the P&amp;L log). Backtests use it too, on each replayed
          day&apos;s own P&amp;L.
        </>
      }
    >
      <UnitSwitch unit={unit} onChange={setUnit} capital={cap} />
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <LevelBox label="Quit at a loss of" unit={unit} rs={stop} pct={stopPct} onRs={setStop} onPct={setStopPct} capital={cap} />
        <label className="flex min-h-9 items-center gap-1.5 self-end text-xs text-slate-200">
          <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} className="accent-violet-400" />
          Profit safety net
        </label>
        <LevelBox label="Once profit reaches" unit={unit} rs={start} pct={startPct} onRs={setStart} onPct={setStartPct} capital={cap} disabled={!on} />
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Keep (% of best)
          <input inputMode="decimal" value={keep} onChange={(e) => setKeep(e.target.value)} className={clsx(input, "w-20")} disabled={!on} />
        </label>
        <LevelBox label="From profit (0 = none)" unit={unit} rs={start2} pct={start2Pct} onRs={setStart2} onPct={setStart2Pct} capital={cap} disabled={!on} />
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Keep (% of best)
          <input inputMode="decimal" value={keep2} onChange={(e) => setKeep2(e.target.value)} className={clsx(input, "w-20")} disabled={!on || !s2} />
        </label>
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={useMine} className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
          Use -4,000 / 5,000 · 70 % / 10,000 · 75 %
        </button>
      </div>
      {on && s1 > 0 ? (
        <p className="mt-2 text-xs text-slate-400">
          Example: best {inr(s1)} → net {inr(example(s1))}
          {" · "}best {inr(s1 * 1.6)} → net {inr(example(s1 * 1.6))}
          {s2 ? (
            <>
              {" · "}best {inr(s2 * 2)} → net {inr(example(s2 * 2))}
            </>
          ) : null}
        </p>
      ) : null}
      {guard ? (
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-300">
          <span>
            Live P&amp;L now:{" "}
            <span className={clsx("font-mono font-semibold", total > 0 ? "text-emerald-300" : total < 0 ? "text-rose-300" : "text-slate-200")}>
              {inr(total)}
            </span>
          </span>
          <span>
            Best today: <span className="font-mono">{inr(guard.peak ?? 0)}</span>
          </span>
          <span>
            Safety net:{" "}
            <span className="font-mono font-semibold text-amber-200">
              {guard.trail ? (guard.floor != null ? inr(guard.floor) : `not yet (until ${inr(guard.trail_start)})`) : "off"}
            </span>
          </span>
        </div>
      ) : null}
      {note ? <p className="mt-1 text-xs text-slate-400">{note}</p> : null}
      {guard && guard.halted_bots.length > 0 && guard.hit === "trail" ? (
        <p role="status" className="mt-2 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
          {guard.reason} (Bots {guard.halted_bots.join(", ")}).
        </p>
      ) : null}
    </SettingsCard>
  );
}
