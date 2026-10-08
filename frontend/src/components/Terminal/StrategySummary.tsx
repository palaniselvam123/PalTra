"use client";

import { InfoTip } from "@/components/ui/InfoTip";
import { useEffect, useState, type ReactNode } from "react";
import clsx from "clsx";
import { Loader2, Settings2 } from "lucide-react";
import { BB_EXITS, inr, smaApi, type BbExit, type CandleMinutes, type SmaConfig, type StopType } from "@/lib/smaApi";
import { crossExitOn as crossExitIsOn, noExitOn, strategyNotes } from "@/lib/strategyChecks";

/** The settings page section for this desk's strategy. */
export function strategySettingsHref(research: boolean): string {
  return research ? "/settings/?desk=research#sma-strategy" : "/settings/#sma-strategy";
}

/** "" = the shared settings; a symbol = that stock's own. */
const ALL = "";

type Toggle = {
  key: keyof SmaConfig;
  label: string;
  /** The numbers it uses, set on the Settings page. */
  detail: (c: SmaConfig) => string;
  /** Shown only when this is true (e.g. gap-mode exits need gap mode). */
  when?: (c: SmaConfig) => boolean;
  /** Read the switch when it is not a plain flag (e.g. on unless set off). */
  on?: (c: SmaConfig) => boolean;
};

const isOn = (t: Toggle, c: SmaConfig) => (t.on ? t.on(c) : Boolean(c[t.key]));

const ENTRY: Toggle[] = [
  {
    key: "use_gap_mode",
    label: "SMA gap mode",
    detail: (c) => `cross arms · buy ≥ ${c.gap_entry_long ?? 0.05}% · sell ≤ ${c.gap_entry_short ?? -0.05}%`,
  },
];

const FILTERS: Toggle[] = [
  { key: "use_candle_dir", label: "Candle direction", detail: (c) => `last ${c.candle_dir_count ?? 2} · ${(c.candle_dir_rule ?? "CLOSES").toLowerCase()}` },
  { key: "use_adx_filter", label: "ADX", detail: (c) => `≥ ${c.adx_threshold}` },
  { key: "use_vwap", label: "VWAP side", detail: () => "buy above · sell below" },
  { key: "use_volume", label: "Volume", detail: (c) => `≥ ${c.volume_min_ratio ?? 1}× avg` },
  { key: "use_density", label: "Candle body", detail: (c) => `≥ ${c.density_min_pct ?? 50}%` },
  { key: "use_rsi", label: "RSI", detail: (c) => `buy ${c.rsi_long_min}–${c.rsi_long_max} · sell ${c.rsi_short_min}–${c.rsi_short_max}` },
  { key: "use_bollinger", label: "Bollinger width", detail: (c) => `≥ ${c.bb_min_width_pct ?? 0.15}%` },
  { key: "use_gap_long", label: "Gap range (buy)", detail: (c) => `${c.gap_long_min ?? 0.02}–${c.gap_long_max ?? 0.5}%` },
  { key: "use_gap_short", label: "Gap range (sell)", detail: (c) => `${c.gap_short_min ?? -0.5} to ${c.gap_short_max ?? -0.02}%` },
];

const GAP_EXITS: Toggle[] = [
  {
    key: "gap_fade_confirm_sma",
    label: "Ride out pullbacks",
    detail: (c) => `fade needs a close past SMA ${c.sma_slow}`,
    when: (c) => Boolean(c.use_gap_mode),
  },
  {
    key: "gap_fade_intrabar",
    label: "Fade check every second",
    detail: () => "live price, not only closed candles",
    when: (c) => Boolean(c.use_gap_mode),
  },
];

const BB_LABEL: Record<BbExit, string> = { OFF: "Off", BAND: "Band", MIDDLE: "Middle", BOTH: "Both" };
const STOP_LABEL: Record<StopType, string> = { ATR: "ATR", TSL: "Trailing (TSL)", SMA_GAP: "SMA gap" };

/**
 * Strategy selection on the terminal: switch each part of the strategy on or
 * off (gap mode, filters, exits, stop, flip), for all stocks or one stock.
 * Each switch saves at once. The numbers behind them live on the Settings page.
 */
export function StrategySummary({
  config,
  research = false,
  onChanged,
}: {
  config: SmaConfig | null;
  research?: boolean;
  onChanged?: () => void;
}) {
  const [scope, setScope] = useState<string>(ALL);
  const [view, setView] = useState<SmaConfig | null>(config);
  const [own, setOwn] = useState<Partial<SmaConfig>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // The shared view follows the page's config; a stock's view is loaded for it.
  useEffect(() => {
    if (scope === ALL) {
      setView(config);
      setOwn({});
    }
  }, [config, scope]);
  useEffect(() => {
    if (scope === ALL) return;
    let live = true;
    smaApi
      .stockConfig(scope)
      .then((row) => {
        if (!live) return;
        setView(row);
        setOwn(row.own ?? {});
      })
      .catch((e: unknown) => live && setMsg({ ok: false, text: e instanceof Error ? e.message : "Could not load that stock" }));
    return () => {
      live = false;
    };
  }, [scope]);

  if (!config || !view) return null;
  const c = view;
  const liveMoney = !research && (config.trading_mode ?? "PAPER").toUpperCase() === "LIVE";
  const armed = (config.trade_symbols ?? []).map((s) => s.toUpperCase());
  const withOwn = Object.keys(config.stock_settings ?? {});
  const scopes = Array.from(new Set([...armed, ...withOwn])).sort();
  const overrides = (key: keyof SmaConfig) =>
    scope === ALL ? withOwn.filter((name) => key in ((config.stock_settings ?? {})[name] ?? {})) : [];

  const save = async (values: Partial<SmaConfig>, label: string) => {
    setSaving(label);
    setMsg(null);
    const before = view;
    setView({ ...view, ...values });
    try {
      if (scope === ALL) {
        const next = await smaApi.saveConfig(values);
        setView(next);
      } else {
        const row = await smaApi.saveStockConfig(scope, values);
        setView(row);
        setOwn(row.own ?? {});
      }
      setMsg({ ok: true, text: `Saved: ${label}${scope ? ` for ${scope}` : ""}. It applies from the next candle; open positions stay open.` });
      onChanged?.();
    } catch (e: unknown) {
      setView(before);
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Save failed" });
    } finally {
      setSaving(null);
    }
  };

  const flip = (t: Toggle) => {
    const next = !isOn(t, c);
    if (t.key === "cross_exit" && !next) {
      const sure = window.confirm(
        `Turn the SMA cross exit OFF${liveMoney ? " with LIVE money" : ""}? An opposite cross will no longer close or reverse an open trade; ` +
          `only the stop / target, gap fade, Bollinger exit or the ${config.square_off_time} square-off will.`
      );
      if (!sure) return;
    }
    if (t.key === "flip_orders" && liveMoney) {
      const sure = window.confirm(
        next
          ? "Turn the flip ON with LIVE money? Buy signals will place real SELL orders and sell signals real BUY orders."
          : "Turn the flip OFF with LIVE money? Orders will follow the signals again."
      );
      if (!sure) return;
    }
    save({ [t.key]: next } as Partial<SmaConfig>, `${t.label} ${next ? "on" : "off"}`);
  };

  const stopOn = c.use_stop !== false;
  const toggleStop = () => {
    if (stopOn) {
      const sure = window.confirm(
        liveMoney
          ? "Turn the stop-loss OFF with LIVE money? New entries will be sent to Groww with no stop order."
          : "Turn the stop-loss OFF? New entries will have no stop."
      );
      if (!sure) return;
    }
    save({ use_stop: !stopOn }, `Stop-loss ${stopOn ? "off" : "on"}`);
  };

  const crossExitOn = crossExitIsOn(c);
  const noExit = noExitOn(c);
  // The no-exit warning has its own banner here.
  const notes = strategyNotes(c).filter((n) => n.id !== "no-exit");
  const busy = saving != null;

  const switchChip = (t: Toggle) => {
    if (t.when && !t.when(c)) return null;
    const on = isOn(t, c);
    const others = overrides(t.key);
    const mine = scope !== ALL && t.key in own;
    return (
      <button
        key={String(t.key)}
        type="button"
        role="switch"
        aria-checked={on}
        disabled={busy}
        onClick={() => flip(t)}
        title={others.length ? `${others.join(", ")} set this for themselves` : undefined}
        className={clsx(
          "flex min-h-12 min-w-0 items-start gap-2 rounded-lg px-3 py-2 text-left ring-1 ring-inset disabled:opacity-60",
          on ? "bg-emerald-500/10 ring-emerald-400/50" : "bg-white/[0.02] ring-white/10 hover:bg-white/5"
        )}
      >
        <span
          aria-hidden
          className={clsx(
            "mt-0.5 flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 transition-colors",
            on ? "justify-end bg-emerald-500" : "justify-start bg-slate-600"
          )}
        >
          <span className="h-3 w-3 rounded-full bg-white" />
        </span>
        <span className="min-w-0">
          <span className={clsx("block text-sm font-semibold", on ? "text-slate-100" : "text-slate-300")}>
            {t.label}
            {mine ? <span className="ml-1 rounded bg-violet-500/20 px-1 text-[10px] font-semibold text-violet-200">own</span> : null}
            {others.length ? (
              <span className="ml-1 rounded bg-white/10 px-1 text-[10px] font-normal text-slate-300">{others.length} own</span>
            ) : null}
          </span>
          <span className="block truncate text-[11px] text-slate-400">{t.detail(c)}</span>
        </span>
      </button>
    );
  };

  const segmented = <T extends string>(
    label: string,
    value: T,
    options: readonly T[],
    names: Record<T, string>,
    onPick: (v: T) => void
  ) => (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-slate-400">{label}</span>
      <span role="group" aria-label={label} className="inline-flex rounded-md ring-1 ring-inset ring-white/15">
        {options.map((o) => (
          <button
            key={o}
            type="button"
            aria-pressed={value === o}
            disabled={busy}
            onClick={() => value !== o && onPick(o)}
            className={clsx(
              "min-h-8 px-2.5 text-xs first:rounded-l-md last:rounded-r-md disabled:opacity-60",
              value === o ? "bg-sky-500/25 font-semibold text-sky-100" : "text-slate-300 hover:bg-white/5"
            )}
          >
            {names[o]}
          </button>
        ))}
      </span>
    </div>
  );

  const group = (title: string, children: ReactNode) => (
    <div>
      <div className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-400">{title}</div>
      {children}
    </div>
  );

  const gapExits = GAP_EXITS.filter((t) => !t.when || t.when(c));

  return (
    <section aria-label="Strategy" className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[11px] uppercase tracking-[0.14em] text-slate-400">
            Strategy{research ? " · research desk" : ""}
          </span>
          <label className="flex items-center gap-1.5 text-xs text-slate-400">
            For
            <select
              value={scope}
              onChange={(e) => {
                setMsg(null);
                setScope(e.target.value);
              }}
              className="min-h-8 rounded-md border border-white/15 bg-black/30 px-2 text-xs text-slate-100"
            >
              <option value={ALL}>All stocks</option>
              {scopes.map((name) => (
                <option key={name} value={name}>
                  {name}
                  {withOwn.includes(name) ? " (own)" : ""}
                </option>
              ))}
            </select>
          </label>
          {scope !== ALL && Object.keys(own).length ? (
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setSaving("reset");
                try {
                  const row = await smaApi.resetStockConfig(scope);
                  setView(row);
                  setOwn({});
                  setMsg({ ok: true, text: `${scope} follows the shared strategy again.` });
                  onChanged?.();
                } catch (e: unknown) {
                  setMsg({ ok: false, text: e instanceof Error ? e.message : "Reset failed" });
                } finally {
                  setSaving(null);
                }
              }}
              className="text-xs text-violet-300 underline-offset-2 hover:underline"
            >
              Use the shared strategy
            </button>
          ) : null}
          {saving ? <Loader2 size={14} aria-label="Saving" className="animate-spin text-slate-400" /> : null}
        </div>
        <a
          href={strategySettingsHref(research)}
          className="flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <Settings2 size={14} aria-hidden /> Edit numbers in Settings
        </a>
      </div>
      {liveMoney ? (
        <p className="mt-2 rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-xs text-rose-200">
          LIVE money: each switch changes what the bot sends to Groww from the next candle.
        </p>
      ) : null}

      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        {group(
          "Entry",
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="sm:col-span-2">
              {segmented<"SMA" | "PATTERN">(
                "Enter on",
                (c.entry_mode ?? "SMA") as "SMA" | "PATTERN",
                ["SMA", "PATTERN"],
                { SMA: `SMA ${c.sma_fast}/${c.sma_slow} cross`, PATTERN: "Candle patterns" },
                (v) => save({ entry_mode: v }, v === "PATTERN" ? "Entry on candle patterns" : "Entry on the SMA cross")
              )}
            </div>
            {(c.entry_mode ?? "SMA") !== "PATTERN" ? (
              <div className="sm:col-span-2">
                {segmented<"1" | "2" | "3" | "5" | "10" | "15">(
                  "Candle",
                  String(c.candle_minutes ?? 1) as "1" | "2" | "3" | "5" | "10" | "15",
                  ["1", "2", "3", "5", "10", "15"],
                  { "1": "1m", "2": "2m", "3": "3m", "5": "5m", "10": "10m", "15": "15m" },
                  (v) => save({ candle_minutes: Number(v) as CandleMinutes }, `${v}-minute candles`)
                )}
              </div>
            ) : null}
            {(c.entry_mode ?? "SMA") === "PATTERN" ? (
              <div className="space-y-2 rounded-lg p-2 ring-1 ring-inset ring-white/10 sm:col-span-2">
                <div className="flex items-center gap-1 text-[11px] text-slate-400">
                  <InfoTip label="About candle-pattern entries">
                    When a candle closes: a bullish pattern buys and a bearish one sells short at the start of the next candle;
                    the trade closes at that candle&apos;s end. Each trade pays a full round of charges.
                  </InfoTip>
                  How pattern trades work
                </div>
                {segmented<"1" | "3" | "5">(
                  "Candle",
                  String(c.pattern_tf ?? 1) as "1" | "3" | "5",
                  ["1", "3", "5"],
                  { "1": "1 min", "3": "3 min", "5": "5 min" },
                  (v) => save({ pattern_tf: Number(v) as 1 | 3 | 5 }, `Pattern candle ${v} min`)
                )}
                {segmented<"STRONG" | "ALL">(
                  "Patterns",
                  ((c.pattern_set ?? "STRONG").toUpperCase() as "STRONG" | "ALL"),
                  ["STRONG", "ALL"],
                  { STRONG: "Strong only", ALL: "All" },
                  (v) => save({ pattern_set: v }, v === "STRONG" ? "Strong patterns only" : "All patterns")
                )}
                <div className="grid gap-2 sm:grid-cols-2">
                  {switchChip({
                    key: "pattern_trend",
                    label: "Only with the SMA trend",
                    detail: (cc) => `bullish only while SMA ${cc.sma_fast} > SMA ${cc.sma_slow}, bearish only below`,
                  })}
                  <div className="flex min-h-12 items-center rounded-lg px-3 py-2 text-[11px] text-slate-400 ring-1 ring-inset ring-white/10">
                    {Number(c.pattern_min_edge ?? 1.5) > 0
                      ? `Skips candles smaller than ${c.pattern_min_edge ?? 1.5}× the charges`
                      : "No charge check (set it in Settings)"}
                  </div>
                </div>
              </div>
            ) : (
              ENTRY.map(switchChip)
            )}
            {switchChip({
              key: "flip_orders",
              label: "Flip (buy ⇄ sell)",
              detail: () => "buy signals sell · sell signals buy",
            })}
          </div>
        )}
        {group(
          "Exit & stop",
          <div className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <button
                type="button"
                role="switch"
                aria-checked={stopOn}
                disabled={busy}
                onClick={toggleStop}
                className={clsx(
                  "flex min-h-12 items-start gap-2 rounded-lg px-3 py-2 text-left ring-1 ring-inset disabled:opacity-60",
                  stopOn ? "bg-emerald-500/10 ring-emerald-400/50" : "bg-amber-500/10 ring-amber-400/60"
                )}
              >
                <span
                  aria-hidden
                  className={clsx(
                    "mt-0.5 flex h-4 w-7 shrink-0 items-center rounded-full p-0.5",
                    stopOn ? "justify-end bg-emerald-500" : "justify-start bg-amber-500"
                  )}
                >
                  <span className="h-3 w-3 rounded-full bg-white" />
                </span>
                <span>
                  <span className={clsx("block text-sm font-semibold", stopOn ? "text-slate-100" : "text-amber-200")}>
                    Stop-loss {stopOn ? "ON" : "OFF"}
                  </span>
                  <span className="block text-[11px] text-slate-400">
                    {stopOn
                      ? c.stop_type === "TSL"
                        ? `TSL ₹${c.tsl_sl_points ?? 20} · trail ₹${c.tsl_trail_points ?? 10}`
                        : c.stop_type === "SMA_GAP"
                          ? `SMA gap ×${c.gap_sl_mult ?? 1} · target ×${c.gap_tp_mult ?? 2}`
                          : `${c.atr_multiplier}× ATR`
                      : "no stop order on new entries"}
                  </span>
                </span>
              </button>
              {switchChip({
                key: "cross_exit",
                label: "SMA cross exit",
                detail: () => (crossExitOn ? "opposite cross closes and reverses" : "off: a cross does not close the trade"),
                when: () => (c.entry_mode ?? "SMA") !== "PATTERN",
                on: () => crossExitOn,
              })}
              {gapExits.map(switchChip)}
            </div>
            {noExit ? (
              <p role="alert" className="rounded-md border border-amber-400/50 bg-amber-400/[0.1] px-2 py-1.5 text-xs font-semibold text-amber-200">
                No exit is on: the SMA cross exit, stop-loss, gap mode and Bollinger exit are all off, so an open trade is
                held until the {config.square_off_time} square-off.
              </p>
            ) : null}
            {stopOn
              ? segmented<StopType>("Stop type", (c.stop_type ?? "ATR") as StopType, ["ATR", "TSL", "SMA_GAP"], STOP_LABEL, (v) =>
                  save({ stop_type: v }, `Stop type ${STOP_LABEL[v]}`)
                )
              : null}
            {segmented<BbExit>("Bollinger exit", ((c.bb_exit ?? "OFF").toUpperCase() as BbExit), BB_EXITS, BB_LABEL, (v) =>
              save({ bb_exit: v }, `Bollinger exit ${BB_LABEL[v]}`)
            )}
            <p className="text-[11px] text-slate-400">
              {(c.entry_mode ?? "SMA") === "PATTERN"
                ? "Always: the trade closes at the end of its candle"
                : crossExitOn
                  ? "The opposite cross closes the trade"
                  : "An opposite cross does not close the trade"}
              {c.use_gap_mode ? ` · gap fades back to ${c.gap_exit_long ?? 0.02}% / ${c.gap_exit_short ?? -0.02}%` : ""}
              {c.use_gap_mode && Number(c.gap_giveback_pct ?? 0) > 0 ? ` or gives back ${c.gap_giveback_pct}%` : ""} · square-off{" "}
              {config.square_off_time}.
            </p>
          </div>
        )}
      </div>

      <div className="mt-4">
        {group("Entry filters (an entry must pass every one that is on)", <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{FILTERS.map(switchChip)}</div>)}
      </div>

      <p className="mt-3 text-[11px] text-slate-400">
        Limits: qty {c.qty} · max loss {inr(Number(config.max_daily_loss))} · {config.max_trades_per_day} trades/day · no
        entries after {config.entry_cutoff_time || "15:00"}.
      </p>
      {notes.length ? (
        <p className="mt-2 rounded-md border border-amber-400/40 bg-amber-400/[0.08] px-2 py-1.5 text-xs text-amber-100">
          {notes.length === 1 ? "1 setting overlaps or looks lopsided" : `${notes.length} settings overlap or look lopsided`} —{" "}
          <a href={strategySettingsHref(research)} className="font-semibold underline underline-offset-2">
            see the notes in Settings
          </a>
          .
        </p>
      ) : null}
      {msg ? (
        <p role="status" className={clsx("mt-2 text-xs", msg.ok ? "text-emerald-300" : "text-rose-300")}>
          {msg.text}
        </p>
      ) : null}
    </section>
  );
}
