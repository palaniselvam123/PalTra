"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, FileDown, LineChart, Loader2, Play, RotateCcw, Trash2 } from "lucide-react";
import clsx from "clsx";
import { inr, smaApi, type ReplayDayRow, type ReplayRun, type ReplayStockRow, type ScalpPick, type ScalpPickRule } from "@/lib/smaApi";
import { SortTh, useSort } from "./sortable";
import { Badge, Skeleton, pnlTone } from "./ui";
import { REASON_SHORT } from "./TradeHistoryTable";
import { chartHref } from "./StockTabs";

/** Opens one stock's chart for one day of a finished (or playing) run, in a new tab. */
function RunChartLink({ run, symbol, date }: { run: ReplayRun; symbol: string; date: string }) {
  const bot = Number(run.settings.bot ?? 1) || 1;
  return (
    <a
      href={chartHref(symbol, { date, runId: run.id, desk: bot === 1 ? "live" : `bot${bot}` })}
      target="_blank"
      rel="noreferrer"
      title={`Chart of ${symbol} on ${date} with this run's trades`}
      aria-label={`Chart of ${symbol} on ${date}`}
      onClick={(e) => e.stopPropagation()}
      className="ml-1.5 inline-flex h-7 w-7 items-center justify-center rounded-md text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
    >
      <LineChart size={13} aria-hidden />
    </a>
  );
}

export type Settings = ReplayRun["settings"];

function signed(v: number): string {
  return `${v > 0 ? "+" : ""}${inr(v)}`;
}

function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" }).format(d);
}

function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d);
}

type ScalpPickInfo = ScalpPickRule & { universe?: number; picks?: Record<string, ScalpPick[]> };

/** The Scalp-pick rule and picks a run recorded, or null for a plain replay. */
export function scalpPickOf(s: Settings): ScalpPickInfo | null {
  const raw = (s as Record<string, unknown>).scalp_pick;
  return raw && typeof raw === "object" ? (raw as ScalpPickInfo) : null;
}

/** One line naming the strategy a run used. */
export function strategyLabel(s: Settings): string {
  const pick = scalpPickOf(s);
  const parts: string[] = pick ? [`Scalp top ${pick.top_n} @ ${pick.pick_time}`] : [];
  // Runs of bots 2-4 name the bot whose settings they played.
  if (Number(s.bot ?? 1) > 1) parts.push(`${String(s.bot_name ?? `Bot ${s.bot}`)}'s settings`);
  if (String(s.entry_mode ?? "SMA") === "PATTERN") {
    parts.push(
      `Candle patterns ${s.pattern_tf ?? 1}m (${String(s.pattern_set ?? "STRONG") === "ALL" ? "all" : "strong"}${
        s.pattern_trend ? ", with SMA trend" : ""
      })`
    );
  } else if (String(s.entry_mode ?? "SMA") === "COLOUR") {
    parts.push(`Candle colour ${s.pattern_tf ?? 1}m (hold while the colour holds)`);
  } else {
    parts.push(`SMA ${s.sma_fast ?? 9}/${s.sma_slow ?? 21}`);
    if (s.cross_exit === false) parts.push("no cross exit");
    if (s.cross_exit !== false && Number(s.exit_candle_minutes ?? 0) > 0 && Number(s.exit_candle_minutes) !== Number(s.candle_minutes ?? 1))
      parts.push(`exit on ${s.exit_candle_minutes}m candle`);
    if (Number(s.late_entry_min ?? 0) > 0) parts.push(`late entry ${s.late_entry_min}m`);
    if (s.join_trend) parts.push(`join morning trend from 09:15+${s.join_after_min ?? 15}m`);
    if (Number(s.counter_exit_count ?? 0) > 0) parts.push(`exit on ${s.counter_exit_count} counter candles`);
    if (s.counter_entry) parts.push("early reversal entry");
    if (s.turn_entry) parts.push("SMA 9 turn entry");
    if (Number(s.counter_exit_count ?? 0) > 0 && s.counter_reenter) parts.push(`re-enter up to ${s.counter_reenter_max ?? 3}×`);
    if (s.use_ema) parts.push(`EMA ${s.ema_fast ?? 9}/${s.ema_slow ?? 200} entry`);
    if (s.ema_exit) parts.push(`EMA ${s.ema_fast ?? 9}/${s.ema_slow ?? 200} exit`);
    if (s.use_level) parts.push(`level ${s.level_line ?? "BB_MIDDLE"}`);
    if (s.use_supertrend) parts.push(`Supertrend ${s.st_period ?? 10}×${s.st_mult ?? 3} entry`);
    if (s.st_exit) parts.push(`Supertrend ${s.st_period ?? 10}×${s.st_mult ?? 3} exit`);
    if (s.slope_exit) parts.push(`SMA 9 slope exit (bend ${s.slope_bend_pct ?? 60}%, over ${s.slope_lookback ?? 2})`);
    if ((s.slope_exit || s.vturn_exit) && Number(s.slope_minutes ?? 0) > 0) parts.push(`slope on ${s.slope_minutes}m candle`);
    if (s.vturn_exit) parts.push("SMA 9 V-turn exit");
    if (s.as_armed) parts.push("as armed live");
    if (s.slow_sma_exit) parts.push("slow SMA exit");
    if (s.recovery_hold) parts.push(`recovery hold (${s.recovery_tolerance_pct ?? 0.15}%, ${s.recovery_max_candles ?? 6} candles)`);
    if (s.breakout_handoff) parts.push(`breakout hand-off to Bot 3 (${s.breakout_atr_mult ?? 1.8}× ATR)`);
    if (s.tall_pullback) parts.push(`tall-candle pullback (${s.tall_atr_mult ?? 1.5}× ATR, ${s.tall_expiry_candles ?? 2} candles)`);
    if (s.day_extreme_guard) parts.push(`day high/low guard (${s.day_extreme_pct ?? 0.15}%, pull ${s.day_extreme_pull_pct ?? 0.15}%)`);
    if (s.entry_candle_confirm) parts.push(`entry candle ${s.entry_candle_pct ?? 0.05}% past open`);
    if (s.extreme_entry) parts.push(`limit at candle ${s.extreme_anchor === "SMA_9" ? "extreme / fast SMA" : "extreme"} (${s.extreme_expiry_candles ?? 2} candles)`);
    const orChecks = [s.use_or_volume && "volume", s.use_or_vwap && "VWAP", s.use_or_spread && "SMA spread", s.use_or_break && "break"].filter(Boolean);
    if (orChecks.length) parts.push(`opening range: ${orChecks.join(", ")}`);
  }
  if (s.flip_orders) parts.push("FLIPPED (buy signals sell)");
  if (s.use_stop === false) parts.push("no stop");
  else if (s.stop_type === "SMA_GAP") parts.push(`SMA-gap stop ×${s.gap_sl_mult} · target ×${s.gap_tp_mult} · min ${s.gap_min_pct}%`);
  else if (s.stop_type === "TSL") parts.push(tslText(s));
  else if (s.stop_type === "SUPERTREND") parts.push(`Supertrend stop (${s.st_period ?? 10}, ×${s.st_mult ?? 3})`);
  else parts.push(`${s.atr_multiplier ?? 1.5}× ATR stop`);
  if (bbExitText(s)) parts.push(`BB exit: ${bbExitText(s)}`);
  if (gapModeText(s)) parts.push(`Gap mode ${gapModeText(s)}`);
  const filters: string[] = [];
  if (s.use_vwap) filters.push("VWAP");
  if (s.use_volume) filters.push(`Vol ≥${s.volume_min_ratio}×`);
  if (s.use_density) filters.push(`Density ≥${s.density_min_pct}%`);
  if (s.use_rsi) filters.push(`RSI ${s.rsi_long_min}–${s.rsi_long_max}/${s.rsi_short_min}–${s.rsi_short_max}`);
  if (s.use_bollinger) filters.push(`BB ${s.bb_period ?? 20}/${s.bb_std ?? 2}σ`);
  if (gapText(s)) filters.push(`Gap ${gapText(s)}`);
  if (s.use_candle_dir) filters.push(`Candles ${s.candle_dir_count ?? 2} ${String(s.candle_dir_rule ?? "CLOSES").toLowerCase()}`);
  if (s.use_adx_filter) filters.push(`ADX ≥${s.adx_threshold}`);
  parts.push(filters.length ? filters.join(", ") : "no filters");
  parts.push(`qty ${s.qty ?? "—"}`);
  return parts.join(" · ");
}

/** A trailing stop's numbers in its own unit: ₹ points, or % of the entry price. */
function tslText(s: Settings): string {
  if (s.tsl_mode === "PERCENT") {
    const target = Number(s.tsl_target_pct ?? 0) > 0 ? ` · target ${s.tsl_target_pct}%` : "";
    return `TSL ${s.tsl_sl_pct ?? 1}% · trail ${s.tsl_trail_pct ?? 0.5}%${target}`;
  }
  const target = Number(s.tsl_target_points ?? 0) > 0 ? ` · target ₹${s.tsl_target_points}` : "";
  return `TSL ₹${s.tsl_sl_points ?? 20} · trail ₹${s.tsl_trail_points ?? 10}${target}`;
}

/** The stop part of a strategy in a few words, e.g. "1.5× ATR" or "TSL ₹20 / ₹10". */
export function stopShort(s: Settings): string {
  if (s.use_stop === false) return "No stop";
  if (s.stop_type === "SMA_GAP") return `SMA-gap ×${s.gap_sl_mult}/×${s.gap_tp_mult}`;
  if (s.stop_type === "TSL") {
    if (s.tsl_mode === "PERCENT") {
      const target = Number(s.tsl_target_pct ?? 0) > 0 ? ` · T ${s.tsl_target_pct}%` : "";
      return `TSL ${s.tsl_sl_pct ?? 1}% / ${s.tsl_trail_pct ?? 0.5}%${target}`;
    }
    const target = Number(s.tsl_target_points ?? 0) > 0 ? ` · T ₹${s.tsl_target_points}` : "";
    return `TSL ₹${s.tsl_sl_points ?? 20} / ₹${s.tsl_trail_points ?? 10}${target}`;
  }
  if (s.stop_type === "SUPERTREND") return `Supertrend ${s.st_period ?? 10}/×${s.st_mult ?? 3}`;
  return `${s.atr_multiplier ?? 1.5}× ATR`;
}

/** The SMA gap ranges that were ticked, e.g. "buy 0.02–0.5% · sell -0.5–-0.02%", or "". */
export function gapText(s: Settings): string {
  const parts: string[] = [];
  if (s.use_gap_long) parts.push(`buy ${s.gap_long_min ?? 0.02}–${s.gap_long_max ?? 0.5}%`);
  if (s.use_gap_short) parts.push(`sell ${s.gap_short_min ?? -0.5}–${s.gap_short_max ?? -0.02}%`);
  return parts.join(" · ");
}

/** Gap mode in a few words, e.g. "in ≥0.05/≤-0.05 · out 0.02/-0.02 · wait 2m", or "". */
export function gapModeText(s: Settings): string {
  if (!s.use_gap_mode) return "";
  const parts = [
    `in ≥${s.gap_entry_long ?? 0.05}/≤${s.gap_entry_short ?? -0.05}%`,
    `out ${s.gap_exit_long ?? 0.02}/${s.gap_exit_short ?? -0.02}%`,
  ];
  if (Number(s.gap_giveback_pct ?? 0) > 0) parts.push(`give back ${s.gap_giveback_pct}%`);
  if (s.gap_fade_confirm_sma) parts.push("exit on close past SMA");
  if (Number(s.gap_fade_min_candles ?? 0) > 0) parts.push(`fade ${s.gap_fade_min_candles} candles`);
  if (s.gap_fade_intrabar) parts.push("fade checked each second");
  if (s.gap_fade_minute) parts.push("fade checked each minute");
  if (Number(s.gap_entry_delay_min ?? 0) > 0) parts.push(`wait ${s.gap_entry_delay_min}m`);
  if (Number(s.gap_entry_window_min ?? 0) > 0) parts.push(`within ${s.gap_entry_window_min}m`);
  return parts.join(" · ");
}

const BB_EXIT_TEXT: Record<string, string> = { BAND: "band target", MIDDLE: "middle band", BOTH: "band + middle" };
type RunFilters = { text: string; bot: string; result: string; status: string; from: string; to: string };
const NO_FILTERS: RunFilters = { text: "", bot: "ALL", result: "ALL", status: "ALL", from: "", to: "" };

/** Does a run pass the Backtests filters? Stock / run # search, bot, result, status and replayed dates. */
function runMatches(run: ReplayRun, f: RunFilters): boolean {
  const text = f.text.trim().toUpperCase();
  if (text) {
    const hay = [`#${run.id}`, String(run.id), ...run.symbols].join(" ").toUpperCase();
    if (!text.split(/[\s,]+/).filter(Boolean).every((part) => hay.includes(part))) return false;
  }
  if (f.bot !== "ALL" && String(Number(run.settings.bot ?? 1) || 1) !== f.bot) return false;
  const t = run.totals;
  if (f.result === "PROFIT" && !(t.trades > 0 && t.net > 0)) return false;
  if (f.result === "LOSS" && !(t.trades > 0 && t.net < 0)) return false;
  if (f.result === "NONE" && t.trades > 0) return false;
  if (f.status !== "ALL" && run.status !== f.status) return false;
  if (f.from && run.end_date < f.from) return false;
  if (f.to && run.start_date > f.to) return false;
  return true;
}

// The runs list asks for the newest RUNS_PAGE runs, then RUNS_PAGE more per "Show older runs" (server cap 500).
const RUNS_PAGE = 50;
const RUNS_MAX = 500;

/** The Bollinger exit in a few words, or "" when it was off. */
export function bbExitText(s: Settings): string {
  return BB_EXIT_TEXT[String(s.bb_exit ?? "OFF").toUpperCase()] ?? "";
}

/** The entry filters that were on, e.g. "VWAP, RSI", or "no filters". */
export function filtersShort(s: Settings): string {
  const on: string[] = [];
  if (s.use_vwap) on.push("VWAP");
  if (s.use_volume) on.push("Vol");
  if (s.use_density) on.push("Density");
  if (s.use_rsi) on.push("RSI");
  if (s.use_bollinger) on.push("BB");
  if (s.use_gap_long || s.use_gap_short) on.push("Gap");
  if (s.use_candle_dir) on.push("Candles");
  if (s.use_adx_filter) on.push("ADX");
  return on.length ? on.join(", ") : "no filters";
}

export const SETTING_ROWS: [string, (s: Settings) => string][] = [
  ["SMA", (s) => `${s.sma_fast} / ${s.sma_slow}`],
  [
    "Stop",
    (s) =>
      s.use_stop === false
        ? "Off"
        : s.stop_type === "SMA_GAP"
          ? `SMA gap: stop ×${s.gap_sl_mult}, target ×${s.gap_tp_mult}, min gap ${s.gap_min_pct}%`
          : s.stop_type === "TSL"
            ? `Trailing: ${tslText(s)}`
          : s.stop_type === "SUPERTREND"
            ? `Supertrend line (${s.st_period ?? 10} candles, ×${s.st_mult ?? 3}), moved each candle`
          : `${s.atr_multiplier}× ATR (${s.atr_period})`,
  ],
  [
    "SMA cross exit",
    (s) =>
      s.cross_exit === false
        ? "Off (a cross does not close the trade)"
        : Number(s.exit_candle_minutes ?? 0) > 0 && Number(s.exit_candle_minutes) !== Number(s.candle_minutes ?? 1)
          ? `On, read on ${s.exit_candle_minutes}-min candles (no reverse)`
          : "On",
  ],
  ["Join morning trend", (s) => (s.join_trend ? `On, from ${s.join_after_min ?? 15} min after 09:15` : "Off")],
  ["Counter-candle exit", (s) => (Number(s.counter_exit_count ?? 0) > 0 ? `${s.counter_exit_count} candles against (${s.counter_rule ?? "COLOUR"}${s.counter_need_fast === false ? "" : ", past fast SMA"}${Number(s.counter_min_body ?? 0) > 0 ? `, body ≥ ${s.counter_min_body} × ATR` : ""})` : "Off")],
  ["Early reversal entry", (s) => (s.counter_entry ? "On" : "Off")],
  ["SMA 9 turn entry", (s) => (s.turn_entry ? `On (V-turn numbers below${s.uturn_on ? `; rounded U after ${s.uturn_run ?? 5} candles, ${s.uturn_min_pct ?? 0.005}%/candle` : ""}${s.turn_toward_cross ? "; only toward the 9/21 cross" : ""}${s.turn_exit_candle ? "; closes on a candle of the other colour" : ""}${s.turn_colour_exit ? "; turn trades exit on the other colour" : ""})` : "Off")],
  ["Re-enter after counter exit", (s) => (Number(s.counter_exit_count ?? 0) > 0 && s.counter_reenter ? `Up to ${s.counter_reenter_max ?? 3} per SMA run` : "Off")],
  ["EMA entry", (s) => (s.use_ema ? `On (EMA ${s.ema_fast ?? 9} vs ${s.ema_slow ?? 200})` : "Off")],
  ["EMA cross exit", (s) => (s.ema_exit ? `On (EMA ${s.ema_fast ?? 9} vs ${s.ema_slow ?? 200})` : "Off")],
  ["Price level entry", (s) => (s.use_level ? `On (${{ BB_MIDDLE: "Bollinger middle", BB_UPPER: "outer Bollinger band", PIVOT_P: "pivot P", PIVOT_R1: "R1 / S1" }[String(s.level_line ?? "BB_MIDDLE")] ?? s.level_line})` : "Off")],
  ["Supertrend entry", (s) => (s.use_supertrend ? `On (${s.st_period ?? 10}, ${s.st_mult ?? 3})` : "Off")],
  ["Supertrend exit", (s) => (s.st_exit ? `On (${s.st_period ?? 10}, ${s.st_mult ?? 3})` : "Off")],
  ["Slow SMA exit", (s) => (s.slow_sma_exit ? "On" : "Off")],
  ["SMA 9 V-turn exit", (s) => (s.vturn_exit ? `On (run ${s.vturn_move_pct ?? 0.02}%, turn ${s.vturn_turn_pct ?? 0.015}%/candle within ${s.vturn_within ?? 6})${s.vturn_handoff ? "; a loss moves the stock to Bot 2" : ""}` : "Off")],
  ["SMA 9 slope exit", (s) => (s.slope_exit ? `On (over ${s.slope_lookback ?? 2} candles, bend ${s.slope_bend_pct ?? 60}%${s.slope_turn === false ? "" : `, turn ${s.slope_turn_pct ?? 0.02}%/candle`})` : "Off")],
  ["SMA 9 deceleration guard", (s) => (s.decel_exit ? `On (${s.decel_drop_pct ?? 50}% off peak, arms ${s.decel_arm_atr ?? 0.1} ATR, ${s.decel_confirm === "OFF" ? "no close check" : "close through SMA 9"}${s.decel_peak_from === "RUN" ? ", peak from run start" : ""})` : "Off")],
  ["Staged stop plan", (s) => (s.stop_plan ? `Breakeven at +${s.plan_be_r ?? 1.5}R, Supertrend trail from +${s.plan_trail_r ?? 2}R` : "Off")],
  ["SMA 9 slope candle", (s) => (Number(s.slope_minutes ?? 0) > 0 ? `${s.slope_minutes}-min candles` : "Bot's own candle")],
  ["Recovery hold", (s) => (s.recovery_hold ? `On (target ${s.recovery_tolerance_pct ?? 0.15}%, floor ${s.recovery_emergency_mult ?? 1.5}× / ${s.recovery_atr_mult ?? 2.5}× ATR, ${s.recovery_max_candles ?? 6} candles${s.recovery_handoff === false ? "" : ", hand-off to Bot 2"})` : "Off")],
  ["Breakout hand-off", (s) => (s.breakout_handoff ? `On (candle ${s.breakout_atr_mult ?? 1.8}× ATR + gap ${s.breakout_gap_pct ?? 0.2}% or volume ${s.breakout_volume_mult ?? 1.5}×, to Bot 3)` : "Off")],
  ["Tall-candle pullback", (s) => (s.tall_pullback ? `On (${s.tall_atr_mult ?? 1.5}× ATR, wait ${s.tall_expiry_candles ?? 2} candles)` : "Off")],
  ["Day high/low guard", (s) => (s.day_extreme_guard ? `On (within ${s.day_extreme_pct ?? 0.15}%, wait ${s.day_extreme_pull_pct ?? 0.15}% back or the fast SMA, ${s.day_extreme_candles ?? 3} candles)` : "Off")],
  ["Entry candle must go the trade's way", (s) => (s.entry_candle_confirm ? `On (${s.entry_candle_pct ?? 0.05}% past its open, wait ${s.entry_candle_max ?? 2} candles)` : "Off")],
  ["Candle-extreme limit", (s) => (s.extreme_entry ? `On (${s.extreme_anchor === "SMA_9" ? "low/high or fast SMA" : "low/high"}, cancel beyond ${s.extreme_invalid_pct ?? 0.05}%, wait ${s.extreme_expiry_candles ?? 2} candles${s.extreme_cancel_on_cross === false ? "" : ", cancel on a cross against"})` : "Off")],
  ["Late entry", (s) => (Number(s.late_entry_min ?? 0) > 0 ? `${s.late_entry_min} min after a refused cross` : "Off")],
  ["Quantity", (s) => String(s.qty)],
  ["VWAP filter", (s) => (s.use_vwap ? "On" : "Off")],
  ["Volume filter", (s) => (s.use_volume ? `≥ ${s.volume_min_ratio}× avg of 20` : "Off")],
  ["Density filter", (s) => (s.use_density ? `≥ ${s.density_min_pct}%` : "Off")],
  [
    "RSI filter",
    (s) => (s.use_rsi ? `buy ${s.rsi_long_min}–${s.rsi_long_max}, sell ${s.rsi_short_min}–${s.rsi_short_max}` : "Off"),
  ],
  [
    "Bollinger filter",
    (s) =>
      s.use_bollinger
        ? `${s.bb_period ?? 20} / ${s.bb_std ?? 2}σ · squeeze < ${s.bb_min_width_pct ?? 0.15}%`
        : "Off",
  ],
  ["SMA gap filter", (s) => gapText(s) || "Off"],
  [
    "Candle direction",
    (s) => (s.use_candle_dir ? `last ${s.candle_dir_count ?? 2} · ${String(s.candle_dir_rule ?? "CLOSES").toLowerCase()}` : "Off"),
  ],
  ["SMA gap mode", (s) => gapModeText(s) || "Off"],
  [
    "Bollinger exit",
    (s) => (bbExitText(s) ? `${bbExitText(s)} · ${s.bb_period ?? 20} / ${s.bb_std ?? 2}σ` : "Off"),
  ],
  ["ADX filter", (s) => (s.use_adx_filter ? `≥ ${s.adx_threshold}` : "Off")],
  ["Daily loss limit", (s) => inr(Number(s.max_daily_loss ?? 0))],
  ["No new entries after", (s) => String(s.entry_cutoff_time ?? "15:00")],
  ["Square-off", (s) => String(s.square_off_time ?? "15:15")],
];

/** Replay runs side by side, and one run's day-wise P&L. */
export function BacktestRuns() {
  const [runs, setRuns] = useState<ReplayRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ReplayRun | null>(null);
  const [printing, setPrinting] = useState<number | null>(null);
  const [filters, setFilters] = useState<RunFilters>(NO_FILTERS);
  const [starting, setStarting] = useState<number | null>(null);

  /** Replay the same stocks and days again on the run's bot, with that bot's settings as they are now. */
  const runAgain = (run: ReplayRun) => {
    const bot = Number(run.settings.bot ?? 1) || 1;
    const days = run.end_date !== run.start_date ? `${run.start_date} → ${run.end_date}` : run.start_date;
    const ask =
      `Run ${days} again on Bot ${bot} with ${run.symbols.join(", ")}?\n\n` +
      `It uses Bot ${bot}'s settings as they are now (not run #${run.id}'s), so you can compare the two runs. ` +
      `A replay already playing on Bot ${bot} stops. Practice money only.`;
    if (!window.confirm(ask)) return;
    setStarting(run.id);
    smaApi
      .replayStart(run.start_date, run.start_time || "09:15", 300, run.end_date, run.symbols, bot, Boolean(run.settings.as_armed))
      .then(() => {
        setError(null);
        load();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Run again failed"))
      .finally(() => setStarting(null));
  };

  /** One run as a PDF: its settings, totals, day-wise P&L and every trade. */
  const downloadPdf = useCallback(async (run: ReplayRun) => {
    setPrinting(run.id);
    try {
      const [full, book] = await Promise.all([smaApi.replayRun(run.id), smaApi.tradeBook("REPLAY")]);
      const { downloadBacktestPdf } = await import("@/lib/backtestPdf");
      await downloadBacktestPdf({
        run: full,
        trades: book.rows.filter((trade) => trade.run_id === run.id),
        strategy: strategyLabel(full.settings),
        settings: SETTING_ROWS.map(([label, value]) => [label, value(full.settings)]),
        reasons: REASON_SHORT,
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? `PDF for run #${run.id} failed: ${err.message}` : "PDF failed");
    } finally {
      setPrinting(null);
    }
  }, []);

  // The newest run opens by itself once, so its day-wise P&L shows without a click.
  const autoOpened = useRef(false);
  // How many of the newest runs to list; "Show older runs" raises it (the server keeps every run).
  const [limit, setLimit] = useState(RUNS_PAGE);
  const load = useCallback(() => {
    smaApi
      .replayRuns(limit)
      .then((rows) => {
        setRuns(rows);
        setError(null);
        if (!autoOpened.current && rows.length > 0) {
          autoOpened.current = true;
          setOpenId(rows[0].id);
        }
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Runs did not load"));
  }, [limit]);

  useEffect(() => {
    load();
    const poll = setInterval(load, 5000);
    return () => clearInterval(poll);
  }, [load]);

  useEffect(() => {
    if (openId == null) {
      setDetail(null);
      return;
    }
    let stop = false;
    const fetchOne = () =>
      smaApi
        .replayRun(openId)
        .then((run) => {
          if (!stop) setDetail(run);
        })
        .catch(() => {});
    fetchOne();
    const poll = setInterval(fetchOne, 5000);
    return () => {
      stop = true;
      clearInterval(poll);
    };
  }, [openId]);

  type RunKey = "id" | "days" | "strategy" | "trades" | "win" | "profit" | "loss" | "net" | "dd";
  const shownRuns = useMemo(() => (runs ?? []).filter((run) => runMatches(run, filters)), [runs, filters]);
  const filtering = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);
  const { sorted: sortedRuns, sort: runSort, onSort: onRunSort } = useSort<ReplayRun, RunKey>(shownRuns, (r, k) =>
    k === "id"
      ? r.id
      : k === "days"
        ? r.start_date
        : k === "strategy"
          ? strategyLabel(r.settings)
          : k === "trades"
            ? r.totals.trades
            : k === "win"
              ? r.totals.trades
                ? r.totals.win_rate
                : null
              : k === "profit"
                ? r.totals.profit
                : k === "loss"
                  ? r.totals.loss
                  : k === "net"
                    ? r.totals.net
                    : r.totals.max_drawdown
  );

  const remove = (run: ReplayRun) => {
    if (!window.confirm(`Delete run #${run.id} (${run.start_date} → ${run.end_date}) and its replay trades?`)) return;
    smaApi
      .deleteReplayRun(run.id)
      .then(() => {
        if (openId === run.id) setOpenId(null);
        load();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Delete failed"));
  };

  if (runs == null) {
    return (
      <div aria-busy="true" className="space-y-2 border-t border-white/10 p-4">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  const best = runs.filter((r) => r.totals.trades > 0).reduce<ReplayRun | null>(
    (top, r) => (top == null || r.totals.net > top.totals.net ? r : top),
    null
  );

  return (
    <div className="border-t border-white/10">
      <p className="px-4 pt-3 text-xs text-slate-400">
        Each replay run with the strategy settings it used. Run the same days again with other settings to compare. P&amp;L
        is practice money on Groww&apos;s past candles.
      </p>
      {error ? (
        <p role="alert" className="px-4 pt-2 text-xs text-rose-300">
          {error}
        </p>
      ) : null}
      {runs.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-400">
          No runs yet. Use “Replay past days” above: pick a From and To date and press Start.
        </p>
      ) : (
        <>
        <RunFilterBar filters={filters} onChange={setFilters} shown={shownRuns.length} total={runs.length} />
        {filtering && shownRuns.length === 0 ? (
          <p className="px-4 py-4 text-sm text-slate-400">No runs match these filters.</p>
        ) : null}
        <div className="overflow-x-auto px-2 py-3 sm:px-4">
          <table className="w-full min-w-[860px] whitespace-nowrap text-left text-xs">
            <thead className="text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <SortTh label="Run" k="id" sort={runSort} onSort={onRunSort} />
                <SortTh label="Days" k="days" text sort={runSort} onSort={onRunSort} />
                <SortTh label="Strategy" k="strategy" text sort={runSort} onSort={onRunSort} />
                <SortTh label="Trades" k="trades" num sort={runSort} onSort={onRunSort} />
                <SortTh label="Win %" k="win" num sort={runSort} onSort={onRunSort} />
                <SortTh label="Profit" k="profit" num sort={runSort} onSort={onRunSort} />
                <SortTh label="Loss" k="loss" num sort={runSort} onSort={onRunSort} />
                <SortTh label="P&L" k="net" num title="Profit + loss, before charges" sort={runSort} onSort={onRunSort} />
                <SortTh label="Max DD" k="dd" num sort={runSort} onSort={onRunSort} />
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {sortedRuns.map((run) => {
                const t = run.totals;
                const on = openId === run.id;
                return (
                  <Fragment key={run.id}>
                    <tr
                      className={clsx("cursor-pointer hover:bg-white/[0.03]", on && "bg-sky-500/[0.07]")}
                      onClick={() => setOpenId(on ? null : run.id)}
                    >
                      <td className="px-2 py-2 align-top">
                        <div className="flex items-center gap-1.5 font-semibold text-slate-100">
                          #{run.id}
                          {best?.id === run.id && runs.length > 1 ? <Badge color="green">Best net</Badge> : null}
                          {run.status === "RUNNING" ? <Badge color="violet">Playing</Badge> : null}
                          {run.status === "STOPPED" ? <Badge color="slate">Stopped</Badge> : null}
                          {run.status === "INTERRUPTED" ? <Badge color="amber">Cut short</Badge> : null}
                        </div>
                        <div className="text-slate-400">
                          {shortDate(run.start_date)}
                          {run.end_date !== run.start_date ? ` → ${shortDate(run.end_date)}` : ""}
                        </div>
                        <button
                          type="button"
                          aria-expanded={on}
                          aria-controls={`run-${run.id}-days`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenId(on ? null : run.id);
                          }}
                          className="mt-1 inline-flex min-h-9 items-center gap-1 rounded-md border border-sky-500/40 px-2 text-xs font-semibold text-sky-300 hover:bg-sky-500/10"
                        >
                          {on ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                          Day-wise P&amp;L
                        </button>
                      </td>
                      <td className="px-2 py-2 align-top font-mono text-slate-200">
                        {run.days_done}/{run.days_total}
                        <div className="font-sans text-xs text-slate-400">
                          {t.green_days}↑ {t.red_days}↓
                        </div>
                      </td>
                      <td className="max-w-[22rem] px-2 py-2 align-top text-slate-300">
                        <div className="truncate" title={strategyLabel(run.settings)}>
                          {strategyLabel(run.settings)}
                        </div>
                        <div className="truncate text-xs text-slate-400" title={run.symbols.join(", ")}>
                          {run.symbols.join(", ")}
                        </div>
                      </td>
                      <td className="px-2 py-2 text-right align-top font-mono text-slate-200">{t.trades}</td>
                      <td className="px-2 py-2 text-right align-top font-mono text-slate-200">{t.win_rate.toFixed(1)}%</td>
                      <td className="px-2 py-2 text-right align-top font-mono text-emerald-300">{signed(t.profit)}</td>
                      <td className="px-2 py-2 text-right align-top font-mono text-rose-300">{signed(t.loss)}</td>
                      <td className={clsx("px-2 py-2 text-right align-top font-mono font-semibold", pnlTone(t.net))}>
                        {signed(t.net)}
                      </td>
                      <td className="px-2 py-2 text-right align-top font-mono text-rose-300">{signed(t.max_drawdown)}</td>
                      <td className="px-2 py-2 text-right align-top">
                        {(run.status === "INTERRUPTED" || run.status === "STOPPED") &&
                        run.days_done < run.days_total &&
                        !scalpPickOf(run.settings) ? (
                          <button
                            type="button"
                            aria-label={`Resume run ${run.id}`}
                            title={`Carry on from day ${run.days_done + 1} of ${run.days_total}: finished days keep their results, the unfinished day plays again with the run's saved settings`}
                            onClick={(e) => {
                              e.stopPropagation();
                              const bot = Number(run.settings.bot ?? 1) || 1;
                              smaApi
                                .replayResume(run.id, 300, bot)
                                .then(() => {
                                  setError(null);
                                  load();
                                })
                                .catch((err: unknown) => setError(err instanceof Error ? err.message : "Resume failed"));
                            }}
                            className="mr-1 inline-flex min-h-9 items-center gap-1 rounded-md border border-violet-400/50 px-2 text-xs font-semibold text-violet-200 hover:bg-violet-500/10"
                          >
                            <Play size={12} aria-hidden /> Resume
                          </button>
                        ) : null}
                        {run.status !== "RUNNING" && !scalpPickOf(run.settings) && run.symbols.length > 0 ? (
                          <button
                            type="button"
                            aria-label={`Run ${run.id} again`}
                            title="Run again: the same stocks and days on the same bot, with the bot's settings as they are now"
                            disabled={starting === run.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              runAgain(run);
                            }}
                            className="mr-1 inline-flex min-h-9 min-w-9 items-center justify-center gap-1 rounded-md border border-emerald-500/40 px-2 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-60"
                          >
                            {starting === run.id ? (
                              <Loader2 size={13} aria-hidden className="animate-spin" />
                            ) : (
                              <RotateCcw size={13} aria-hidden />
                            )}
                            <span className="hidden lg:inline">Run again</span>
                          </button>
                        ) : null}
                        <button
                          type="button"
                          aria-label={`Download run ${run.id} as PDF`}
                          title="Download PDF: settings, totals, day-wise P&L and every trade"
                          disabled={printing === run.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            void downloadPdf(run);
                          }}
                          className="mr-1 inline-flex min-h-9 items-center gap-1 rounded-md border border-sky-500/40 px-2 text-xs font-semibold text-sky-300 hover:bg-sky-500/10 disabled:opacity-60"
                        >
                          {printing === run.id ? (
                            <Loader2 size={13} aria-hidden className="animate-spin" />
                          ) : (
                            <FileDown size={13} aria-hidden />
                          )}
                          PDF
                        </button>
                        <button
                          type="button"
                          aria-label={`Delete run ${run.id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            remove(run);
                          }}
                          className="inline-flex min-h-9 min-w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/5 hover:text-rose-300"
                        >
                          <Trash2 size={14} aria-hidden />
                        </button>
                      </td>
                    </tr>
                    {on ? (
                      <tr id={`run-${run.id}-days`} className="hidden bg-white/[0.015] sm:table-row">
                        <td colSpan={10} className="p-0 whitespace-normal">
                          <RunDetail run={detail?.id === run.id ? detail : null} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          {runs.length >= limit && limit < RUNS_MAX ? (
            <button
              type="button"
              className="mt-3 rounded-md border border-white/10 px-3 py-1.5 text-xs text-sky-300 hover:bg-white/5"
              onClick={() => setLimit((n) => Math.min(RUNS_MAX, n + RUNS_PAGE))}
            >
              Show older runs
            </button>
          ) : null}
        </div>
        </>
      )}
      {/* Phones: the runs table scrolls sideways, so the breakup sits below it at full width.
          Scrolls into view on open so the user doesn't have to swipe past the runs list. */}
      {openId != null ? (
        <MobileRunDetail runId={openId} detail={detail} />
      ) : null}
    </div>
  );
}

function RunFilterBar({
  filters,
  onChange,
  shown,
  total,
}: {
  filters: RunFilters;
  onChange: (next: RunFilters) => void;
  shown: number;
  total: number;
}) {
  const set = (key: keyof RunFilters, value: string) => onChange({ ...filters, [key]: value });
  const box =
    "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-xs text-slate-100 focus:border-sky-400 focus:outline-none";
  const changed = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);
  return (
    <div className="flex flex-wrap items-end gap-2 px-2 pt-3 sm:px-4" role="search" aria-label="Filter backtest runs">
      <label className="flex min-w-[10rem] flex-1 flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400 sm:flex-none">
        Stock or run #
        <input
          type="search"
          value={filters.text}
          onChange={(e) => set("text", e.target.value)}
          placeholder="e.g. SBIN or 42"
          className={clsx(box, "normal-case tracking-normal")}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
        Bot
        <select value={filters.bot} onChange={(e) => set("bot", e.target.value)} className={box}>
          <option value="ALL">All</option>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={String(n)}>
              Bot {n}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
        Result
        <select value={filters.result} onChange={(e) => set("result", e.target.value)} className={box}>
          <option value="ALL">All</option>
          <option value="PROFIT">In profit</option>
          <option value="LOSS">In loss</option>
          <option value="NONE">No trades</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
        Status
        <select value={filters.status} onChange={(e) => set("status", e.target.value)} className={box}>
          <option value="ALL">All</option>
          <option value="FINISHED">Finished</option>
          <option value="RUNNING">Playing</option>
          <option value="INTERRUPTED">Cut short</option>
          <option value="STOPPED">Stopped</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
        Days from
        <input type="date" value={filters.from} onChange={(e) => set("from", e.target.value)} className={box} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-slate-400">
        to
        <input type="date" value={filters.to} onChange={(e) => set("to", e.target.value)} className={box} />
      </label>
      {changed ? (
        <button
          type="button"
          onClick={() => onChange(NO_FILTERS)}
          className="min-h-9 rounded-md border border-white/10 px-2 text-xs text-sky-300 hover:bg-white/5"
        >
          Clear
        </button>
      ) : null}
      <span className="pb-2 text-xs text-slate-400" aria-live="polite">
        {changed ? `${shown} of ${total} runs` : `${total} runs`}
      </span>
    </div>
  );
}

function MobileRunDetail({ runId, detail }: { runId: number; detail: ReplayRun | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    // Only on phones (the inline <tr> handles sm+ already); let the DOM paint, then scroll.
    if (window.matchMedia("(min-width: 640px)").matches) return;
    const id = window.setTimeout(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    return () => window.clearTimeout(id);
  }, [runId]);
  return (
    <div ref={ref} className="scroll-mt-16 sm:hidden">
      <RunDetail run={detail?.id === runId ? detail : null} />
    </div>
  );
}

/** One run's breakup. A trade-book summary passes its own heading and settings. */
export function RunDetail({
  run,
  heading,
  subheading,
  settingsTitle = "Settings used",
  settingsRows,
}: {
  run: ReplayRun | null;
  heading?: string;
  subheading?: string;
  settingsTitle?: string;
  settingsRows?: [string, string][];
}) {
  if (!run) {
    return (
      <div aria-busy="true" className="border-t border-white/10 p-4">
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  const t = run.totals;
  const days = run.days ?? [];
  return <RunDetailBody run={run} t={t} days={days} heading={heading} subheading={subheading} settingsTitle={settingsTitle} settingsRows={settingsRows} />;
}

/** Each stock's result on one day of the run, best first. */
function stocksOnDay(stocks: ReplayStockRow[], date: string): { symbol: string; day: ReplayDayRow }[] {
  const out: { symbol: string; day: ReplayDayRow }[] = [];
  for (const s of stocks) {
    const day = s.days.find((d) => d.date === date);
    if (day && day.trades > 0) out.push({ symbol: s.symbol, day });
  }
  return out.sort((a, b) => b.day.net - a.day.net);
}

function RunDetailBody({
  run,
  t,
  days,
  heading,
  subheading,
  settingsTitle,
  settingsRows,
}: {
  run: ReplayRun;
  t: ReplayRun["totals"];
  days: ReplayDayRow[];
  heading?: string;
  subheading?: string;
  settingsTitle: string;
  settingsRows?: [string, string][];
}) {
  // A day opens to show how each stock did that day (the stock table below opens the other way).
  const [openDay, setOpenDay] = useState<string | null>(null);
  const stocks = run.stocks ?? [];
  type DayKey = "date" | "trades" | "wins" | "profit" | "loss" | "gross" | "charges" | "net" | "cumulative";
  const { sorted: sortedDays, sort: daySort, onSort: onDaySort } = useSort<ReplayDayRow, DayKey>(days, (d, k) => d[k]);
  return (
    <section aria-label={heading ?? `Run ${run.id} day-wise P&L`} className="border-t border-white/10 px-2 py-3 sm:px-4">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2 px-2">
        <h3 className="text-sm font-semibold text-slate-100">
          {heading ?? `Run #${run.id} · day-wise P&L`}
          <span className="ml-2 font-normal text-slate-400">
            {subheading ??
              (run.start_date
                ? `${dayLabel(run.start_date)}${run.end_date !== run.start_date ? ` → ${dayLabel(run.end_date)}` : ""} · start ${run.start_time}`
                : "")}
          </span>
        </h3>
        <span className={clsx("font-mono text-sm font-semibold", pnlTone(t.net))}>P&amp;L {signed(t.net)}</span>
      </div>
      {scalpPickOf(run.settings) ? <PickList pick={scalpPickOf(run.settings)!} /> : null}
      {(run.stocks ?? []).length > 1 ? <ByStock stocks={run.stocks ?? []} settings={run.settings} /> : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] whitespace-nowrap text-left text-xs">
          <thead className="text-xs uppercase tracking-wider text-slate-400">
            <tr>
              <SortTh label="Day" k="date" text sort={daySort} onSort={onDaySort} />
              <SortTh label="Trades" k="trades" num sort={daySort} onSort={onDaySort} />
              <SortTh label="W / L" k="wins" num title="Sort by wins" sort={daySort} onSort={onDaySort} />
              <SortTh label="Profit" k="profit" num sort={daySort} onSort={onDaySort} />
              <SortTh label="Loss" k="loss" num sort={daySort} onSort={onDaySort} />
              <SortTh label="Gross" k="gross" num sort={daySort} onSort={onDaySort} />
              <SortTh label="Charges" k="charges" num sort={daySort} onSort={onDaySort} />
              <SortTh label="P&L" k="net" num title="Profit + loss, before charges" sort={daySort} onSort={onDaySort} />
              <SortTh label="Running total" k="cumulative" num sort={daySort} onSort={onDaySort} />
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono">
            {days.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-2 py-4 font-sans text-slate-400">
                  {heading ? "No closed trades match these filters." : "No closed trades in this run yet."}
                </td>
              </tr>
            ) : (
              sortedDays.map((d) => {
                const isOpen = openDay === d.date;
                const perStock = stocks.length ? stocksOnDay(stocks, d.date) : [];
                return (
                <Fragment key={d.date}>
                <tr>
                  <td className="px-2 py-1.5 font-sans text-slate-200">
                    {perStock.length ? (
                      <button
                        type="button"
                        onClick={() => setOpenDay(isOpen ? null : d.date)}
                        aria-expanded={isOpen}
                        title="Show each stock on this day"
                        className="inline-flex items-center gap-1 hover:text-white"
                      >
                        {isOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
                        {dayLabel(d.date)}
                      </button>
                    ) : (
                      dayLabel(d.date)
                    )}
                    {/* A real run (not a trade-book summary) with one stock: its chart for that day. */}
                    {!heading && run.id > 0 && run.symbols.length === 1 ? (
                      <RunChartLink run={run} symbol={run.symbols[0]} date={d.date} />
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-200">{d.trades}</td>
                  <td className="px-2 py-1.5 text-right text-slate-300">
                    {d.wins} / {d.losses}
                  </td>
                  <td className="px-2 py-1.5 text-right text-emerald-300">{signed(d.profit)}</td>
                  <td className="px-2 py-1.5 text-right text-rose-300">{signed(d.loss)}</td>
                  <td className={clsx("px-2 py-1.5 text-right", pnlTone(d.gross))}>{signed(d.gross)}</td>
                  <td className="px-2 py-1.5 text-right text-amber-300">{inr(d.charges)}</td>
                  <td className={clsx("px-2 py-1.5 text-right font-semibold", pnlTone(d.net))}>{signed(d.net)}</td>
                  <td className={clsx("px-2 py-1.5 text-right", pnlTone(d.cumulative))}>{signed(d.cumulative)}</td>
                </tr>
                {isOpen
                  ? perStock.map(({ symbol, day: sd }) => (
                      <tr key={`${d.date}-${symbol}`} className="bg-white/[0.02] text-slate-300">
                        <td className="px-2 py-1 pl-8 font-sans font-semibold text-amber-300">
                          {symbol}
                          {!heading && run.id > 0 ? <RunChartLink run={run} symbol={symbol} date={d.date} /> : null}
                        </td>
                        <td className="px-2 py-1 text-right">{sd.trades}</td>
                        <td className="px-2 py-1 text-right">
                          {sd.wins} / {sd.losses}
                        </td>
                        <td className="px-2 py-1 text-right text-emerald-300">{signed(sd.profit)}</td>
                        <td className="px-2 py-1 text-right text-rose-300">{signed(sd.loss)}</td>
                        <td className={clsx("px-2 py-1 text-right", pnlTone(sd.gross))}>{signed(sd.gross)}</td>
                        <td className="px-2 py-1 text-right text-amber-300">{inr(sd.charges)}</td>
                        <td className={clsx("px-2 py-1 text-right", pnlTone(sd.net))}>{signed(sd.net)}</td>
                        <td className="px-2 py-1 text-right text-slate-500" title="Share of the day's P&L">
                          {d.net !== 0 ? `${Math.round((sd.net / Math.abs(d.net)) * 100)}% of day` : "—"}
                        </td>
                      </tr>
                    ))
                  : null}
                </Fragment>
                );
              })
            )}
          </tbody>
          {days.length > 0 ? (
            <tfoot className="border-t border-white/15 font-mono text-slate-100">
              <tr>
                <td className="px-2 py-2 font-sans font-semibold">Total · {days.length} days</td>
                <td className="px-2 py-2 text-right">{t.trades}</td>
                <td className="px-2 py-2 text-right">
                  {t.wins} / {t.losses}
                </td>
                <td className="px-2 py-2 text-right text-emerald-300">{signed(t.profit)}</td>
                <td className="px-2 py-2 text-right text-rose-300">{signed(t.loss)}</td>
                <td className={clsx("px-2 py-2 text-right", pnlTone(t.gross))}>{signed(t.gross)}</td>
                <td className="px-2 py-2 text-right text-amber-300">{inr(t.charges)}</td>
                <td className={clsx("px-2 py-2 text-right font-semibold", pnlTone(t.net))}>{signed(t.net)}</td>
                <td className="px-2 py-2 text-right text-slate-400">max DD {signed(t.max_drawdown)}</td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      <div className="mt-3 px-2">
        <div className="mb-1 text-xs uppercase tracking-wider text-slate-400">{settingsTitle}</div>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
          {(settingsRows ?? SETTING_ROWS.map(([label, fmt]) => [label, fmt(run.settings)] as [string, string])).map(
            ([label, value]) => (
              <div key={label} className="flex justify-between gap-3 border-b border-white/5 py-1">
                <dt className="text-slate-400">{label}</dt>
                <dd className="text-right text-slate-200">{value}</dd>
              </div>
            )
          )}
          <div className="flex justify-between gap-3 border-b border-white/5 py-1">
            <dt className="text-slate-400">Stocks</dt>
            <dd className="text-right text-slate-200">{run.symbols.join(", ")}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

/** The run split by stock. Each row opens that stock's own day-wise P&L. */
function ByStock({ stocks, settings }: { stocks: ReplayStockRow[]; settings: Settings }) {
  const [open, setOpen] = useState<string | null>(null);
  const base = strategyLabel(settings);
  type StockKey = "symbol" | "trades" | "wins" | "win" | "profit" | "loss" | "gross" | "charges" | "net" | "dd";
  const { sorted, sort, onSort } = useSort<ReplayStockRow, StockKey>(stocks, (r, k) =>
    k === "symbol"
      ? r.symbol
      : k === "win"
        ? r.totals.trades
          ? r.totals.win_rate
          : null
        : k === "dd"
          ? r.totals.max_drawdown
          : r.totals[k]
  );
  return (
    <div className="mb-4">
      <div className="mb-1 px-2 text-xs uppercase tracking-wider text-slate-400">By stock</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] whitespace-nowrap text-left text-xs">
          <thead className="text-xs uppercase tracking-wider text-slate-400">
            <tr>
              <SortTh label="Stock" k="symbol" text sort={sort} onSort={onSort} />
              <SortTh label="Trades" k="trades" num sort={sort} onSort={onSort} />
              <SortTh label="W / L" k="wins" num title="Sort by wins" sort={sort} onSort={onSort} />
              <SortTh label="Win %" k="win" num sort={sort} onSort={onSort} />
              <SortTh label="Profit" k="profit" num sort={sort} onSort={onSort} />
              <SortTh label="Loss" k="loss" num sort={sort} onSort={onSort} />
              <SortTh label="Gross" k="gross" num sort={sort} onSort={onSort} />
              <SortTh label="Charges" k="charges" num sort={sort} onSort={onSort} />
              <SortTh label="P&L" k="net" num title="Profit + loss, before charges" sort={sort} onSort={onSort} />
              <SortTh label="Max DD" k="dd" num sort={sort} onSort={onSort} />
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono">
            {sorted.map((s) => {
              const t = s.totals;
              const own = s.strategy ? strategyLabel({ ...settings, ...s.strategy }) : null;
              const isOpen = open === s.symbol;
              return (
                <Fragment key={s.symbol}>
                  <tr>
                    <td className="px-2 py-1.5 font-sans">
                      <button
                        type="button"
                        disabled={s.days.length === 0}
                        onClick={() => setOpen(isOpen ? null : s.symbol)}
                        aria-expanded={isOpen}
                        className="inline-flex items-center gap-1 font-semibold text-amber-300 disabled:cursor-default"
                      >
                        {s.days.length > 0 ? (
                          isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />
                        ) : (
                          <span className="w-[13px]" />
                        )}
                        {s.symbol}
                      </button>
                      {/* A stock with its own settings gets a small chip; the full strategy is in its tooltip, not repeated per row. */}
                      {own && own !== base ? (
                        <span
                          className="ml-1.5 rounded-full border border-violet-400/40 px-1.5 py-0.5 text-[10px] font-medium text-violet-300"
                          title={`Own settings: ${own}`}
                        >
                          Own
                        </span>
                      ) : null}
                    </td>
                    <td className="px-2 py-1.5 text-right text-slate-200">{t.trades}</td>
                    <td className="px-2 py-1.5 text-right text-slate-300">
                      {t.wins} / {t.losses}
                    </td>
                    <td className="px-2 py-1.5 text-right text-slate-300">{t.trades ? `${t.win_rate.toFixed(1)}%` : "—"}</td>
                    <td className="px-2 py-1.5 text-right text-emerald-300">{signed(t.profit)}</td>
                    <td className="px-2 py-1.5 text-right text-rose-300">{signed(t.loss)}</td>
                    <td className={clsx("px-2 py-1.5 text-right", pnlTone(t.gross))}>{signed(t.gross)}</td>
                    <td className="px-2 py-1.5 text-right text-amber-300">{inr(t.charges)}</td>
                    <td className={clsx("px-2 py-1.5 text-right font-semibold", pnlTone(t.net))}>{signed(t.net)}</td>
                    <td className="px-2 py-1.5 text-right text-slate-400">{signed(t.max_drawdown)}</td>
                  </tr>
                  {isOpen
                    ? s.days.map((d) => (
                        <tr key={`${s.symbol}-${d.date}`} className="bg-white/[0.02] text-slate-300">
                          <td className="px-2 py-1 pl-8 font-sans text-slate-400">{dayLabel(d.date)}</td>
                          <td className="px-2 py-1 text-right">{d.trades}</td>
                          <td className="px-2 py-1 text-right">
                            {d.wins} / {d.losses}
                          </td>
                          <td className="px-2 py-1 text-right">—</td>
                          <td className="px-2 py-1 text-right text-emerald-300">{signed(d.profit)}</td>
                          <td className="px-2 py-1 text-right text-rose-300">{signed(d.loss)}</td>
                          <td className={clsx("px-2 py-1 text-right", pnlTone(d.gross))}>{signed(d.gross)}</td>
                          <td className="px-2 py-1 text-right text-amber-300">{inr(d.charges)}</td>
                          <td className={clsx("px-2 py-1 text-right", pnlTone(d.net))}>{signed(d.net)}</td>
                          <td className={clsx("px-2 py-1 text-right", pnlTone(d.cumulative))} title="Running total for this stock">
                            {signed(d.cumulative)}
                          </td>
                        </tr>
                      ))
                    : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A Scalp-pick run: which stocks each day picked at the pick time, and why. */
function PickList({ pick }: { pick: ScalpPickInfo }) {
  const days = Object.keys(pick.picks ?? {}).sort();
  return (
    <div className="mb-4 px-2">
      <div className="mb-1 text-xs uppercase tracking-wider text-slate-400">
        Scalp picks · top {pick.top_n} at {pick.pick_time}
        {pick.universe ? ` from ${pick.universe} stocks` : ""} · ATR ≥ {pick.min_atr_pct}%/min · ≥ ₹{pick.min_value_cr} cr
        {pick.require_bias ? " · with a bias" : ""} · spread not checked
      </div>
      {days.length === 0 ? (
        <p className="text-xs text-slate-400">No day has been picked yet.</p>
      ) : (
        <div className="space-y-0.5 text-xs">
          {days.map((d) => {
            const list = pick.picks?.[d] ?? [];
            return (
              <div key={d} className="flex flex-wrap items-baseline gap-x-3 border-b border-white/5 py-1">
                <span className="w-28 shrink-0 text-slate-400">{dayLabel(d)}</span>
                {list.length === 0 ? (
                  <span className="text-slate-500">nothing ready at {pick.pick_time} — no trades</span>
                ) : (
                  list.map((p) => (
                    <span key={p.symbol} className="font-mono" title={`ATR ${p.atr_pct ?? "—"}%/min · ₹${p.value_cr ?? "—"} cr · 5m ${p.move_5m_pct ?? "—"}%`}>
                      <span className="font-semibold text-amber-300">{p.symbol}</span>{" "}
                      <span className="text-slate-400">{p.score.toFixed(0)}</span>{" "}
                      <span className={p.bias === "LONG" ? "text-sky-300" : p.bias === "SHORT" ? "text-violet-300" : "text-slate-500"}>
                        {p.bias === "NONE" ? "" : p.bias}
                      </span>
                    </span>
                  ))
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
