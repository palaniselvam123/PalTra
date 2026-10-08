"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { BB_EXITS, smaApi, type BbExit, type SmaConfig } from "@/lib/smaApi";
import { strategyNotes } from "@/lib/strategyChecks";

type Props = {
  config: SmaConfig | null;
  onChanged: () => void;
  /** Full page width (the Settings page): two columns on wide screens and a save bar that stays in view. */
  wide?: boolean;
};

/** "" edits the shared settings; a symbol edits that stock's own. */
const ALL = "";

const LABELS: Record<string, string> = {
  qty: "qty",
  sma_fast: "fast MA",
  sma_slow: "slow MA",
  atr_period: "ATR period",
  atr_multiplier: "ATR ×",
  use_stop: "stop on/off",
  stop_type: "stop type",
  use_adx_filter: "ADX",
  adx_threshold: "ADX threshold",
  use_vwap: "VWAP",
  use_volume: "volume",
  use_density: "density",
  use_rsi: "RSI",
  use_bollinger: "Bollinger",
  bb_exit: "Bollinger exit",
  use_gap_long: "SMA gap (buy)",
  use_gap_short: "SMA gap (sell)",
  use_gap_mode: "Gap mode",
  use_candle_dir: "candle direction",
  flip_orders: "flip",
};

export function ownSummary(own: Record<string, unknown> | undefined): string {
  const keys = Object.keys(own ?? {});
  if (!keys.length) return "";
  const named = keys.map((k) => LABELS[k] ?? k.replace(/_/g, " "));
  return named.length > 4 ? `${named.slice(0, 4).join(", ")} +${named.length - 4}` : named.join(", ");
}

export function StrategyConfigPanel({ config, onChanged, wide = false }: Props) {
  const [form, setForm] = useState<SmaConfig | null>(config);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scope, setScope] = useState<string>(ALL);
  const [own, setOwn] = useState<Partial<SmaConfig>>({});
  const dirty = useRef(false);
  const [confirmClear, setConfirmClear] = useState(false);
  // Shown on the Save bar; the browser also asks before the page is left with these edits.
  const [unsaved, setUnsaved] = useState(false);
  const markDirty = (on: boolean) => {
    dirty.current = on;
    setUnsaved(on);
  };
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  const armed = (config?.trade_symbols ?? []).map((s) => s.toUpperCase());
  const withOwn = Object.keys(config?.stock_settings ?? {});
  const scopes = Array.from(new Set([...armed, ...withOwn, ...(scope ? [scope] : [])])).sort();
  const stockScope = scope !== ALL;

  useEffect(() => {
    if (!dirty.current && !stockScope) setForm(config);
  }, [config, stockScope]);

  // A stock's form shows the values the bot will use for it.
  useEffect(() => {
    if (!stockScope) {
      setOwn({});
      return;
    }
    let live = true;
    smaApi
      .stockConfig(scope)
      .then((row) => {
        if (!live) return;
        markDirty(false);
        setForm(row);
        setOwn(row.own ?? {});
      })
      .catch((e: unknown) => live && setMsg(e instanceof Error ? e.message : "Could not load that stock"));
    return () => {
      live = false;
    };
  }, [scope, stockScope]);

  if (!form) return null;

  const pick = (next: string) => {
    markDirty(false);
    setMsg(null);
    if (next === ALL) setForm(config);
    setScope(next);
  };
  const isOwn = (key: keyof SmaConfig) => stockScope && key in own;

  const set = (key: keyof SmaConfig, value: string | boolean) => {
    markDirty(true);
    setForm((prev) => {
      if (!prev) return prev;
      if (typeof value === "boolean" || typeof prev[key] === "boolean") {
        return { ...prev, [key]: Boolean(value) };
      }
      // Keep the typed text, including a trailing decimal, until Save parses it.
      return { ...prev, [key]: value };
    });
    setMsg(null);
  };

  const strategyBody = (): Partial<SmaConfig> => ({
    qty: Number(form.qty),
    sma_fast: Number(form.sma_fast),
    sma_slow: Number(form.sma_slow),
    atr_period: Number(form.atr_period),
    atr_multiplier: Number(form.atr_multiplier),
    use_adx_filter: form.use_adx_filter,
    use_stop: form.use_stop !== false,
    stop_type: form.stop_type === "SMA_GAP" || form.stop_type === "TSL" ? form.stop_type : "ATR",
    tsl_sl_points: Number(form.tsl_sl_points ?? 20),
    tsl_trail_points: Number(form.tsl_trail_points ?? 10),
    tsl_target_points: Number(form.tsl_target_points ?? 0),
    gap_sl_mult: Number(form.gap_sl_mult ?? 1),
    gap_tp_mult: Number(form.gap_tp_mult ?? 2),
    gap_min_pct: Number(form.gap_min_pct ?? 0.2),
    adx_threshold: Number(form.adx_threshold),
    use_vwap: Boolean(form.use_vwap),
    use_volume: Boolean(form.use_volume),
    volume_min_ratio: Number(form.volume_min_ratio ?? 1),
    use_density: Boolean(form.use_density),
    density_min_pct: Number(form.density_min_pct ?? 50),
    use_rsi: Boolean(form.use_rsi),
    rsi_long_min: Number(form.rsi_long_min ?? 40),
    rsi_long_max: Number(form.rsi_long_max ?? 70),
    rsi_short_min: Number(form.rsi_short_min ?? 30),
    rsi_short_max: Number(form.rsi_short_max ?? 60),
    use_bollinger: Boolean(form.use_bollinger),
    bb_period: Number(form.bb_period ?? 20),
    bb_std: Number(form.bb_std ?? 2),
    bb_min_width_pct: Number(form.bb_min_width_pct ?? 0.15),
    bb_exit: BB_EXITS.includes(form.bb_exit as BbExit) ? (form.bb_exit as BbExit) : "OFF",
    use_gap_long: Boolean(form.use_gap_long),
    gap_long_min: Number(form.gap_long_min ?? 0.02),
    gap_long_max: Number(form.gap_long_max ?? 0.5),
    use_gap_short: Boolean(form.use_gap_short),
    gap_short_min: Number(form.gap_short_min ?? -0.5),
    gap_short_max: Number(form.gap_short_max ?? -0.02),
    use_candle_dir: Boolean(form.use_candle_dir),
    candle_dir_count: Math.max(1, Math.round(Number(form.candle_dir_count ?? 2))),
    candle_dir_rule: (["CLOSES", "COLOUR", "BOTH"] as const).includes(form.candle_dir_rule as "CLOSES")
      ? (form.candle_dir_rule as "CLOSES" | "COLOUR" | "BOTH")
      : "CLOSES",
    use_gap_mode: Boolean(form.use_gap_mode),
    gap_entry_long: Number(form.gap_entry_long ?? 0.05),
    gap_exit_long: Number(form.gap_exit_long ?? 0.02),
    gap_entry_short: Number(form.gap_entry_short ?? -0.05),
    gap_exit_short: Number(form.gap_exit_short ?? -0.02),
    gap_giveback_pct: Number(form.gap_giveback_pct ?? 0),
    gap_fade_confirm_sma: Boolean(form.gap_fade_confirm_sma),
    gap_fade_min_candles: Math.max(0, Math.round(Number(form.gap_fade_min_candles ?? 0))),
    gap_fade_intrabar: Boolean(form.gap_fade_intrabar),
    flip_orders: Boolean(form.flip_orders),
    entry_mode: form.entry_mode === "PATTERN" ? "PATTERN" : "SMA",
    pattern_tf: ([1, 3, 5].includes(Number(form.pattern_tf)) ? Number(form.pattern_tf) : 1) as 1 | 3 | 5,
    pattern_trend: Boolean(form.pattern_trend),
    pattern_set: form.pattern_set === "ALL" ? "ALL" : "STRONG",
    pattern_min_edge: Math.max(0, Number(form.pattern_min_edge ?? 1.5)),
    gap_entry_delay_min: Math.round(Number(form.gap_entry_delay_min ?? 0)),
    gap_entry_window_min: Math.round(Number(form.gap_entry_window_min ?? 0)),
  });

  const saveStock = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const row = await smaApi.saveStockConfig(scope, strategyBody());
      markDirty(false);
      setForm(row);
      setOwn(row.own ?? {});
      setMsg(
        Object.keys(row.own ?? {}).length
          ? `Saved for ${scope}. Its next order uses these settings.`
          : `Saved. ${scope} matches the shared settings, so it follows them.`
      );
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const resetStock = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const row = await smaApi.resetStockConfig(scope);
      markDirty(false);
      setForm(row);
      setOwn({});
      setMsg(`Saved. ${scope} follows the shared settings again.`);
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  };

  // Every stock with its own settings back to the shared ones, one reset each, after a confirm.
  const clearAllOwn = async () => {
    setBusy(true);
    setMsg(null);
    const failed: string[] = [];
    for (const name of withOwn) {
      try {
        await smaApi.resetStockConfig(name);
      } catch {
        failed.push(name);
      }
    }
    setBusy(false);
    setConfirmClear(false);
    const done = withOwn.length - failed.length;
    setMsg(
      failed.length
        ? `Cleared ${done} of ${withOwn.length}. Not cleared: ${failed.join(", ")} — try again.`
        : `Saved. ${done} stock${done === 1 ? "" : "s"} follow the shared settings again.`
    );
    onChanged();
  };

  const save = async () => {
    if (!form) return;
    if (stockScope) return saveStock();
    const cap = Number(form.max_trades_per_day);
    if (!Number.isInteger(cap) || cap < 1 || cap > 100) {
      setMsg("Max trades / day must be a whole number from 1 to 100.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      // The chart stock is picked on the terminal; Save leaves it alone so it cannot undo a newer pick.
      await smaApi.saveConfig({
        ...strategyBody(),
        max_daily_loss: Number(form.max_daily_loss),
        max_trades_per_day: Number(form.max_trades_per_day),
        square_off_time: form.square_off_time,
        entry_cutoff_time: form.entry_cutoff_time || "15:00",
      });
      markDirty(false);
      setMsg("Saved. Press Start bot. Open positions stay open.");
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className={clsx(
        "rounded-xl border border-white/5 bg-[#151921] p-4",
        wide && "lg:p-6",
      )}
    >
      {/* Two fixed columns on a wide screen: setup, stop and entry filters on the left; Bollinger, gap and
          candle-direction rules on the right. Each block stays whole. */}
      <div className={clsx(wide && "lg:grid lg:grid-cols-2 lg:gap-x-10")}>
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Strategy & risk</div>
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-[11px] uppercase tracking-wider text-slate-400">Settings for</span>
            <select
              value={scope}
              onChange={(e) => pick(e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value={ALL}>All stocks (shared settings)</option>
              {scopes.map((name) => (
                <option key={name} value={name}>
                  {name}
                  {withOwn.includes(name) ? " — own settings" : " — uses shared"}
                </option>
              ))}
            </select>
          </label>
          {stockScope ? (
            <p className="mt-2 rounded-md border border-violet-400/25 bg-violet-400/[0.06] p-2 text-[11px] leading-snug text-slate-300">
              {Object.keys(own).length
                ? `${scope} has its own ${ownSummary(own)} (marked “own”). Everything else follows the shared settings.`
                : `${scope} uses the shared settings. Change any value below and Save to give it its own.`}{" "}
              Daily loss, trades per day, entry cut-off and square-off are for the whole account.
            </p>
          ) : withOwn.length ? (
            <p className="mt-2 text-[11px] leading-snug text-slate-400">
              Own settings:{" "}
              {withOwn.map((name, i) => (
                <span key={name}>
                  {i ? "; " : ""}
                  <button type="button" className="text-violet-300 underline-offset-2 hover:underline" onClick={() => pick(name)}>
                    {name}
                  </button>{" "}
                  ({ownSummary(config?.stock_settings?.[name] as Record<string, unknown>)})
                </span>
              ))}
              . Changing a shared value here does not change those.{" "}
              {confirmClear ? (
                <span className="mt-1 flex flex-wrap items-center gap-2 rounded-md border border-amber-400/30 bg-amber-400/[0.08] p-2 text-[11px] text-slate-200">
                  Clear the own settings of {withOwn.length} stock{withOwn.length === 1 ? "" : "s"} ({withOwn.join(", ")})? Each
                  then trades with the shared settings from its next order.
                  <button
                    type="button"
                    disabled={busy}
                    onClick={clearAllOwn}
                    className="rounded-md bg-amber-500 px-2 py-1 font-semibold text-white hover:bg-amber-400 disabled:opacity-50"
                  >
                    {busy ? "Clearing…" : "Yes, clear all"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setConfirmClear(false)}
                    className="rounded-md px-2 py-1 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmClear(true)}
                  className="mt-1 inline-flex rounded-md px-2 py-0.5 font-semibold text-rose-300 ring-1 ring-inset ring-rose-400/40 hover:bg-rose-500/10"
                >
                  Clear all own settings
                </button>
              )}
            </p>
          ) : null}
          <SectionTitle>Size and signal</SectionTitle>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field label="Quantity" own={isOwn("qty")} value={String(form.qty)} onChange={(v) => set("qty", v)} />
            <Field label="Fast MA" own={isOwn("sma_fast")} value={String(form.sma_fast)} onChange={(v) => set("sma_fast", v)} />
            <Field label="Slow MA" own={isOwn("sma_slow")} value={String(form.sma_slow)} onChange={(v) => set("sma_slow", v)} />
          </div>
          {!stockScope && (
            <>
              <SectionTitle>Daily limits (this bot)</SectionTitle>
              <div className="mt-2 grid grid-cols-2 items-end gap-2">
                <Field label="Max daily loss ₹" value={String(form.max_daily_loss)} onChange={(v) => set("max_daily_loss", v)} />
                <Field label="Max trades / day (1–100)" value={String(form.max_trades_per_day)} onChange={(v) => set("max_trades_per_day", v)} />
                <Field
                  label="No new entries after"
                  value={form.entry_cutoff_time || "15:00"}
                  onChange={(v) => set("entry_cutoff_time", v)}
                />
                <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-400">
                If the bot stopped on the trade cap, type a higher max and press Save, then Start. Open positions
                stay open. A loss-limit stop stays locked.
              </p>
            </>
          )}
          <SectionTitle>Order direction</SectionTitle>
          <label
            className={clsx(
              "mt-2 flex items-start gap-2 rounded-md p-2 text-sm text-slate-300 ring-1 ring-inset",
              form.flip_orders ? "bg-amber-400/[0.08] ring-amber-400/40" : "ring-white/10"
            )}
          >
            <input
              type="checkbox"
              checked={Boolean(form.flip_orders)}
              onChange={(e) => set("flip_orders", e.target.checked)}
              className="mt-1 accent-[#F59E0B]"
            />
            <span>
              {isOwn("flip_orders") && <OwnTag />}
              <b className="text-amber-200">Flip strategy</b> — every buy signal places a SELL order and every sell signal a
              BUY. All conditions (cross, filters, gap mode) stay the same, and the trade closes when the signal&apos;s trade
              would (opposite cross, gap fade, Bollinger exit, square-off). The stop-loss and target guard the real, flipped
              position. Works in PAPER, LIVE, Research and Replay — try it on Replay or Research first.
            </span>
          </label>
          <SectionTitle>Stop-loss</SectionTitle>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.use_stop !== false}
              onChange={(e) => set("use_stop", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_stop") && <OwnTag />}
            {form.stop_type === "SMA_GAP"
              ? "Stop-loss on new entries (moving, from the SMA gap). Uncheck to enter with no stop."
              : form.stop_type === "TSL"
                ? "Trailing stop-loss on new entries. Uncheck to enter with no stop order."
                : `Exchange stop-loss at ${form.atr_multiplier}× ATR. Uncheck to enter with no stop order.`}
          </label>
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-[11px] uppercase tracking-wider text-slate-400">
              Stop type{isOwn("stop_type") && <OwnTag />}
            </span>
            <select
              value={form.stop_type === "SMA_GAP" || form.stop_type === "TSL" ? form.stop_type : "ATR"}
              onChange={(e) => set("stop_type", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="ATR">ATR — fixed stop at {String(form.atr_multiplier)}× ATR</option>
              <option value="SMA_GAP">SMA gap — moving stop + target</option>
              <option value="TSL">Trailing stop (TSL) — ₹ steps, like Groww</option>
            </select>
          </label>
          {form.stop_type !== "TSL" ? (
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <Field label="ATR period" own={isOwn("atr_period")} value={String(form.atr_period)} onChange={(v) => set("atr_period", v)} />
              <Field label="ATR stop ×" own={isOwn("atr_multiplier")} value={String(form.atr_multiplier)} onChange={(v) => set("atr_multiplier", v)} />
            </div>
          ) : null}
          {form.stop_type === "SMA_GAP" ? (
            <div className="mt-2 rounded-md border border-sky-400/20 bg-sky-400/[0.04] p-2">
              <div className="grid grid-cols-3 items-end gap-2">
                <Field label="Stop × gap" value={String(form.gap_sl_mult ?? 1)} onChange={(v) => set("gap_sl_mult", v)} own={isOwn("gap_sl_mult")} />
                <Field label="Target × gap" value={String(form.gap_tp_mult ?? 2)} onChange={(v) => set("gap_tp_mult", v)} own={isOwn("gap_tp_mult")} />
                <Field label="Min gap %" value={String(form.gap_min_pct ?? 0.2)} onChange={(v) => set("gap_min_pct", v)} own={isOwn("gap_min_pct")} />
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-400">
                Gap % = SMA 9 vs SMA 21 on the last closed candle (at least the min gap). A buy gets stop = price −
                gap × stop multiple and target = price + gap × target multiple; a sell is the mirror. Recalculated every
                closed 1-minute candle: the stop only moves in your favour, the target follows the gap both ways.
                PAPER only — in LIVE the bot keeps the {String(form.atr_multiplier)}× ATR exchange stop.
              </p>
            </div>
          ) : null}
          {form.stop_type === "TSL" ? (
            <div className="mt-2 rounded-md border border-emerald-400/20 bg-emerald-400/[0.04] p-2">
              <div className="grid grid-cols-3 items-end gap-2">
                <Field label="Stop ₹" value={String(form.tsl_sl_points ?? 20)} onChange={(v) => set("tsl_sl_points", v)} own={isOwn("tsl_sl_points")} />
                <Field label="Trail every ₹" value={String(form.tsl_trail_points ?? 10)} onChange={(v) => set("tsl_trail_points", v)} own={isOwn("tsl_trail_points")} />
                <Field label="Target ₹" value={String(form.tsl_target_points ?? 0)} onChange={(v) => set("tsl_target_points", v)} own={isOwn("tsl_target_points")} />
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-400">
                Stop ₹ is the distance from your entry; Target ₹ 0 means no target. A buy at ₹1,000 with stop ₹
                {String(form.tsl_sl_points ?? 20)} starts its stop at ₹
                {(1000 - Number(form.tsl_sl_points ?? 20)).toLocaleString("en-IN")}. Each ₹{String(form.tsl_trail_points ?? 10)} the
                price gains past its best so far moves the stop up ₹{String(form.tsl_trail_points ?? 10)}; it never moves back. A
                sell is the mirror. {Number(form.tsl_target_points ?? 0) > 0
                  ? `The trade also closes at ₹${String(form.tsl_target_points)} profit per share.`
                  : "No target: the trailing stop, an opposite cross or square-off closes the trade."}{" "}
                In LIVE the bot moves your Groww stop order in place, so the position always has a stop.
              </p>
            </div>
          ) : null}
          <SectionTitle>Entry filters</SectionTitle>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            These apply only when checked, on a crossover. Force order skips them. An unchecked box is ignored. A
            close still happens on the opposite cross.
          </p>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.use_adx_filter}
              onChange={(e) => set("use_adx_filter", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_adx_filter") && <OwnTag />}
            ADX trend filter (block entries when ADX is below the threshold)
          </label>
          <Field label="ADX threshold" own={isOwn("adx_threshold")} value={String(form.adx_threshold)} onChange={(v) => set("adx_threshold", v)} />
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_vwap)}
              onChange={(e) => set("use_vwap", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_vwap") && <OwnTag />}
            VWAP. Buy at or above today’s VWAP. Sell at or below it.
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_volume)}
              onChange={(e) => set("use_volume", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_volume") && <OwnTag />}
            Volume. The closed candle must be at least this multiple of the previous 20 candles.
          </label>
          <Field
            label="Volume multiple"
            value={String(form.volume_min_ratio ?? 1)}
            onChange={(v) => set("volume_min_ratio", v)} own={isOwn("volume_min_ratio")}
          />
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_density)}
              onChange={(e) => set("use_density", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_density") && <OwnTag />}
            Density. The candle body must cover at least this percent of its high-to-low range.
          </label>
          <Field
            label="Density %"
            value={String(form.density_min_pct ?? 50)}
            onChange={(v) => set("density_min_pct", v)} own={isOwn("density_min_pct")}
          />
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_rsi)}
              onChange={(e) => set("use_rsi", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_rsi") && <OwnTag />}
            RSI(14). A buy must sit in the buy range. A sell must sit in the sell range.
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Buy RSI from" value={String(form.rsi_long_min ?? 40)} onChange={(v) => set("rsi_long_min", v)} own={isOwn("rsi_long_min")} />
            <Field label="Buy RSI to" value={String(form.rsi_long_max ?? 70)} onChange={(v) => set("rsi_long_max", v)} own={isOwn("rsi_long_max")} />
            <Field label="Sell RSI from" value={String(form.rsi_short_min ?? 30)} onChange={(v) => set("rsi_short_min", v)} own={isOwn("rsi_short_min")} />
            <Field label="Sell RSI to" value={String(form.rsi_short_max ?? 60)} onChange={(v) => set("rsi_short_max", v)} own={isOwn("rsi_short_max")} />
          </div>
        </div>
        <div className="min-w-0">
          <SectionTitle>Entry filters (more)</SectionTitle>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_bollinger)}
              onChange={(e) => set("use_bollinger", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_bollinger") && <OwnTag />}
            Bollinger Bands. Skip a buy that closed above the upper band (or a sell below the lower): it is chasing a
            spike. Skip any cross while the bands are squeezed: the stock is going sideways.
          </label>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field label="Period" value={String(form.bb_period ?? 20)} onChange={(v) => set("bb_period", v)} own={isOwn("bb_period")} />
            <Field label="Width (σ)" value={String(form.bb_std ?? 2)} onChange={(v) => set("bb_std", v)} own={isOwn("bb_std")} />
            <Field label="Squeeze below %" value={String(form.bb_min_width_pct ?? 0.15)} onChange={(v) => set("bb_min_width_pct", v)} own={isOwn("bb_min_width_pct")} />
          </div>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            Squeeze is the band width as % of price on 1-minute candles; 0 turns the squeeze check off.
          </p>
          <div className="mt-4 text-sm text-slate-300">
            SMA 9/21 gap range. Gap % = (SMA 9 − SMA 21) ÷ SMA 21 × 100 on the cross candle: positive when SMA 9 is
            above, negative when below. Tick the side(s) to check; an unticked side trades as before.
          </div>
          <div className="mt-2 grid items-end gap-2 sm:grid-cols-2">
            <div className="rounded-md border border-white/10 p-2">
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={Boolean(form.use_gap_long)}
                  onChange={(e) => set("use_gap_long", e.target.checked)}
                  className="accent-[#10B981]"
                />
                {isOwn("use_gap_long") && <OwnTag />}
                Buy (LONG)
              </label>
              <div className="mt-2 grid grid-cols-2 items-end gap-2">
                <Field label="Min %" signed value={String(form.gap_long_min ?? 0.02)} onChange={(v) => set("gap_long_min", v)} own={isOwn("gap_long_min")} />
                <Field label="Max %" signed value={String(form.gap_long_max ?? 0.5)} onChange={(v) => set("gap_long_max", v)} own={isOwn("gap_long_max")} />
              </div>
            </div>
            <div className="rounded-md border border-white/10 p-2">
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={Boolean(form.use_gap_short)}
                  onChange={(e) => set("use_gap_short", e.target.checked)}
                  className="accent-[#10B981]"
                />
                {isOwn("use_gap_short") && <OwnTag />}
                Sell (SHORT)
              </label>
              <div className="mt-2 grid grid-cols-2 items-end gap-2">
                <Field label="Min %" signed value={String(form.gap_short_min ?? -0.5)} onChange={(v) => set("gap_short_min", v)} own={isOwn("gap_short_min")} />
                <Field label="Max %" signed value={String(form.gap_short_max ?? -0.02)} onChange={(v) => set("gap_short_max", v)} own={isOwn("gap_short_max")} />
              </div>
            </div>
          </div>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            Negative numbers are allowed (a sell gap is usually negative, e.g. −0.5 to −0.02). On the cross candle the
            gap is usually small, so a tight range skips most crosses; skipped crosses show as ✕ on the chart with the
            gap. Force order skips this check.
          </p>
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_candle_dir)}
              onChange={(e) => set("use_candle_dir", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_candle_dir") && <OwnTag />}
            Candle direction — the candles before the entry must move the trade&apos;s way.
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Last candles to check (1–10)"
              value={String(form.candle_dir_count ?? 2)}
              onChange={(v) => set("candle_dir_count", v)}
              own={isOwn("candle_dir_count")}
            />
            <label className="block text-sm text-slate-300">
              <span className="text-[11px] uppercase tracking-wider text-slate-400">
                Rule{isOwn("candle_dir_rule") && <OwnTag />}
              </span>
              <select
                value={form.candle_dir_rule ?? "CLOSES"}
                onChange={(e) => set("candle_dir_rule", e.target.value)}
                className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
              >
                <option value="CLOSES">Closes rising (buy) / falling (sell)</option>
                <option value="COLOUR">Colour green (buy) / red (sell)</option>
                <option value="BOTH">Both</option>
              </select>
            </label>
          </div>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            Checked on the closed candles just before the order: on the cross candle normally, or in gap mode on the
            candle where the gap reaches the entry level. With 2 and Closes, a buy needs the last two closes to have
            each risen (e.g. 100.0 → 100.4 → 100.9); a sell needs them each to have fallen. Refused crosses show as ✕
            “Candles” on the chart. Force order skips this check.
          </p>
          <SectionTitle>Exits</SectionTitle>
          <label className="mt-2 block text-sm text-slate-300">
            <span className="text-[11px] uppercase tracking-wider text-slate-400">
              Bollinger exit{isOwn("bb_exit") && <OwnTag />}
            </span>
            <select
              value={BB_EXITS.includes(form.bb_exit as BbExit) ? form.bb_exit : "OFF"}
              onChange={(e) => set("bb_exit", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="OFF">Off — exit only on the stop, target, cross or square-off</option>
              <option value="BAND">Band target — book profit when a candle closes at the far band</option>
              <option value="MIDDLE">Middle band — exit when a candle closes back across the middle</option>
              <option value="BOTH">Both — band target or middle band, whichever comes first</option>
            </select>
          </label>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            Read once per closed 1-minute candle after the entry, on the Period and Width above (the bands show on the
            chart). The middle-band exit waits until a candle has closed on the trade&apos;s side of the middle first.
            Your stop keeps working; in LIVE the exchange stop is cancelled just before the exit is sent.
          </p>
          <SectionTitle>How a trade opens</SectionTitle>
          <div className="mt-2 rounded-md p-2 ring-1 ring-inset ring-white/10">
            <div className="text-sm text-slate-300">
              Entry — what opens a trade{isOwn("entry_mode") && <OwnTag />}
            </div>
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <label className="block text-sm text-slate-300">
                <span className="text-[11px] uppercase tracking-wider text-slate-400">Enter on</span>
                <select
                  value={form.entry_mode ?? "SMA"}
                  onChange={(e) => set("entry_mode", e.target.value)}
                  className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                >
                  <option value="SMA">SMA cross</option>
                  <option value="PATTERN">Candle patterns</option>
                </select>
              </label>
              <label className="block text-sm text-slate-300">
                <span className="text-[11px] uppercase tracking-wider text-slate-400">Pattern candle{isOwn("pattern_tf") ? " (own)" : ""}</span>
                <select
                  value={String(form.pattern_tf ?? 1)}
                  onChange={(e) => set("pattern_tf", e.target.value)}
                  className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                >
                  <option value="1">1 minute</option>
                  <option value="3">3 minutes</option>
                  <option value="5">5 minutes</option>
                </select>
              </label>
              <label className="block text-sm text-slate-300">
                <span className="text-[11px] uppercase tracking-wider text-slate-400">Patterns{isOwn("pattern_set") ? " (own)" : ""}</span>
                <select
                  value={form.pattern_set ?? "STRONG"}
                  onChange={(e) => set("pattern_set", e.target.value)}
                  className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                >
                  <option value="STRONG">Strong only (engulfing, stars, three soldiers/crows, piercing/dark cloud, marubozu)</option>
                  <option value="ALL">All (adds hammer, shooting star, harami, tweezers, dragonfly/gravestone doji)</option>
                </select>
              </label>
              <Field
                label="Skip candles under N× the charges (0 = off)"
                value={String(form.pattern_min_edge ?? 1.5)}
                onChange={(v) => set("pattern_min_edge", v)}
                own={isOwn("pattern_min_edge")}
              />
            </div>
            <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={Boolean(form.pattern_trend)}
                onChange={(e) => set("pattern_trend", e.target.checked)}
                className="mt-1 accent-[#10B981]"
              />
              <span>
                {isOwn("pattern_trend") && <OwnTag />}
                Only with the SMA trend — a bullish pattern only while SMA {form.sma_fast ?? 9} is above SMA{" "}
                {form.sma_slow ?? 21}, a bearish one only below.
              </span>
            </label>
            <p className="mt-1 text-[11px] leading-snug text-slate-400">
              With Candle patterns, each time a candle of this size closes its pattern is read (the names in the chart&apos;s
              data table). A bullish pattern buys and a bearish one sells short at the start of the next candle; the trade
              closes at the end of that candle (exit “end of the pattern candle”), then the next candle is judged afresh.
              Entry filters, the stop, the flip, market hours, the cut-off and the caps still apply; gap mode does not. Each
              trade pays a full round of charges, so the charge check skips candles that usually move less than N× those
              charges for this quantity. Try it in Replay first.
            </p>
          </div>
          <SectionTitle>SMA gap mode</SectionTitle>
          {form.entry_mode === "PATTERN" ? (
            <p className="mt-1 text-[11px] leading-snug text-amber-300">
              Not used while “Enter on” is Candle patterns: pattern trades open at the candle start and close at its end.
            </p>
          ) : null}
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_gap_mode)}
              onChange={(e) => set("use_gap_mode", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_gap_mode") && <OwnTag />}
            SMA gap mode — enter when the gap widens past a level, exit when it fades back.
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Buy: enter at gap ≥ %" signed value={String(form.gap_entry_long ?? 0.05)} onChange={(v) => set("gap_entry_long", v)} own={isOwn("gap_entry_long")} />
            <Field label="Buy: exit at gap ≤ %" signed value={String(form.gap_exit_long ?? 0.02)} onChange={(v) => set("gap_exit_long", v)} own={isOwn("gap_exit_long")} />
            <Field label="Sell: enter at gap ≤ %" signed value={String(form.gap_entry_short ?? -0.05)} onChange={(v) => set("gap_entry_short", v)} own={isOwn("gap_entry_short")} />
            <Field label="Sell: exit at gap ≥ %" signed value={String(form.gap_exit_short ?? -0.02)} onChange={(v) => set("gap_exit_short", v)} own={isOwn("gap_exit_short")} />
            <Field label="Also exit after giving back % of peak (0 = off)" value={String(form.gap_giveback_pct ?? 0)} onChange={(v) => set("gap_giveback_pct", v)} own={isOwn("gap_giveback_pct")} />
            <Field label="Wait after the level is met (min)" value={String(form.gap_entry_delay_min ?? 0)} onChange={(v) => set("gap_entry_delay_min", v)} own={isOwn("gap_entry_delay_min")} />
            <Field label="Give up after the cross (min, 0 = never)" value={String(form.gap_entry_window_min ?? 0)} onChange={(v) => set("gap_entry_window_min", v)} own={isOwn("gap_entry_window_min")} />
            <Field
              label="Fade exit: gap narrowing for N candles in a row (0 = off)"
              value={String(form.gap_fade_min_candles ?? 0)}
              onChange={(v) => set("gap_fade_min_candles", v)}
              own={isOwn("gap_fade_min_candles")}
            />
          </div>
          {strategyNotes(form).length ? (
            <ul aria-label="Setting notes" className="mt-2 space-y-1.5">
              {strategyNotes(form).map((note) => (
                <li
                  key={note.id}
                  className="rounded-md border border-amber-400/40 bg-amber-400/[0.08] p-2 text-[12px] leading-snug text-amber-100"
                >
                  {note.text}
                  {note.fix ? (
                    <button
                      type="button"
                      onClick={() => {
                        for (const [key, value] of Object.entries(note.fix!.values)) {
                          set(key as keyof SmaConfig, String(value));
                        }
                        setMsg("Changed — press Save to keep it.");
                      }}
                      className="ml-2 rounded px-2 py-0.5 text-[11px] font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-400/15"
                    >
                      {note.fix.label}
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.gap_fade_confirm_sma)}
              onChange={(e) => set("gap_fade_confirm_sma", e.target.checked)}
              className="mt-1 accent-[#10B981]"
            />
            <span>
              {isOwn("gap_fade_confirm_sma") && <OwnTag />}
              Ride out pullbacks — exit on a fade only when a candle also closes on the wrong side of SMA {form.sma_slow ?? 21}{" "}
              (below it for a buy, above it for a sell).
            </span>
          </label>
          <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.gap_fade_intrabar)}
              onChange={(e) => set("gap_fade_intrabar", e.target.checked)}
              className="mt-1 accent-[#10B981]"
            />
            <span>
              {isOwn("gap_fade_intrabar") && <OwnTag />}
              Check the fade every second — judge the exit on the live price, as if that second closed the candle, instead of
              waiting for the minute to close. Faster on a sharp reversal; a candle that turns back inside the minute can
              exit too early. Entries still wait for the candle to close. With “1s OFF” in the status bar it checks at the
              slower quote pace (every few seconds).
            </span>
          </label>
          <p className="mt-1 text-[11px] leading-snug text-slate-400">
            A cross only arms the trade; the order goes on the first closed candle whose gap reaches the entry level
            (plus the wait, if set — the gap must still be there). While the gap keeps widening the trade is held; once it
            has cleared the exit level and fades back to it (or gives back the set share of its widest), the trade closes
            (exit reason “Gap fade”). With “Ride out pullbacks”, a narrowing that keeps the candles on the trade&apos;s side of
            SMA {form.sma_slow ?? 21} is treated as a pullback and held; the exit waits for a close through it. An opposite cross still closes at once, and the reverse waits for its own gap. The
            stop, filters, entry cut-off and square-off still apply. An exit level beyond the entry level (e.g. sell in at
            −0.08, out at −0.39) is a lock-in level: the gap exit waits until the gap has been that wide, then closes when it
            comes back to it; a trade whose gap never gets that wide is left to the stop, the opposite cross and
            square-off.
          </p>
        </div>
      </div>
      <div
        className={clsx(
          "mt-3 flex flex-wrap items-center gap-3",
          wide && "sticky bottom-0 -mx-4 border-t border-white/10 bg-[#151921] px-4 py-3 lg:-mx-6 lg:px-6",
        )}
      >
        <button
          disabled={busy}
          onClick={save}
          className={clsx(
            "rounded-md px-3 py-1.5 text-sm font-medium",
            unsaved ? "bg-emerald-500 text-white hover:bg-emerald-400" : "bg-white/10 text-slate-100 hover:bg-white/15"
          )}
        >
          {stockScope ? `Save for ${scope}` : "Save"}
        </button>
        {unsaved && !busy ? (
          <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-[11px] font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/30">
            Unsaved changes
          </span>
        ) : null}
        {stockScope && Object.keys(own).length > 0 && (
          <button
            disabled={busy}
            onClick={resetStock}
            className="rounded-md px-3 py-1.5 text-sm text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            Use shared settings
          </button>
        )}
        {msg && (
          <span role="status" className={msg.startsWith("Saved") ? "text-xs text-slate-400" : "text-xs font-semibold text-amber-300"}>
            {msg}
          </span>
        )}
      </div>
    </section>
  );
}

function OwnTag() {
  return <span className="ml-1 shrink-0 text-[11px] normal-case tracking-normal text-violet-300">· own</span>;
}

function Field({
  label,
  value,
  onChange,
  own,
  signed,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** This stock sets the value itself rather than using the shared one. */
  own?: boolean;
  /** Allows a minus sign: phones' decimal keypads have none. */
  signed?: boolean;
}) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(value);
  }, [value]);
  return (
    <label className="block">
      <span className="text-[11px] uppercase leading-tight tracking-wider text-slate-400">
        {label}
        {own && <span className="ml-1 whitespace-nowrap normal-case tracking-normal text-violet-300">· own</span>}
      </span>
      <input
        inputMode={signed ? "text" : "decimal"}
        value={text}
        onFocus={() => {
          focused.current = true;
        }}
        onBlur={() => {
          focused.current = false;
        }}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value);
        }}
        className={clsx(
          "mt-0.5 w-full min-w-[4.5rem] rounded-md border bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none focus:border-[#10B981]/50",
          own ? "border-violet-400/50" : "border-white/10"
        )}
      />
    </label>
  );
}

/** A small heading that splits the settings into groups. */
function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="mt-5 border-t border-white/10 pt-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-300 first:mt-3">
      {children}
    </div>
  );
}
