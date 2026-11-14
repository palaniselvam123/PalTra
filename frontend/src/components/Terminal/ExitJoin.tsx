"use client";

import { NewTag } from "@/components/ui/NewTag";
import clsx from "clsx";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * How exits combine (backend exit_join.py): OR = any one ticked exit closes the trade (the default); AND = the exits
 * ticked here only vote, and the trade closes once enough of them (all, or N) fired within the last few candles.
 * Stops, targets, the gap fade, the square-off and the loss limit always close on their own.
 */
export const EXIT_KEYS: { key: string; label: string; on: (f: Record<string, unknown>) => boolean }[] = [
  { key: "CROSS", label: "SMA cross", on: (f) => f.cross_exit !== false },
  { key: "COLOUR", label: "Candle colour", on: (f) => Boolean(f.turn_colour_exit || f.reverse_colour_exit) },
  { key: "VTURN", label: "SMA 9 V-turn", on: (f) => Boolean(f.vturn_exit) },
  { key: "TURN", label: "SMA 9 turn", on: (f) => Boolean(f.slope_exit) && f.slope_turn !== false },
  { key: "BEND", label: "SMA 9 bend", on: (f) => Boolean(f.slope_exit) && Number(f.slope_bend_pct ?? 60) > 0 },
  { key: "DECEL", label: "SMA 9 deceleration", on: (f) => Boolean(f.decel_exit) },
  { key: "COUNTER", label: "Counter candles", on: (f) => Number(f.counter_exit_count ?? 0) > 0 },
  { key: "SMALL", label: "Smaller candles", on: (f) => String(f.small_exit_minutes ?? "").trim() !== "" },
  { key: "XCANDLE", label: "Exit candle cross", on: (f) => Number(f.exit_candle_minutes ?? 0) > 0 },
  { key: "ST", label: "Supertrend", on: (f) => Boolean(f.st_exit) },
  { key: "BB", label: "Bollinger", on: (f) => String(f.bb_exit ?? "OFF").toUpperCase() !== "OFF" },
  { key: "EMA", label: "EMA cross", on: (f) => Boolean(f.ema_exit) },
  { key: "MACD", label: "MACD", on: (f) => Boolean(f.macd_exit) },
  { key: "CVD", label: "Cumulative delta", on: (f) => Boolean(f.cvd_div_exit) },
  { key: "SLOW", label: "Slow SMA", on: (f) => Boolean(f.slow_sma_exit) },
];

export function exitGroup(value: unknown): string[] {
  const keys = new Set(
    String(value ?? "")
      .split(",")
      .map((k) => k.trim().toUpperCase())
      .filter(Boolean)
  );
  return EXIT_KEYS.map((k) => k.key).filter((k) => keys.has(k));
}

export function exitJoinText(f: SmaConfig): string | null {
  const group = exitGroup(f.exit_group);
  if (String(f.exit_join ?? "OR").toUpperCase() !== "AND" || group.length < 2) return null;
  const need = Number(f.exit_need ?? 0) || 0;
  const names = group.map((k) => EXIT_KEYS.find((e) => e.key === k)?.label ?? k).join(" + ");
  const count = need > 0 && need < group.length ? `any ${need} of` : "all of";
  return `Exits set to AND: closes only when ${count} ${names} fire within ${Number(f.exit_within ?? 3) || 3} candles`;
}

export function ExitJoinCard({
  form,
  set,
  isOwn,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const f = form as unknown as Record<string, unknown>;
  const and = String(form.exit_join ?? "OR").toUpperCase() === "AND";
  const picked = exitGroup(form.exit_group);
  const toggle = (key: string, tick: boolean) => {
    const next = new Set(picked);
    if (tick) next.add(key);
    else next.delete(key);
    set("exit_group", EXIT_KEYS.map((k) => k.key).filter((k) => next.has(k)).join(","));
  };
  const need = Number(form.exit_need ?? 0) || 0;
  const own = isOwn("exit_join") || isOwn("exit_group") || isOwn("exit_need") || isOwn("exit_within");
  return (
    <div className={clsx("mt-4 rounded-md border p-2", and ? "border-amber-400/40 bg-amber-500/[0.04]" : "border-white/10")}>
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-300">
        <b className="text-slate-100">How exits combine</b> <NewTag id="set-exit-join" />
        {own ? <span className="text-xs text-violet-300">· own</span> : null}
        <div className="ml-auto flex gap-1" role="radiogroup" aria-label="How exits combine">
          {(["OR", "AND"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={(and ? "AND" : "OR") === m}
              onClick={() => set("exit_join", m)}
              className={clsx(
                "min-h-8 rounded-md px-3 text-xs font-semibold ring-1 ring-inset",
                (and ? "AND" : "OR") === m ? "bg-sky-500/20 text-sky-100 ring-sky-400/50" : "text-slate-300 ring-white/15 hover:bg-white/5"
              )}
            >
              {m === "OR" ? "OR: any one closes" : "AND: they must agree"}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        {and
          ? "The exits ticked below only vote. The trade closes once enough of them have fired within the last few candles; until then it is held. Exits not ticked still close on their own. The stop-loss, trailing stop, targets, gap fade, square-off and loss limit always close at once."
          : "Every exit that is on closes the trade by itself: the first one to fire wins (how the bot has always worked)."}
      </p>
      {and ? (
        <>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5" role="group" aria-label="Exits that must agree">
            {EXIT_KEYS.map((e) => {
              const on = e.on(f);
              return (
                <label key={e.key} className={clsx("flex items-center gap-1.5 text-sm", on ? "text-slate-200" : "text-slate-500")}>
                  <input type="checkbox" checked={picked.includes(e.key)} onChange={(ev) => toggle(e.key, ev.target.checked)} className="accent-[#10B981]" />
                  {e.label}
                  {picked.includes(e.key) && !on ? <span className="text-xs text-amber-300">(that exit is off)</span> : null}
                </label>
              );
            })}
          </div>
          <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-slate-300">
            <label className="block">
              Must agree
              <select
                value={need > 0 && need < picked.length ? String(need) : "0"}
                onChange={(e) => set("exit_need", e.target.value)}
                className="mt-1 block min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              >
                <option value="0">All of them</option>
                {Array.from({ length: Math.max(0, picked.length - 1) }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={String(n)}>
                    Any {n} of {picked.length}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              Within (candles)
              <select
                value={String(Number(form.exit_within ?? 3) || 3)}
                onChange={(e) => set("exit_within", e.target.value)}
                className="mt-1 block min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              >
                {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => (
                  <option key={n} value={String(n)}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            {picked.length < 2 ? <span className="text-amber-300">Tick at least two exits, or it works like OR.</span> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
