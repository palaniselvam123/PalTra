"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, FileDown, Loader2, Trash2 } from "lucide-react";
import clsx from "clsx";
import { inr, smaApi, type ReplayRun, type ReplayStockRow, type ScalpPick, type ScalpPickRule } from "@/lib/smaApi";
import { Badge, Skeleton, pnlTone } from "./ui";
import { REASON_SHORT } from "./TradeHistoryTable";

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
  } else parts.push(`SMA ${s.sma_fast ?? 9}/${s.sma_slow ?? 21}`);
  if (s.flip_orders) parts.push("FLIPPED (buy signals sell)");
  if (s.use_stop === false) parts.push("no stop");
  else if (s.stop_type === "SMA_GAP") parts.push(`SMA-gap stop ×${s.gap_sl_mult} · target ×${s.gap_tp_mult} · min ${s.gap_min_pct}%`);
  else if (s.stop_type === "TSL")
    parts.push(
      `TSL ₹${s.tsl_sl_points ?? 20} · trail ₹${s.tsl_trail_points ?? 10}` +
        (Number(s.tsl_target_points ?? 0) > 0 ? ` · target ₹${s.tsl_target_points}` : "")
    );
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

/** The stop part of a strategy in a few words, e.g. "1.5× ATR" or "TSL ₹20 / ₹10". */
export function stopShort(s: Settings): string {
  if (s.use_stop === false) return "No stop";
  if (s.stop_type === "SMA_GAP") return `SMA-gap ×${s.gap_sl_mult}/×${s.gap_tp_mult}`;
  if (s.stop_type === "TSL") {
    const target = Number(s.tsl_target_points ?? 0) > 0 ? ` · T ₹${s.tsl_target_points}` : "";
    return `TSL ₹${s.tsl_sl_points ?? 20} / ₹${s.tsl_trail_points ?? 10}${target}`;
  }
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
  if (Number(s.gap_entry_delay_min ?? 0) > 0) parts.push(`wait ${s.gap_entry_delay_min}m`);
  if (Number(s.gap_entry_window_min ?? 0) > 0) parts.push(`within ${s.gap_entry_window_min}m`);
  return parts.join(" · ");
}

const BB_EXIT_TEXT: Record<string, string> = { BAND: "band target", MIDDLE: "middle band", BOTH: "band + middle" };

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
            ? `Trailing: ₹${s.tsl_sl_points ?? 20} from entry, every ₹${s.tsl_trail_points ?? 10}, target ${
                Number(s.tsl_target_points ?? 0) > 0 ? `₹${s.tsl_target_points}` : "none"
              }`
          : `${s.atr_multiplier}× ATR (${s.atr_period})`,
  ],
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
  const load = useCallback(() => {
    smaApi
      .replayRuns()
      .then((rows) => {
        setRuns(rows);
        setError(null);
        if (!autoOpened.current && rows.length > 0) {
          autoOpened.current = true;
          setOpenId(rows[0].id);
        }
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Runs did not load"));
  }, []);

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
        <div className="overflow-x-auto px-2 py-3 sm:px-4">
          <table className="w-full min-w-[860px] whitespace-nowrap text-left text-xs">
            <thead className="text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-2 py-2">Run</th>
                <th className="px-2 py-2">Days</th>
                <th className="px-2 py-2">Strategy</th>
                <th className="px-2 py-2 text-right">Trades</th>
                <th className="px-2 py-2 text-right">Win %</th>
                <th className="px-2 py-2 text-right">Profit</th>
                <th className="px-2 py-2 text-right">Loss</th>
                <th className="px-2 py-2 text-right" title="Profit + loss, before charges">P&amp;L</th>
                <th className="px-2 py-2 text-right">Max DD</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {runs.map((run) => {
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
                          className="mt-1 inline-flex min-h-8 items-center gap-1 rounded-md border border-sky-500/40 px-2 text-[11px] font-semibold text-sky-300 hover:bg-sky-500/10"
                        >
                          {on ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                          Day-wise P&amp;L
                        </button>
                      </td>
                      <td className="px-2 py-2 align-top font-mono text-slate-200">
                        {run.days_done}/{run.days_total}
                        <div className="font-sans text-[11px] text-slate-400">
                          {t.green_days}↑ {t.red_days}↓
                        </div>
                      </td>
                      <td className="max-w-[22rem] px-2 py-2 align-top text-slate-300">
                        <div className="truncate" title={strategyLabel(run.settings)}>
                          {strategyLabel(run.settings)}
                        </div>
                        <div className="truncate text-[11px] text-slate-400" title={run.symbols.join(", ")}>
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
                        <button
                          type="button"
                          aria-label={`Download run ${run.id} as PDF`}
                          title="Download PDF: settings, totals, day-wise P&L and every trade"
                          disabled={printing === run.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            void downloadPdf(run);
                          }}
                          className="mr-1 inline-flex min-h-8 items-center gap-1 rounded-md border border-sky-500/40 px-2 text-[11px] font-semibold text-sky-300 hover:bg-sky-500/10 disabled:opacity-60"
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
                          className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/5 hover:text-rose-300"
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
        </div>
      )}
      {/* Phones: the runs table scrolls sideways, so the breakup sits below it at full width. */}
      {openId != null ? (
        <div className="sm:hidden">
          <RunDetail run={detail?.id === openId ? detail : null} />
        </div>
      ) : null}
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
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <th className="px-2 py-2">Day</th>
              <th className="px-2 py-2 text-right">Trades</th>
              <th className="px-2 py-2 text-right">W / L</th>
              <th className="px-2 py-2 text-right">Profit</th>
              <th className="px-2 py-2 text-right">Loss</th>
              <th className="px-2 py-2 text-right">Gross</th>
              <th className="px-2 py-2 text-right">Charges</th>
              <th className="px-2 py-2 text-right" title="Profit + loss, before charges">P&amp;L</th>
              <th className="px-2 py-2 text-right">Running total</th>
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
              days.map((d) => (
                <tr key={d.date}>
                  <td className="px-2 py-1.5 font-sans text-slate-200">{dayLabel(d.date)}</td>
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
              ))
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
        <div className="mb-1 text-[11px] uppercase tracking-wider text-slate-400">{settingsTitle}</div>
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
  return (
    <div className="mb-4">
      <div className="mb-1 px-2 text-[11px] uppercase tracking-wider text-slate-400">By stock</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] whitespace-nowrap text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <th className="px-2 py-2">Stock</th>
              <th className="px-2 py-2 text-right">Trades</th>
              <th className="px-2 py-2 text-right">W / L</th>
              <th className="px-2 py-2 text-right">Win %</th>
              <th className="px-2 py-2 text-right">Profit</th>
              <th className="px-2 py-2 text-right">Loss</th>
              <th className="px-2 py-2 text-right">Gross</th>
              <th className="px-2 py-2 text-right">Charges</th>
              <th className="px-2 py-2 text-right" title="Profit + loss, before charges">P&amp;L</th>
              <th className="px-2 py-2 text-right">Max DD</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono">
            {stocks.map((s) => {
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
                      {own && own !== base ? (
                        <div className="pl-[18px] text-[11px] font-normal text-violet-300" title="This stock traded with its own settings">
                          {own}
                        </div>
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
      <div className="mb-1 text-[11px] uppercase tracking-wider text-slate-400">
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
                      <span className={p.bias === "LONG" ? "text-emerald-400" : p.bias === "SHORT" ? "text-rose-400" : "text-slate-500"}>
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
