"use client";

import { InfoTip } from "@/components/ui/InfoTip";
import { useEffect, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { BB_EXITS, CANDLE_MINUTES, smaApi, type BbExit, type CandleMinutes, type SmaConfig } from "@/lib/smaApi";
import { crossExitOn, noExitOn, strategyNotes } from "@/lib/strategyChecks";

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
  candle_minutes: "candle",
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
  cross_exit: "cross exit",
  flip_orders: "flip",
  review_on: "1-min review",
  review_gap_pct: "review band",
  review_cooldown_min: "review cooldown",
  review_check_minutes: "review check candle",
  review_check_mode: "review check mode",
  review_default_answer: "review default",
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
  const squareOff = form.square_off_time || config?.square_off_time || "15:15";
  // The no-exit warning sits with the exits, the rest with gap mode.
  const gapNotes = strategyNotes(form).filter((n) => n.id !== "no-exit");

  const set = (key: keyof SmaConfig, value: string | boolean | number) => {
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
    candle_minutes: (CANDLE_MINUTES.includes(Number(form.candle_minutes) as CandleMinutes)
      ? Number(form.candle_minutes)
      : 1) as CandleMinutes,
    atr_period: Number(form.atr_period),
    atr_multiplier: Number(form.atr_multiplier),
    use_adx_filter: form.use_adx_filter,
    use_stop: form.use_stop !== false,
    stop_type: form.stop_type === "SMA_GAP" || form.stop_type === "TSL" ? form.stop_type : "ATR",
    tsl_sl_points: Number(form.tsl_sl_points ?? 20),
    tsl_trail_points: Number(form.tsl_trail_points ?? 10),
    tsl_target_points: Number(form.tsl_target_points ?? 0),
    tsl_mode: String(form.tsl_mode ?? "POINTS") as "POINTS" | "PERCENT",
    tsl_sl_pct: Number(form.tsl_sl_pct ?? 1.0),
    tsl_trail_pct: Number(form.tsl_trail_pct ?? 0.5),
    tsl_target_pct: Number(form.tsl_target_pct ?? 0),
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
    cross_exit: crossExitOn(form),
    flip_orders: Boolean(form.flip_orders),
    entry_mode: form.entry_mode === "PATTERN" ? "PATTERN" : "SMA",
    pattern_tf: ([1, 3, 5].includes(Number(form.pattern_tf)) ? Number(form.pattern_tf) : 1) as 1 | 3 | 5,
    pattern_trend: Boolean(form.pattern_trend),
    pattern_set: form.pattern_set === "ALL" ? "ALL" : "STRONG",
    pattern_min_edge: Math.max(0, Number(form.pattern_min_edge ?? 1.5)),
    gap_entry_delay_min: Math.round(Number(form.gap_entry_delay_min ?? 0)),
    gap_entry_window_min: Math.round(Number(form.gap_entry_window_min ?? 0)),
    review_on: Boolean(form.review_on),
    review_gap_pct: Number(form.review_gap_pct ?? 0.03),
    review_cooldown_min: Math.max(0, Math.round(Number(form.review_cooldown_min ?? 15))),
    review_check_minutes: Number(form.review_check_minutes ?? 1),
    review_check_mode: String(form.review_check_mode ?? "ALWAYS") as "ALWAYS" | "ONLY_IF_AGAINST" | "ONLY_IF_NOT_WITH",
    review_default_answer: String(form.review_default_answer ?? "PROMPT") as "PROMPT" | "EXIT" | "CONTINUE",
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
      <div className="min-w-0">
          <div className="text-xs uppercase tracking-[0.14em] text-slate-400">Strategy & risk</div>
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-wider text-slate-400">Settings for</span>
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
            <p className="mt-2 rounded-md border border-violet-400/25 bg-violet-400/[0.06] p-2 text-xs leading-snug text-slate-300">
              {Object.keys(own).length
                ? `${scope} has its own ${ownSummary(own)} (marked “own”). Everything else follows the shared settings.`
                : `${scope} uses the shared settings. Change any value below and Save to give it its own.`}{" "}
              Daily loss, trades per day, entry cut-off and square-off are for the whole account.
            </p>
          ) : withOwn.length ? (
            <p className="mt-2 text-xs leading-snug text-slate-400">
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
                <span className="mt-1 flex flex-wrap items-center gap-2 rounded-md border border-amber-400/30 bg-amber-400/[0.08] p-2 text-xs text-slate-200">
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
      </div>
      <FieldGroup title="Entry signal" hint="Moving averages, candle, how a trade opens, order direction, SMA gap mode" wide={wide}>
        <div className="min-w-0">
          <SectionTitle>Moving averages</SectionTitle>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Fast MA" own={isOwn("sma_fast")} value={String(form.sma_fast)} onChange={(v) => set("sma_fast", v)} />
            <Field label="Slow MA" own={isOwn("sma_slow")} value={String(form.sma_slow)} onChange={(v) => set("sma_slow", v)} />
          </div>
          <div className="mt-2 flex items-center gap-1 text-xs text-slate-300">
            <label htmlFor="sma-candle-interval">Candle interval{isOwn("candle_minutes") ? " (own)" : ""}</label>
            <InfoTip label="About the candle interval">
              The SMAs, ATR stop, filters and exits read candles of this length, built from the 1-minute tape from 09:15.
              A signal is judged when each candle closes. Candle patterns keep their own candle.
            </InfoTip>
          </div>
          <label className="block text-sm text-slate-300">
            <select
              id="sma-candle-interval"
              value={String(form.candle_minutes ?? 1)}
              onChange={(e) => set("candle_minutes", e.target.value)}
              className="mt-1 block h-10 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
            >
              {CANDLE_MINUTES.map((m) => (
                <option key={m} value={String(m)}>
                  {m} minute{m === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </label>
          <SectionTitle>How a trade opens</SectionTitle>
          <div className="mt-2 rounded-md p-2 ring-1 ring-inset ring-white/10">
            <div className="flex items-center gap-1 text-sm text-slate-300">
              Entry — what opens a trade{isOwn("entry_mode") && <OwnTag />}
              <InfoTip label="About candle-pattern entries">
                SMA cross: the 9/21 crossover opens trades. Candle patterns: each time a candle of the chosen size closes its
                pattern is read (the names in the chart&apos;s data table). A bullish pattern buys and a bearish one sells short
                at the start of the next candle; the trade closes at the end of that candle (exit “end of the pattern
                candle”), then the next candle is judged afresh. Entry filters, the stop, the flip, market hours, the cut-off
                and the caps still apply; gap mode does not. Each trade pays a full round of charges, so the charge check
                skips candles that usually move less than N× those charges for this quantity. Try it in Replay first.
              </InfoTip>
            </div>
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <label className="block text-sm text-slate-300">
                <span className="text-xs uppercase tracking-wider text-slate-400">Enter on</span>
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
                <span className="text-xs uppercase tracking-wider text-slate-400">Pattern candle{isOwn("pattern_tf") ? " (own)" : ""}</span>
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
                <span className="text-xs uppercase tracking-wider text-slate-400">Patterns{isOwn("pattern_set") ? " (own)" : ""}</span>
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
                Only with the SMA trend{" "}
                <InfoTip label="About trading patterns with the trend">
                  A bullish pattern trades only while SMA {form.sma_fast ?? 9} is above SMA {form.sma_slow ?? 21}, a bearish one
                  only below.
                </InfoTip>
              </span>
            </label>
          </div>
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
              <b className="text-amber-200">Flip strategy</b> — buy signals sell, sell signals buy{" "}
              <InfoTip label="About the flip strategy">
                Every buy signal places a SELL order and every sell signal a BUY. All conditions (cross, filters, gap mode) stay
                the same, and the trade closes when the signal&apos;s trade would (opposite cross, gap fade, Bollinger exit,
                square-off). The stop-loss and target guard the real, flipped position. Works in PAPER, LIVE, Research and Replay
                — try it on Replay or Research first.
              </InfoTip>
            </span>
          </label>
        </div>
        <div className="min-w-0">
          <SectionTitle>SMA gap mode</SectionTitle>
          {form.entry_mode === "PATTERN" ? (
            <p className="mt-1 text-xs leading-snug text-amber-300">
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
            SMA gap mode — enter when the gap widens, exit when it fades
            <InfoTip label="About SMA gap mode">
              A cross only arms the trade; the order goes on the first closed candle whose gap reaches the entry level (plus
              the wait, if set — the gap must still be there). While the gap keeps widening the trade is held; once it has
              cleared the exit level and fades back to it (or gives back the set share of its widest), the trade closes (exit
              reason “Gap fade”). An opposite cross still closes at once, and the reverse waits for its own gap. The stop,
              filters, entry cut-off and square-off still apply. An exit level beyond the entry level (e.g. sell in at −0.08,
              out at −0.39) is a lock-in level: the gap exit waits until the gap has been that wide, then closes when it comes
              back to it; a trade whose gap never gets that wide is left to the stop, the opposite cross and square-off.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Buy: enter at gap ≥" suffix="%" signed value={String(form.gap_entry_long ?? 0.05)} onChange={(v) => set("gap_entry_long", v)} own={isOwn("gap_entry_long")} />
            <Field label="Buy: exit at gap ≤" suffix="%" signed value={String(form.gap_exit_long ?? 0.02)} onChange={(v) => set("gap_exit_long", v)} own={isOwn("gap_exit_long")} />
            <Field label="Sell: enter at gap ≤" suffix="%" signed value={String(form.gap_entry_short ?? -0.05)} onChange={(v) => set("gap_entry_short", v)} own={isOwn("gap_entry_short")} />
            <Field label="Sell: exit at gap ≥" suffix="%" signed value={String(form.gap_exit_short ?? -0.02)} onChange={(v) => set("gap_exit_short", v)} own={isOwn("gap_exit_short")} />
            <Field label="Also exit after giving back % of peak (0 = off)" value={String(form.gap_giveback_pct ?? 0)} onChange={(v) => set("gap_giveback_pct", v)} own={isOwn("gap_giveback_pct")} />
            <Field label="Wait after the level is met" suffix="min" value={String(form.gap_entry_delay_min ?? 0)} onChange={(v) => set("gap_entry_delay_min", v)} own={isOwn("gap_entry_delay_min")} />
            <Field label="Give up after the cross (min, 0 = never)" value={String(form.gap_entry_window_min ?? 0)} onChange={(v) => set("gap_entry_window_min", v)} own={isOwn("gap_entry_window_min")} />
            <Field
              label="Fade exit: gap narrowing for N candles in a row (0 = off)"
              value={String(form.gap_fade_min_candles ?? 0)}
              onChange={(v) => set("gap_fade_min_candles", v)}
              own={isOwn("gap_fade_min_candles")}
            />
          </div>
          {gapNotes.length ? (
            <ul aria-label="Setting notes" className="mt-2 space-y-1.5">
              {gapNotes.map((note) => (
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
                          set(key as keyof SmaConfig, typeof value === "boolean" ? value : String(value));
                        }
                        setMsg("Changed — press Save to keep it.");
                      }}
                      className="ml-2 rounded px-2 py-0.5 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-400/15"
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
              Ride out pullbacks{" "}
              <InfoTip label="About riding out pullbacks">
                Exit on a fade only when a candle also closes on the wrong side of SMA {form.sma_slow ?? 21} (below it for a buy,
                above it for a sell). A narrowing that keeps the candles on the trade&apos;s side is treated as a pullback and
                held.
              </InfoTip>
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
              Check the fade every second{" "}
              <InfoTip label="About checking the fade every second">
                Judges the exit on the live price, as if that second closed the candle, instead of waiting for the minute to
                close. Faster on a sharp reversal; a candle that turns back inside the minute can exit too early. Entries still
                wait for the candle to close. With “1s OFF” in the status bar it checks at the slower quote pace (every few
                seconds).
              </InfoTip>
            </span>
          </label>
        </div>
      </FieldGroup>
      <FieldGroup title="Filters" hint="ADX, VWAP, volume, density, RSI, Bollinger, gap range, candle direction" wide={wide}>
        <div className="min-w-0">
          <SectionTitle
            info={
              <>
                These apply only when ticked, on a crossover; an unticked box is ignored. A trade needs every ticked filter to
                agree. Force order skips them.{crossExitOn(form) ? " A close still happens on the opposite cross." : ""}
              </>
            }
          >
            Entry filters
          </SectionTitle>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.use_adx_filter}
              onChange={(e) => set("use_adx_filter", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_adx_filter") && <OwnTag />}
            ADX trend filter
            <InfoTip label="About the ADX filter">Blocks entries while ADX (trend strength) is below the threshold.</InfoTip>
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
            VWAP
            <InfoTip label="About the VWAP filter">A buy needs the close at or above today&apos;s VWAP; a sell at or below it.</InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_volume)}
              onChange={(e) => set("use_volume", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_volume") && <OwnTag />}
            Volume
            <InfoTip label="About the volume filter">
              The closed candle&apos;s volume must be at least the multiple below of the average of the previous 20 candles.
            </InfoTip>
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
            Density (candle body)
            <InfoTip label="About the density filter">
              The candle body must cover at least the percent below of its high-to-low range: skips doji-like, undecided candles.
            </InfoTip>
          </label>
          <Field
            label="Density" suffix="%"
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
            RSI(14)
            <InfoTip label="About the RSI filter">A buy needs RSI inside the buy range below; a sell inside the sell range.</InfoTip>
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
            Bollinger Bands
            <InfoTip label="About the Bollinger filter">
              Skips a buy that closed above the upper band (or a sell below the lower): it is chasing a spike. Skips any cross
              while the bands are squeezed: the stock is going sideways. Squeeze is the band width as % of price on 1-minute
              candles; 0 turns the squeeze check off.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field label="Period" value={String(form.bb_period ?? 20)} onChange={(v) => set("bb_period", v)} own={isOwn("bb_period")} />
            <Field label="Width (σ)" value={String(form.bb_std ?? 2)} onChange={(v) => set("bb_std", v)} own={isOwn("bb_std")} />
            <Field label="Squeeze below" suffix="%" value={String(form.bb_min_width_pct ?? 0.15)} onChange={(v) => set("bb_min_width_pct", v)} own={isOwn("bb_min_width_pct")} />
          </div>
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            SMA 9/21 gap range
            <InfoTip label="About the SMA gap range filter">
              Gap % = (SMA 9 − SMA 21) ÷ SMA 21 × 100 on the cross candle: positive when SMA 9 is above, negative when below.
              Tick the side(s) to check; an unticked side trades as before. Negative numbers are allowed (a sell gap is
              usually negative, e.g. −0.5 to −0.02). On the cross candle the gap is usually small, so a tight range skips most
              crosses; skipped crosses show as ✕ on the chart with the gap. Force order skips this check.
            </InfoTip>
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
                <Field label="Min" suffix="%" signed value={String(form.gap_long_min ?? 0.02)} onChange={(v) => set("gap_long_min", v)} own={isOwn("gap_long_min")} />
                <Field label="Max" suffix="%" signed value={String(form.gap_long_max ?? 0.5)} onChange={(v) => set("gap_long_max", v)} own={isOwn("gap_long_max")} />
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
                <Field label="Min" suffix="%" signed value={String(form.gap_short_min ?? -0.5)} onChange={(v) => set("gap_short_min", v)} own={isOwn("gap_short_min")} />
                <Field label="Max" suffix="%" signed value={String(form.gap_short_max ?? -0.02)} onChange={(v) => set("gap_short_max", v)} own={isOwn("gap_short_max")} />
              </div>
            </div>
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_candle_dir)}
              onChange={(e) => set("use_candle_dir", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_candle_dir") && <OwnTag />}
            Candle direction
            <InfoTip label="About the candle direction filter">
              The last N closed candles before the order must move the trade&apos;s way: on the cross candle normally, or in gap
              mode on the candle where the gap reaches the entry level. Closes: each close beyond the one before (with 2, a buy
              needs e.g. 100.0 → 100.4 → 100.9). Colour: each candle green for a buy, red for a sell. Both: both rules. Refused
              crosses show as ✕ “Candles” on the chart. Force order skips this check.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Last candles to check (1–10)"
              value={String(form.candle_dir_count ?? 2)}
              onChange={(v) => set("candle_dir_count", v)}
              own={isOwn("candle_dir_count")}
            />
            <label className="block text-sm text-slate-300">
              <span className="text-xs uppercase tracking-wider text-slate-400">
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
        </div>
      </FieldGroup>
      <FieldGroup title="Exits & stops" hint="Stop type, ₹ stop / trail / target, ATR stop, cross exit, Bollinger exit" wide={wide}>
        <div className="min-w-0">
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
              ? "Stop-loss on new entries (moving, from the SMA gap)"
              : form.stop_type === "TSL"
                ? "Trailing stop-loss on new entries"
                : `Exchange stop-loss at ${form.atr_multiplier}× ATR`}
            <InfoTip label="About the stop-loss">
              Unticked, new entries go in with no stop order: only the other exits (cross, gap fade, Bollinger exit, target)
              and the square-off close them.
            </InfoTip>
          </label>
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-wider text-slate-400">
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
                <Field label="Min gap" suffix="%" value={String(form.gap_min_pct ?? 0.2)} onChange={(v) => set("gap_min_pct", v)} own={isOwn("gap_min_pct")} />
              </div>
              <Hint label="About the SMA gap stop">
                Gap % = SMA 9 vs SMA 21 on the last closed candle (at least the min gap). A buy gets stop = price −
                gap × stop multiple and target = price + gap × target multiple; a sell is the mirror. Recalculated every
                closed 1-minute candle: the stop only moves in your favour, the target follows the gap both ways.
                PAPER only — in LIVE the bot keeps the {String(form.atr_multiplier)}× ATR exchange stop.
              </Hint>
            </div>
          ) : null}
          {form.stop_type === "TSL" ? (
            <div className="mt-2 rounded-md border border-emerald-400/20 bg-emerald-400/[0.04] p-2">
              <div className="mb-2 flex items-center gap-2 text-xs text-slate-300">
                <span>Units:</span>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="radio"
                    name="tsl_mode"
                    checked={(form.tsl_mode ?? "POINTS") === "POINTS"}
                    onChange={() => set("tsl_mode", "POINTS")}
                    className="accent-sky-400"
                  />
                  Points (₹)
                </label>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="radio"
                    name="tsl_mode"
                    checked={form.tsl_mode === "PERCENT"}
                    onChange={() => set("tsl_mode", "PERCENT")}
                    className="accent-sky-400"
                  />
                  Percent (%)
                </label>
                <span className="text-[11px] text-slate-500">
                  % scales with the stock price — 1% is ₹1 on a ₹100 stock and ₹10 on a ₹1,000 stock.
                </span>
              </div>
              {(form.tsl_mode ?? "POINTS") === "PERCENT" ? (
                <div className="grid grid-cols-3 items-end gap-2">
                  <Field label="Stop" suffix="%" value={String(form.tsl_sl_pct ?? 1)} onChange={(v) => set("tsl_sl_pct", v)} own={isOwn("tsl_sl_pct")} />
                  <Field label="Trail every" suffix="%" value={String(form.tsl_trail_pct ?? 0.5)} onChange={(v) => set("tsl_trail_pct", v)} own={isOwn("tsl_trail_pct")} />
                  <Field label="Target" suffix="%" value={String(form.tsl_target_pct ?? 0)} onChange={(v) => set("tsl_target_pct", v)} own={isOwn("tsl_target_pct")} />
                </div>
              ) : (
                <div className="grid grid-cols-3 items-end gap-2">
                  <Field label="Stop" suffix="₹" value={String(form.tsl_sl_points ?? 20)} onChange={(v) => set("tsl_sl_points", v)} own={isOwn("tsl_sl_points")} />
                  <Field label="Trail every" suffix="₹" value={String(form.tsl_trail_points ?? 10)} onChange={(v) => set("tsl_trail_points", v)} own={isOwn("tsl_trail_points")} />
                  <Field label="Target" suffix="₹" value={String(form.tsl_target_points ?? 0)} onChange={(v) => set("tsl_target_points", v)} own={isOwn("tsl_target_points")} />
                </div>
              )}
              <Hint label="About the trailing stop">
                {(form.tsl_mode ?? "POINTS") === "PERCENT" ? (
                  <>
                    <b>Percent mode:</b> Stop % is the distance from entry; Target % 0 means no target. A buy at ₹1,000 with stop
                    {" "}{String(form.tsl_sl_pct ?? 1)}% starts its stop at ₹
                    {(1000 * (1 - Number(form.tsl_sl_pct ?? 1) / 100)).toLocaleString("en-IN")}. Each {String(form.tsl_trail_pct ?? 0.5)}%
                    the price gains past its best so far moves the stop {String(form.tsl_trail_pct ?? 0.5)}%; it never moves back. A sell is the mirror.
                  </>
                ) : (
                  <>
                    Stop ₹ is the distance from your entry; Target ₹ 0 means no target. A buy at ₹1,000 with stop ₹
                    {String(form.tsl_sl_points ?? 20)} starts its stop at ₹
                    {(1000 - Number(form.tsl_sl_points ?? 20)).toLocaleString("en-IN")}. Each ₹{String(form.tsl_trail_points ?? 10)} the
                    price gains past its best so far moves the stop up ₹{String(form.tsl_trail_points ?? 10)}; it never moves back. A sell is the mirror.
                  </>
                )}{" "}
                {Number((form.tsl_mode ?? "POINTS") === "PERCENT" ? form.tsl_target_pct : form.tsl_target_points) > 0
                  ? `The trade also closes at the target.`
                  : "No target: the trailing stop, an opposite cross or square-off closes the trade."}{" "}
                In LIVE the bot moves your Groww stop order in place, so the position always has a stop.
              </Hint>
            </div>
          ) : null}
        </div>
        <div className="min-w-0">
          <SectionTitle>Exits</SectionTitle>
          <label
            className={clsx(
              "mt-2 flex items-start gap-2 rounded-md p-2 text-sm text-slate-300 ring-1 ring-inset",
              crossExitOn(form) ? "ring-white/10" : "bg-amber-400/[0.08] ring-amber-400/40"
            )}
          >
            <input
              type="checkbox"
              checked={crossExitOn(form)}
              onChange={(e) => set("cross_exit", e.target.checked)}
              className="mt-1 accent-[#10B981]"
            />
            <span>
              {isOwn("cross_exit") && <OwnTag />}
              <b className="text-slate-100">SMA cross exit</b> — an opposite cross closes and reverses{" "}
              <InfoTip label="About the SMA cross exit">
                Ticked, an opposite cross closes the trade and opens the reverse. Unticked, the trade is held through opposite
                crosses: crosses then only open a trade while flat, and the stop / target, gap fade, Bollinger exit or the{" "}
                {squareOff} square-off closes it. Works with any of those, or alone. Not used with candle patterns (they close at
                the end of their candle).
              </InfoTip>
            </span>
          </label>
          {noExitOn(form) ? (
            <p role="alert" className="mt-2 rounded-md border border-amber-400/60 bg-amber-400/[0.12] p-2 text-[12px] font-semibold leading-snug text-amber-200">
              No exit is selected: the SMA cross exit, stop-loss, gap mode and Bollinger exit are all off. A trade will be
              held, with no stop, until the {squareOff} square-off.
              <button
                type="button"
                onClick={() => {
                  set("cross_exit", true);
                  setMsg("Changed — press Save to keep it.");
                }}
                className="ml-2 rounded px-2 py-0.5 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-400/15"
              >
                Turn the SMA cross exit on
              </button>
            </p>
          ) : null}
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              Bollinger exit{isOwn("bb_exit") && <OwnTag />}
              <InfoTip label="About the Bollinger exit">
                Read once per closed 1-minute candle after the entry, on the Bollinger Period and Width (the bands show on the
                chart). The middle-band exit waits until a candle has closed on the trade&apos;s side of the middle first. Your
                stop keeps working; in LIVE the exchange stop is cancelled just before the exit is sent.
              </InfoTip>
            </span>
            <select
              value={BB_EXITS.includes(form.bb_exit as BbExit) ? form.bb_exit : "OFF"}
              onChange={(e) => set("bb_exit", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="OFF">Off — no Bollinger exit</option>
              <option value="BAND">Band target — book profit when a candle closes at the far band</option>
              <option value="MIDDLE">Middle band — exit when a candle closes back across the middle</option>
              <option value="BOTH">Both — band target or middle band, whichever comes first</option>
            </select>
          </label>
          <SectionTitle
            info={
              <>
                Not an exit by itself. When an open trade&apos;s SMA {form.sma_fast}/{form.sma_slow} gap on the bot&apos;s own candle
                narrows to within the band (or, with the cross exit off, has just crossed inside it), the terminal and Telegram show
                the last closed 1-minute candles and ask EXIT or WAIT. EXIT closes that trade (exit reason USER_REVIEW_EXIT); WAIT
                and no answer keep it open and the strategy carries on. One review per uncertain stretch, none within the cooldown
                of the last one on that trade. Needs a candle of 2 minutes or more. A replay pauses at each review.
              </>
            }
          >
            1-minute review
          </SectionTitle>
          <label className="mt-2 flex items-start gap-2 rounded-md p-2 text-sm text-slate-300 ring-1 ring-inset ring-white/10">
            <input
              type="checkbox"
              checked={Boolean(form.review_on)}
              onChange={(e) => set("review_on", e.target.checked)}
              className="mt-1 accent-[#10B981]"
            />
            <span>
              {isOwn("review_on") && <OwnTag />}
              <b className="text-slate-100">Ask me when the SMAs are uncertain</b> — EXIT or WAIT; no answer keeps the trade
              {Number(form.candle_minutes ?? 1) < 2 ? (
                <span className="block text-xs text-amber-300">Needs a candle of 2 minutes or more (now {form.candle_minutes ?? 1} min).</span>
              ) : null}
            </span>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Uncertain within ±"
              suffix="%"
              own={isOwn("review_gap_pct")}
              value={String(form.review_gap_pct ?? 0.03)}
              onChange={(v) => set("review_gap_pct", v)}
            />
            <Field
              label="Cooldown per trade"
              suffix="min"
              own={isOwn("review_cooldown_min")}
              value={String(form.review_cooldown_min ?? 15)}
              onChange={(v) => set("review_cooldown_min", v)}
            />
          </div>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <label className="block text-xs text-slate-400">
              Check candle{isOwn("review_check_minutes") && <OwnTag />}
              <select
                value={String(form.review_check_minutes ?? 1)}
                onChange={(e) => set("review_check_minutes", Number(e.target.value))}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
              >
                {[1, 2, 3, 5, 10, 15].map((m) => (
                  <option key={m} value={m}>
                    {m} min
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-slate-400">
              Check mode{isOwn("review_check_mode") && <OwnTag />}
              <select
                value={String(form.review_check_mode ?? "ALWAYS")}
                onChange={(e) => set("review_check_mode", e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
              >
                <option value="ALWAYS">Prompt whenever uncertain</option>
                <option value="ONLY_IF_NOT_WITH">Only if the check candle is not with the trade</option>
                <option value="ONLY_IF_AGAINST">Only if the check candle is against the trade</option>
              </select>
            </label>
          </div>
          <label className="mt-2 block text-xs text-slate-400">
            If I don&apos;t answer{isOwn("review_default_answer") && <OwnTag />}
            <select
              value={String(form.review_default_answer ?? "PROMPT")}
              onChange={(e) => {
                const next = e.target.value;
                if (
                  next === "EXIT" &&
                  String(form.trading_mode ?? "").toUpperCase() === "LIVE" &&
                  !window.confirm(
                    "Exit automatically on LIVE: the bot will close the reviewed trade without asking you. Continue?"
                  )
                ) {
                  return;
                }
                set("review_default_answer", next);
              }}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
            >
              <option value="PROMPT">Prompt me (default) — no answer keeps the trade</option>
              <option value="CONTINUE">Continue automatically — no prompt, trade stays</option>
              <option value="EXIT">Exit automatically — no prompt, trade is closed</option>
            </select>
          </label>
        </div>
      </FieldGroup>
      <FieldGroup title="Risk & session" hint="Quantity, max daily loss, max trades, cut-off, square-off" wide={wide}>
        <div className="min-w-0">
          <SectionTitle>Size</SectionTitle>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Quantity" own={isOwn("qty")} value={String(form.qty)} onChange={(v) => set("qty", v)} />
          </div>
          {!stockScope && (
            <>
              <SectionTitle
                info={
                  <>
                    Max daily loss and trades a day are for this bot only. If the bot stopped on the trade cap, type a higher
                    max and press Save, then Start. Open positions stay open. A loss-limit stop stays locked for the day.
                  </>
                }
              >
                Daily limits (this bot)
              </SectionTitle>
              <div className="mt-2 grid grid-cols-2 items-end gap-2">
                <Field label="Max daily loss" suffix="₹" value={String(form.max_daily_loss)} onChange={(v) => set("max_daily_loss", v)} />
                <Field label="Max trades / day (1–100)" value={String(form.max_trades_per_day)} onChange={(v) => set("max_trades_per_day", v)} />
                <Field
                  label="No new entries after"
                  value={form.entry_cutoff_time || "15:00"}
                  onChange={(v) => set("entry_cutoff_time", v)}
                />
                <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
              </div>
            </>
          )}
        </div>
      </FieldGroup>
      <div
        className={clsx(
          "mt-3 flex flex-wrap items-center gap-3",
          wide && "sticky bottom-0 z-10 -mx-4 border-t border-white/10 bg-[#151921] px-4 py-3 shadow-[0_-8px_16px_-8px_rgba(0,0,0,0.5)] lg:-mx-6 lg:px-6",
        )}
      >
        <button
          disabled={busy}
          onClick={save}
          className={clsx(
            "inline-flex h-11 items-center rounded-md px-5 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9",
            unsaved ? "bg-emerald-600 hover:bg-emerald-500" : "bg-sky-600 hover:bg-sky-500"
          )}
        >
          {stockScope ? `Save for ${scope}` : "Save"}
        </button>
        {unsaved && !busy ? (
          <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-xs font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/30">
            Unsaved changes
          </span>
        ) : null}
        {stockScope && Object.keys(own).length > 0 && (
          <button
            disabled={busy}
            onClick={resetStock}
            className="inline-flex h-11 items-center rounded-md px-4 text-sm text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 sm:h-9"
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

/** A collapsible group of settings (open by default). */
function FieldGroup({ title, hint, wide, children }: { title: string; hint: string; wide: boolean; children: ReactNode }) {
  return (
    <details open className="group/fs mt-4 rounded-lg border border-white/10 bg-white/[0.02]">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-3 text-sm font-semibold text-slate-100 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 [&::-webkit-details-marker]:hidden">
        <ChevronDown size={16} aria-hidden className="shrink-0 text-slate-400 transition-transform group-[:not([open])]/fs:-rotate-90" />
        {title}
        <span className="hidden truncate text-xs font-normal text-slate-400 sm:inline">— {hint}</span>
      </summary>
      <div className={clsx("px-3 pb-3", wide && "lg:grid lg:grid-cols-2 lg:gap-x-10")}>{children}</div>
    </details>
  );
}

function OwnTag() {
  return <span className="ml-1 shrink-0 text-xs normal-case tracking-normal text-violet-300">· own</span>;
}

function Field({
  label,
  value,
  onChange,
  own,
  signed,
  suffix,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** This stock sets the value itself rather than using the shared one. */
  own?: boolean;
  /** Allows a minus sign: phones' decimal keypads have none. */
  signed?: boolean;
  /** Unit shown inside the box (₹, %, min). Display only. */
  suffix?: string;
}) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(value);
  }, [value]);
  return (
    <label className="block">
      <span className="block text-xs leading-tight text-slate-300">
        {label}
        {own && <span className="ml-1 whitespace-nowrap normal-case tracking-normal text-violet-300">· own</span>}
      </span>
      <span className="relative mt-1 block">
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
            "block h-10 w-full min-w-[4.5rem] rounded-md border bg-black/40 px-2 font-mono text-sm tabular-nums text-[#f8fafc] outline-none focus:border-[#10B981]/50 focus-visible:ring-2 focus-visible:ring-sky-400",
            suffix && "pr-9",
            own ? "border-violet-400/50" : "border-white/10"
          )}
        />
        {suffix ? (
          <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center font-mono text-xs text-slate-400">{suffix}</span>
        ) : null}
      </span>
    </label>
  );
}

/** A small heading that splits the settings into groups. */
function SectionTitle({ children, info }: { children: ReactNode; info?: ReactNode }) {
  return (
    <div className="mt-5 flex items-center gap-1 border-t border-white/10 pt-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-300 first:mt-3">
      {children}
      {info ? <InfoTip label={`About ${typeof children === "string" ? children : "this section"}`}>{info}</InfoTip> : null}
    </div>
  );
}

/** A one-line "How it works" with the explanation in an ⓘ popover, under a group of fields. */
function Hint({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="mt-1.5 flex items-center gap-1 text-xs text-slate-400">
      <InfoTip label={label}>{children}</InfoTip>
      How it works
    </div>
  );
}
