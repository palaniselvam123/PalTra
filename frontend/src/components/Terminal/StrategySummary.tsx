"use client";

import clsx from "clsx";
import { Settings2 } from "lucide-react";
import { inr, type SmaConfig } from "@/lib/smaApi";
import { strategyNotes } from "@/lib/strategyChecks";
import { bbExitText, stopShort, type Settings } from "./BacktestRuns";
import { ownSummary } from "./StrategyConfigPanel";

/** The settings page section for this desk's strategy. */
export function strategySettingsHref(research: boolean): string {
  return research ? "/settings/?desk=research#sma-strategy" : "/settings/#sma-strategy";
}

function filters(c: SmaConfig): string[] {
  const out: string[] = [];
  if (c.use_adx_filter) out.push(`ADX ≥ ${c.adx_threshold}`);
  if (c.use_vwap) out.push("VWAP side");
  if (c.use_volume) out.push(`volume ≥ ${c.volume_min_ratio}× avg`);
  if (c.use_density) out.push(`candle body ≥ ${c.density_min_pct}%`);
  if (c.use_rsi) out.push(`RSI buy ${c.rsi_long_min}–${c.rsi_long_max}, sell ${c.rsi_short_min}–${c.rsi_short_max}`);
  if (c.use_bollinger) out.push(`Bollinger width ≥ ${c.bb_min_width_pct}%`);
  if (c.use_gap_long) out.push(`buy gap ${c.gap_long_min}–${c.gap_long_max}%`);
  if (c.use_gap_short) out.push(`sell gap ${c.gap_short_min}–${c.gap_short_max}%`);
  if (c.use_candle_dir) {
    const rule = c.candle_dir_rule === "COLOUR" ? "green / red" : c.candle_dir_rule === "BOTH" ? "rising and green / falling and red" : "closes rising / falling";
    out.push(`last ${c.candle_dir_count ?? 2} candles ${rule}`);
  }
  return out;
}

/**
 * The strategy in plain words: how the bot enters, what it filters, how it
 * exits and the day's limits. Read-only; the settings live on the Settings page.
 */
export function StrategySummary({ config, research = false }: { config: SmaConfig | null; research?: boolean }) {
  if (!config) return null;
  const c = config;
  const s = c as unknown as Settings;
  const gap = Boolean(c.use_gap_mode);
  const entry = gap
    ? [
        `SMA ${c.sma_fast}/${c.sma_slow} cross on closed 1-minute candles arms the trade`,
        `buy when the gap reaches ≥ ${c.gap_entry_long ?? 0.05}%, sell when ≤ ${c.gap_entry_short ?? -0.05}%`,
        ...(Number(c.gap_entry_delay_min ?? 0) > 0 ? [`then wait ${c.gap_entry_delay_min} min`] : []),
        ...(Number(c.gap_entry_window_min ?? 0) > 0 ? [`give up after ${c.gap_entry_window_min} min`] : []),
      ]
    : [`SMA ${c.sma_fast}/${c.sma_slow} cross on closed 1-minute candles: buy above, sell below`];
  const exits = [
    "opposite cross",
    ...(gap ? [`gap fades back to ${c.gap_exit_long ?? 0.02}% (buy) / ${c.gap_exit_short ?? -0.02}% (sell)`] : []),
    ...(gap && Number(c.gap_giveback_pct ?? 0) > 0 ? [`gap gives back ${c.gap_giveback_pct}% of its widest`] : []),
    ...(gap && Number(c.gap_fade_min_candles ?? 0) > 0 ? [`only after ${c.gap_fade_min_candles} narrowing candles`] : []),
    ...(gap && c.gap_fade_confirm_sma ? [`only once a candle closes past SMA ${c.sma_slow}`] : []),
    ...(gap && c.gap_fade_intrabar ? ["fade checked every second"] : []),
    ...(bbExitText(s) ? [`Bollinger ${bbExitText(s)}`] : []),
    `square-off ${c.square_off_time}`,
  ];
  const own = Object.entries(c.stock_settings ?? {}).filter(([, v]) => v && Object.keys(v).length);
  const notes = strategyNotes(c);
  const rows: [string, string, string?][] = [
    ["Entry", entry.join(" · ")],
    ["Filters", filters(c).join(" · ") || "none"],
    ["Exit", exits.join(" · ")],
    ["Stop", c.use_stop === false ? "OFF — no stop order" : stopShort(s), c.use_stop === false ? "text-amber-300 font-semibold" : undefined],
    ...(c.flip_orders ? [["Flip", "ON ⇄ — buy signals sell, sell signals buy", "text-amber-300 font-semibold"] as [string, string, string]] : []),
    [
      "Limits",
      `qty ${c.qty} · max loss ${inr(Number(c.max_daily_loss))} · ${c.max_trades_per_day} trades/day · no entries after ${c.entry_cutoff_time || "15:00"}`,
    ],
  ];

  return (
    <section aria-label="Strategy" className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-[0.14em] text-slate-400">
          Strategy{research ? " · research desk" : ""}
        </div>
        <a
          href={strategySettingsHref(research)}
          className="flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <Settings2 size={14} aria-hidden /> Edit in Settings
        </a>
      </div>
      <dl className="mt-3 grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2 text-sm">
        {rows.map(([label, value, tone]) => (
          <div key={label} className="contents">
            <dt className="text-[11px] uppercase tracking-wider text-slate-400">{label}</dt>
            <dd className={clsx("leading-snug text-slate-200", tone)}>{value}</dd>
          </div>
        ))}
        {own.length ? (
          <div className="contents">
            <dt className="text-[11px] uppercase tracking-wider text-slate-400">Own</dt>
            <dd className="leading-snug text-slate-300">
              {own.map(([name, v]) => `${name}: ${ownSummary(v as Record<string, unknown>)}`).join(" · ")}
            </dd>
          </div>
        ) : null}
      </dl>
      {notes.length ? (
        <p className="mt-3 rounded-md border border-amber-400/40 bg-amber-400/[0.08] px-2 py-1.5 text-xs text-amber-100">
          {notes.length === 1 ? "1 setting overlaps or looks lopsided" : `${notes.length} settings overlap or look lopsided`} —{" "}
          <a href={strategySettingsHref(research)} className="font-semibold underline underline-offset-2">
            see the notes in Settings
          </a>
          .
        </p>
      ) : null}
    </section>
  );
}
