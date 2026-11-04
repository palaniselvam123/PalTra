"use client";

import clsx from "clsx";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * Reversed trades' own settings (backend reverse_exit.py, `reverse_settings`): entry checks, the counter-candle exit,
 * the other exits and the trailing stop used only for the reverse order ("Reverse instead of only closing") and while
 * that reversed trade is open. A group left unticked keeps the bot's own values. Saved with the normal Save.
 */
export type ReverseSettings = Record<string, string | number | boolean>;

type FieldDef =
  | { key: string; label: string; kind: "bool" }
  | { key: string; label: string; kind: "num"; dflt: number; suffix?: string }
  | { key: string; label: string; kind: "pick"; dflt: string; options: { value: string; label: string }[] };

type Group = { id: string; title: string; hint: string; fields: FieldDef[] };

const STOP_MODES = [
  { value: "POINTS", label: "₹ points" },
  { value: "PERCENT", label: "% of entry" },
  { value: "ATR", label: "ATR multiples" },
];

export const REVERSE_GROUPS: Group[] = [
  {
    id: "entry",
    title: "Entry checks for the reverse order",
    hint: "Read when the reverse is asked for. Untick a check here to let the reverse order go without it.",
    fields: [
      { key: "use_vwap", label: "VWAP check", kind: "bool" },
      { key: "use_rsi", label: "RSI check", kind: "bool" },
      { key: "rsi_long_min", label: "Buy RSI from", kind: "num", dflt: 40 },
      { key: "rsi_long_max", label: "Buy RSI to", kind: "num", dflt: 70 },
      { key: "rsi_short_min", label: "Sell RSI from", kind: "num", dflt: 30 },
      { key: "rsi_short_max", label: "Sell RSI to", kind: "num", dflt: 60 },
      { key: "use_adx_filter", label: "ADX check", kind: "bool" },
      { key: "adx_threshold", label: "ADX at least", kind: "num", dflt: 20 },
      { key: "use_cvd", label: "Cumulative delta check", kind: "bool" },
      { key: "cvd_lookback", label: "Delta over (candles)", kind: "num", dflt: 5 },
    ],
  },
  {
    id: "counter",
    title: "Counter-candle exit",
    hint: "Closes the reversed trade after N candles in a row against it.",
    fields: [
      { key: "counter_exit_count", label: "Candles against (0 = off)", kind: "num", dflt: 0 },
      {
        key: "counter_rule",
        label: "Against means",
        kind: "pick",
        dflt: "COLOUR",
        options: [
          { value: "COLOUR", label: "Colour (green / red)" },
          { value: "CLOSES", label: "Each close beyond the last" },
          { value: "BOTH", label: "Both" },
        ],
      },
      { key: "counter_need_fast", label: "Last close must be past the fast SMA", kind: "bool" },
      { key: "counter_min_body", label: "Min body × ATR (0 = off)", kind: "num", dflt: 0 },
    ],
  },
  {
    id: "exits",
    title: "Other exits",
    hint: "Which of these exits watch the reversed trade.",
    fields: [
      {
        key: "bb_exit",
        label: "Bollinger exit",
        kind: "pick",
        dflt: "OFF",
        options: [
          { value: "OFF", label: "Off" },
          { value: "BAND", label: "Far band (target)" },
          { value: "MIDDLE", label: "Back across the middle" },
          { value: "BOTH", label: "Band + middle" },
        ],
      },
      { key: "st_exit", label: "Supertrend exit", kind: "bool" },
      { key: "vturn_exit", label: "SMA 9 V-turn exit", kind: "bool" },
      { key: "cross_exit", label: "SMA cross exit", kind: "bool" },
    ],
  },
  {
    id: "stop",
    title: "Trailing stop and target",
    hint: "Used while the stop type is Trailing (TSL). Target 0 = none.",
    fields: [
      { key: "tsl_mode", label: "Units", kind: "pick", dflt: "POINTS", options: STOP_MODES },
      { key: "tsl_sl_points", label: "Stop", kind: "num", dflt: 20, suffix: "₹" },
      { key: "tsl_trail_points", label: "Trail", kind: "num", dflt: 10, suffix: "₹" },
      { key: "tsl_target_points", label: "Target", kind: "num", dflt: 0, suffix: "₹" },
      { key: "tsl_sl_pct", label: "Stop", kind: "num", dflt: 1, suffix: "%" },
      { key: "tsl_trail_pct", label: "Trail", kind: "num", dflt: 0.5, suffix: "%" },
      { key: "tsl_target_pct", label: "Target", kind: "num", dflt: 0, suffix: "%" },
      { key: "tsl_sl_atr", label: "Stop", kind: "num", dflt: 1.5, suffix: "× ATR" },
      { key: "tsl_trail_atr", label: "Trail", kind: "num", dflt: 0.5, suffix: "× ATR" },
      { key: "tsl_target_atr", label: "Target", kind: "num", dflt: 3, suffix: "× ATR" },
    ],
  },
];

const ALL_FIELDS = REVERSE_GROUPS.flatMap((g) => g.fields);
const NUMERIC = new Set(ALL_FIELDS.filter((f) => f.kind === "num").map((f) => f.key));
const BOOL = new Set(ALL_FIELDS.filter((f) => f.kind === "bool").map((f) => f.key));

/** The setting read from the saved config (an object, or a JSON string from an older copy). */
export function readReverse(value: unknown): ReverseSettings {
  let v = value;
  if (typeof v === "string") {
    if (!v.trim()) return {};
    try {
      v = JSON.parse(v);
    } catch {
      return {};
    }
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return {};
  const out: ReverseSettings = {};
  for (const f of ALL_FIELDS) {
    const x = (v as Record<string, unknown>)[f.key];
    if (typeof x === "string" || typeof x === "number" || typeof x === "boolean") out[f.key] = x;
  }
  return out;
}

/** What Save sends: numbers as numbers; a box left empty is dropped (the bot's value then applies). */
export function reverseBody(value: unknown): ReverseSettings {
  const out: ReverseSettings = {};
  for (const [k, v] of Object.entries(readReverse(value))) {
    if (NUMERIC.has(k)) {
      const n = Number(v);
      if (String(v).trim() !== "" && Number.isFinite(n)) out[k] = n;
    } else if (BOOL.has(k)) {
      out[k] = Boolean(v);
    } else {
      out[k] = String(v);
    }
  }
  return out;
}

/** Which groups have their own settings ("entry", "counter", "exits", "stop"). */
export function reverseGroupsOn(value: unknown): string[] {
  const own = readReverse(value);
  return REVERSE_GROUPS.filter((g) => g.fields.some((f) => f.key in own)).map((g) => g.id);
}

function botValue(form: SmaConfig, f: FieldDef): string | number | boolean {
  const v = (form as unknown as Record<string, unknown>)[f.key];
  if (f.kind === "bool") return f.key === "counter_need_fast" || f.key === "cross_exit" ? v !== false : Boolean(v);
  if (f.kind === "pick") return typeof v === "string" && v ? v.toUpperCase() : f.dflt;
  return v == null || v === "" ? f.dflt : (v as number);
}

export function ReverseOwnSettings({
  form,
  onChange,
  own,
  disabled,
}: {
  form: SmaConfig;
  onChange: (next: ReverseSettings) => void;
  own: boolean;
  disabled: boolean;
}) {
  const current = readReverse(form.reverse_settings);
  const groupOn = (g: Group) => g.fields.some((f) => f.key in current);
  const toggleGroup = (g: Group, on: boolean) => {
    const next = { ...current };
    for (const f of g.fields) {
      if (on) next[f.key] = botValue(form, f);
      else delete next[f.key];
    }
    onChange(next);
  };
  const setField = (key: string, value: string | number | boolean) => onChange({ ...current, [key]: value });
  const stopMode = String(current.tsl_mode ?? "POINTS").toUpperCase();
  const stopKind = stopMode === "PERCENT" ? "_pct" : stopMode === "ATR" ? "_atr" : "_points";

  return (
    <div className={clsx("mt-3 rounded-md border border-white/10 p-2", disabled && "opacity-60")}>
      <div className="text-sm font-semibold text-slate-100">
        Reversed trades: their own entry checks, exits and stop
        {own ? <span className="ml-1 text-xs font-normal text-violet-300">· own</span> : null}
      </div>
      <p className="mt-0.5 text-xs text-slate-400">
        Tick a group to give the reverse order and the reversed trade (&quot;Reversed on exit&quot;) their own numbers. An
        unticked group uses the bot&apos;s settings above. Normal trades are never affected; the cut-off, trade caps, loss
        limit and one LIVE bot per stock always apply. A trade reversed by hand with the Reverse button is not covered.
        {disabled ? " Tick an exit under “Reverse instead of only closing” first." : ""}
      </p>
      <div className="mt-2 space-y-2">
        {REVERSE_GROUPS.map((g) => {
          const on = groupOn(g);
          const fields = g.id === "stop" ? g.fields.filter((f) => f.key === "tsl_mode" || f.key.endsWith(stopKind)) : g.fields;
          return (
            <div key={g.id} className={clsx("rounded-md p-2 ring-1 ring-inset", on ? "bg-emerald-500/[0.05] ring-emerald-400/40" : "ring-white/10")}>
              <label className="flex items-center gap-2 text-sm text-slate-200">
                <input type="checkbox" checked={on} onChange={(e) => toggleGroup(g, e.target.checked)} className="accent-[#10B981]" />
                <b>{g.title}</b>
                <span className="text-xs text-slate-400">{on ? "own" : "same as the bot"}</span>
              </label>
              <p className="ml-6 text-xs text-slate-400">{g.hint}</p>
              {on ? (
                <div className="ml-6 mt-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-4">
                  {fields.map((f) =>
                    f.kind === "bool" ? (
                      <label key={f.key} className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
                        <input
                          type="checkbox"
                          checked={Boolean(current[f.key])}
                          onChange={(e) => setField(f.key, e.target.checked)}
                          className="accent-[#10B981]"
                        />
                        {f.label}
                      </label>
                    ) : f.kind === "pick" ? (
                      <label key={f.key} className="block text-xs text-slate-300">
                        {f.label}
                        <select
                          value={String(current[f.key] ?? f.dflt)}
                          onChange={(e) => setField(f.key, e.target.value)}
                          className="mt-1 block min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
                        >
                          {f.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <label key={f.key} className="block text-xs text-slate-300">
                        {f.label}
                        {f.suffix ? ` (${f.suffix})` : ""}
                        <input
                          inputMode="decimal"
                          value={String(current[f.key] ?? "")}
                          onChange={(e) => setField(f.key, e.target.value)}
                          className="mt-1 block min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 font-mono text-sm text-slate-100"
                        />
                      </label>
                    )
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
