"use client";

import { NewTag } from "@/components/ui/NewTag";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import clsx from "clsx";
import { Check, Eye, MessageCircle, Sparkles } from "lucide-react";
import type { SmaConfig } from "@/lib/smaApi";

/**
 * "What do you trade with?": tick the ways of trading you use and the strategy form shows only their
 * settings. Display only: it hides parts of the form, never changes or saves a setting. A setting that is
 * on stays listed in "What's switched on" whatever is ticked here.
 */

export type StyleId = "sma" | "gap" | "trend" | "colour" | "turn" | "ind" | "bots" | "review";

export type TradeStyle = {
  id: StyleId;
  title: string;
  /** One plain line: what the bot does. */
  what: string;
  /** When it tends to suit. */
  best: string;
  /** What the form shows for it. */
  shows: string;
  /** Is any of its settings on in this form? */
  on: (f: SmaConfig) => boolean;
};

const yes = (v: unknown) => v === true || v === "true";
const num = (v: unknown) => Number(v ?? 0) || 0;
const txt = (v: unknown) => String(v ?? "").trim().toUpperCase();
const picked = (v: unknown, off = "OFF") => txt(v) !== "" && txt(v) !== off;

export const STYLES: TradeStyle[] = [
  {
    id: "sma",
    title: "SMA cross",
    what: "Buy when the fast average crosses above the slow one, sell when it crosses below.",
    best: "The base strategy. Works when a stock makes clean moves; loses in sideways chop.",
    shows: "Late entry, ADX strength check, cross exit on another candle size, slow-SMA exit",
    on: (f) => txt(f.entry_mode || "SMA") === "SMA",
  },
  {
    id: "gap",
    title: "SMA gap (band)",
    what: "Wait until the two averages are a set distance apart before entering; leave when the gap fades.",
    best: "Skips weak crosses. Suits choppy stocks where most crosses fail.",
    shows: "SMA gap mode (entry and exit levels, fade rules), the gap range check",
    on: (f) => yes(f.use_gap_mode) || yes(f.use_gap_long) || yes(f.use_gap_short),
  },
  {
    id: "trend",
    title: "Uptrend / downtrend",
    what: "Ride a stock that is already trending up or down, without waiting for a fresh cross.",
    best: "Strong one-way days and stocks picked from the Scalp page's strong-trend scans.",
    shows: "Join the morning trend, always in a trade, Supertrend, EMA, 52-week high, price level, opening range, day high/low guard",
    on: (f) =>
      yes(f.join_trend) || yes(f.always_in) || yes(f.use_supertrend) || yes(f.st_exit) || yes(f.use_ema) || yes(f.ema_exit) ||
      yes(f.use_high52) || yes(f.use_level) || yes(f.use_fresh) || yes(f.use_or_volume) || yes(f.use_or_vwap) || yes(f.use_or_spread) || yes(f.use_or_break) ||
      yes(f.day_extreme_guard) || yes(f.tall_pullback),
  },
  {
    id: "colour",
    title: "Candle colour & patterns",
    what: "Trade the candles themselves: green buys, red sells; or candle patterns; or the day's second candle.",
    best: "Fast scalps on active stocks. Needs a body-size floor so tiny candles do not flip the trade.",
    shows: "Candle direction check, Heikin Ashi colours, smaller-candle rules, counter candles, entry-candle check",
    on: (f) =>
      ["COLOUR", "PATTERN", "NTH_CANDLE"].includes(txt(f.entry_mode)) || yes(f.use_candle_dir) || yes(f.ha_colours) ||
      txt(f.sc_minutes) !== "" || txt(f.small_exit_minutes) !== "" || num(f.counter_exit_count) > 0 || yes(f.entry_candle_confirm) || yes(f.extreme_entry),
  },
  {
    id: "turn",
    title: "Reversals (SMA 9 turn)",
    what: "Act when the fast line bends or makes a V, before the averages actually cross.",
    best: "Getting out (or in) early at turning points. More trades, more false alarms.",
    shows: "SMA 9 turn entry and alert, slope / V-turn / deceleration exits, early reversal entry, reverse on exit",
    on: (f) =>
      yes(f.turn_entry) || yes(f.turn_alert) || yes(f.counter_entry) || yes(f.slope_exit) || yes(f.vturn_exit) || yes(f.decel_exit) ||
      txt(f.reverse_on_exit) !== "",
  },
  {
    id: "ind",
    title: "Indicator checks",
    what: "Extra yes / no checks before a trade: VWAP, RSI, MACD, Bollinger, volume, cumulative delta.",
    best: "Filtering bad entries of any strategy above. Start with one or two; each one skips trades.",
    shows: "VWAP, volume, RSI, Bollinger, MACD, CVD, EMA, Supertrend checks and their exits",
    on: (f) =>
      yes(f.use_vwap) || yes(f.use_volume) || yes(f.use_density) || yes(f.use_rsi) || yes(f.use_bollinger) || yes(f.use_macd) ||
      yes(f.use_cvd) || yes(f.use_adx_filter) || picked(f.bb_exit) || yes(f.macd_exit) || yes(f.cvd_div_exit),
  },
  {
    id: "bots",
    title: "Several bots (hand-offs)",
    what: "Move a stock to another bot when it turns choppy, breaks out or hits its stop.",
    best: "Only when you run Bots 1-3 together. Leave off while learning one bot.",
    shows: "Recovery hold, hand-off to Bot 2 / Bot 3, trend breakout, stop-loss hand-off, un-arm when the trend fades",
    on: (f) =>
      yes(f.recovery_hold) || yes(f.breakout_handoff) || picked(f.stop_handoff) || num(f.chop_losses) > 0 || yes(f.vturn_handoff) ||
      picked(f.handoff_candle) || yes(f.fade_unarm),
  },
  {
    id: "review",
    title: "Ask me first (review)",
    what: "When a trade looks uncertain the bot asks you: exit or wait.",
    best: "Longer candles (5 minutes and up) while you are watching the screen.",
    shows: "1-minute review: what counts as uncertain, the default answer",
    on: (f) => yes(f.review_on),
  },
];

/** The styles whose settings are on in this form. */
export function detectStyles(f: SmaConfig): StyleId[] {
  return STYLES.filter((s) => s.on(f)).map((s) => s.id);
}

export type StyleView = { all: boolean; picks: StyleId[] };

const KEY = "sma.settings.styles";

/** The owner's ticks, kept per browser. Until they choose, the styles in use are ticked. */
export function useStyleView(form: SmaConfig | null): [StyleView, (v: StyleView) => void] {
  const [saved, setSaved] = useState<StyleView | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (raw) {
        const v = JSON.parse(raw) as StyleView;
        const ids = new Set(STYLES.map((s) => s.id));
        setSaved({ all: Boolean(v.all), picks: (v.picks ?? []).filter((p) => ids.has(p)) });
      }
    } catch {
      // No storage: the ticks last for this visit.
    }
    setReady(true);
  }, []);
  const set = (v: StyleView) => {
    setSaved(v);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(v));
    } catch {
      // Ignored: see above.
    }
  };
  if (saved) return [saved, set];
  // Nothing chosen yet: everything shows until the form has loaded, then the styles in use.
  return [!ready || !form ? { all: true, picks: [] } : { all: false, picks: detectStyles(form) }, set];
}

/** The set of styles the form shows; null = everything. */
export const StyleCtx = createContext<Set<string> | null>(null);

/** Shows its children only while one of its styles (space separated) is ticked. Layout is untouched. */
export function Only({ s, children }: { s: string; children: ReactNode }) {
  const shown = useContext(StyleCtx);
  const show = !shown || s.split(" ").some((x) => shown.has(x));
  return <div className={show ? "contents" : "hidden"}>{children}</div>;
}

/** Opens the "Ask the bot" widget with a question typed in (the owner still presses Send). */
function askAssistant(question: string) {
  window.dispatchEvent(new CustomEvent("paltra:ask", { detail: question }));
}

export function StylePicker({
  form,
  view,
  onChange,
  botLabel,
}: {
  form: SmaConfig;
  view: StyleView;
  onChange: (v: StyleView) => void;
  /** "Bot 3", "the Research desk", or a stock's name: used in the question for the assistant. */
  botLabel: string;
}) {
  const inUse = detectStyles(form);
  const picks = new Set(view.picks);
  const toggle = (id: StyleId) => {
    const next = new Set(picks);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange({ all: false, picks: STYLES.map((s) => s.id).filter((s) => next.has(s)) });
  };
  const hiddenOn = view.all ? [] : STYLES.filter((s) => inUse.includes(s.id) && !picks.has(s.id));
  const chosen = STYLES.filter((s) => picks.has(s.id));
  const question = chosen.length
    ? `On ${botLabel} I want to trade with: ${chosen.map((s) => s.title).join(", ")}. Which settings should be on and which should be off? Give me a simple starting set-up and propose the changes.`
    : `I am not sure which strategy to use on ${botLabel}. Look at its settings and recent trades and tell me in simple words which way of trading suits it, and propose the changes.`;
  const small = "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400";
  return (
    <div id="sma-styles" className="mt-4 scroll-mt-24 rounded-lg border border-violet-400/30 bg-violet-400/[0.05] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-100">
          What do you want to trade with? <NewTag id="set-style-picker" />
        </h3>
        <span className="text-xs text-slate-400">Tick one or more. The form below shows only what they need. Nothing is changed or saved by ticking.</span>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4" role="group" aria-label="Ways of trading">
        {STYLES.map((s) => {
          const ticked = !view.all && picks.has(s.id);
          const used = inUse.includes(s.id);
          return (
            <button
              key={s.id}
              type="button"
              aria-pressed={ticked}
              onClick={() => toggle(s.id)}
              title={`Shows: ${s.shows}`}
              className={clsx(
                "flex flex-col items-start gap-1 rounded-lg p-2.5 text-left ring-1 ring-inset transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400",
                ticked ? "bg-violet-500/20 ring-violet-400/60" : "bg-white/[0.02] ring-white/10 hover:bg-white/[0.05]"
              )}
            >
              <span className="flex w-full items-center gap-2">
                <span
                  aria-hidden
                  className={clsx(
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded ring-1 ring-inset",
                    ticked ? "bg-violet-500 text-white ring-violet-300" : "ring-slate-400/70"
                  )}
                >
                  {ticked ? <Check size={12} /> : null}
                </span>
                <span className="text-sm font-semibold text-slate-100">{s.title}</span>
                {used ? (
                  <span className="ml-auto shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">in use</span>
                ) : null}
              </span>
              <span className="text-xs leading-snug text-slate-300">{s.what}</span>
              <span className="text-[11px] leading-snug text-slate-400">{s.best}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => onChange({ all: false, picks: [] })} className={clsx(small, "text-slate-200 ring-white/15 hover:bg-white/5")}>
          Uncheck all
        </button>
        <button
          type="button"
          onClick={() => onChange({ all: false, picks: inUse })}
          title="Tick the ways of trading whose settings are switched on now"
          className={clsx(small, "text-slate-200 ring-white/15 hover:bg-white/5")}
        >
          <Sparkles size={13} aria-hidden /> Tick what is in use
        </button>
        <button
          type="button"
          aria-pressed={view.all}
          onClick={() => onChange({ all: !view.all, picks: view.picks })}
          className={clsx(small, view.all ? "bg-sky-400/20 text-sky-100 ring-sky-400/50" : "text-slate-200 ring-white/15 hover:bg-white/5")}
        >
          <Eye size={13} aria-hidden /> Show every setting
        </button>
        <button
          type="button"
          onClick={() => askAssistant(question)}
          title="Opens the assistant with this question typed in. It can only propose changes; you apply them."
          className={clsx(small, "ml-auto bg-violet-500/20 text-violet-100 ring-violet-400/50 hover:bg-violet-500/30")}
        >
          <MessageCircle size={13} aria-hidden /> Ask the assistant to set this up
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400" role="status">
        {view.all
          ? "Showing every setting."
          : chosen.length
            ? `Showing: the basics (averages, candle size, how a trade opens, stop-loss, size and times) plus ${chosen.map((s) => s.title).join(", ")}.`
            : "Nothing ticked: showing only the basics (averages, candle size, how a trade opens, stop-loss, size and times)."}
      </p>
      {hiddenOn.length ? (
        <p className="mt-2 rounded-md border border-amber-400/40 bg-amber-400/[0.08] p-2 text-xs leading-snug text-amber-100">
          Still switched on but hidden from the form: <b>{hiddenOn.map((s) => s.title).join(", ")}</b>. They keep working; see
          &quot;What&apos;s switched on&quot; below to turn them off.{" "}
          <button
            type="button"
            onClick={() => onChange({ all: false, picks: STYLES.map((s) => s.id).filter((id) => picks.has(id) || inUse.includes(id)) })}
            className="font-semibold underline hover:text-white"
          >
            Show them
          </button>
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Lose less in sideways markets": a starting set of existing settings, shown with the form's values now and put
 * into the form on request. Nothing is saved until the owner presses Save. Display / form-fill only.
 */
type Fill = Partial<Record<keyof SmaConfig, string | boolean>>;

type Step = { id: string; title: string; why: string; now: string; done: boolean; fill: Fill; optional?: boolean };

function sidewaysSteps(f: SmaConfig): Step[] {
  const adx = num(f.adx_threshold);
  const cap = num(f.max_trades_per_stock);
  const counter = num(f.counter_exit_count);
  const chop = num(f.chop_losses);
  return [
    {
      id: "adx",
      title: "Only enter when the trend is strong (ADX at least 20)",
      why: "ADX measures how strongly a stock is moving one way. In a sideways market it is low, so the cross is skipped.",
      now: yes(f.use_adx_filter) ? `on, at least ${adx}` : "off",
      done: yes(f.use_adx_filter) && adx >= 20,
      fill: { use_adx_filter: true, adx_threshold: String(Math.max(adx, 20)) },
    },
    {
      id: "cap",
      title: "Stop trading a stock after 3 trades in a day",
      why: "A stock that keeps giving signals is going back and forth. After three tries the bot leaves it alone until tomorrow.",
      now: cap > 0 ? `${cap} a day` : "no limit",
      done: cap > 0 && cap <= 3,
      fill: { max_trades_per_stock: "3" },
    },
    {
      id: "counter",
      title: "Get out after 3 candles in a row against the trade",
      why: "Without it the bot waits for the averages to cross back, which is late in a sideways market. This leaves sooner.",
      now: counter > 0 ? `${counter} candles` : "off",
      done: counter > 0 && counter <= 3,
      fill: { counter_exit_count: "3", counter_need_fast: true },
    },
    {
      id: "chop",
      title: "After 2 losing trades, move the stock to Bot 2",
      why: "Bot 2 is the sideways bot. Only useful when Bot 2 is running; otherwise leave this unticked.",
      now: chop > 0 ? `after ${chop} losses` : "off",
      done: chop > 0 && chop <= 2,
      fill: { chop_losses: "2" },
      optional: true,
    },
  ];
}

export function SidewaysGuard({ form, onFill }: { form: SmaConfig; onFill: (values: Fill) => void }) {
  const steps = sidewaysSteps(form);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const isTicked = (s: Step) => ticked[s.id] ?? (!s.optional && !s.done);
  const chosen = steps.filter((s) => !s.done && isTicked(s));
  const core = steps.filter((s) => !s.optional);
  const coreDone = core.filter((s) => s.done).length;
  return (
    <details id="sma-sideways" className="group/sw mt-3 scroll-mt-24 rounded-lg border border-amber-400/30 bg-amber-400/[0.04]">
      <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-2 rounded-lg px-3 text-sm font-semibold text-slate-100 hover:bg-white/[0.03] [&::-webkit-details-marker]:hidden">
        Losing money in sideways markets? A starting set-up <NewTag id="set-sideways" link={false} />
        <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", coreDone === core.length ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300")}>
          {coreDone} of {core.length} on
        </span>
        <span className="text-xs font-normal text-slate-400">— three settings that skip weak signals and leave earlier. Click to open.</span>
      </summary>
      <div className="px-3 pb-3">
        <p className="text-xs leading-relaxed text-slate-300">
          A sideways market is a stock that goes up and down without getting anywhere. The SMA cross loses there because every small
          turn looks like a signal. These settings already exist in the form; this box only fills them in for you.
        </p>
        <ul className="mt-2 space-y-1.5">
          {steps.map((s) => (
            <li key={s.id} className="rounded-md border border-white/10 bg-white/[0.02] p-2">
              <label className="flex items-start gap-2 text-sm text-slate-200">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={s.done || isTicked(s)}
                  disabled={s.done}
                  onChange={(e) => setTicked((t) => ({ ...t, [s.id]: e.target.checked }))}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-semibold">{s.title}</span>
                  {s.optional ? <span className="ml-2 text-[11px] text-slate-400">optional</span> : null}
                  <span className={clsx("ml-2 text-[11px]", s.done ? "text-emerald-300" : "text-slate-400")}>
                    {s.done ? `already set (${s.now})` : `now: ${s.now}`}
                  </span>
                  <span className="block text-xs leading-snug text-slate-400">{s.why}</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={!chosen.length}
            onClick={() => onFill(Object.assign({}, ...chosen.map((s) => s.fill)) as Fill)}
            className="inline-flex min-h-9 items-center rounded-md bg-amber-500/20 px-3 text-xs font-semibold text-amber-100 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-500/30 disabled:opacity-50"
          >
            Put the ticked ones in the form
          </button>
          <span className="text-xs text-slate-400">Then press Save at the bottom. Nothing changes until you do.</span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-slate-400">
          This is a sensible starting point, not a proven winner: each setting also skips some good trades. Before using it with real
          money, run a{" "}
          <a href="/scalp/#backtest" className="text-sky-300 underline hover:text-sky-200">
            Backtest
          </a>{" "}
          with and without it, and use the Scalp page&apos;s Sideways test to see which of your stocks are the sideways ones.
        </p>
      </div>
    </details>
  );
}

/** "Fresh trend" as one strategy to test: every setting it needs, with the form's value now. Form-fill only. */
function freshSteps(f: SmaConfig): Step[] {
  const num = (v: unknown, d: number) => (Number.isFinite(Number(v)) ? Number(v) : d);
  const candle = num(f.candle_minutes, 1);
  const late = num(f.late_entry_min, 0);
  const atrTrail = Boolean(f.use_stop ?? true) && f.stop_type === "TSL" && f.tsl_mode === "ATR";
  return [
    {
      id: "candle",
      title: "5-minute candles, entries on the SMA cross",
      why: "The 30-day test read 5-minute candles. The cross itself still opens the trade.",
      now: `${candle}-minute candles${f.entry_mode && f.entry_mode !== "SMA" ? `, entry ${f.entry_mode}` : ""}`,
      done: candle === 5 && (f.entry_mode ?? "SMA") === "SMA",
      fill: { candle_minutes: "5", entry_mode: "SMA" },
    },
    {
      id: "fresh",
      title: "Enter only on a fresh trend (new cross, beyond VWAP, gap widening, volume 1.5× usual)",
      why: "This is the rule itself. Every other cross is skipped.",
      now: f.use_fresh ? `on, volume ${num(f.fresh_rvol, 1.5)}×` : "off",
      done: Boolean(f.use_fresh) && num(f.fresh_rvol, 1.5) === 1.5,
      fill: { use_fresh: true, fresh_rvol: "1.5" },
    },
    {
      id: "late",
      title: "Late entry: 15 minutes",
      why: "A cross that fails the check on its own candle may still enter on the next two candles, while it is still fresh.",
      now: late > 0 ? `${late} min` : "off",
      done: late === 15,
      fill: { late_entry_min: "15" },
    },
    {
      id: "stop",
      title: "Trailing stop in ATR multiples: stop 1.5, trail 0.5, no target",
      why: "The exit that did best in the test on most stocks. The stop fits each stock's own candle size.",
      now: atrTrail ? `stop ${num(f.tsl_sl_atr, 1.5)} / trail ${num(f.tsl_trail_atr, 0.5)} / target ${num(f.tsl_target_atr, 3)} × ATR` : "another stop",
      done: atrTrail && num(f.tsl_sl_atr, 1.5) === 1.5 && num(f.tsl_trail_atr, 0.5) === 0.5 && num(f.tsl_target_atr, 3) === 0,
      fill: { use_stop: true, stop_type: "TSL", tsl_mode: "ATR", tsl_sl_atr: "1.5", tsl_trail_atr: "0.5", tsl_target_atr: "0" },
    },
  ];
}

export function FreshSetup({ form, onFill }: { form: SmaConfig; onFill: (values: Fill) => void }) {
  const steps = freshSteps(form);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const isTicked = (s: Step) => ticked[s.id] ?? !s.done;
  const chosen = steps.filter((s) => !s.done && isTicked(s));
  const done = steps.filter((s) => s.done).length;
  return (
    <details id="sma-fresh-setup" className="group/fr mt-3 scroll-mt-24 rounded-lg border border-sky-400/30 bg-sky-400/[0.04]">
      <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-2 rounded-lg px-3 text-sm font-semibold text-slate-100 hover:bg-white/[0.03] [&::-webkit-details-marker]:hidden">
        Test the &quot;Fresh trend&quot; strategy: a one-click set-up <NewTag id="set-fresh" link={false} />
        <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold", done === steps.length ? "bg-emerald-500/15 text-emerald-300" : "bg-sky-500/15 text-sky-300")}>
          {done} of {steps.length} set
        </span>
        <span className="text-xs font-normal text-slate-400">— fresh cross + VWAP + volume, on its own. Click to open.</span>
      </summary>
      <div className="px-3 pb-3">
        <p className="text-xs leading-relaxed text-slate-300">
          This fills the form so the bot trades only the Fresh trend signal: a new SMA cross with the price beyond VWAP, the gap
          widening and heavy volume. Use it on a practice bot, then run a Backtest to see how it does with your real settings.
          Other entry checks and exits that are already on stay on: switch them off under &quot;What&apos;s switched on&quot; to
          test this rule alone.
        </p>
        <ul className="mt-2 space-y-1.5">
          {steps.map((s) => (
            <li key={s.id} className="rounded-md border border-white/10 bg-white/[0.02] p-2">
              <label className="flex items-start gap-2 text-sm text-slate-200">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={s.done || isTicked(s)}
                  disabled={s.done}
                  onChange={(e) => setTicked((t) => ({ ...t, [s.id]: e.target.checked }))}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-semibold">{s.title}</span>
                  <span className={clsx("ml-2 text-[11px]", s.done ? "text-emerald-300" : "text-slate-400")}>
                    {s.done ? `already set (${s.now})` : `now: ${s.now}`}
                  </span>
                  <span className="block text-xs leading-snug text-slate-400">{s.why}</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={!chosen.length}
            onClick={() => onFill(Object.assign({}, ...chosen.map((s) => s.fill)) as Fill)}
            className="inline-flex min-h-9 items-center rounded-md bg-sky-500/20 px-3 text-xs font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/50 hover:bg-sky-500/30 disabled:opacity-50"
          >
            Put the ticked ones in the form
          </button>
          <span className="text-xs text-slate-400">Then press Save at the bottom. Nothing changes until you do.</span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-slate-400">
          In a 30-day test on about 500 stocks this was the best early signal, but the edge was small (about the size of the
          charges) and mostly in F&amp;O stocks. Prove it with a{" "}
          <a href="/scalp/#backtest" className="text-sky-300 underline hover:text-sky-200">
            Backtest
          </a>{" "}
          on a practice bot before any real money.
        </p>
      </div>
    </details>
  );
}
