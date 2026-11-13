"use client";

import { usePinned } from "@/lib/usePinned";
import { PinToggle } from "@/components/ui/PinToggle";
import { InfoTip } from "@/components/ui/InfoTip";
import { SlopeEditor } from "@/components/Settings/SlopeEditor";
import { AdxPicture, AtrPicture, CandleRulesPicture, CandleSizePicture, GapModePicture } from "@/components/Settings/StrategyPictures";
import { StrategyPresets } from "@/components/Settings/StrategyPresets";
import { TierTag } from "@/components/Settings/SettingsCard";
import { FreshSetup, Only, SidewaysGuard, StyleCtx, StylePicker, useStyleView } from "@/components/Settings/StylePicker";
import { useEffect, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { ExitJoinCard, exitJoinText } from "@/components/Terminal/ExitJoin";
import { REVERSE_GROUPS, ReverseOwnSettings, reverseBody, reverseGroupsOn, type ReverseSettings } from "@/components/Terminal/ReverseOwnSettings";
import { ChevronDown, Search } from "lucide-react";
import { SETTING_LABELS } from "@/lib/settingLabels";
import { TslCalculator } from "@/components/Settings/TslCalculator";
import { BB_EXITS, CANDLE_MINUTES, EXIT_CANDLE_MINUTES, SLOPE_MINUTES, currentDesk, smaApi, type BbExit, type CandleMinutes, type SmaConfig } from "@/lib/smaApi";
import { crossExitOn, exitOrder, noExitOn, strategyNotes, type StrategyNote } from "@/lib/strategyChecks";

type Props = {
  config: SmaConfig | null;
  onChanged: () => void;
  /** Full page width (the Settings page): two columns on wide screens and a save bar that stays in view. */
  wide?: boolean;
};

function deskLabel(): string {
  const d = currentDesk();
  return d === "research" ? "the Research desk" : d === "live" ? "Bot 1" : `Bot ${d.replace("bot", "")}`;
}

/** "" edits the shared settings; a symbol edits that stock's own. */
const ALL = "";
const GROUP_KEY = "sma.settings.group";
/** The classes a matching field wears while a search is on. */
const LIT = ["ring-2", "ring-amber-400/80", "rounded-md", "bg-amber-400/10"];
const GROUP_CHIPS: { id: string; label: string; title: string }[] = [
  { id: "enter", label: "1 Enter", title: "When to enter" },
  { id: "check", label: "2 Checks", title: "Checks before entering" },
  { id: "exit", label: "3 Exit", title: "When to exit" },
  { id: "gap", label: "4 Gap mode", title: "SMA gap mode" },
  { id: "stop", label: "5 Stop & target", title: "Stop-loss and target" },
  { id: "risk", label: "6 Size & limits", title: "Size, daily limits and times" },
  { id: "advanced", label: "7 Advanced", title: "Moving stocks between bots, asking me" },
];
const CHECK_MINUTES_OPTIONS = [1, 2, 3, 5, 10, 15] as const;
/** What each numbered group is for, in plain words, shown when the group is open; and which ones a beginner needs. */
const GROUP_INTRO: Record<string, { tier?: "start" | "advanced"; intro: string }> = {
  enter: {
    tier: "start",
    intro:
      "The bot normally buys when the fast average (SMA 9) crosses above the slow one (SMA 21) and sells short on the opposite cross, on the candle size you pick. Everything else in this group is an extra way in. Leave the extras off until a backtest says they help.",
  },
  check: {
    intro:
      "Each ticked check can say no to a signal. More checks mean fewer trades, not better ones by themselves: tick one, backtest it, and keep it only if the numbers improve.",
  },
  exit: {
    intro:
      "What closes a trade apart from the stop. The opposite cross is the normal exit; every extra exit you tick can close the trade earlier, and whichever fires first wins.",
  },
  gap: {
    tier: "advanced",
    intro:
      "Instead of trading the cross itself, wait until the two averages have opened a gap of a set size, and close when the gap fades back. Leave it off until you have read its lesson and tried it on a replay.",
  },
  stop: {
    tier: "start",
    intro:
      "The one thing every trade must have. A stop limits how much one trade can lose; a trailing stop follows the price and locks in gains. Start with a trailing stop in rupees you can afford to lose per share.",
  },
  risk: {
    tier: "start",
    intro:
      "How many shares per trade, the most the bot may lose in a day, the most trades it may take, and when it stops entering and closes everything. Set the daily loss first.",
  },
  advanced: {
    tier: "advanced",
    intro:
      "Rules that move a stock to another bot (Bot 2 for choppy stocks, Bot 3 for trends) and the 1-minute review that asks you what to do. Leave them off until the basics work.",
  },
};

function toCheckMinutes(value: unknown): number[] {
  const allowed = new Set<number>(CHECK_MINUTES_OPTIONS);
  const parts = Array.isArray(value)
    ? value
    : String(value ?? "1")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
  const kept: number[] = [];
  for (const part of parts) {
    const n = Number(part);
    if (Number.isFinite(n) && allowed.has(n) && !kept.includes(n)) kept.push(n);
  }
  return kept.length ? kept : [1];
}

export const LABELS = SETTING_LABELS;

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
  // The stock whose own settings are in the form now (a Save for another stock waits until its row is loaded).
  const [loadedScope, setLoadedScope] = useState<string>(ALL);
  const dirty = useRef(false);
  const [confirmClear, setConfirmClear] = useState(false);
  // The wide page's Save bar stays at the bottom of the screen unless unfrozen.
  const [saveBarPinned, setSaveBarPinned] = usePinned("settings-save-bar");
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

  // One numbered group open at a time (remembered in this browser), so the form is a short page.
  // "Find a setting" opens every group with a match and highlights the matching fields instead.
  const rootRef = useRef<HTMLElement>(null);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ groups: string[]; count: number }>({ groups: [], count: 0 });
  const [foundAt, setFoundAt] = useState(0);
  const lit = useRef<HTMLElement[]>([]);
  useEffect(() => {
    try {
      setOpenGroup(localStorage.getItem(GROUP_KEY));
    } catch {
      /* private window */
    }
  }, []);
  const showGroup = (id: string | null, scroll = false) => {
    setOpenGroup(id);
    try {
      if (id) localStorage.setItem(GROUP_KEY, id);
      else localStorage.removeItem(GROUP_KEY);
    } catch {
      /* private window */
    }
    if (id && scroll) requestAnimationFrame(() => document.getElementById(`sma-${id}`)?.scrollIntoView({ block: "start" }));
  };
  const toggleGroup = (id: string, isOpen: boolean) => {
    if (query.trim()) return; // while searching the matches decide what is open
    if (isOpen && openGroup !== id) showGroup(id);
    else if (!isOpen && openGroup === id) showGroup(null);
  };
  // A link to a part of the form (#sma-stop, #sma-candle-interval …) opens the group it sits in.
  useEffect(() => {
    const follow = () => {
      const id = window.location.hash.slice(1);
      if (!id.startsWith("sma-")) return;
      const el = document.getElementById(id);
      const group = el?.closest<HTMLElement>("details[id^='sma-']")?.id.slice(4);
      if (!group) return;
      showGroup(group);
      // The group itself has a scroll margin for the pinned bars; a field inside it is put mid-screen instead.
      const block = el?.tagName === "DETAILS" ? "start" : "center";
      requestAnimationFrame(() => requestAnimationFrame(() => el?.scrollIntoView({ block })));
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // The search reads the rendered labels (a closed group's fields are in the page, just folded).
  useEffect(() => {
    for (const el of lit.current) el.classList.remove(...LIT);
    lit.current = [];
    const q = query.trim().toLowerCase();
    if (q.length < 2 || !rootRef.current) {
      setFound({ groups: [], count: 0 });
      return;
    }
    const hits: HTMLElement[] = [];
    const groups = new Set<string>();
    rootRef.current.querySelectorAll<HTMLElement>("details[id^='sma-'] label, details[id^='sma-'] [data-setting-title]").forEach((el) => {
      if (!(el.textContent ?? "").toLowerCase().includes(q)) return;
      // A label inside a matched label (a tick box in a row) counts once.
      if (hits.some((h) => h.contains(el))) return;
      hits.push(el);
      const g = el.closest<HTMLElement>("details[id^='sma-']")?.id.slice(4);
      if (g) groups.add(g);
    });
    for (const el of hits) el.classList.add(...LIT);
    lit.current = hits;
    setFound({ groups: [...groups], count: hits.length });
    setFoundAt(0);
    if (hits[0]) requestAnimationFrame(() => hits[0].scrollIntoView({ block: "center" }));
  }, [query]);
  const jumpFound = (step: number) => {
    if (!lit.current.length) return;
    const next = (foundAt + step + lit.current.length) % lit.current.length;
    setFoundAt(next);
    lit.current[next].scrollIntoView({ block: "center" });
  };
  const searching = query.trim().length >= 2;
  const isOpen = (id: string) => (searching ? found.groups.includes(id) : openGroup === id);

  const armed = (config?.trade_symbols ?? []).map((s) => s.toUpperCase());
  const withOwn = Object.keys(config?.stock_settings ?? {});
  const scopes = Array.from(new Set([...armed, ...withOwn, ...(scope ? [scope] : [])])).sort();
  const stockScope = scope !== ALL;
  const [styleView, setStyleView] = useStyleView(form);
  /** The styles whose settings show; null = all (also while searching, so a match is never hidden). */
  const shownStyles = styleView.all || searching ? null : new Set<string>(styleView.picks);
  const groupShown = (id: string) =>
    !shownStyles || (id === "gap" ? shownStyles.has("gap") : id === "advanced" ? shownStyles.has("bots") || shownStyles.has("review") : true);

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
        setLoadedScope(scope);
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

  const setReverse = (next: ReverseSettings) => {
    markDirty(true);
    setForm((prev) => (prev ? { ...prev, reverse_settings: next } : prev));
    setMsg(null);
  };
  const set = (key: keyof SmaConfig, value: string | boolean | number | number[]) => {
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

  // The "What's switched on" list and the "N on" count on each group.
  // Saved strategies & history: reload the list after each Save; Load puts a copy's numbers into the form.
  const [savedCount, setSavedCount] = useState(0);
  const loadPreset = (settings: Partial<SmaConfig>, note: string) => {
    markDirty(true);
    setForm((prev) => (prev ? { ...prev, ...settings } : prev));
    setMsg(`Loaded "${note}" — press Save to keep it.`);
  };
  const rules = activeRules(form);
  const onCount = (group: RuleGroup) => rules.filter((r) => r.group === group).length;
  const turnOff = (values: Partial<Record<keyof SmaConfig, string | boolean>>) => {
    for (const [key, value] of Object.entries(values)) set(key as keyof SmaConfig, value as string | boolean);
    setMsg("Changed — press Save to keep it.");
  };

  const strategyBody = (): Partial<SmaConfig> => ({
    qty: Number(form.qty),
    sma_fast: Number(form.sma_fast),
    sma_medium: form.sma_medium != null && String(form.sma_medium).trim() !== "" ? Number(form.sma_medium) : null,
    sma_slow: Number(form.sma_slow),
    candle_minutes: (CANDLE_MINUTES.includes(Number(form.candle_minutes) as CandleMinutes)
      ? Number(form.candle_minutes)
      : 1) as CandleMinutes,
    exit_candle_minutes: EXIT_CANDLE_MINUTES.includes(Number(form.exit_candle_minutes ?? 0))
      ? Number(form.exit_candle_minutes ?? 0)
      : 0,
    small_exit_minutes: String(form.small_exit_minutes ?? ""),
    small_exit_cross: form.small_exit_cross !== false,
    small_exit_slope: form.small_exit_slope !== false,
    small_exit_counter: Math.min(10, Math.max(0, Math.round(Number(form.small_exit_counter ?? 0)) || 0)),
    small_exit_loss_pct: Math.min(50, Math.max(0, Number(form.small_exit_loss_pct ?? 0) || 0)),
    small_exit_agree: Math.min(5, Math.max(1, Math.round(Number(form.small_exit_agree ?? 1)) || 1)),
    sc_minutes: String(form.sc_minutes ?? ""),
    sc_count: Math.min(5, Math.max(1, Math.round(Number(form.sc_count ?? 2)) || 2)),
    sc_join: form.sc_join === "ANY" || form.sc_join === "AT_LEAST" ? form.sc_join : "ALL",
    sc_need: Math.min(5, Math.max(1, Math.round(Number(form.sc_need ?? 1)) || 1)),
    sc_entry: Boolean(form.sc_entry),
    sc_confirm: Boolean(form.sc_confirm),
    atr_period: Number(form.atr_period),
    atr_multiplier: Number(form.atr_multiplier),
    use_adx_filter: form.use_adx_filter,
    use_stop: form.use_stop !== false,
    stop_type: form.stop_type === "SMA_GAP" || form.stop_type === "TSL" || form.stop_type === "SUPERTREND" || form.stop_type === "VWAP" ? form.stop_type : "ATR",
    vwap_stop_atr: Number(form.vwap_stop_atr ?? 0.5),
    atr_target_mult: Math.max(0, Number(form.atr_target_mult ?? 0) || 0),
    tsl_sl_points: Number(form.tsl_sl_points ?? 20),
    tsl_trail_points: Number(form.tsl_trail_points ?? 10),
    tsl_target_points: Number(form.tsl_target_points ?? 0),
    tsl_mode: String(form.tsl_mode ?? "POINTS") as "POINTS" | "PERCENT" | "ATR",
    tsl_sl_pct: Number(form.tsl_sl_pct ?? 1.0),
    tsl_trail_pct: Number(form.tsl_trail_pct ?? 0.5),
    tsl_target_pct: Number(form.tsl_target_pct ?? 0),
    tsl_sl_atr: Number(form.tsl_sl_atr ?? 1.5),
    tsl_trail_atr: Number(form.tsl_trail_atr ?? 0.5),
    tsl_target_atr: Number(form.tsl_target_atr ?? 3),
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
    gap_fade_minute: Boolean(form.gap_fade_minute),
    cross_exit: crossExitOn(form),
    late_entry_min: Math.min(375, Math.max(0, Math.round(Number(form.late_entry_min ?? 0)))),
    join_trend: Boolean(form.join_trend),
    always_in: Boolean(form.always_in),
    always_in_reverse: Boolean(form.always_in_reverse),
    join_after_min: Math.min(360, Math.max(0, Math.round(Number(form.join_after_min ?? 15)))),
    counter_exit_count: Math.min(10, Math.max(0, Math.round(Number(form.counter_exit_count ?? 0)))),
    counter_rule: (["CLOSES", "COLOUR", "BOTH"] as const).includes(form.counter_rule as "CLOSES")
      ? (form.counter_rule as "CLOSES" | "COLOUR" | "BOTH")
      : "COLOUR",
    counter_need_fast: form.counter_need_fast !== false,
    counter_min_body: Math.min(5, Math.max(0, Number(form.counter_min_body ?? 0) || 0)),
    counter_reenter: Boolean(form.counter_reenter),
    counter_reenter_max: Math.min(10, Math.max(1, Math.round(Number(form.counter_reenter_max ?? 3)) || 3)),
    counter_entry: Boolean(form.counter_entry),
    turn_alert: Boolean(form.turn_alert),
    turn_entry: Boolean(form.turn_entry),
    uturn_on: Boolean(form.uturn_on),
    turn_toward_cross: Boolean(form.turn_toward_cross),
    turn_exit_candle: Boolean(form.turn_exit_candle),
    turn_colour_exit: Boolean(form.turn_colour_exit),
    uturn_run: Math.min(30, Math.max(2, Math.round(Number(form.uturn_run ?? 5)) || 5)),
    uturn_min_pct: Math.min(1, Math.max(0, Number(form.uturn_min_pct ?? 0.005) || 0)),
    use_ema: Boolean(form.use_ema),
    ema_exit: Boolean(form.ema_exit),
    ema_fast: Math.min(500, Math.max(1, Math.round(Number(form.ema_fast ?? 9)))),
    ema_slow: Math.min(500, Math.max(1, Math.round(Number(form.ema_slow ?? 200)))),
    use_macd: Boolean(form.use_macd),
    macd_exit: Boolean(form.macd_exit),
    macd_fast: Math.min(200, Math.max(1, Math.round(Number(form.macd_fast ?? 12)) || 12)),
    macd_slow: Math.min(200, Math.max(1, Math.round(Number(form.macd_slow ?? 26)) || 26)),
    macd_signal: Math.min(200, Math.max(1, Math.round(Number(form.macd_signal ?? 9)) || 9)),
    use_cvd: Boolean(form.use_cvd),
    cvd_lookback: Math.min(50, Math.max(1, Math.round(Number(form.cvd_lookback ?? 5)) || 5)),
    cvd_div_exit: Boolean(form.cvd_div_exit),
    cvd_div_lookback: Math.min(100, Math.max(3, Math.round(Number(form.cvd_div_lookback ?? 10)) || 10)),
    reverse_on_exit: String(form.reverse_on_exit ?? ""),
    reverse_colour_exit: Boolean(form.reverse_colour_exit),
    reverse_settings: reverseBody(form.reverse_settings),
    exit_join: String(form.exit_join ?? "OR").toUpperCase() === "AND" ? "AND" : "OR",
    exit_group: String(form.exit_group ?? ""),
    exit_need: Math.max(0, Math.min(15, Math.round(Number(form.exit_need ?? 0) || 0))),
    exit_within: Math.max(1, Math.min(20, Math.round(Number(form.exit_within ?? 3) || 3))),
    ha_colours: Boolean(form.ha_colours),
    use_supertrend: Boolean(form.use_supertrend),
    use_level: Boolean(form.use_level),
    use_fresh: Boolean(form.use_fresh),
    fresh_rvol: Math.min(20, Math.max(0, Number(form.fresh_rvol ?? 1.5) || 0)),
    use_high52: Boolean(form.use_high52),
    high52_side: ["LONG", "SHORT", "BOTH"].includes(String(form.high52_side)) ? (String(form.high52_side) as SmaConfig["high52_side"]) : "LONG",
    high52_pct: Math.min(20, Math.max(0, Number(form.high52_pct ?? 0) || 0)),
    level_line: ["BB_MIDDLE", "BB_UPPER", "PIVOT_P", "PIVOT_R1"].includes(String(form.level_line)) ? (String(form.level_line) as SmaConfig["level_line"]) : "BB_MIDDLE",
    st_exit: Boolean(form.st_exit),
    slope_exit: Boolean(form.slope_exit),
    slope_minutes: SLOPE_MINUTES.includes(Number(form.slope_minutes ?? 0)) ? Number(form.slope_minutes ?? 0) : 0,
    slope_lookback: Math.min(10, Math.max(1, Math.round(Number(form.slope_lookback ?? 2) || 2))),
    slope_bend_pct: Math.min(100, Math.max(0, Number(form.slope_bend_pct ?? 60) || 0)),
    slope_turn: form.slope_turn !== false,
    slope_turn_pct: Math.min(5, Math.max(0, Number(form.slope_turn_pct ?? 0.02) || 0)),
    vturn_exit: Boolean(form.vturn_exit),
    vturn_move_pct: Math.min(5, Math.max(0, Number(form.vturn_move_pct ?? 0.02) || 0)),
    vturn_turn_pct: Math.min(5, Math.max(0, Number(form.vturn_turn_pct ?? 0.015) || 0)),
    vturn_within: Math.min(10, Math.max(1, Math.round(Number(form.vturn_within ?? 6)) || 6)),
    vturn_handoff: Boolean(form.vturn_exit) && Boolean(form.vturn_handoff),
    stop_handoff: ["BACK", "BOT2", "BOT3"].includes(String(form.stop_handoff)) ? (String(form.stop_handoff) as "BACK" | "BOT2" | "BOT3") : "OFF",
    stop_handoff_loss: form.stop_handoff_loss !== false,
    handoff_now: Boolean(form.handoff_now),
    pivot_target: form.pivot_target === "R1" || form.pivot_target === "R2" ? form.pivot_target : "OFF",
    stop_plan: Boolean(form.stop_plan),
    decel_exit: Boolean(form.decel_exit),
    decel_lookback: Math.min(10, Math.max(1, Math.round(Number(form.decel_lookback ?? 2) || 2))),
    decel_arm_atr: Math.max(0, Number(form.decel_arm_atr ?? 0.1) || 0),
    decel_drop_pct: Math.min(100, Math.max(0, Number(form.decel_drop_pct ?? 50) || 0)),
    decel_min_atr: Math.max(0, Number(form.decel_min_atr ?? 0.02) || 0),
    decel_candles: Math.min(5, Math.max(1, Math.round(Number(form.decel_candles ?? 2) || 2))),
    decel_confirm: form.decel_confirm === "OFF" ? "OFF" : "CLOSE",
    decel_pierce_atr: Math.max(0, Number(form.decel_pierce_atr ?? 0) || 0),
    decel_peak_from: form.decel_peak_from === "RUN" ? "RUN" : "ENTRY",
    plan_be_r: Number(form.plan_be_r ?? 1.5) > 0 ? Number(form.plan_be_r ?? 1.5) : 1.5,
    plan_trail_r: Number(form.plan_trail_r ?? 2) > 0 ? Number(form.plan_trail_r ?? 2) : 2,
    st_period: Math.min(100, Math.max(1, Math.round(Number(form.st_period ?? 10)))),
    st_mult: Math.min(10, Math.max(0.5, Number(form.st_mult ?? 3) || 3)),
    tall_pullback: Boolean(form.tall_pullback),
    tall_atr_mult: Math.min(10, Math.max(0.5, Number(form.tall_atr_mult ?? 1.5) || 1.5)),
    tall_expiry_candles: Math.min(10, Math.max(1, Math.round(Number(form.tall_expiry_candles ?? 2) || 2))),
    extreme_entry: Boolean(form.extreme_entry),
    extreme_anchor: form.extreme_anchor === "SMA_9" ? "SMA_9" : "CANDLE_EXTREME",
    extreme_invalid_pct: Math.min(5, Math.max(0, Number(form.extreme_invalid_pct ?? 0.05) || 0)),
    extreme_expiry_candles: Math.min(10, Math.max(1, Math.round(Number(form.extreme_expiry_candles ?? 2) || 2))),
    extreme_cancel_on_cross: form.extreme_cancel_on_cross !== false,
    entry_candle_confirm: Boolean(form.entry_candle_confirm),
    entry_candle_pct: Math.min(1, Math.max(0, Number(form.entry_candle_pct ?? 0.05) || 0)),
    entry_candle_max: Math.min(5, Math.max(1, Math.round(Number(form.entry_candle_max ?? 2)) || 2)),
    handoff_candle: (["WAIT", "FOLLOW"].includes(String(form.handoff_candle ?? "OFF").toUpperCase()) ? String(form.handoff_candle).toUpperCase() : "OFF") as "OFF" | "WAIT" | "FOLLOW",
    day_extreme_guard: Boolean(form.day_extreme_guard),
    day_extreme_pct: Math.min(5, Math.max(0, Number(form.day_extreme_pct ?? 0.15) || 0)),
    day_extreme_pull_pct: Math.min(5, Math.max(0, Number(form.day_extreme_pull_pct ?? 0.15) || 0)),
    day_extreme_candles: Math.min(10, Math.max(1, Math.round(Number(form.day_extreme_candles ?? 3)) || 3)),
    slow_sma_exit: Boolean(form.slow_sma_exit),
    recovery_hold: Boolean(form.recovery_hold),
    recovery_tolerance_pct: Math.min(5, Math.max(0, Number(form.recovery_tolerance_pct ?? 0.15) || 0)),
    recovery_emergency_mult: Math.min(10, Math.max(1, Number(form.recovery_emergency_mult ?? 1.5) || 1.5)),
    recovery_atr_mult: Math.min(20, Math.max(0.5, Number(form.recovery_atr_mult ?? 2.5) || 2.5)),
    recovery_max_candles: Math.min(75, Math.max(1, Math.round(Number(form.recovery_max_candles ?? 6) || 6))),
    recovery_handoff: form.recovery_handoff !== false,
    breakout_handoff: Boolean(form.breakout_handoff),
    breakout_atr_mult: Math.min(10, Math.max(0.5, Number(form.breakout_atr_mult ?? 1.8) || 1.8)),
    breakout_gap_pct: Math.min(5, Math.max(0, Number(form.breakout_gap_pct ?? 0.2) || 0)),
    breakout_volume_mult: Math.min(10, Math.max(1, Number(form.breakout_volume_mult ?? 1.5) || 1.5)),
    breakout_rule: String(form.breakout_rule ?? "CANDLE").toUpperCase() === "STRONG" ? "STRONG" : "CANDLE",
    breakout_cut: Boolean(form.breakout_cut),
    breakout_when: String(form.breakout_when ?? "ANY").toUpperCase() === "IN_PROFIT" ? "IN_PROFIT" : "ANY",
    breakout_min_profit_pct: Math.min(20, Math.max(0, Number(form.breakout_min_profit_pct ?? 0) || 0)),
    breakout_min_profit_rs: Math.min(10000, Math.max(0, Number(form.breakout_min_profit_rs ?? 0) || 0)),
    chop_losses: Math.min(10, Math.max(0, Math.round(Number(form.chop_losses ?? 0) || 0))),
    breakout_trend_checks: String(form.breakout_trend_checks ?? ""),
    breakout_trend_need: Math.min(7, Math.max(1, Math.round(Number(form.breakout_trend_need ?? 6) || 6))),
    use_or_volume: Boolean(form.use_or_volume),
    use_or_vwap: Boolean(form.use_or_vwap),
    use_or_spread: Boolean(form.use_or_spread),
    use_or_break: Boolean(form.use_or_break),
    or_volume_ratio: Math.min(20, Math.max(0, Number(form.or_volume_ratio ?? 2) || 0)),
    or_spread_pct: Math.min(5, Math.max(0, Number(form.or_spread_pct ?? 0.1) || 0)),
    flip_orders: Boolean(form.flip_orders),
    max_trades_per_stock: Math.min(100, Math.max(0, Math.round(Number(form.max_trades_per_stock ?? 0)) || 0)),
    entry_mode:
      form.entry_mode === "PATTERN" || form.entry_mode === "COLOUR" || form.entry_mode === "NTH_CANDLE" ? form.entry_mode : "SMA",
    nth_candle_no: Math.min(6, Math.max(1, Math.round(Number(form.nth_candle_no ?? 2)) || 2)),
    nth_candle_minutes: ([1, 3, 5, 10, 15].includes(Number(form.nth_candle_minutes)) ? Number(form.nth_candle_minutes) : 5) as 1 | 3 | 5 | 10 | 15,
    nth_min_body_pct: Math.min(2, Math.max(0, Number(form.nth_min_body_pct ?? 0) || 0)),
    nth_min_rvol: Math.min(10, Math.max(0, Number(form.nth_min_rvol ?? 0) || 0)),
    nth_side_rule: form.nth_side_rule === "MAJORITY" || form.nth_side_rule === "AGREE" ? form.nth_side_rule : "NTH",
    nth_majority_by: form.nth_majority_by === "SIZE" ? "SIZE" : "COUNT",
    nth_dir_rule: form.nth_dir_rule === "CLOSES" || form.nth_dir_rule === "BOTH" ? form.nth_dir_rule : "COLOUR",
    nth_skip_checks: Boolean(form.nth_skip_checks),
    pattern_tf: ([1, 3, 5, 10, 15, 30, 60].includes(Number(form.pattern_tf)) ? Number(form.pattern_tf) : 1) as 1 | 3 | 5 | 10 | 15 | 30 | 60,
    colour_count: Math.min(5, Math.max(1, Math.round(Number(form.colour_count ?? 1)) || 1)),
    colour_min_body: Math.min(2, Math.max(0, Number(form.colour_min_body ?? 0) || 0)),
    colour_body_unit: form.colour_body_unit === "ATR" ? "ATR" : "PCT",
    colour_body_by_tf: String(form.colour_body_by_tf ?? ""),
    colour_body_atr: Math.min(5, Math.max(0, Number(form.colour_body_atr ?? 0) || 0)),
    colour_body_seq: String(form.colour_body_seq ?? ""),
    colour_inside_minutes: ([0, 1, 2, 3, 5, 10].includes(Number(form.colour_inside_minutes)) ? Number(form.colour_inside_minutes) : 0) as 0 | 1 | 2 | 3 | 5 | 10,
    colour_inside_body: Math.min(2, Math.max(0, Number(form.colour_inside_body ?? 0) || 0)),
    colour_exit_on: Boolean(form.colour_exit_on),
    colour_exit_count: Math.min(5, Math.max(1, Math.round(Number(form.colour_exit_count ?? 2)) || 2)),
    colour_exit_body: Math.min(2, Math.max(0, Number(form.colour_exit_body ?? 0.2) || 0)),
    colour_exit_inside: Boolean(form.colour_exit_inside),
    colour_force: Boolean(form.colour_force),
    colour_pull: (["OFF", "HALF", "LOW", "SMA9"].includes(String(form.colour_pull)) ? String(form.colour_pull) : "OFF") as "OFF" | "HALF" | "LOW" | "SMA9",
    colour_pull_candles: Math.min(3, Math.max(1, Math.round(Number(form.colour_pull_candles ?? 1)) || 1)),
    colour_pull_market: Boolean(form.colour_pull_market),
    pattern_trend: Boolean(form.pattern_trend),
    pattern_set: form.pattern_set === "ALL" ? "ALL" : "STRONG",
    pattern_min_edge: Math.max(0, Number(form.pattern_min_edge ?? 1.5)),
    gap_entry_delay_min: Math.round(Number(form.gap_entry_delay_min ?? 0)),
    gap_entry_window_min: Math.round(Number(form.gap_entry_window_min ?? 0)),
    review_on: Boolean(form.review_on),
    review_gap_pct: Number(form.review_gap_pct ?? 0.03),
    review_cooldown_min: Math.max(0, Math.round(Number(form.review_cooldown_min ?? 15))),
    review_check_minutes: toCheckMinutes(form.review_check_minutes),
    review_check_mode: String(form.review_check_mode ?? "ALWAYS") as "ALWAYS" | "ONLY_IF_AGAINST" | "ONLY_IF_NOT_WITH",
    review_default_answer: String(form.review_default_answer ?? "PROMPT") as "PROMPT" | "EXIT" | "REVERSE" | "CONTINUE",
    review_conditions: reviewConditions(form.review_conditions).join(","),
    review_join: form.review_join === "AND" ? "AND" : "OR",
    review_slope_candles: Math.min(20, Math.max(1, Math.round(Number(form.review_slope_candles ?? 3)))),
    review_slope_flat: Number(form.review_slope_flat ?? 0.01),
    review_steep_pct: Math.max(0.001, Number(form.review_steep_pct ?? 0.03)),
  });

  /** Saved values that are not numbers ("5,000", "1.5x"): sent as they were, they were silently dropped or became 0. */
  const unreadable = (body: Partial<SmaConfig>): string[] => {
    const bad: string[] = [];
    const raw = form as unknown as Record<string, unknown>;
    for (const [key, value] of Object.entries(body)) {
      if (typeof value !== "number") continue;
      const typed = raw[key];
      const text = typeof typed === "string" ? typed.trim() : "";
      if ((text !== "" && !Number.isFinite(Number(text))) || !Number.isFinite(value)) {
        bad.push(SETTING_LABELS[key] ?? key);
      }
    }
    return bad;
  };

  /** On a LIVE bot, ask before a Save that turns the stop off, the flip on or the SMA cross exit off (the
   *  terminal's Strategy card already asks for the same switches). */
  const liveRiskOk = (body: Partial<SmaConfig>): boolean => {
    if ((form.trading_mode ?? "PAPER").toUpperCase() !== "LIVE" || !config) return true;
    const risks: string[] = [];
    if (body.use_stop === false && config.use_stop !== false) risks.push("turn the stop-loss OFF (real orders without a stop)");
    if (body.flip_orders === true && !config.flip_orders) risks.push("turn the flip strategy ON (buy signals sell, sell signals buy)");
    if (body.cross_exit === false && config.cross_exit !== false) risks.push("turn the SMA cross exit OFF");
    if (!risks.length) return true;
    return window.confirm(`This bot is LIVE (real money). This Save will ${risks.join(" and ")}${stockScope ? ` for ${scope}` : ""}. Continue?`);
  };

  const saveStock = async () => {
    if (loadedScope !== scope) {
      setMsg(`Still loading ${scope}'s settings. Save again in a moment.`);
      return;
    }
    const body = strategyBody();
    const bad = unreadable(body);
    if (bad.length) {
      setMsg(`Not saved: ${bad.join(", ")} ${bad.length === 1 ? "is" : "are"} not a number. Type digits only (no commas or letters).`);
      return;
    }
    if (!liveRiskOk(body)) return;
    setBusy(true);
    setMsg(null);
    try {
      const row = await smaApi.saveStockConfig(scope, body);
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

  /** Reset to defaults: every shared strategy field back to its default. Trade list, mode and name stay. */
  const [confirmReset, setConfirmReset] = useState(false);
  const resetDefaults = async (withStocks: boolean) => {
    setConfirmReset(false);
    setBusy(true);
    setMsg(null);
    try {
      const live = String(form?.trading_mode ?? "").toUpperCase() === "LIVE";
      if (live && !window.confirm("This bot is LIVE. Reset its strategy settings to the defaults anyway?")) return;
      const out = await smaApi.resetConfig(live, withStocks);
      markDirty(false);
      setForm(out.config);
      setMsg(
        out.changed.length
          ? `Reset to defaults: ${out.changed.length} setting${out.changed.length === 1 ? "" : "s"} changed${withStocks ? ", every stock's own settings cleared" : ""}. Press Start bot.`
          : "Already at the defaults."
      );
      setSavedCount((n) => n + 1);
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Reset failed");
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
    const body = strategyBody();
    const bad = unreadable({ ...body, max_daily_loss: Number(form.max_daily_loss) });
    if (bad.length) {
      setMsg(`Not saved: ${bad.join(", ")} ${bad.length === 1 ? "is" : "are"} not a number. Type digits only (no commas or letters).`);
      return;
    }
    if (!liveRiskOk(body)) return;
    setBusy(true);
    setMsg(null);
    try {
      // The chart stock is picked on the terminal; Save leaves it alone so it cannot undo a newer pick.
      await smaApi.saveConfig({
        ...body,
        max_daily_loss: Number(form.max_daily_loss),
        max_trades_per_day: Number(form.max_trades_per_day),
        square_off_time: form.square_off_time,
        entry_cutoff_time: form.entry_cutoff_time || "15:00",
        ...(currentDesk() === "research" ? {} : { auto_start: Boolean(form.auto_start) }),
      });
      markDirty(false);
      setMsg("Saved. Press Start bot. Open positions stay open.");
      setSavedCount((n) => n + 1);
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      ref={rootRef}
      className={clsx(
        "rounded-xl border border-white/5 bg-[#151921] p-4",
        wide && "lg:p-6",
      )}
    >
      {/* The scope picker, then "What's switched on", then the numbered groups (enter, checks, exits, gap mode, stop,
          size and times, advanced). Every field saves through strategyBody() exactly as before. */}
      <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
            <span className="text-sm font-semibold text-slate-100">Settings for</span>
            <InfoTip label="About shared and per-stock settings">
              The shared settings apply to every stock this bot trades. Pick a stock to give it its own numbers (marked
              &quot;own&quot;); whatever you do not change there keeps following the shared ones. Most people never need a
              per-stock setting.
            </InfoTip>
          </div>
          <label className="mt-1 block text-sm text-slate-300">
            <span className="sr-only">Settings for</span>
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
      <StylePicker form={form} view={styleView} onChange={setStyleView} botLabel={scope || deskLabel()} />
      <SidewaysGuard form={form} onFill={turnOff} />
      <FreshSetup form={form} onFill={turnOff} />
      <ActiveRules form={form} scope={scope} onOff={turnOff} notes={gapNotes} />
      {!stockScope ? <StrategyPresets current={strategyBody()} labels={LABELS} refreshKey={savedCount} onLoad={loadPreset} /> : null}
      <div className="mt-4 flex flex-wrap items-center gap-2" role="group" aria-label="Settings groups">
        {GROUP_CHIPS.map((g) => {
          const on = g.id === "risk" ? 0 : onCount(g.id as RuleGroup);
          const active = isOpen(g.id);
          if (!groupShown(g.id)) return null;
          return (
            <button
              key={g.id}
              type="button"
              aria-pressed={active}
              onClick={() => {
                if (searching) setQuery("");
                showGroup(active && !searching ? null : g.id, true);
              }}
              title={g.title}
              className={clsx(
                "flex min-h-9 items-center gap-1.5 rounded-full px-3 text-xs font-semibold ring-1 ring-inset",
                active ? "bg-sky-400/20 text-sky-100 ring-sky-400/50" : "text-slate-300 ring-white/15 hover:bg-white/5"
              )}
            >
              {g.label}
              {on ? <span className="rounded-full bg-emerald-500/20 px-1.5 text-[10px] text-emerald-300">{on} on</span> : null}
            </button>
          );
        })}
        <label className="relative ml-auto flex min-w-[12rem] flex-1 items-center sm:max-w-xs">
          <Search size={14} aria-hidden className="pointer-events-none absolute left-2 text-slate-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                jumpFound(e.shiftKey ? -1 : 1);
              }
            }}
            placeholder="Find a setting… (e.g. trail, RSI, cut-off)"
            aria-label="Find a setting"
            className="min-h-9 w-full rounded-md border border-white/15 bg-black/30 pl-7 pr-2 text-xs text-slate-100 placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          />
        </label>
        {searching ? (
          <span className="flex items-center gap-1 text-xs text-slate-300" role="status">
            {found.count ? `${foundAt + 1} of ${found.count}` : "No setting matches"}
            {found.count > 1 ? (
              <>
                <button type="button" onClick={() => jumpFound(-1)} aria-label="Previous match" className="min-h-8 rounded px-1.5 ring-1 ring-inset ring-white/15 hover:bg-white/5">
                  ‹
                </button>
                <button type="button" onClick={() => jumpFound(1)} aria-label="Next match" className="min-h-8 rounded px-1.5 ring-1 ring-inset ring-white/15 hover:bg-white/5">
                  ›
                </button>
              </>
            ) : null}
          </span>
        ) : null}
      </div>
      {!searching && !openGroup ? (
        <p className="mt-2 text-xs text-slate-400">
          Pick a group above to open its settings, or type what you are looking for. A beginner needs 1, 5 and 6 (marked
          &quot;Start here&quot;); 4 and 7 are advanced.
        </p>
      ) : null}
      <StyleCtx.Provider value={shownStyles}>
      <FieldGroup id="enter" open={isOpen("enter")} onToggle={toggleGroup} title="1. When to enter" hint="The signal that opens a trade, and extra ways in" on={onCount("enter")} wide={wide}>
        <div className="min-w-0">
          <SectionTitle>Moving averages</SectionTitle>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Fast MA" own={isOwn("sma_fast")} value={String(form.sma_fast)} onChange={(v) => set("sma_fast", v)} />
            <Field label="Slow MA" own={isOwn("sma_slow")} value={String(form.sma_slow)} onChange={(v) => set("sma_slow", v)} />
          </div>
          <div className="mt-2 flex items-center gap-1 text-xs text-slate-300">
            <label>
              Medium MA{isOwn("sma_medium") ? " (own)" : ""}
            </label>
            <InfoTip label="About the medium MA (triple MA)">
              Optional third SMA between fast and slow. When set, a signal fires only when all
              three are aligned: fast &gt; medium &gt; slow for a buy, fast &lt; medium &lt; slow
              for a sell. Leave blank to use the standard two-SMA crossover.
            </InfoTip>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <input
              type="number"
              min={Number(form.sma_fast) + 1}
              max={Number(form.sma_slow) - 1}
              placeholder="off"
              value={form.sma_medium != null && String(form.sma_medium).trim() !== "" ? String(form.sma_medium) : ""}
              onChange={(e) => set("sma_medium", e.target.value === "" ? "" : e.target.value)}
              className="h-10 w-28 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
            />
            {form.sma_medium != null && String(form.sma_medium).trim() !== "" ? (
              <button
                type="button"
                onClick={() => set("sma_medium", "")}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Clear
              </button>
            ) : null}
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
          <CandleSizePicture form={form} set={set} />
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
                skips candles that usually move less than N× those charges for this quantity. Candle colour: each time a candle of
                the chosen size closes, green buys and red sells at the start of the next candle; the trade is held while the
                candles keep its colour (a doji holds) and the first candle of the other colour closes it (exit “candle turned
                the other colour”) and opens the other way at once. Second candle: once a day, when the day&apos;s Nth candle
                of the chosen size closes (the 2nd 5-minute candle by default, as on the Scalp page&apos;s Second candle test),
                green buys and red sells at the start of the next candle; nothing else opens a trade that day, and the stop,
                trailing stop, target, the ticked exits and the square-off close it. The stop, target, filters and square-off
                apply to every mode; the pullback, candle-extreme and entry-candle waits do not (these modes order at the
                candle start). Try it in Replay first.
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
                  <option value="COLOUR">Candle colour (hold until the other colour)</option>
                  <option value="NTH_CANDLE">Second candle (the day&apos;s Nth candle, once a day)</option>
                </select>
              </label>
              {form.entry_mode === "NTH_CANDLE" ? (
                <>
                  <label className="block text-sm text-slate-300">
                    <span className="text-xs uppercase tracking-wider text-slate-400">
                      Which candle{isOwn("nth_candle_no") ? " (own)" : ""}
                    </span>
                    <select
                      value={String(form.nth_candle_no ?? 2)}
                      onChange={(e) => set("nth_candle_no", e.target.value)}
                      className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                    >
                      {[1, 2, 3, 4, 5, 6].map((n) => (
                        <option key={n} value={String(n)}>
                          {n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`} candle of the day
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-sm text-slate-300">
                    <span className="text-xs uppercase tracking-wider text-slate-400">
                      Candle size{isOwn("nth_candle_minutes") ? " (own)" : ""}
                    </span>
                    <select
                      value={String(form.nth_candle_minutes ?? 5)}
                      onChange={(e) => set("nth_candle_minutes", e.target.value)}
                      className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                    >
                      {[1, 3, 5, 10, 15].map((m) => (
                        <option key={m} value={String(m)}>
                          {m} minute{m === 1 ? "" : "s"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Field
                    label="Smallest candle body, % of price (0 = any)"
                    value={String(form.nth_min_body_pct ?? 0)}
                    onChange={(v) => set("nth_min_body_pct", v)}
                    own={isOwn("nth_min_body_pct")}
                  />
                  <Field
                    label="Volume vs usual at least (× usual, 0 = off)"
                    value={String(form.nth_min_rvol ?? 0)}
                    onChange={(v) => set("nth_min_rvol", v)}
                    own={isOwn("nth_min_rvol")}
                  />
                  <label className="block text-sm text-slate-300">
                    <span className="text-xs uppercase tracking-wider text-slate-400">
                      Trade which way{isOwn("nth_side_rule") ? " (own)" : ""}
                    </span>
                    <select
                      value={String(form.nth_side_rule ?? "NTH")}
                      onChange={(e) => set("nth_side_rule", e.target.value)}
                      className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                    >
                      <option value="NTH">That candle&apos;s colour</option>
                      <option value="MAJORITY">Most of candles 1 to {Number(form.nth_candle_no ?? 2)}</option>
                      <option value="AGREE">That candle, only if most agree</option>
                    </select>
                  </label>
                  <label className="block text-sm text-slate-300">
                    <span className="text-xs uppercase tracking-wider text-slate-400">
                      A candle is up / down by{isOwn("nth_dir_rule") ? " (own)" : ""}
                    </span>
                    <select
                      value={String(form.nth_dir_rule ?? "COLOUR")}
                      onChange={(e) => set("nth_dir_rule", e.target.value)}
                      title="Colour = green / red. Closes = its close above / below the previous candle's close (candle 1 against yesterday's last close). Both = green and a higher close, red and a lower close; anything else counts as no direction."
                      className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                    >
                      <option value="COLOUR">Colour (green / red)</option>
                      <option value="CLOSES">Closes (higher / lower than the candle before)</option>
                      <option value="BOTH">Both (green and a higher close)</option>
                    </select>
                  </label>
                  {String(form.nth_side_rule ?? "NTH") !== "NTH" ? (
                    <label className="block text-sm text-slate-300">
                      <span className="text-xs uppercase tracking-wider text-slate-400">
                        Count &quot;most&quot; by{isOwn("nth_majority_by") ? " (own)" : ""}
                      </span>
                      <select
                        value={String(form.nth_majority_by ?? "COUNT")}
                        onChange={(e) => set("nth_majority_by", e.target.value)}
                        className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                      >
                        <option value="COUNT">Green vs red candles (a tie = no order)</option>
                        <option value="SIZE">Candle sizes added up</option>
                      </select>
                    </label>
                  ) : null}
                  <label className="col-span-2 flex min-h-9 items-start gap-2 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={Boolean(form.nth_skip_checks)}
                      onChange={(e) => set("nth_skip_checks", e.target.checked)}
                      className="mt-1 accent-[#10B981]"
                    />
                    <span>
                      Enter at once, skip the entry checks{isOwn("nth_skip_checks") ? " (own)" : ""}
                      <span className="block text-xs text-slate-400">
                        VWAP, RSI, ADX, EMA, Supertrend and the other checks in &quot;2. Checks before entering&quot; are not read, and the
                        flip is not applied. Market hours, the cut-off, the Trade list, the trade caps, the loss limit and one LIVE bot per
                        stock still apply. The smallest body and volume vs usual above still count when set.
                      </span>
                    </span>
                  </label>
                  <p className="col-span-2 text-xs text-slate-400">
                    The Most active routine as a bot: when the{" "}
                    {Number(form.nth_candle_no ?? 2) === 1 ? "1st" : Number(form.nth_candle_no ?? 2) === 2 ? "2nd" : Number(form.nth_candle_no ?? 2) === 3 ? "3rd" : `${Number(form.nth_candle_no ?? 2)}th`}{" "}
                    {Number(form.nth_candle_minutes ?? 5)}-minute candle of the day closes (
                    {(() => {
                      const end = 9 * 60 + 15 + Number(form.nth_candle_no ?? 2) * Number(form.nth_candle_minutes ?? 5);
                      return `${String(Math.floor(end / 60)).padStart(2, "0")}:${String(end % 60).padStart(2, "0")}`;
                    })()}
                    ), a green candle buys and a red one sells at once; a doji, a body under the floor or too little volume
                    vs usual (Groww&apos;s volume at that candle against the same candle on earlier days) means no order that
                    day. Each stock orders once a day; after a stop-out nothing re-enters. The bot&apos;s own candle becomes this
                    size, so the ATR stop, the exits and the chart read it. No order before 09:20 (the opening buffer), so on
                    1-minute candles pick the 6th candle or later. Measure it first on the Scalp page&apos;s Second candle tab.
                  </p>
                </>
              ) : null}
              <label className={clsx("block text-sm text-slate-300", form.entry_mode === "NTH_CANDLE" && "hidden")}>
                <span className="text-xs uppercase tracking-wider text-slate-400">
                  {form.entry_mode === "COLOUR" ? "Colour candle" : "Pattern candle"}
                  {isOwn("pattern_tf") ? " (own)" : ""}
                </span>
                <select
                  value={String(form.pattern_tf ?? 1)}
                  onChange={(e) => set("pattern_tf", e.target.value)}
                  className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                >
                  <option value="1">1 minute</option>
                  <option value="3">3 minutes</option>
                  <option value="5">5 minutes</option>
                  <option value="10">10 minutes</option>
                  <option value="15">15 minutes</option>
                  <option value="30">30 minutes</option>
                  <option value="60">60 minutes</option>
                </select>
              </label>
              {form.entry_mode === "COLOUR" ? (
                <>
                  <label className="block text-sm text-slate-300">
                    <span className="text-xs uppercase tracking-wider text-slate-400">
                      Candles in a row{isOwn("colour_count") ? " (own)" : ""}
                    </span>
                    <select
                      value={String(form.colour_count ?? 1)}
                      onChange={(e) => set("colour_count", e.target.value)}
                      className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                    >
                      <option value="1">1 candle (turn on the first)</option>
                      <option value="2">2 candles</option>
                      <option value="3">3 candles</option>
                      <option value="4">4 candles</option>
                      <option value="5">5 candles</option>
                    </select>
                  </label>
                  <ColourBodySize form={form} set={set} isOwn={isOwn} />
                  <p className="col-span-2 text-xs text-slate-400">
                    Enters, and turns a trade the other way, only once {Number(form.colour_count ?? 1)} closed candle
                    {Number(form.colour_count ?? 1) > 1 ? "s" : ""} in a row have the new colour. A candle whose body is under the
                    smallest body counts as no colour: the trade is held, like a doji. With Heikin Ashi colours on, the Heikin
                    Ashi body is measured.
                  </p>
                </>
              ) : null}
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
          <Only s="sma trend turn colour">
          <SectionTitle>Extra ways in (all off by default)</SectionTitle>
          </Only>
          <Only s="trend">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.always_in)}
              onChange={(e) => set("always_in", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("always_in") && <OwnTag />}
            Always in a trade: hold every armed stock, enter again right after each exit
            <InfoTip label="About always in a trade">
              The bot no longer waits for a fresh SMA cross. Every armed stock that is flat is entered at once on the side
              the SMAs point to now (SMA fast above slow buys, below sells), whenever the cross happened and whatever the
              candles look like: when the bot starts, when a stock is armed, and straight after any exit (stop, target,
              trailing stop or any exit rule ticked in step 3). The ticked entry checks (VWAP, RSI…), the stop, flip, market
              hours, the cut-off, the trade caps and the loss limit still apply; a check that says no is asked again on the
              next closed candle. An opposite cross still closes and reverses. Re-entering after a stop can mean many trades
              on a choppy stock: set a per-stock trade cap in step 6 and try it on Replay first. Gap mode and candle patterns
              ignore it.
            </InfoTip>
          </label>
          <label className={`ml-6 mt-2 flex items-center gap-2 text-sm text-slate-300 ${form.always_in ? "" : "opacity-50"}`}>
            <input
              type="checkbox"
              checked={Boolean(form.always_in_reverse)}
              disabled={!form.always_in}
              onChange={(e) => set("always_in_reverse", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("always_in_reverse") && <OwnTag />}
            After a losing trade, enter the opposite way
            <InfoTip label="About reverse after a loss">
              Needs &quot;Always in a trade&quot;. When a trade closes at a loss (before charges), by any exit (stop, trailing
              stop or an exit rule), the next entry goes the other way at once: a losing buy is followed by a sell, a losing
              sell by a buy, whatever the SMAs say. After a winning trade the bot re-enters on the side the SMAs point to, as
              before. The entry checks you ticked still apply; if one says no, it is asked again on the next candle. Entry
              reason &quot;Reversed after loss&quot;.
            </InfoTip>
          </label>
          </Only>
          <Only s="trend">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.join_trend)}
              onChange={(e) => set("join_trend", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("join_trend") && <OwnTag />}
            Join the morning trend
            <InfoTip label="About joining the morning trend">
              For a stock that opens already trending (its SMA cross printed yesterday or before 09:20), so the bot has no
              fresh cross to trade. Once a day, from the minutes below after 09:15, while the bot is flat, it treats the side
              the SMAs are on as a cross: the first closed candle on which every ticked filter (VWAP, RSI…), ADX and, in gap
              mode, the gap level agree opens the trade. Only the trend already running at that time is joined; a later cross
              is traded as a normal cross. A stopped-out join is not re-entered that day.
            </InfoTip>
          </label>
          <Field
            label="Join from (min after 09:15)"
            value={String(form.join_after_min ?? 15)}
            onChange={(v) => set("join_after_min", v)}
            own={isOwn("join_after_min")}
          />
          </Only>
          <Only s="sma">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Late entry
            <InfoTip label="About late entry">
              When a filter (VWAP, RSI, ADX or any other ticked one) refuses a cross, keep the cross armed for this many
              minutes. On each later closed candle the filters are checked again, and the first candle on which they all agree
              places the order, as long as the fast SMA is still on the cross&apos;s side. It gives up when the SMAs cross back,
              the time runs out or the entry cut-off is reached. Catches a strong trend whose cross came on the wrong side of
              VWAP. 0 = off. Gap mode has its own waiting entry and ignores this.
            </InfoTip>
          </div>
          <Field
            label="Keep a refused cross armed for (min, 0 = off)"
            value={String(form.late_entry_min ?? 0)}
            onChange={(v) => set("late_entry_min", v)}
            own={isOwn("late_entry_min")}
          />
          {form.use_gap_mode ? (
            <p className="mt-1 text-xs text-amber-300">Ignored while SMA gap mode is on (gap mode keeps a refused cross waiting itself).</p>
          ) : null}
          </Only>
          <Only s="turn colour">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.counter_entry)}
              onChange={(e) => set("counter_entry", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("counter_entry") && <OwnTag />}
            Early reversal: enter before the cross after N candles against the trend
            <InfoTip label="About the early reversal entry">
              While flat, a run of candles against the side the SMAs are on (fast below slow, then N green candles closing above
              the fast SMA) opens the trade that way before the cross, once per SMA run. N is the counter-candle exit count in
              step 3, or 3 when that is 0. Every check, the cut-off, the caps and the stop still apply.
            </InfoTip>
          </label>
          </Only>
          <Only s="turn">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.turn_entry)}
              onChange={(e) => set("turn_entry", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("turn_entry") && <OwnTag />}
            SMA {form.sma_fast ?? 9} turn: order at once when the fast SMA makes a V
            <InfoTip label="About the SMA 9 turn entry">
              On the candle where the fast SMA turns sharply (rising then falling = sell, falling then rising = buy), the bot
              orders that way at once, before the 9/21 cross. A turn is read with the V-turn numbers in step 3 (steep run, turn
              back, within N candles); a slow rounding or a sideways drift never counts. An open trade the other way is closed
              and reversed like an opposite cross (held when the SMA cross exit is off). Every check, the stop, the cut-off and
              the caps still apply. The SMA cross keeps trading as before. Gap mode and candle patterns ignore it.
            </InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.turn_alert)}
              onChange={(e) => set("turn_alert", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("turn_alert") && <OwnTag />}
            Telegram me when the SMA {form.sma_fast ?? 9} turns (V) on an armed stock
            <InfoTip label="About the SMA 9 turn alert">
              One Telegram on the candle the fast SMA turns sharply, for each armed stock while the bot runs. Alert only, unless
              the turn entry above is ticked too. Uses the V-turn numbers in step 3.
            </InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.uturn_on)}
              disabled={!form.turn_entry && !form.turn_alert}
              onChange={(e) => set("uturn_on", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("uturn_on") && <OwnTag />}
            Also count a rounded U turn (not only a sharp V)
            <InfoTip label="About the rounded U turn">
              A gentle turn: the fast SMA rose (or fell) for at least the candles below, flattened out, and now moves the other
              way by at least the slope below. It fires on the first candle beyond that slope, often a candle or two before a
              sharp V would. Used by the turn alert and the turn entry above; the alert says whether it was a V or a U. A turn
              the same way within 6 candles of the last one is the same turn (one alert, one order). Catches more turns, so
              more alerts in sideways stocks.
            </InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.turn_toward_cross)}
              disabled={!form.turn_entry}
              onChange={(e) => set("turn_toward_cross", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("turn_toward_cross") && <OwnTag />}
            Only take turns toward the 9/21 cross
            <InfoTip label="About turns toward the cross">
              A turn opens a trade only when it points toward the SMA 9 / SMA 21 cross: a sell on a turn down while SMA 9 is above
              SMA 21, a buy on a turn up while it is below. A turn the other way (a buy above, a sell below) opens nothing; if it
              is against an open trade it only closes that trade, without the reverse. Every entry check still applies.
            </InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.turn_exit_candle)}
              disabled={!form.turn_entry}
              onChange={(e) => set("turn_exit_candle", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("turn_exit_candle") && <OwnTag />}
            SMA 9 turns and SMA 9 exits close a trade only on a candle of the other colour
            <InfoTip label="About waiting for the candle colour">
              SMA 9 is an average of the last 9 candles, so it can turn down on a green candle. With this on, a turn against an
              open long closes it only when a red candle closes (a short: a green one). A turn on a candle of the trade&apos;s own
              colour waits: the first later red (or green) candle closes the trade and, when allowed, orders the other way. The
              wait ends if SMA 9 swings back with the trade. The SMA 9 slope and V-turn exits in step 3 wait the same way: a hit
              on a candle of the trade&apos;s own colour is held and judged again on the next candle. The stop and the other exits
              still guard the trade meanwhile.
            </InfoTip>
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.turn_colour_exit)}
              disabled={!form.turn_entry}
              onChange={(e) => set("turn_colour_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("turn_colour_exit") && <OwnTag />}
            Close a turn trade on the first candle of the other colour
            <InfoTip label="About closing a turn trade on the candle colour">
              A trade opened by an SMA 9 turn closes on the first candle that closes the other way after its entry: red for a
              long, green for a short (saved as &quot;Colour turned&quot;). No reverse: the next turn opens the next trade.
              Trades opened by a cross, gap mode or anything else are not affected. The stop and the other exits still apply.
            </InfoTip>
          </label>
          {form.uturn_on ? (
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <Field
                label="U: run before (candles)"
                value={String(form.uturn_run ?? 5)}
                onChange={(v) => set("uturn_run", v)}
                own={isOwn("uturn_run")}
              />
              <Field
                label="U: slope at least (%/candle)"
                value={String(form.uturn_min_pct ?? 0.005)}
                onChange={(v) => set("uturn_min_pct", v)}
                own={isOwn("uturn_min_pct")}
              />
            </div>
          ) : null}
          {form.turn_entry || form.turn_alert ? (
            <p className="mt-2 text-xs text-slate-400">
              The V and U shapes these read are drawn, with their angles, in the{" "}
              <a href="#sma-slope-editor" className="text-sky-300 underline-offset-2 hover:underline">
                slope editor (step 3)
              </a>
              .
            </p>
          ) : null}
          </Only>
          <Only s="trend colour bots">
          <SectionTitle>How the entry order is placed</SectionTitle>
          </Only>
          <Only s="trend">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.tall_pullback)}
              onChange={(e) => set("tall_pullback", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("tall_pullback") && <OwnTag />}
            Tall candle: wait for a pullback before entering
            <InfoTip label="About the tall-candle pullback">
              When the signal candle is tall (high − low at least the multiple below × ATR), the bot does not buy at its top or
              sell at its bottom. It waits for the price to come back: a buy at the higher of the fast SMA and the candle&apos;s
              half-way point, a sell at the lower of the fast SMA and the half-way point. The first price at that level sends the
              normal entry (every check, the stop, the trade cap and the cut-off still apply). If the price has not come back
              after the candles below have closed, no order is sent. Nothing waits on Groww&apos;s book in the meantime. A smaller
              signal candle orders at once, as before. A flipped stock always orders at once. Off by default; try it on Replay first.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Tall when height ≥ (× ATR)"
              value={String(form.tall_atr_mult ?? 1.5)}
              onChange={(v) => set("tall_atr_mult", v)}
              own={isOwn("tall_atr_mult")}
            />
            <Field
              label="Wait at most (candles)"
              value={String(form.tall_expiry_candles ?? 2)}
              onChange={(v) => set("tall_expiry_candles", v)}
              own={isOwn("tall_expiry_candles")}
            />
          </div>
          <CandleRulesPicture form={form} set={set} parts={["tall"]} />
          </Only>
          <Only s="trend colour">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.extreme_entry)}
              onChange={(e) => set("extreme_entry", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("extreme_entry") && <OwnTag />}
            Limit entry at the signal candle&apos;s low / high
            <InfoTip label="About the candle-extreme limit entry">
              Every signal (an SMA cross, a gap-mode entry, a join, a late or early-reversal entry; not candle patterns) waits
              at the signal candle&apos;s favourable extreme instead of ordering at once: a buy at its low, a sell at its high
              (or the fast SMA when it is nearer, with &quot;Fast SMA&quot; below). It is judged on the side the real order goes,
              so a flipped stock&apos;s buy signal sells at the high. The wait is cancelled when the price breaks beyond the
              other side of the candle by more than the % below (SETUP_INVALIDATED_OPPOSITE_BREAK), when a closed candle shows
              the SMAs crossed against the signal (if ticked), or when the candles below close without a fill. The first price at
              the limit sends the normal entry: every check, the stop, the trade cap and the cut-off still apply. Nothing waits
              on Groww&apos;s book. Takes over from the tall-candle pullback when both are on. Off by default; try it on Replay first.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <label className="block text-sm text-slate-300">
              <span className="text-xs uppercase tracking-wider text-slate-400">
                Limit at{isOwn("extreme_anchor") && <OwnTag />}
              </span>
              <select
                value={form.extreme_anchor ?? "CANDLE_EXTREME"}
                onChange={(e) => set("extreme_anchor", e.target.value)}
                className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
              >
                <option value="CANDLE_EXTREME">Candle low / high</option>
                <option value="SMA_9">Fast SMA (if nearer)</option>
              </select>
            </label>
            <Field
              label="Cancel beyond the candle by (%)"
              value={String(form.extreme_invalid_pct ?? 0.05)}
              onChange={(v) => set("extreme_invalid_pct", v)}
              own={isOwn("extreme_invalid_pct")}
            />
            <Field
              label="Wait at most (candles)"
              value={String(form.extreme_expiry_candles ?? 2)}
              onChange={(v) => set("extreme_expiry_candles", v)}
              own={isOwn("extreme_expiry_candles")}
            />
            <label className="flex min-h-11 items-center gap-2 text-sm text-slate-300 sm:min-h-9">
              <input
                type="checkbox"
                checked={form.extreme_cancel_on_cross !== false}
                onChange={(e) => set("extreme_cancel_on_cross", e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn("extreme_cancel_on_cross") && <OwnTag />}
              Cancel if the SMAs cross against it
            </label>
          </div>
          </Only>
          <Only s="colour">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.entry_candle_confirm)}
              onChange={(e) => set("entry_candle_confirm", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("entry_candle_confirm") && <OwnTag />}
            Enter only while the entry candle goes the trade&apos;s way
            <InfoTip label="About the entry-candle check">
              The checks read closed candles, so a signal from a green candle can still fill on a candle that then closes red.
              With this on, the order waits inside its own candle: a sell goes only once the price is below that candle&apos;s
              open by the margin (red so far), a buy only once it is above it (green so far). If a candle closes the wrong way
              the next one can still fill, up to the number of candles below; it is cancelled if the SMAs cross against the
              signal, at the cut-off or the trade cap. When it fills the price is a little worse than the candle&apos;s open, and
              a move that dips first may be missed. The tall-candle pullback and the candle-extreme limit wait for their own
              price and take over from it.
            </InfoTip>
          </label>
          </Only>
          <Only s="bots">
          <label className="mt-3 block text-xs text-slate-400">
            <span className="flex items-center gap-1">
              Hand-off orders to this bot {isOwn("handoff_candle") && <OwnTag />}
            </span>
            <select
              value={String(form.handoff_candle ?? "OFF").toUpperCase()}
              onChange={(e) => set("handoff_candle", e.target.value)}
              className="mt-1 min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            >
              <option value="OFF">Order at once (as before)</option>
              <option value="WAIT">Wait for a candle going its way, else skip</option>
              <option value="FOLLOW">Follow the forming candle: green buys, red sells</option>
            </select>
            <span className="mt-1 block text-slate-500">
              When another bot hands a stock over and this bot should order at once (a trend hand-off, or Bot 2 ordering at
              once), look at this bot&apos;s forming candle first. Uses the % and candle count below.
            </span>
          </label>
          </Only>
          <Only s="colour bots">
          {form.entry_candle_confirm || String(form.handoff_candle ?? "OFF").toUpperCase() !== "OFF" ? (
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <Field
                label="Past the candle's open by (%)"
                value={String(form.entry_candle_pct ?? 0.05)}
                onChange={(v) => set("entry_candle_pct", v)}
                own={isOwn("entry_candle_pct")}
              />
              <Field
                label="Wait at most (candles)"
                value={String(form.entry_candle_max ?? 2)}
                onChange={(v) => set("entry_candle_max", v)}
                own={isOwn("entry_candle_max")}
              />
            </div>
          ) : null}
          </Only>
          <Only s="trend">
          <label id="sma-day-extreme" className="mt-4 flex scroll-mt-24 items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.day_extreme_guard)}
              onChange={(e) => set("day_extreme_guard", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("day_extreme_guard") && <OwnTag />}
            Don&apos;t buy at the day&apos;s high or short at the day&apos;s low: wait for a pullback
            <a href="/learn/?lesson=day-high-low" className="text-xs text-sky-300 underline hover:text-sky-200">
              Guide (PDF)
            </a>
            <InfoTip label="About the day high/low guard">
              In a strong trend the signal candle often closes right at the day&apos;s high (a buy) or low (a short), so the
              order fills at the most stretched price of the day and the first pullback turns it into a loss. With this on, a
              buy whose signal candle closed within the distance below of today&apos;s high (a sell: of today&apos;s low) waits
              instead at the nearer of the fast SMA and the pullback below the close. It fills on the first price at or past
              that level (every check, the stop, caps and the cut-off still apply) and is dropped after the candles below, if
              the SMAs cross against it, at the cut-off or the trade cap: if the price never comes back, there is no trade.
              Candle patterns and candle colour do not use it; the tall-candle pullback and the candle-extreme limit already
              wait for a better price and take over from it.
            </InfoTip>
          </label>
          {form.day_extreme_guard ? (
            <div className="mt-2 grid grid-cols-3 items-end gap-2">
              <Field
                label="Near the high/low (%)"
                value={String(form.day_extreme_pct ?? 0.15)}
                onChange={(v) => set("day_extreme_pct", v)}
                own={isOwn("day_extreme_pct")}
              />
              <Field
                label="Wait for a pullback of (%)"
                value={String(form.day_extreme_pull_pct ?? 0.15)}
                onChange={(v) => set("day_extreme_pull_pct", v)}
                own={isOwn("day_extreme_pull_pct")}
              />
              <Field
                label="Wait at most (candles)"
                value={String(form.day_extreme_candles ?? 3)}
                onChange={(v) => set("day_extreme_candles", v)}
                own={isOwn("day_extreme_candles")}
              />
            </div>
          ) : null}
          </Only>
        </div>
      </FieldGroup>
      <FieldGroup id="check" open={isOpen("check")} onToggle={toggleGroup} title="2. Checks before entering" hint="Every ticked check must agree, or the signal is skipped" on={onCount("check")} wide={wide}>
        <div className="min-w-0">
          <SectionTitle
            info={
              <>
                These apply only when ticked, on a crossover; an unticked box is ignored. A trade needs every ticked filter to
                agree. Force order skips them.{crossExitOn(form) ? " A close still happens on the opposite cross." : ""}
              </>
            }
          >
            Main checks
          </SectionTitle>
          <Only s="sma trend ind">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.use_adx_filter}
              onChange={(e) => set("use_adx_filter", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_adx_filter") && <OwnTag />}
            Only when the trend is strong (ADX)
            <InfoTip label="About the ADX filter">Blocks entries while ADX (trend strength) is below the threshold.</InfoTip>
          </label>
          <Field label="ADX threshold" own={isOwn("adx_threshold")} value={String(form.adx_threshold)} onChange={(v) => set("adx_threshold", v)} />
          <AdxPicture form={form} set={set} />
          </Only>
          <Only s="ind">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_vwap)}
              onChange={(e) => set("use_vwap", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_vwap") && <OwnTag />}
            Price on the right side of VWAP
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
            Volume above average
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
            Solid candle body (skip doji)
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
            RSI(14) in range
            <InfoTip label="About the RSI filter">A buy needs RSI inside the buy range below; a sell inside the sell range.</InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field label="Buy RSI from" value={String(form.rsi_long_min ?? 40)} onChange={(v) => set("rsi_long_min", v)} own={isOwn("rsi_long_min")} />
            <Field label="Buy RSI to" value={String(form.rsi_long_max ?? 70)} onChange={(v) => set("rsi_long_max", v)} own={isOwn("rsi_long_max")} />
            <Field label="Sell RSI from" value={String(form.rsi_short_min ?? 30)} onChange={(v) => set("rsi_short_min", v)} own={isOwn("rsi_short_min")} />
            <Field label="Sell RSI to" value={String(form.rsi_short_max ?? 60)} onChange={(v) => set("rsi_short_max", v)} own={isOwn("rsi_short_max")} />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_bollinger)}
              onChange={(e) => set("use_bollinger", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_bollinger") && <OwnTag />}
            Bollinger: skip spikes and squeezes
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
          </Only>
          <Only s="gap">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            {form.use_gap_mode ? "Max gap for entry (don't chase a stretched gap)" : "Only when the SMA gap is in a range"}
            <InfoTip label="About the SMA gap range filter">
              {form.use_gap_mode
                ? "Gap mode is on: this is read on the candle where the order goes, so its Min repeats gap mode's entry level (the higher one wins). Only its Max adds anything: no entry while the gap is already wider. "
                : ""}
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
          </Only>
        </div>
        <div className="min-w-0">
          <Only s="colour trend ind">
          <SectionTitle>More checks</SectionTitle>
          </Only>
          <Only s="colour">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_candle_dir)}
              onChange={(e) => set("use_candle_dir", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_candle_dir") && <OwnTag />}
            Last candles must move the trade&apos;s way
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
          <CandleRulesPicture form={form} set={set} parts={["direction"]} />
          </Only>
          <Only s="trend ind">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            EMA lines (check here; the exit is in step 3)
            <InfoTip label="About the EMA cross">
              A second pair of lines, like Groww&apos;s &quot;EMA Cross 9 200&quot;: a fast EMA and a slow EMA, with the lengths
              you type below. The bot still trades the SMA cross; the EMAs are an extra check on its own candles. Entry: a buy
              only while the fast EMA is above the slow one, a sell only while it is below; otherwise the cross is skipped (✕
              &quot;EMA&quot; on the chart). Exit: an open trade closes when the two EMAs cross against it after the entry (fast
              drops below slow in a buy, rises above it in a sell). The slow EMA needs that many closed candles first (200
              five-minute candles is about 3 days of history). Off by default; try it on Replay first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Fast EMA (candles)"
              value={String(form.ema_fast ?? 9)}
              onChange={(v) => set("ema_fast", v)}
              own={isOwn("ema_fast")}
            />
            <Field
              label="Slow EMA (candles)"
              value={String(form.ema_slow ?? 200)}
              onChange={(v) => set("ema_slow", v)}
              own={isOwn("ema_slow")}
            />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_ema)}
              onChange={(e) => set("use_ema", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_ema") && <OwnTag />}
            Buy only while the fast EMA is above the slow one, sell only while below
          </label>
          </Only>
          <Only s="ind">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            MACD (check here; the exit is in step 3)
            <InfoTip label="About MACD">
              MACD line = EMA(fast) − EMA(slow) of the closes; the signal line is an EMA of the MACD line; the histogram is
              the gap between them. Read on the bot&apos;s own closed candles. The bot still trades the SMA cross; MACD is an
              extra check. Entry: a buy only while the MACD line is above its signal line (histogram above zero), a sell only
              while it is below; otherwise the cross is skipped (✕ &quot;MACD&quot; on the chart). Exit: an open trade closes
              when MACD crosses its signal line against it after the entry. Usual numbers 12 / 26 / 9. Off by default; try it
              on Replay first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field label="Fast (candles)" value={String(form.macd_fast ?? 12)} onChange={(v) => set("macd_fast", v)} own={isOwn("macd_fast")} />
            <Field label="Slow (candles)" value={String(form.macd_slow ?? 26)} onChange={(v) => set("macd_slow", v)} own={isOwn("macd_slow")} />
            <Field label="Signal (candles)" value={String(form.macd_signal ?? 9)} onChange={(v) => set("macd_signal", v)} own={isOwn("macd_signal")} />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_macd)}
              onChange={(e) => set("use_macd", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_macd") && <OwnTag />}
            Buy only while MACD is above its signal line, sell only while below
          </label>
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Cumulative delta (check here; the divergence exit is in step 3)
            <InfoTip label="About cumulative delta">
              Groww does not say which shares were bought at the ask and which sold at the bid, so each candle&apos;s
              delta is estimated: its volume × (close − open) ÷ (high − low). A strong green candle counts as mostly
              buying, a doji as none. The deltas are added up from 09:15 each day. Entry: a buy only while the cumulative
              delta has risen over the last N closed candles (since the open when fewer have closed), a sell only while it
              has fallen; otherwise the cross is skipped (✕ &quot;CVD&quot; on the chart). Off by default; try it on Replay
              first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field label="Compare over (candles)" value={String(form.cvd_lookback ?? 5)} onChange={(v) => set("cvd_lookback", v)} own={isOwn("cvd_lookback")} />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_cvd)}
              onChange={(e) => set("use_cvd", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_cvd") && <OwnTag />}
            Buy only while the cumulative delta is rising, sell only while it is falling
          </label>
          </Only>
          <Only s="colour">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.ha_colours)}
              onChange={(e) => set("ha_colours", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("ha_colours") && <OwnTag />}
            Read candle colours from Heikin Ashi candles
            <InfoTip label="About Heikin Ashi colours">
              Heikin Ashi candles are averages (close = (open + high + low + close) / 4, open = the middle of the candle
              before), so a trend shows as a long run of one colour and a lone red candle in a rise mostly disappears. With
              this on, every rule that reads a candle&apos;s colour reads the Heikin Ashi one: the candle direction check,
              counter candles (exit, early reversal, re-entry), candle colour entries, and the SMA 9 turn&apos;s &quot;other
              colour&quot; rules. Prices never change: orders, stops, targets, SMAs and P&amp;L stay on the real candles.
              Candle patterns keep the real candles. Off by default; try it on Replay first.
            </InfoTip>
          </label>
          </Only>
          <Only s="trend ind">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Supertrend (check here; the exit is in step 3)
            <InfoTip label="About the Supertrend">
              One line that follows the price, the same as Groww&apos;s &quot;Supertrend&quot; (ATR length 10, factor 3). It sits
              under the price while the trend is up (green) and above it while the trend is down (red), at a distance of
              factor × ATR, so it gives a fast-moving stock more room. It flips when a candle closes on the other side.
              Entry: a buy only while it is green, a sell only while it is red; otherwise the cross is skipped (✕ &quot;ST&quot;
              on the chart). Exit: an open trade closes when the Supertrend flips against it after the entry. A bigger
              factor (4) means fewer, later exits; a smaller one (2) means earlier exits and more shake-outs. Off by default;
              try it on Replay first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="ATR length (candles)"
              value={String(form.st_period ?? 10)}
              onChange={(v) => set("st_period", v)}
              own={isOwn("st_period")}
            />
            <Field
              label="Factor (× ATR)"
              value={String(form.st_mult ?? 3)}
              onChange={(v) => set("st_mult", v)}
              own={isOwn("st_mult")}
            />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_supertrend)}
              onChange={(e) => set("use_supertrend", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_supertrend") && <OwnTag />}
            Buy only while it is green, sell only while red
          </label>
          </Only>
          <Only s="trend">
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_level)}
              onChange={(e) => set("use_level", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_level") && <OwnTag />}
            Enter only beyond a price level
            <InfoTip label="About the price level check">
              The signal candle must close beyond the line picked below, on the trade&apos;s side: a buy above it, a sell below
              it. Bollinger middle: the 20-candle average (Bollinger numbers in this step). Upper band: a buy above the upper
              band, a sell below the lower band (only breakouts). Pivot P / R1: the day&apos;s pivot from the previous session; a
              buy above P (or R1), a sell below P (or S1). When the line is not known yet (too few candles, no previous session)
              the entry is refused. Read like the other checks, so with late entry on a refused signal can still enter once the
              price gets there.
            </InfoTip>
          </label>
          {form.use_level ? (
            <label className="mt-2 flex flex-col gap-1 text-xs text-slate-400">
              {isOwn("level_line") && <OwnTag />}
              Price level (buy above / sell below)
              <select
                value={String(form.level_line ?? "BB_MIDDLE")}
                onChange={(e) => set("level_line", e.target.value)}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              >
                <option value="BB_MIDDLE">Bollinger middle band</option>
                <option value="BB_UPPER">Upper band for a buy, lower band for a sell</option>
                <option value="PIVOT_P">Pivot P</option>
                <option value="PIVOT_R1">R1 for a buy, S1 for a sell</option>
              </select>
            </label>
          ) : null}
          <label id="sma-fresh" className="mt-4 flex scroll-mt-24 items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_fresh)}
              onChange={(e) => set("use_fresh", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_fresh") && <OwnTag />}
            Enter only on a fresh trend
            <InfoTip label="About the fresh trend check">
              The Trend quality page&apos;s Fresh trend flag as an entry rule. A buy needs all of: the two averages crossed up
              within the last 3 candles, the price and the fast average are above the day&apos;s average price (VWAP), the gap
              between the averages is wider than on the candle before, and the stock has traded at least the volume below
              against its usual for this time of day (0 = volume not checked). A sell is the mirror. With &quot;Late entry&quot;
              set to three candles (15 minutes on 5-minute candles), a cross that fails on its own candle can still enter on
              the next two. Until volume vs usual is known (no earlier day in the candles) the entry is refused.
            </InfoTip>
          </label>
          {form.use_fresh ? (
            <label className="mt-2 flex max-w-xs flex-col gap-1 text-xs text-slate-400">
              {isOwn("fresh_rvol") && <OwnTag />}
              Volume at least (× the usual for this time, 0 = any)
              <input
                type="number"
                min={0}
                max={20}
                step={0.1}
                value={String(form.fresh_rvol ?? 1.5)}
                onChange={(e) => set("fresh_rvol", e.target.value)}
                className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              />
            </label>
          ) : null}
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.use_high52)}
              onChange={(e) => set("use_high52", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("use_high52") && <OwnTag />}
            Enter only at the 52-week high
            <InfoTip label="About the 52-week high check">
              A breakout check. The signal candle must close at the stock&apos;s 52-week high (the highest daily high of the last 52
              weeks before that day, from Groww&apos;s daily candles): a buy at or above it, with &quot;Buys only&quot; a sell is refused.
              &quot;Sells only&quot; is the mirror at the 52-week low; &quot;Both&quot; judges each side on its own level. The band lets a
              close a little under the high count too (2 = within 2% of it). The levels load once a day in the background; until
              they have loaded the entry is refused, like the other checks when a line is not ready. The Scalp page&apos;s 52-week
              high tab lists the stocks near their high to arm.
            </InfoTip>
          </label>
          {form.use_high52 ? (
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-slate-400">
                {isOwn("high52_side") && <OwnTag />}
                Which side
                <select
                  value={String(form.high52_side ?? "LONG")}
                  onChange={(e) => set("high52_side", e.target.value)}
                  className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
                >
                  <option value="LONG">Buys only, at the 52-week high (sells refused)</option>
                  <option value="SHORT">Sells only, at the 52-week low (buys refused)</option>
                  <option value="BOTH">Both: buys at the high, sells at the low</option>
                </select>
              </label>
              <Field
                label="Band under the high (0 = must reach it)"
                suffix="%"
                value={String(form.high52_pct ?? 0)}
                onChange={(v) => set("high52_pct", v)}
                own={isOwn("high52_pct")}
              />
            </div>
          ) : null}
          </Only>
          <Only s="trend">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Opening range (09:15–09:30, entries only)
            <InfoTip label="About the opening range">
              Four checks on the first 15 minutes of the day. Each is its own tick box; a trade is taken only if every ticked
              check agrees. Volume: the 09:15–09:30 volume is at least the number below times the usual per-minute volume.
              VWAP: every opening candle closed on the trade&apos;s side of VWAP. SMA spread: the 9 and 21 lines are pulling
              apart by at least the % below and widening. Break: the first candle at or after 09:30 closes beyond the opening
              high (buy) or low (sell). Off by default; try it on Replay first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Volume ≥ × usual (opening)"
              value={String(form.or_volume_ratio ?? 2)}
              onChange={(v) => set("or_volume_ratio", v)}
              own={isOwn("or_volume_ratio")}
            />
            <Field
              label="SMA spread ≥ % (opening)"
              value={String(form.or_spread_pct ?? 0.1)}
              onChange={(v) => set("or_spread_pct", v)}
              own={isOwn("or_spread_pct")}
            />
          </div>
          {([
            ["use_or_volume", "Volume check"],
            ["use_or_vwap", "VWAP check (all opening candles on one side)"],
            ["use_or_spread", "SMA spread check (lines pulling apart)"],
            ["use_or_break", "Break check (first 09:15–09:30 high or low broken at 09:30)"],
          ] as const).map(([key, label]) => (
            <label key={key} className="mt-2 flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={Boolean(form[key])}
                onChange={(e) => set(key, e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn(key) && <OwnTag />}
              {label}
            </label>
          ))}
          </Only>
        </div>
      </FieldGroup>
      <FieldGroup id="exit" open={isOpen("exit")} onToggle={toggleGroup} title="3. When to exit" hint="Whichever ticked exit comes first closes the trade" on={onCount("exit")} wide={wide}>
        <div className="min-w-0">
          <SectionTitle>Main exit</SectionTitle>
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
          <Only s="sma">
          <div className={clsx("mt-2 rounded-md p-2 ring-1 ring-inset ring-white/10", !crossExitOn(form) && "opacity-50")}>
            <label htmlFor="sma-exit-candle" className="flex items-center gap-1 text-sm text-slate-300">
              {isOwn("exit_candle_minutes") && <OwnTag />}
              <b className="text-slate-100">Read the cross exit on</b>
              <InfoTip label="About the exit candle (mixed candles)">
                Enter on one candle size and exit on another. Entries keep the candle interval set in step 1 (for example 1
                minute). With a different size here (for example 15 minutes), an opposite cross on the entry candle no longer
                closes the trade: the trade closes when the SMAs cross against it on the exit candle. A trade entered while
                the exit candle already points the other way closes at the next exit-candle close once its own candle has
                turned against it too. No reverse: the next trade waits for its own entry signal. The stop, target and the
                other exits still work on the entry candle. Needs the SMA cross exit ticked.
              </InfoTip>
            </label>
            <select
              id="sma-exit-candle"
              disabled={!crossExitOn(form)}
              value={String(EXIT_CANDLE_MINUTES.includes(Number(form.exit_candle_minutes ?? 0)) ? Number(form.exit_candle_minutes ?? 0) : 0)}
              onChange={(e) => set("exit_candle_minutes", e.target.value)}
              className="mt-1 block h-10 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
            >
              {EXIT_CANDLE_MINUTES.map((m) => (
                <option key={m} value={String(m)}>
                  {m === 0 ? `Same candle as entries (${form.candle_minutes ?? 1} min)` : `${m}-minute candles${m === Number(form.candle_minutes ?? 1) ? " (same as entries)" : ""}`}
                </option>
              ))}
            </select>
          </div>
          </Only>
          <Only s="colour">
          <SmallCandlesCard form={form} set={set} isOwn={isOwn} />
          <SmallColourCard form={form} set={set} isOwn={isOwn} />
          </Only>
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
          <Only s="ind">
          <label className="mt-3 block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              Bollinger exit{isOwn("bb_exit") && <OwnTag />}
              <InfoTip label="About the Bollinger exit">
                Read once per closed candle after the entry, on the Bollinger Period and Width set in step 2 (the bands show on
                the chart). The middle-band exit waits until a candle has closed on the trade&apos;s side of the middle first. Your
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
          </Only>
          <Only s="sma trend">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.slow_sma_exit)}
              onChange={(e) => set("slow_sma_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("slow_sma_exit") && <OwnTag />}
            Close when a candle closes beyond the slow SMA against the trade
            <InfoTip label="About the slow SMA exit">
              A trade closes only when a candle closes beyond the slow SMA against it (below for a buy, above for a sell); small
              candles against it are ignored. While this is on, the counter-candle exit below is not used. Meant for the trend
              bot (Bot 3).
            </InfoTip>
          </label>
          </Only>
          <Only s="colour turn">
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Close after N candles in a row against the trade
            <InfoTip label="About counter candles">
              A run of closed candles against the trade often turns it before the SMAs cross: green candles in a sell, red in a
              buy. Exit: an open trade closes once its last N closed candles (counted after the entry) all went against it,
              and, with the fast-SMA box ticked, the last close is past the fast SMA on that side. Early entry: while flat, the
              same run against the side the SMAs are on (fast below slow, then N green candles closing above the fast SMA)
              opens the trade that way before the cross, once per SMA run. Every filter, the cut-off, the caps and the stop
              still apply. Off by default; try it on Replay first.
            </InfoTip>
          </div>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Exit after N candles against (0 = off)"
              value={String(form.counter_exit_count ?? 0)}
              onChange={(v) => set("counter_exit_count", v)}
              own={isOwn("counter_exit_count")}
            />
            <label className="block text-sm text-slate-300">
              <span className="text-xs uppercase tracking-wider text-slate-400">
                Counted by{isOwn("counter_rule") && <OwnTag />}
              </span>
              <select
                value={form.counter_rule ?? "COLOUR"}
                onChange={(e) => set("counter_rule", e.target.value)}
                className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
              >
                <option value="COLOUR">Colour (green / red)</option>
                <option value="CLOSES">Closes rising / falling</option>
                <option value="BOTH">Both</option>
              </select>
            </label>
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.counter_need_fast !== false}
              onChange={(e) => set("counter_need_fast", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("counter_need_fast") && <OwnTag />}
            Last close must be past the fast SMA
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Exit: min body × ATR (0 = any size)"
              value={String(form.counter_min_body ?? 0)}
              onChange={(v) => set("counter_min_body", v)}
              own={isOwn("counter_min_body")}
            />
            <p className="pb-2 text-xs text-slate-400">
              Each counter candle&apos;s body (close − open) must be at least this × its ATR, so small candles against the
              trade do not count. 0.5 = half an ATR.
            </p>
          </div>
          <CandleRulesPicture form={form} set={set} parts={["counter"]} />
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.counter_reenter)}
              disabled={!(Number(form.counter_exit_count ?? 0) > 0)}
              onChange={(e) => set("counter_reenter", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("counter_reenter") && <OwnTag />}
            Re-enter if the move carries on
            <InfoTip label="About re-entering after a counter-candle exit">
              After the counter-candle exit, the bot keeps watching while the SMAs stay on the trade&apos;s side. The first
              later candle that breaks the exit candle the trade&apos;s way puts the trade back on: in a sell, a red candle
              closing below the green exit candle&apos;s low; in a buy, a green candle closing above the red one&apos;s high.
              Another candle against it exits again. Stops when the SMAs cross back, or after the number of re-entries
              below in one SMA run. Every filter, the cut-off, the caps and the stop still apply. Needs the exit above (N ≥ 1).
            </InfoTip>
          </label>
          {form.counter_reenter && Number(form.counter_exit_count ?? 0) > 0 ? (
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <Field
                label="Re-entries per SMA run (1-10)"
                value={String(form.counter_reenter_max ?? 3)}
                onChange={(v) => set("counter_reenter_max", v)}
                own={isOwn("counter_reenter_max")}
              />
              <p className="pb-2 text-xs text-slate-400">
                Caps the round trips when a stock chops sideways without crossing. The daily trade cap still applies.
              </p>
            </div>
          ) : null}
          </Only>
          <Only s="trend ind">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.ema_exit)}
              onChange={(e) => set("ema_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("ema_exit") && <OwnTag />}
            Close when the EMAs cross against the trade (lengths set in step 2)
          </label>
          </Only>
          <Only s="ind">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.macd_exit)}
              onChange={(e) => set("macd_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("macd_exit") && <OwnTag />}
            Close when MACD crosses its signal line against the trade (numbers set in step 2)
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.cvd_div_exit)}
              onChange={(e) => set("cvd_div_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("cvd_div_exit") && <OwnTag />}
            Close on a cumulative delta divergence
            <InfoTip label="About the divergence exit">
              Once per closed candle after the entry: a buy closes when the candle&apos;s high is above every high of the
              candles before it today (up to the look-back) but the cumulative delta is lower than it was at that earlier
              high: price made a new high, buyers did not. A short closes on a new low the delta does not follow. No
              reverse. Exit reason CVD_DIVERGENCE.
            </InfoTip>
          </label>
          {form.cvd_div_exit && (
            <div className="mt-2 grid grid-cols-3 items-end gap-2">
              <Field label="Look-back (candles)" value={String(form.cvd_div_lookback ?? 10)} onChange={(v) => set("cvd_div_lookback", v)} own={isOwn("cvd_div_lookback")} />
            </div>
          )}
          </Only>
          <ExitJoinCard form={form} set={set} isOwn={isOwn} />
          <Only s="turn">
          <ReverseOnExit form={form} set={set} own={isOwn("reverse_on_exit")} ownColour={isOwn("reverse_colour_exit")} />
          <ReverseOwnSettings form={form} onChange={setReverse} own={isOwn("reverse_settings")} disabled={!form.reverse_on_exit} />
          </Only>
          <Only s="trend ind">
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.st_exit)}
              onChange={(e) => set("st_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("st_exit") && <OwnTag />}
            Close when the Supertrend flips against the trade (settings in step 2)
          </label>
          </Only>
        </div>
        <div className="min-w-0">
          <Only s="turn">
          <SectionTitle>SMA 9 line exits</SectionTitle>
          <div className="mt-3 rounded-md p-2 ring-1 ring-inset ring-white/10">
            <label htmlFor="sma-slope-candle" className="flex items-center gap-1 text-sm text-slate-300">
              {isOwn("slope_minutes") && <OwnTag />}
              <b className="text-slate-100">Read the SMA 9 slope on</b>
              <InfoTip label="About the slope candle">
                The slope exits below (bend, turn and V-turn) normally read the bot&apos;s own candle, so on 15-minute candles
                a V in the SMA 9 line is only seen 15-30 minutes after its bottom. Pick a smaller candle (for example 1 or 5
                minutes) to read the slope on that size instead, built from the stock&apos;s 1-minute candles: the turn is seen
                sooner, but the line is also noisier. Entries stay on the bot&apos;s own candle. &quot;Slope over&quot; and the
                V-turn window then count these smaller candles, and &quot;wait for a candle of the other colour&quot; reads the
                colour of the smaller candle. To see the same numbers on the chart, set the chart to this candle size.
              </InfoTip>
            </label>
            <select
              id="sma-slope-candle"
              value={String(SLOPE_MINUTES.includes(Number(form.slope_minutes ?? 0)) ? Number(form.slope_minutes ?? 0) : 0)}
              onChange={(e) => set("slope_minutes", e.target.value)}
              className="mt-1 block h-10 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
            >
              {SLOPE_MINUTES.map((m) => (
                <option key={m} value={String(m)}>
                  {m === 0 ? `The bot's own candle (${form.candle_minutes ?? 1} min)` : `${m}-minute candles${m === Number(form.candle_minutes ?? 1) ? " (same as the bot)" : ""}`}
                </option>
              ))}
            </select>
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.slope_exit)}
              onChange={(e) => set("slope_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("slope_exit") && <OwnTag />}
            Close when the SMA 9 line flattens or turns
            <InfoTip label="About the SMA 9 slope exit">
              The angle of a line on the chart depends on its zoom, so the bot reads the fast SMA&apos;s slope in % per candle:
              how much it moved over the last few candles (below), per candle. Read once per closed candle after the entry, on
              the trade&apos;s signal side. Bend: the trade closes (SMA9_BEND) once the slope has dropped by the % below from its
              steepest since the entry, e.g. from 0.20 %/candle to 0.08 % with 60 — the line flattening at the top of a run.
              Turn: it closes (SMA9_TURN) once the slope is at least the %/candle below against the trade (the line pointing the
              other way). Usually one or two candles earlier than the SMA cross; in a trend with pauses it also exits on a
              harmless flattening, so try it on Replay first. 0 % bend = bend exit off. Off by default.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Slope over (candles)"
              value={String(form.slope_lookback ?? 2)}
              onChange={(v) => set("slope_lookback", v)}
              own={isOwn("slope_lookback")}
            />
            <Field
              label="Bend: exit at % off the steepest (0 = off)"
              value={String(form.slope_bend_pct ?? 60)}
              onChange={(v) => set("slope_bend_pct", v)}
              own={isOwn("slope_bend_pct")}
            />
            <label className="flex min-h-11 items-center gap-2 text-sm text-slate-300 sm:min-h-9">
              <input
                type="checkbox"
                checked={form.slope_turn !== false}
                onChange={(e) => set("slope_turn", e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn("slope_turn") && <OwnTag />}
              Turn exit
            </label>
            <Field
              label="Turn at (%/candle against)"
              value={String(form.slope_turn_pct ?? 0.02)}
              onChange={(v) => set("slope_turn_pct", v)}
              own={isOwn("slope_turn_pct")}
            />
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.vturn_exit)}
              onChange={(e) => set("vturn_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("vturn_exit") && <OwnTag />}
            V-turn exit: close on a sharp V in the SMA 9 line
            <InfoTip label="About the V-turn exit">
              Closes the trade (SMA9_VTURN) when the SMA 9 slope was steep with the trade (at least the first %/candle) and,
              within the candles below, turns steeply against it (at least the second %/candle): the point of a V — a fall
              then a sharp climb in a sell, a rise then a sharp drop in a buy. A slow U-shaped rounding or a sideways drift
              never qualifies, so it is left to the other exits. Uses &quot;Slope over&quot; above; works on its own or next to
              Bend / Turn. A V in price shows in SMA 9 as an arch about 9 candles wide, so keep the window at 5-8. Off by
              default; try it on Replay first.
            </InfoTip>
          </label>
          <div className="mt-2 grid grid-cols-3 items-end gap-2">
            <Field
              label="Steep run (%/candle with)"
              value={String(form.vturn_move_pct ?? 0.02)}
              onChange={(v) => set("vturn_move_pct", v)}
              own={isOwn("vturn_move_pct")}
            />
            <Field
              label="Turn back (%/candle against)"
              value={String(form.vturn_turn_pct ?? 0.015)}
              onChange={(v) => set("vturn_turn_pct", v)}
              own={isOwn("vturn_turn_pct")}
            />
            <Field
              label="Within (candles)"
              value={String(form.vturn_within ?? 6)}
              onChange={(v) => set("vturn_within", v)}
              own={isOwn("vturn_within")}
            />
          </div>
          {form.turn_entry || form.turn_alert ? (
            <p className="mt-1 text-xs text-slate-400">
              These V-turn numbers also drive the SMA {form.sma_fast ?? 9} turn {form.turn_entry ? "entry" : "alert"} in step 1.
            </p>
          ) : null}
          <SlopeEditor form={form} set={set} />
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.vturn_handoff)}
              disabled={!form.vturn_exit}
              onChange={(e) => set("vturn_handoff", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("vturn_handoff") && <OwnTag />}
            After a V-turn exit at a loss, move the stock to Bot 2 (flipped, gap mode)
            <InfoTip label="About the V-turn hand-off">
              When the V-turn exit closes a trade below its entry (a buy) or above it (a sell), the stock leaves this bot&apos;s
              Trade list and is armed on Bot 2 with the same chop settings as the recovery hand-off (flipped, gap mode). Bot 2
              waits for its next cross. A V-turn exit in profit stays here. In LIVE it moves only once Groww shows no shares; a
              PAPER bot never arms a LIVE Bot 2, and one LIVE bot per stock still applies. In a backtest the stock leaves the
              rest of the run. Nothing on Bot 2 itself.
            </InfoTip>
          </label>
          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.decel_exit)}
              onChange={(e) => set("decel_exit", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("decel_exit") && <OwnTag />}
            Deceleration guard: close when the SMA {form.sma_fast ?? 9} line bends, before it turns
            <InfoTip label="About the deceleration guard">
              The slope is measured in ATR per candle: (SMA now − SMA k candles ago) ÷ (k × ATR 14), so the same numbers fit a
              cheap and a costly stock, a fast morning and a quiet afternoon. From the entry the bot keeps the steepest slope
              (the peak). It closes the trade (SMA9_DECEL) on the first closed candle where the peak reached the arm level (a real
              run), the slope has dropped the % below from that peak (the line flattening), it has kept bending for the
              candles below (the slope falling by at least the bend each candle), and, with the close check on, the candle closed
              through the SMA {form.sma_fast ?? 9} against the trade. It does not wait for the slope to turn negative, so it is
              usually one or two candles before the V-turn and the SMA cross. Reads the same candle as the other SMA 9 exits
              (&quot;Read the SMA 9 slope on&quot; above). Off by default; try it on Replay first.
            </InfoTip>
          </label>
          <div className={clsx("mt-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-3", !form.decel_exit && "opacity-50")}>
            <Field label="Slope over (candles)" value={String(form.decel_lookback ?? 2)} onChange={(v) => set("decel_lookback", v)} own={isOwn("decel_lookback")} />
            <Field label="Arms at (ATR/candle)" value={String(form.decel_arm_atr ?? 0.1)} onChange={(v) => set("decel_arm_atr", v)} own={isOwn("decel_arm_atr")} />
            <Field label="Down from peak" suffix="%" value={String(form.decel_drop_pct ?? 50)} onChange={(v) => set("decel_drop_pct", v)} own={isOwn("decel_drop_pct")} />
            <Field label="Bend per candle (ATR)" value={String(form.decel_min_atr ?? 0.02)} onChange={(v) => set("decel_min_atr", v)} own={isOwn("decel_min_atr")} />
            <Field label="Bending candles" value={String(form.decel_candles ?? 2)} onChange={(v) => set("decel_candles", v)} own={isOwn("decel_candles")} />
            <Field label="Close through by (ATR)" value={String(form.decel_pierce_atr ?? 0)} onChange={(v) => set("decel_pierce_atr", v)} own={isOwn("decel_pierce_atr")} />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.decel_confirm !== "OFF"}
              disabled={!form.decel_exit}
              onChange={(e) => set("decel_confirm", e.target.checked ? "CLOSE" : "OFF")}
              className="accent-[#10B981]"
            />
            {isOwn("decel_confirm") && <OwnTag />}
            Only when the candle also closes through the SMA {form.sma_fast ?? 9} (fewer early exits, a little later)
          </label>
          <label className={clsx("mt-2 block text-sm text-slate-300", !form.decel_exit && "opacity-50")}>
            <span className="flex items-center gap-1 text-xs uppercase tracking-wider text-slate-400">
              Measure the peak slope from{isOwn("decel_peak_from") && <OwnTag />}
              <InfoTip label="About where the peak is measured from">
                The entry (default): the steepest slope is counted from the first candle that closes after the entry, so the guard
                needs at least two candles after the entry. The start of the run: it is counted from the first candle of the
                current stretch where the SMA {form.sma_fast ?? 9} has pointed the trade&apos;s way (back from the entry, same day),
                so a trade bought late in a run, when the line is already flattening, can be closed on its first candle.
              </InfoTip>
            </span>
            <select
              value={form.decel_peak_from === "RUN" ? "RUN" : "ENTRY"}
              disabled={!form.decel_exit}
              onChange={(e) => set("decel_peak_from", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="ENTRY">The entry (needs two candles after it)</option>
              <option value="RUN">The start of the current run (can act on the first candle)</option>
            </select>
          </label>
          </Only>
        </div>
      </FieldGroup>
      <Only s="gap">
      <FieldGroup id="gap" open={isOpen("gap")} onToggle={toggleGroup} title="4. SMA gap mode" hint="Changes both the entry and the exit: waits for the gap to widen, exits when it fades" on={onCount("gap")} wide={wide}>
        <div className="min-w-0">
          {form.entry_mode === "PATTERN" || form.entry_mode === "COLOUR" ? (
            <p className="mt-1 text-xs leading-snug text-amber-300">
              Not used while “Enter on” is Candle patterns or Candle colour: those trades open at the start of a candle.
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
              Gap % = (SMA {form.sma_fast ?? 9} − SMA {form.sma_slow ?? 21}) ÷ SMA {form.sma_slow ?? 21} × 100. On a ₹1,000 stock,
              0.05% means SMA {form.sma_fast ?? 9} is ₹0.50 above SMA {form.sma_slow ?? 21} (−0.05% = ₹0.50 below). Buy levels
              are positive, sell levels negative; keep each exit between 0 and its entry (buy 0 &lt; exit &lt; entry, sell entry
              &lt; exit &lt; 0).{" "}
              A cross only arms the trade; the order goes on the first closed candle whose gap reaches the entry level (plus
              the wait, if set — the gap must still be there). While the gap keeps widening the trade is held; once it has
              cleared the exit level and fades back to it (or gives back the set share of its widest), the trade closes (exit
              reason “Gap fade”). An opposite cross still closes at once, and the reverse waits for its own gap. The stop,
              filters, entry cut-off and square-off still apply. An exit level beyond the entry level (e.g. sell in at −0.08,
              out at −0.39) is a lock-in level: the gap exit waits until the gap has been that wide, then closes when it comes
              back to it; a trade whose gap never gets that wide is left to the stop, the opposite cross and square-off.
            </InfoTip>
          </label>
          <p className="mt-1 text-xs leading-snug text-slate-400">
            Gap = how far SMA {form.sma_fast ?? 9} is from SMA {form.sma_slow ?? 21}, in % of the price. Buy numbers positive,
            sell numbers negative; each exit sits between 0 and its entry (e.g. buy in 0.05 out 0.02, sell in −0.05 out −0.02).
          </p>
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
          <GapModePicture form={form} set={set} />
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
              Check the fade every second (market hours only){" "}
              <InfoTip label="About checking the fade every second">
                Judges the exit on the live price, as if that second closed the candle, instead of waiting for the minute to
                close. Faster on a sharp reversal; a candle that turns back inside the minute can exit too early. Entries still
                wait for the candle to close. With “1s OFF” in the status bar it checks at the slower quote pace (every few
                seconds).
              </InfoTip>
            </span>
          </label>
          <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.gap_fade_minute)}
              onChange={(e) => set("gap_fade_minute", e.target.checked)}
              className="mt-1 accent-[#10B981]"
            />
            <span>
              {isOwn("gap_fade_minute") && <OwnTag />}
              Check the fade every 1-minute candle (live and replays){" "}
              <InfoTip label="About checking the fade every minute">
                For a bot on 2-minute or longer candles: each closed 1-minute candle&apos;s close is read as if it closed the
                bot&apos;s candle, so a fade can exit inside a 5-minute candle without waiting for it to close. It uses
                Groww&apos;s 1-minute history, so it also works after hours and in replays / backtests (the every-second check
                needs live market prices). Entries still wait for the bot&apos;s candle to close. No effect on a 1-minute bot.
              </InfoTip>
            </span>
          </label>
        </div>
      </FieldGroup>
      </Only>
      <FieldGroup id="stop" open={isOpen("stop")} onToggle={toggleGroup} title="5. Stop-loss and target" hint="The safety stop on every trade" on={onCount("stop")} wide={wide}>
        <div className="min-w-0">
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
                : form.stop_type === "SUPERTREND"
                  ? "Stop-loss on the Supertrend line (moves each candle)"
                  : form.stop_type === "VWAP"
                    ? `Stop-loss at VWAP ∓ ${String(form.vwap_stop_atr ?? 0.5)}× ATR (moves each candle)`
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
              value={form.stop_type === "SMA_GAP" || form.stop_type === "TSL" || form.stop_type === "SUPERTREND" || form.stop_type === "VWAP" ? form.stop_type : "ATR"}
              onChange={(e) => set("stop_type", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="ATR">ATR — fixed stop at {String(form.atr_multiplier)}× ATR</option>
              <option value="SMA_GAP">SMA gap — moving stop + target</option>
              <option value="TSL">Trailing stop (TSL) — ₹ or % steps, like Groww</option>
              <option value="SUPERTREND">Supertrend — stop on the Supertrend line, moves each candle</option>
              <option value="VWAP">VWAP + ATR buffer — stop at VWAP − k × ATR (buy) / VWAP + k × ATR (sell), moves each candle</option>
            </select>
          </label>
          {form.stop_type === "VWAP" ? (
            <div className="mt-2 rounded-md border border-sky-400/20 bg-sky-400/[0.04] p-2">
              <div className="grid grid-cols-2 items-end gap-2">
                <Field label="Buffer below / above VWAP" suffix="× ATR" value={String(form.vwap_stop_atr ?? 0.5)} onChange={(v) => set("vwap_stop_atr", v)} own={isOwn("vwap_stop_atr")} />
                <label className="block text-xs text-slate-300">
                  <span className="flex items-center justify-between gap-2">
                    <span>Buffer slider</span>
                    <span className="font-mono text-slate-100">{String(form.vwap_stop_atr ?? 0.5)} × ATR</span>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={3}
                    step={0.05}
                    value={Math.min(3, Math.max(0, Number(form.vwap_stop_atr ?? 0.5) || 0))}
                    onChange={(e) => set("vwap_stop_atr", String(Number(e.target.value)))}
                    className="mt-1 w-full accent-sky-400"
                    aria-label="VWAP stop buffer in ATR multiples"
                  />
                </label>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                Stop = VWAP − {String(form.vwap_stop_atr ?? 0.5)} × ATR for a buy, VWAP + {String(form.vwap_stop_atr ?? 0.5)} × ATR for a sell. VWAP is
                where the day&apos;s money has traded on average (institutional fair value); the ATR buffer keeps a wick through it from
                closing the trade. Read on each closed candle and only ever tightened (a buy&apos;s stop rises with VWAP, never falls). If the
                level is on the wrong side when the trade opens, the ATR stop ({String(form.atr_multiplier)}× ATR) guards it until the
                level comes under a buy or above a sell. A price at or past it closes the trade (VWAP SL). LIVE moves the Groww stop order the
                same way.
              </p>
            </div>
          ) : null}
          {form.stop_type === "SUPERTREND" ? (
            <p className="mt-2 text-xs text-slate-400">
              The stop sits on the Supertrend line ({String(form.st_period ?? 10)} candles, ×{String(form.st_mult ?? 3)}; set them under
              Supertrend in 3. When to exit) and moves to the new line after every closed candle, only ever tighter. A price at or past
              the line closes the trade (Supertrend SL). If the line is on the wrong side when the trade opens, the ATR stop below is
              used until the line comes under a buy or above a sell. LIVE moves the Groww stop order the same way.
            </p>
          ) : null}
          <Only s="bots">
          <label className="mt-3 block text-sm text-slate-300">
            <span className="flex items-center gap-1 text-xs uppercase tracking-wider text-slate-400">
              After a stop-loss exit, move the stock to{isOwn("stop_handoff") && <OwnTag />}
              <InfoTip label="About moving a stock after a stop-loss">
                The other hand-offs move a stock after a recovered loss or a losing V-turn (to Bot 2) or a trend breakout (to
                Bot 3); a stop-loss left it where it was. Pick where it goes right after any stop closes a trade (ATR, trailing,
                Supertrend, VWAP, staged plan; never a target): back to the bot it came from (a stock that never moved stays),
                Bot 2, the choppy bot (with the same chop settings as a recovery hand-off: flipped, gap mode), or Bot 3, the
                trend bot (its own settings). It then waits for its next cross there. In LIVE it moves only once Groww shows no
                shares; a PAPER bot never arms a LIVE bot, and one LIVE bot per stock still applies. In a backtest that plays
                the hand-offs the stock carries on in the run with that bot&apos;s settings. Each move is listed under
                Hand-offs. Off by default.
              </InfoTip>
            </span>
            <select
              value={["BACK", "BOT2", "BOT3"].includes(String(form.stop_handoff)) ? String(form.stop_handoff) : "OFF"}
              onChange={(e) => set("stop_handoff", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="OFF">Off — the stock stays on this bot</option>
              <option value="BACK">Back to the bot it came from</option>
              <option value="BOT2">Bot 2 (choppy: flipped, gap mode)</option>
              <option value="BOT3">Bot 3 (trend)</option>
            </select>
          </label>
          {["BACK", "BOT2", "BOT3"].includes(String(form.stop_handoff)) ? (
            <label className="mt-1 flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={form.stop_handoff_loss !== false}
                onChange={(e) => set("stop_handoff_loss", e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn("stop_handoff_loss") && <OwnTag />}
              Only when the stop closed at a loss (a trailing stop in profit leaves the stock here)
            </label>
          ) : null}
          </Only>
          <label className="mt-3 block text-sm text-slate-300">
            <span className="flex items-center gap-1 text-xs uppercase tracking-wider text-slate-400">
              Pivot target{isOwn("pivot_target") && <OwnTag />}
              <InfoTip label="About the pivot target">
                Classic daily pivots from the previous session&apos;s high, low and close: P = (H + L + C) ÷ 3, R1 = 2P − L,
                S1 = 2P − H, R2 = P + (H − L), S2 = P − (H − L). &quot;R1&quot; books a long&apos;s profit at R1 and a short&apos;s at S1;
                when the entry is already past that level it uses R2 / S2, and with no level beyond the entry the trade has no
                pivot target. &quot;R2&quot; uses R2 / S2 only. Checked on every price; whichever exit comes first closes the
                trade (the stop, any other target and the other exits are unchanged). The levels are drawn on the chart
                (Indicators → Pivot points). Off by default.
              </InfoTip>
            </span>
            <select
              value={form.pivot_target === "R1" || form.pivot_target === "R2" ? form.pivot_target : "OFF"}
              onChange={(e) => set("pivot_target", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="OFF">Off — no pivot target</option>
              <option value="R1">R1 for a buy, S1 for a sell (then R2 / S2)</option>
              <option value="R2">R2 for a buy, S2 for a sell</option>
            </select>
          </label>
          <div className={clsx("mt-3 rounded-md p-2 ring-1 ring-inset ring-white/10", (form.stop_type ?? "ATR") !== "ATR" && "opacity-50")}>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={Boolean(form.stop_plan)}
                onChange={(e) => set("stop_plan", e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn("stop_plan") && <OwnTag />}
              Staged stop plan: breakeven, then a Supertrend trail
              <InfoTip label="About the staged stop plan">
                Needs stop type ATR. R is the trade&apos;s risk: entry minus the ATR stop (ATR × the multiple below, e.g. 2.5).
                While the best price since entry is under the first level the ATR stop stays. Once it reaches the breakeven
                level (+1.5R) the stop moves to the entry price plus the round-trip charges, so the trade cannot lose. Once it
                reaches the trail level (+2R) the stop follows the Supertrend line ({String(form.st_period ?? 10)}, ×
                {String(form.st_mult ?? 3)}; set under Supertrend in 3. When to exit) on the bot&apos;s own candles, never below
                breakeven. The stop only ever tightens. Each move is written to the trade (blotter: Stop moves). LIVE moves the
                Groww stop order the same way as the trailing stop. Off by default; try it on Replay first.
              </InfoTip>
            </label>
            <div className="mt-2 grid grid-cols-2 items-end gap-2">
              <Field label="Breakeven at (R)" value={String(form.plan_be_r ?? 1.5)} onChange={(v) => set("plan_be_r", v)} own={isOwn("plan_be_r")} />
              <Field label="Supertrend trail from (R)" value={String(form.plan_trail_r ?? 2)} onChange={(v) => set("plan_trail_r", v)} own={isOwn("plan_trail_r")} />
            </div>
            {(form.stop_type ?? "ATR") !== "ATR" && form.stop_plan ? (
              <p className="mt-2 text-xs text-amber-300">The plan only runs with stop type ATR; it does nothing with this stop type.</p>
            ) : null}
          </div>
          {form.stop_type !== "TSL" ? (
            <>
              <div className="mt-2 grid grid-cols-2 items-end gap-2">
                <Field label="ATR period" own={isOwn("atr_period")} value={String(form.atr_period)} onChange={(v) => set("atr_period", v)} />
                <Field label="ATR stop ×" own={isOwn("atr_multiplier")} value={String(form.atr_multiplier)} onChange={(v) => set("atr_multiplier", v)} />
              </div>
              {form.stop_type !== "SMA_GAP" ? (
                <div id="sma-atr-target" className="mt-2 grid grid-cols-2 items-end gap-2">
                  <Field
                    label="ATR target × (0 = no target)"
                    own={isOwn("atr_target_mult")}
                    value={String(form.atr_target_mult ?? 0)}
                    onChange={(v) => set("atr_target_mult", v)}
                  />
                  <p className="text-[11px] leading-snug text-slate-400">
                    {Number(form.atr_target_mult) > 0
                      ? `Books the profit at ${String(form.atr_target_mult)} × ATR from the entry (buy: above, sell: below), using the same ATR as the stop. With the ${String(form.atr_multiplier)} × ATR stop that risks 1 to make ${(Number(form.atr_target_mult) / (Number(form.atr_multiplier) || 1)).toFixed(1)}.`
                      : "Like the ATR stop, but on the profit side: type 3 to book the profit at 3 × ATR from the entry. 0 = no target (the other exits close the trade)."}
                    {form.use_stop === false ? " Needs the stop-loss switched on." : ""}
                  </p>
                </div>
              ) : null}
              <AtrPicture form={form} set={set} />
            </>
          ) : null}
          {form.stop_type === "SMA_GAP" ? (
            <div className="mt-2 rounded-md border border-sky-400/20 bg-sky-400/[0.04] p-2">
              <div className="grid grid-cols-3 items-end gap-2">
                <Field label="Stop × gap" value={String(form.gap_sl_mult ?? 1)} onChange={(v) => set("gap_sl_mult", v)} own={isOwn("gap_sl_mult")} />
                <Field label="Target × gap" value={String(form.gap_tp_mult ?? 2)} onChange={(v) => set("gap_tp_mult", v)} own={isOwn("gap_tp_mult")} />
                <Field label="Min gap for the stop size" suffix="%" value={String(form.gap_min_pct ?? 0.2)} onChange={(v) => set("gap_min_pct", v)} own={isOwn("gap_min_pct")} />
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
                <label className="inline-flex items-center gap-1">
                  <input
                    type="radio"
                    name="tsl_mode"
                    checked={form.tsl_mode === "ATR"}
                    onChange={() => set("tsl_mode", "ATR")}
                    className="accent-sky-400"
                  />
                  ATR multiples (auto-sized per stock)
                </label>
                <span className="text-[11px] text-slate-500">
                  % scales with the stock price; ATR multiples scale with how far each stock moves per candle, worked out at
                  entry from that stock&apos;s own ATR, so one setting fits every stock a bot is handed.
                </span>
              </div>
              {form.tsl_mode === "ATR" ? (
                <TslAtrSliders form={form} set={set} isOwn={isOwn} />
              ) : (form.tsl_mode ?? "POINTS") === "PERCENT" ? (
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
              <TslCalculator form={form} set={set} symbol={stockScope ? scope : null} armed={armed} />
              <Hint label="About the trailing stop">
                {form.tsl_mode === "ATR" ? (
                  <>
                    <b>ATR mode:</b> at entry the bot reads the stock&apos;s ATR on its own candle and places the stop{" "}
                    {String(form.tsl_sl_atr ?? 1.5)} × ATR away, moves it every {String(form.tsl_trail_atr ?? 0.5)} × ATR the price
                    gains past its best (never back), and books {Number(form.tsl_target_atr ?? 3) > 0 ? `${String(form.tsl_target_atr ?? 3)} × ATR` : "no"} target. A
                    stock that moves ₹2 a candle gets a ₹{(2 * Number(form.tsl_sl_atr ?? 1.5)).toLocaleString("en-IN")} stop, one that moves
                    ₹0.40 a candle a ₹{(0.4 * Number(form.tsl_sl_atr ?? 1.5)).toLocaleString("en-IN")} stop. Without a formed ATR the ₹ points
                    below the sliders are used, so a trade is never left without a stop.
                  </>
                ) : (form.tsl_mode ?? "POINTS") === "PERCENT" ? (
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
      </FieldGroup>
      <FieldGroup id="risk" open={isOpen("risk")} onToggle={toggleGroup} title="6. Size, daily limits and times" hint="Quantity, max daily loss, max trades, cut-off, square-off" wide={wide}>
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
                  label="Max trades per stock / day (0 = no cap)"
                  value={String(form.max_trades_per_stock ?? 0)}
                  onChange={(v) => set("max_trades_per_stock", v)}
                  own={isOwn("max_trades_per_stock")}
                />
                <Field
                  label="No new entries after"
                  value={form.entry_cutoff_time || "15:00"}
                  onChange={(v) => set("entry_cutoff_time", v)}
                />
                <Field label="Square-off" value={form.square_off_time} onChange={(v) => set("square_off_time", v)} />
              </div>
              {currentDesk() !== "research" && (
                <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
                  <input
                    type="checkbox"
                    checked={Boolean(form.auto_start)}
                    onChange={(e) => set("auto_start", e.target.checked)}
                    className="accent-[#10B981]"
                  />
                  Start this bot by itself (PAPER only)
                  <InfoTip label="About auto-start">
                    The bots come back stopped after every deploy or restart, and each morning after the day&apos;s square-off.
                    With this on, the bot presses Start by itself while it is stopped in PAPER and NSE is open, the same as your
                    Start press: a cross already on the chart is not traded, the next one is. It never starts a LIVE bot (that
                    stays your press), a paused bot (Pause is your choice) or one stopped for the day by the loss limit, the
                    trade cap, a panic or the all-bots stop. Works only on the main desk, not the backtest server.
                  </InfoTip>
                  {String(form.trading_mode ?? "").toUpperCase() === "LIVE" && form.auto_start ? (
                    <span className="text-xs text-amber-300">This bot is LIVE: it will not start by itself.</span>
                  ) : null}
                </label>
              )}
            </>
          )}
        </div>
      </FieldGroup>
      <Only s="bots review">
      <FieldGroup id="advanced" open={isOpen("advanced")} onToggle={toggleGroup} title="7. Advanced: moving stocks between bots, asking me" hint="Recovery hold, hand-offs to Bot 2 / Bot 3, 1-minute review" on={onCount("advanced")} wide={wide}>
        <Only s="bots">
        <div className="min-w-0">
          <SectionTitle>Recovery hold and hand-offs</SectionTitle>
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Trend defence and recovery (for the trend bot)
            <InfoTip label="About trend defence and recovery">
              Recovery hold (needs the SMA cross exit on): on an opposite cross, a trade in profit closes at once and the bot
              reverses as usual; a trade at a loss is held instead. In candle-colour entry mode the colour turn plays the
              cross&apos;s part: a turn against a losing trade holds it, and the rules below are read on the colour candles
              (e.g. 15-minute ones). Each candle close after that, it closes as a recovery when
              the loss is back within the target % of the entry or the price comes back to the slow SMA, and then (if ticked)
              the stock moves to Bot 2: flipped, SMA gap mode in at ±0.119 % and out at ±0.003 % with the fade confirmed by a
              close past the slow SMA, VWAP check, RSI buy 40–70 / sell 30–60, and the trend breakout back to Bot 3; Bot 2 waits
              for its next cross. It closes as an
              emergency when the loss grows to the floor × the loss at the cross, or the ATR floor × ATR, or after the
              candles below; that stock is then not traded again on this bot today. A held trade still open at the entry
              cut-off (15:00) closes there. The stop still guards the trade throughout. A practice bot never hands a stock to
              a LIVE Bot 2. A replay (backtest) moves the stock too, so you can check it after market hours; the stock
              then leaves the rest of that replay. Off by default; try it on Replay first.
            </InfoTip>
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.recovery_hold)}
              onChange={(e) => set("recovery_hold", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("recovery_hold") && <OwnTag />}
            Recovery hold: on an opposite cross (or colour turn), close a winner, hold a loser for its recovery
          </label>
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Recovery target (% of entry)"
              value={String(form.recovery_tolerance_pct ?? 0.15)}
              onChange={(v) => set("recovery_tolerance_pct", v)}
              own={isOwn("recovery_tolerance_pct")}
            />
            <Field
              label="Give up after (candles)"
              value={String(form.recovery_max_candles ?? 6)}
              onChange={(v) => set("recovery_max_candles", v)}
              own={isOwn("recovery_max_candles")}
            />
            <Field
              label="Loss floor (× loss at the cross)"
              value={String(form.recovery_emergency_mult ?? 1.5)}
              onChange={(v) => set("recovery_emergency_mult", v)}
              own={isOwn("recovery_emergency_mult")}
            />
            <Field
              label="ATR floor (× ATR)"
              value={String(form.recovery_atr_mult ?? 2.5)}
              onChange={(v) => set("recovery_atr_mult", v)}
              own={isOwn("recovery_atr_mult")}
            />
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.recovery_handoff !== false}
              onChange={(e) => set("recovery_handoff", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("recovery_handoff") && <OwnTag />}
            After a recovery, move the stock to Bot 2 (flipped, gap mode)
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.handoff_now)}
              onChange={(e) => set("handoff_now", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("handoff_now") && <OwnTag />}
            Bot 2 orders at once: the opposite of this bot&apos;s trade (no filters)
            <InfoTip label="About Bot 2 ordering at once">
              When a stock moves to Bot 2 (after a recovery, or a losing V-turn exit with that box ticked), Bot 2 orders straight
              away the opposite way of the trade this bot just closed: a short that lost makes Bot 2 buy, a long makes it sell.
              No SMA cross, gap mode, VWAP / RSI / ADX or flip is read; Bot 2&apos;s own stop, trailing stop, target and exits then
              run the trade. Bot 2 must be running, and market hours, its cut-off, trade cap, loss limit and one LIVE bot per
              stock still apply. If Bot 2 is LIVE this is a real Groww order. Replays only move the stock (they play one bot).
              Off by default; without it Bot 2 waits for its next cross.
            </InfoTip>
          </label>
          <div className="mt-2 grid max-w-sm grid-cols-1 items-end gap-2">
            <Field
              label="Move to Bot 2 after this many losing trades on a stock today (0 = off)"
              value={String(form.chop_losses ?? 0)}
              onChange={(v) => set("chop_losses", v)}
              own={isOwn("chop_losses")}
            />
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Trade first, then sort: once this bot has lost that many trades (before charges) on the same stock today, the stock
            moves to Bot 2 (flipped, gap mode) when it is flat. Bot 2 orders at once too with the box above ticked.
          </p>
          <div className="mt-4 flex items-center gap-1 text-sm text-slate-300">
            Trend breakout (move the stock on)
            <InfoTip label="About the trend breakout">
              Where it goes: a stock this bot got from another bot through a hand-off goes back to the bot it came from
              (usually Bot 1); any other stock goes to Bot 3. What counts: one tall candle (below), or a strong trend: the same 7
              checks as the Scalp page&apos;s &quot;Strong trend&quot; on this bot&apos;s own candles (price beyond SMA 9 beyond SMA 21,
              beyond VWAP, both SMAs sloping, the gap widening, most of the last 12 closes beyond SMA 9, higher highs and lows,
              no cross in 12 candles). An open trade against it: by default it is not cut; it runs to its own stop, target or
              exit, and the stock moves once it is flat. Tick &quot;Close the trade at once&quot; for the old way.{" "}
              For the chop bot (Bot 2). A hand-off does not tick it: a stock moved to Bot 2 follows Bot 2&apos;s own setting here
              (all stocks, or that stock&apos;s own). On each closed candle, a
              breakout is a candle at least the × ATR below tall, plus either an SMA 9/21 gap of at least the % below or
              volume at least the × below of the last 20 candles&apos; average. The day&apos;s first candle is never read: its volume is
              set against the quiet end of the day before, so nearly every stock would look like a breakout at the open. A
              trade already on the breakout&apos;s side (a
              buy on a green breakout, a sell on a red one) is kept and the stock stays on this bot. Otherwise the stock leaves this
              bot&apos;s Trade list (after an open trade against it closes, or at once with the tick box below) and is armed on the
              next bot with that bot&apos;s own settings. Moved to Bot 3, Bot 3 then enters on the breakout&apos;s side (green candle buys, red sells) without waiting for a
              new cross: every Bot 3 check still applies, and the order waits at the breakout candle&apos;s 9 SMA or half-way
              level for 2 candles instead of buying its top. In LIVE the move waits until Groww shows no shares. A practice
              bot never hands a stock to a LIVE Bot 3. A replay with &quot;Play hand-offs&quot; plays the move inside the backtest.
              Off by default; try it on Replay first.
            </InfoTip>
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={Boolean(form.breakout_handoff)}
              onChange={(e) => set("breakout_handoff", e.target.checked)}
              className="accent-[#10B981]"
            />
            {isOwn("breakout_handoff") && <OwnTag />}
            On a breakout, move the stock on (back where it came from, else to Bot 3)
          </label>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <label className="block text-xs text-slate-400">
              <span className="flex items-center gap-1">
                What counts as a breakout {isOwn("breakout_rule") && <OwnTag />}
              </span>
              <select
                value={String(form.breakout_rule ?? "CANDLE").toUpperCase()}
                onChange={(e) => set("breakout_rule", e.target.value)}
                className="mt-1 min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              >
                <option value="CANDLE">One tall candle (the numbers below)</option>
                <option value="STRONG">A strong trend (you pick the checks below)</option>
              </select>
            </label>
            <label className="block text-xs text-slate-400">
              <span className="flex items-center gap-1">
                When to move it {isOwn("breakout_when") && <OwnTag />}
              </span>
              <select
                value={String(form.breakout_when ?? "ANY").toUpperCase()}
                onChange={(e) => set("breakout_when", e.target.value)}
                className="mt-1 min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
              >
                <option value="ANY">Whenever it shows (traded here or not)</option>
                <option value="IN_PROFIT">Only while this bot&apos;s trade rides it in profit</option>
              </select>
            </label>
            {String(form.breakout_when ?? "ANY").toUpperCase() === "IN_PROFIT" ? (
              <p className="self-end text-xs text-slate-400 sm:col-span-2">
                Trade first, then sort: only a stock this bot is holding, on the trend&apos;s side and in profit, moves on. This
                bot keeps its trade; the stock is armed on Bot 3 too (or back where it came from), which opens the same side at
                once with its own size, stop and exits; once this bot&apos;s trade closes, the stock is Bot 3&apos;s only. A stock this
                bot has not traded stays here. Once a day per stock. A backtest holds one trade per stock, so there this
                bot&apos;s trade is closed (TREND_HANDOFF) and Bot 3 takes the same side at once.
              </p>
            ) : (
            <label className="flex items-center gap-2 self-end text-sm text-slate-300">
              <input
                type="checkbox"
                checked={Boolean(form.breakout_cut)}
                onChange={(e) => set("breakout_cut", e.target.checked)}
                className="accent-[#10B981]"
              />
              {isOwn("breakout_cut") && <OwnTag />}
              Close an open trade against it at once (off: it runs to its own exit first)
            </label>
            )}
            {String(form.breakout_when ?? "ANY").toUpperCase() === "IN_PROFIT" ? (
              <>
                <label className="block text-xs text-slate-400">
                  <span className="flex items-center gap-1">
                    Smallest profit before moving, % of entry {isOwn("breakout_min_profit_pct") && <OwnTag />}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={20}
                    step={0.05}
                    value={String(form.breakout_min_profit_pct ?? 0)}
                    onChange={(e) => set("breakout_min_profit_pct", e.target.value)}
                    className="mt-1 min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
                  />
                </label>
                <label className="block text-xs text-slate-400">
                  <span className="flex items-center gap-1">
                    Smallest profit before moving, ₹ a share {isOwn("breakout_min_profit_rs") && <OwnTag />}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={10000}
                    step={0.5}
                    value={String(form.breakout_min_profit_rs ?? 0)}
                    onChange={(e) => set("breakout_min_profit_rs", e.target.value)}
                    className="mt-1 min-h-9 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
                  />
                </label>
                <p className="text-xs text-slate-400 sm:col-span-2">
                  0 = any profit. With 0.5 % the trade must be at least 0.5 % past its entry (₹5 on a ₹1,000 stock) before
                  the stock moves; with both set, both must be reached. Below it the stock stays here and is read again on
                  the next candle.
                </p>
              </>
            ) : null}
          </div>
          {String(form.breakout_rule ?? "CANDLE").toUpperCase() === "STRONG" ? (
            <TrendChecks
              value={String(form.breakout_trend_checks ?? "")}
              need={Number(form.breakout_trend_need ?? 6) || 6}
              onChange={(v) => set("breakout_trend_checks", v)}
              onNeed={(n) => set("breakout_trend_need", String(n))}
              own={isOwn("breakout_trend_checks") || isOwn("breakout_trend_need")}
            />
          ) : null}
          <div
            className={clsx(
              "mt-2 grid-cols-3 items-end gap-2",
              String(form.breakout_rule ?? "CANDLE").toUpperCase() === "STRONG" ? "hidden" : "grid"
            )}
          >
            <Field
              label="Candle (× ATR)"
              value={String(form.breakout_atr_mult ?? 1.8)}
              onChange={(v) => set("breakout_atr_mult", v)}
              own={isOwn("breakout_atr_mult")}
            />
            <Field
              label="Breakout: SMA gap ≥ (%)"
              value={String(form.breakout_gap_pct ?? 0.2)}
              onChange={(v) => set("breakout_gap_pct", v)}
              own={isOwn("breakout_gap_pct")}
            />
            <Field
              label="Volume (× avg)"
              value={String(form.breakout_volume_mult ?? 1.5)}
              onChange={(v) => set("breakout_volume_mult", v)}
              own={isOwn("breakout_volume_mult")}
            />
          </div>
        </div>
        </Only>
        <Only s="review">
        <div className="min-w-0">
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
          <ReviewConditions form={form} set={set} isOwn={isOwn} />
          <div className="mt-2 grid grid-cols-2 items-end gap-2">
            <Field
              label="Ask when the SMA gap is within ±"
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
          <div className="mt-2">
            <div className="text-xs text-slate-400">
              Check candles{isOwn("review_check_minutes") && <OwnTag />}
              <span className="ml-2 text-[11px] text-slate-500">Pick one or more; AGAINST on any fires the review.</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {CHECK_MINUTES_OPTIONS.map((m) => {
                const current = toCheckMinutes(form.review_check_minutes);
                const on = current.includes(m);
                return (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={on}
                    onClick={() => {
                      const next = on ? current.filter((x) => x !== m) : [...current, m].sort((a, b) => a - b);
                      set("review_check_minutes", next.length ? next : [1]);
                    }}
                    className={clsx(
                      "min-h-9 rounded-md px-2.5 text-xs font-semibold ring-1 ring-inset",
                      on
                        ? "bg-sky-500/15 text-sky-200 ring-sky-400/50"
                        : "bg-black/20 text-slate-300 ring-white/15 hover:bg-white/5"
                    )}
                  >
                    {m} min
                  </button>
                );
              })}
            </div>
          </div>
          <label className="mt-2 block text-xs text-slate-400">
            Check mode{isOwn("review_check_mode") && <OwnTag />}
            <select
              value={String(form.review_check_mode ?? "ALWAYS")}
              onChange={(e) => set("review_check_mode", e.target.value)}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="ALWAYS">Prompt whenever uncertain</option>
              <option value="ONLY_IF_NOT_WITH">Only if a check candle is not with the trade</option>
              <option value="ONLY_IF_AGAINST">Only if a check candle is against the trade</option>
            </select>
          </label>
          <label className="mt-2 block text-xs text-slate-400">
            If I don&apos;t answer{isOwn("review_default_answer") && <OwnTag />}
            <select
              value={String(form.review_default_answer ?? "PROMPT")}
              onChange={(e) => {
                const next = e.target.value;
                if (
                  (next === "EXIT" || next === "REVERSE") &&
                  String(form.trading_mode ?? "").toUpperCase() === "LIVE" &&
                  !window.confirm(
                    next === "REVERSE"
                      ? "Exit and reverse automatically on LIVE: the bot will close the reviewed trade and open the other side (two real Groww orders) without asking you. Continue?"
                      : "Exit automatically on LIVE: the bot will close the reviewed trade without asking you. Continue?"
                  )
                ) {
                  return;
                }
                set("review_default_answer", next);
              }}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            >
              <option value="PROMPT">Prompt me (default) — no answer keeps the trade</option>
              <option value="CONTINUE">Continue automatically — no prompt, trade stays</option>
              <option value="EXIT">Exit automatically — no prompt, trade is closed</option>
              <option value="REVERSE">Exit and reverse — no prompt, trade is closed and the other side opened</option>
            </select>
          </label>
        </div>
        </Only>
      </FieldGroup>
      </Only>
      </StyleCtx.Provider>
      <div
        className={clsx(
          "mt-3 flex flex-wrap items-center gap-3",
          wide && saveBarPinned && "sticky bottom-0",
          wide && "z-10 -mx-4 border-t border-white/10 bg-[#151921] px-4 py-3 shadow-[0_-8px_16px_-8px_rgba(0,0,0,0.5)] lg:-mx-6 lg:px-6",
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
        {wide ? <PinToggle pinned={saveBarPinned} onChange={setSaveBarPinned} what="the Save bar (bottom of the screen)" /> : null}
        {unsaved && !busy ? (
          <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-xs font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/30">
            Unsaved changes
          </span>
        ) : null}
        {!stockScope ? (
          confirmReset ? (
            <span className="inline-flex flex-wrap items-center gap-2 rounded-md bg-rose-500/10 px-2 py-1 text-xs text-rose-100 ring-1 ring-inset ring-rose-400/30">
              Put every shared setting back to its default? The Trade list, PAPER / LIVE and the bot name stay.
              <button type="button" disabled={busy} onClick={() => resetDefaults(false)} className="rounded bg-rose-600 px-2 py-0.5 font-semibold text-white hover:bg-rose-500">
                Reset
              </button>
              <button type="button" disabled={busy} onClick={() => resetDefaults(true)} className="rounded bg-rose-600 px-2 py-0.5 font-semibold text-white hover:bg-rose-500">
                Reset + clear stocks&apos; own settings
              </button>
              <button type="button" onClick={() => setConfirmReset(false)} className="rounded px-2 py-0.5 ring-1 ring-inset ring-white/20 hover:bg-white/5">
                Keep
              </button>
            </span>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmReset(true)}
              title="Every shared strategy setting back to its default (what a new bot starts with)"
              className="inline-flex h-11 items-center rounded-md px-4 text-sm text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 sm:h-9"
            >
              Reset to defaults
            </button>
          )
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

type RuleGroup = "enter" | "check" | "exit" | "gap" | "stop" | "advanced";

/** One switched-on setting, in plain words, and what "Turn off" sets (none for the core exits). */
type Rule = { group: RuleGroup; key: string; text: string; off?: Partial<Record<keyof SmaConfig, string | boolean>>; warn?: boolean };

const RULE_GROUPS: { id: RuleGroup; title: string }[] = [
  { id: "enter", title: "1. When to enter" },
  { id: "check", title: "2. Checks before entering" },
  { id: "exit", title: "3. When to exit" },
  { id: "gap", title: "4. SMA gap mode" },
  { id: "stop", title: "5. Stop-loss and target" },
  { id: "advanced", title: "7. Advanced" },
];

/** Every setting that changes what the bot does, when it is on. Display only: reads the form, saves nothing. */
export function activeRules(f: SmaConfig): Rule[] {
  const out: Rule[] = [];
  const add = (r: Rule) => out.push(r);
  const n = (v: unknown, fb: number) => (Number.isFinite(Number(v)) ? Number(v) : fb);
  const fast = f.sma_fast ?? 9;
  const slow = f.sma_slow ?? 21;
  const pattern = f.entry_mode === "PATTERN";
  const colour = f.entry_mode === "COLOUR";
  const nth = f.entry_mode === "NTH_CANDLE";
  const nthNo = Math.min(6, Math.max(1, n(f.nth_candle_no, 2)));
  const nthWord = nthNo === 1 ? "1st" : nthNo === 2 ? "2nd" : nthNo === 3 ? "3rd" : `${nthNo}th`;
  const medium = f.sma_medium != null && String(f.sma_medium).trim() !== "";

  // 1. When to enter
  add(
    pattern
      ? { group: "enter", key: "entry_mode", text: `Enters on ${f.pattern_set === "ALL" ? "all" : "strong"} candle patterns (${f.pattern_tf ?? 1}-min), not the SMA cross; each trade closes at the end of its candle`, off: { entry_mode: "SMA" }, warn: true }
      : nth
        ? { group: "enter", key: "entry_mode", text: `Second candle: once a day, the ${nthWord} ${n(f.nth_candle_minutes, 5)}-min candle's colour buys (green) or sells (red) at its close, not the SMA cross${n(f.nth_min_body_pct, 0) > 0 ? ` (body ≥ ${f.nth_min_body_pct}%)` : ""}${n(f.nth_min_rvol, 0) > 0 ? ` (volume ≥ ${f.nth_min_rvol}× usual)` : ""}${f.nth_dir_rule === "CLOSES" ? " (up / down = a higher / lower close than the candle before)" : f.nth_dir_rule === "BOTH" ? " (up = green and a higher close, down = red and a lower close)" : ""}${f.nth_side_rule === "MAJORITY" ? `, the way most of the first ${n(f.nth_candle_no, 2)} candles went` : f.nth_side_rule === "AGREE" ? ", only when most of the first candles agree" : ""}${f.nth_skip_checks ? ", entering at once without the entry checks" : ""}; the stop, target, exits and square-off close it`, off: { entry_mode: "SMA" }, warn: true }
      : colour
        ? { group: "enter", key: "entry_mode", text: `Rides the ${f.pattern_tf ?? 1}-min candle colour, not the SMA cross: green buys, red sells, held until ${Number(f.colour_count ?? 1) > 1 ? `${f.colour_count} candles` : "a candle"} of the other colour${Number(f.colour_min_body ?? 0) > 0 ? ` (bodies ≥ ${f.colour_min_body}%)` : ""}${String(f.colour_body_seq ?? "") ? ` (each candle ≥ ${f.colour_body_seq}${f.colour_body_unit === "ATR" ? "× ATR" : "%"})` : ""}${Number(f.colour_inside_minutes ?? 0) > 0 ? ` (one candle is enough when all its ${f.colour_inside_minutes}-min candles agree${Number(f.colour_inside_body ?? 0) > 0 ? `, body ≥ ${f.colour_inside_body}%` : ""})` : ""}${String(f.colour_pull ?? "OFF") !== "OFF" ? ` (waits for a pullback to the ${f.colour_pull === "HALF" ? "middle" : f.colour_pull === "LOW" ? "low" : "SMA 9 / low"} of the candle for ${f.colour_pull_candles ?? 1} candle(s)${f.colour_pull_market ? ", then at the market" : ""})` : ""}, then reversed${f.colour_exit_on ? ` (closes by its own rule: ${f.colour_exit_count ?? 2} candle(s) of the other colour${Number(f.colour_exit_body ?? 0.2) > 0 ? `, bodies ≥ ${f.colour_exit_body ?? 0.2}%` : ""})` : ""}${f.colour_force ? "; Force order follows the colour" : ""}`, off: { entry_mode: "SMA" }, warn: true }
        : { group: "enter", key: "entry_mode", text: `Enters when SMA ${fast} crosses SMA ${slow} on ${f.candle_minutes ?? 1}-min candles` }
  );
  if (medium) add({ group: "enter", key: "sma_medium", text: `Third line SMA ${f.sma_medium}: all three must line up`, off: { sma_medium: "" } });
  if (f.flip_orders) add({ group: "enter", key: "flip_orders", text: "Flip: a buy signal SELLS and a sell signal BUYS", off: { flip_orders: false }, warn: true });
  if (f.always_in) add({ group: "enter", key: "always_in", text: "Always in a trade: every armed stock is held on the SMAs' side and entered again right after each exit (no cross needed)", off: { always_in: false }, warn: true });
  if (f.always_in && f.always_in_reverse) add({ group: "enter", key: "always_in_reverse", text: "After a losing trade, the next entry goes the opposite way", off: { always_in_reverse: false } });
  if (f.join_trend) add({ group: "enter", key: "join_trend", text: `Joins a trend already running ${n(f.join_after_min, 15)} min after 09:15 (no cross needed)`, off: { join_trend: false } });
  if (n(f.late_entry_min, 0) > 0) add({ group: "enter", key: "late_entry_min", text: `Late entry: a cross refused by a check is retried for ${n(f.late_entry_min, 0)} min${f.use_gap_mode ? " (ignored: gap mode is on)" : ""}`, off: { late_entry_min: "0" }, warn: Boolean(f.use_gap_mode) });
  if (f.turn_entry) add({ group: "enter", key: "turn_entry", text: `Orders at once when the SMA ${fast} line makes a sharp V (before the cross)`, off: { turn_entry: false } });
  if (f.turn_toward_cross && f.turn_entry) add({ group: "enter", key: "turn_toward_cross", text: "SMA 9 turns trade only toward the 9/21 cross (sell above, buy below)", off: { turn_toward_cross: false } });
  if (f.turn_exit_candle && f.turn_entry) add({ group: "exit", key: "turn_exit_candle", text: "SMA 9 turns and SMA 9 exits close a trade only once a candle of the other colour closes", off: { turn_exit_candle: false } });
  if (f.turn_colour_exit && f.turn_entry) add({ group: "exit", key: "turn_colour_exit", text: "A turn trade closes on the first candle of the other colour", off: { turn_colour_exit: false } });
  if (f.uturn_on && (f.turn_entry || f.turn_alert)) add({ group: "enter", key: "uturn_on", text: `Rounded U turns count too (after ${f.uturn_run ?? 5} candles one way)`, off: { uturn_on: false } });
  if (f.turn_alert) add({ group: "enter", key: "turn_alert", text: `Telegram when the SMA ${fast} line makes a sharp V`, off: { turn_alert: false } });
  if (f.counter_entry) add({ group: "enter", key: "counter_entry", text: "Early reversal: enters before the cross after a run of candles against the trend", off: { counter_entry: false } });
  if (f.tall_pullback) add({ group: "enter", key: "tall_pullback", text: `After a tall candle (≥ ${n(f.tall_atr_mult, 1.5)}× ATR), waits for a pullback before entering`, off: { tall_pullback: false } });
  if (f.day_extreme_guard) add({ group: "enter", key: "day_extreme_guard", text: `Never buys within ${n(f.day_extreme_pct, 0.15)}% of the day's high (or shorts near its low): waits ${n(f.day_extreme_pull_pct, 0.15)}% back or at the fast SMA, up to ${n(f.day_extreme_candles, 3)} candles`, off: { day_extreme_guard: false } });
  if (String(f.handoff_candle ?? "OFF").toUpperCase() === "WAIT") add({ group: "enter", key: "handoff_candle", text: "Hand-off orders wait for a candle going their way, else skip", off: { handoff_candle: "OFF" } });
  if (String(f.handoff_candle ?? "OFF").toUpperCase() === "FOLLOW") add({ group: "enter", key: "handoff_candle", text: "Hand-off orders follow the forming candle: green buys, red sells", off: { handoff_candle: "OFF" } });
  if (f.entry_candle_confirm) add({ group: "enter", key: "entry_candle_confirm", text: `Orders only while the entry candle goes the trade's way (${n(f.entry_candle_pct, 0.05)}% past its open, up to ${n(f.entry_candle_max, 2)} candles)`, off: { entry_candle_confirm: false } });
  if (f.extreme_entry) add({ group: "enter", key: "extreme_entry", text: "Waits to enter at the signal candle's low (buy) / high (sell); may miss the trade", off: { extreme_entry: false } });

  // 2. Checks before entering
  const check = (key: keyof SmaConfig, on: unknown, text: string) => {
    if (on) add({ group: "check", key, text, off: { [key]: false } });
  };
  check("use_adx_filter", f.use_adx_filter, `Trend strength: ADX at least ${f.adx_threshold}`);
  check("use_vwap", f.use_vwap, "Price on the right side of VWAP (buy above, sell below)");
  check("use_volume", f.use_volume, `Volume at least ${n(f.volume_min_ratio, 1)}× the average`);
  check("use_density", f.use_density, `Candle body at least ${n(f.density_min_pct, 50)}% of the candle`);
  check("use_rsi", f.use_rsi, `RSI: buy ${n(f.rsi_long_min, 40)}–${n(f.rsi_long_max, 70)}, sell ${n(f.rsi_short_min, 30)}–${n(f.rsi_short_max, 60)}`);
  check("use_bollinger", f.use_bollinger, "Bollinger: no chasing a spike, no trading a squeeze");
  check("use_gap_long", f.use_gap_long, `Buy only when the SMA gap is ${n(f.gap_long_min, 0.02)}% to ${n(f.gap_long_max, 0.5)}%`);
  check("use_gap_short", f.use_gap_short, `Sell only when the SMA gap is ${n(f.gap_short_min, -0.5)}% to ${n(f.gap_short_max, -0.02)}%`);
  check("use_candle_dir", f.use_candle_dir, `Last ${n(f.candle_dir_count, 2)} candles must move the trade's way`);
  check("use_ema", f.use_ema, `EMA ${n(f.ema_fast, 9)} must be on the trade's side of EMA ${n(f.ema_slow, 200)}`);
  check("use_macd", f.use_macd, `MACD ${n(f.macd_fast, 12)}/${n(f.macd_slow, 26)}/${n(f.macd_signal, 9)} must be on the trade's side of its signal line`);
  check("use_cvd", f.use_cvd, `Cumulative delta must have moved the trade's way over the last ${n(f.cvd_lookback, 5)} candles`);
  check("use_supertrend", f.use_supertrend, "Supertrend must point the trade's way");
  check(
    "use_level",
    f.use_level,
    {
      BB_MIDDLE: "Price beyond the Bollinger middle band (buy above, sell below)",
      BB_UPPER: "Price beyond the outer Bollinger band (buy above the upper, sell below the lower)",
      PIVOT_P: "Price beyond the pivot P (buy above, sell below)",
      PIVOT_R1: "Price beyond R1 / S1 (buy above R1, sell below S1)",
    }[String(f.level_line ?? "BB_MIDDLE")] ?? "Price beyond the chosen level"
  );
  check("use_fresh", f.use_fresh, `Fresh trend only: a new cross, beyond VWAP, the gap widening${Number(f.fresh_rvol ?? 1.5) > 0 ? `, volume ${n(f.fresh_rvol, 1.5)}× usual` : ""}`);
  check(
    "use_high52",
    f.use_high52,
    `${{ LONG: "Buy only at the 52-week high", SHORT: "Sell only at the 52-week low", BOTH: "Buy at the 52-week high, sell at the 52-week low" }[String(f.high52_side ?? "LONG")] ?? "52-week high check"}${n(f.high52_pct, 0) > 0 ? ` (within ${n(f.high52_pct, 0)}%)` : ""}`
  );
  check("use_or_volume", f.use_or_volume, "Opening 15 min: volume check");
  check("use_or_vwap", f.use_or_vwap, "Opening 15 min: VWAP check");
  check("use_or_spread", f.use_or_spread, "Opening 15 min: SMA spread check");
  check("use_or_break", f.use_or_break, "Opening 15 min: high / low break check");

  // 3. When to exit
  const exitCandle = Number(f.exit_candle_minutes ?? 0);
  if (!pattern && crossExitOn(f) && exitCandle > 0 && exitCandle !== Number(f.candle_minutes ?? 1))
    add({ group: "exit", key: "cross_exit", text: `Exits on the SMA cross of ${exitCandle}-min candles (entries on ${f.candle_minutes ?? 1}-min); no reverse`, off: { exit_candle_minutes: "0" } });
  else if (!pattern && crossExitOn(f)) add({ group: "exit", key: "cross_exit", text: "Opposite SMA cross closes the trade and reverses it" });
  if (!pattern && !crossExitOn(f)) add({ group: "exit", key: "cross_exit", text: "SMA cross exit is OFF: trades are held through opposite crosses", warn: true, off: undefined });
  if (f.bb_exit && f.bb_exit !== "OFF")
    add({ group: "exit", key: "bb_exit", text: `Bollinger exit: ${f.bb_exit === "BAND" ? "book profit at the far band" : f.bb_exit === "MIDDLE" ? "close back across the middle band" : "far band or middle band"}`, off: { bb_exit: "OFF" } });
  if (f.slow_sma_exit) add({ group: "exit", key: "slow_sma_exit", text: `Close when a candle closes beyond SMA ${slow} against the trade`, off: { slow_sma_exit: false } });
  const scSizes = String(f.sc_minutes ?? "").split(",").map((s) => Number(s.trim())).filter((n) => n > 0 && n < Number(f.candle_minutes ?? 1));
  if (scSizes.length && f.sc_entry)
    add({ group: "check", key: "sc_entry", text: `Before entering, the last ${n(f.sc_count, 2)} ${scSizes.join(" / ")}-min candles must be green to buy / red to sell (${f.sc_join === "ANY" ? "any one size" : f.sc_join === "AT_LEAST" ? `at least ${n(f.sc_need, 1)} sizes` : "every size"})`, off: { sc_entry: false } });
  if (scSizes.length && f.sc_confirm)
    add({ group: "exit", key: "sc_confirm", text: `A colour-turn exit waits until the last ${n(f.sc_count, 2)} ${scSizes.join(" / ")}-min candles agree`, off: { sc_confirm: false } });
  const smallSizes = String(f.small_exit_minutes ?? "").split(",").map((s) => Number(s.trim())).filter((n) => n > 0 && n < Number(f.candle_minutes ?? 1));
  if (smallSizes.length)
    add({ group: "exit", key: "small_exit_minutes", text: `Watches ${smallSizes.join(" / ")}-min candles while in a trade: exits on their SMA cross / SMA 9 turn against it${Number(f.small_exit_loss_pct ?? 0) > 0 ? ` (only when ${f.small_exit_loss_pct}% under water)` : ""}`, off: { small_exit_minutes: "" } });
  if (n(f.counter_exit_count, 0) > 0) add({ group: "exit", key: "counter_exit_count", text: `Close after ${n(f.counter_exit_count, 0)} candles in a row against the trade`, off: { counter_exit_count: "0" } });
  if (n(f.counter_exit_count, 0) > 0 && f.counter_reenter) add({ group: "enter", key: "counter_reenter", text: `Re-enter after a counter-candle exit when a candle breaks the exit candle (up to ${n(f.counter_reenter_max, 3)} per SMA run)`, off: { counter_reenter: false } });
  if (f.ema_exit) add({ group: "exit", key: "ema_exit", text: `Close when EMA ${n(f.ema_fast, 9)} / ${n(f.ema_slow, 200)} cross against the trade`, off: { ema_exit: false } });
  if (f.macd_exit) add({ group: "exit", key: "macd_exit", text: `Close when MACD ${n(f.macd_fast, 12)}/${n(f.macd_slow, 26)}/${n(f.macd_signal, 9)} crosses its signal line against the trade`, off: { macd_exit: false } });
  if (f.cvd_div_exit) add({ group: "exit", key: "cvd_div_exit", text: `Close when price makes a new high / low over ${n(f.cvd_div_lookback, 10)} candles without the cumulative delta`, off: { cvd_div_exit: false } });
  if (f.reverse_on_exit)
    add({
      group: "exit",
      key: "reverse_on_exit",
      text: `Reverse instead of only closing after the ${String(f.reverse_on_exit)
        .split(",")
        .map((k) => REVERSE_KEYS.find((r) => r.key === k)?.label ?? k)
        .join(" / ")} exit`,
      off: { reverse_on_exit: "" },
    });
  const joinText = exitJoinText(f);
  if (joinText) add({ group: "exit", key: "exit_join", text: joinText, off: { exit_join: "OR" }, warn: true });
  if (f.reverse_on_exit && reverseGroupsOn(f.reverse_settings).length)
    add({
      group: "exit",
      key: "reverse_settings",
      text: `Reversed trades use their own ${reverseGroupsOn(f.reverse_settings)
        .map((id) => REVERSE_GROUPS.find((g) => g.id === id)?.title.toLowerCase() ?? id)
        .join(", ")}`,
      off: { reverse_settings: "" },
    });
  if (f.reverse_on_exit && f.reverse_colour_exit) add({ group: "exit", key: "reverse_colour_exit", text: "A reversed trade closes on the first candle of the other colour", off: { reverse_colour_exit: false } });
  if (f.ha_colours) add({ group: "enter", key: "ha_colours", text: "Candle colour rules read Heikin Ashi candles (prices stay real)", off: { ha_colours: false } });
  if (f.st_exit) add({ group: "exit", key: "st_exit", text: "Close when the Supertrend flips against the trade", off: { st_exit: false } });
  if (f.slope_exit) add({ group: "exit", key: "slope_exit", text: `Close when the SMA ${fast} line flattens or turns`, off: { slope_exit: false } });
  if (f.vturn_exit) add({ group: "exit", key: "vturn_exit", text: `Close on a sharp V-turn in the SMA ${fast} line`, off: { vturn_exit: false } });
  if (f.decel_exit) add({ group: "exit", key: "decel_exit", text: `Close when the SMA ${fast} line decelerates (${f.decel_drop_pct ?? 50}% off its peak slope${f.decel_confirm === "OFF" ? "" : ", close through the line"}${f.decel_peak_from === "RUN" ? ", peak from the run's start" : ""})`, off: { decel_exit: false } });
  {
    const slopeCandle = Number(f.slope_minutes ?? 0);
    if ((f.slope_exit || f.vturn_exit) && slopeCandle > 0 && slopeCandle !== Number(f.candle_minutes ?? 1))
      add({ group: "exit", key: "slope_minutes", text: `SMA ${fast} slope read on ${slopeCandle}-min candles`, off: { slope_minutes: "0" } });
  }
  if (f.stop_handoff && f.stop_handoff !== "OFF")
    add({
      group: "advanced",
      key: "stop_handoff",
      text: `After a stop-loss exit${f.stop_handoff_loss !== false ? " at a loss" : ""}, the stock moves ${
        f.stop_handoff === "BACK" ? "back to the bot it came from" : f.stop_handoff === "BOT2" ? "to Bot 2" : "to Bot 3"
      }`,
      off: { stop_handoff: "OFF" },
    });
  if (f.vturn_exit && f.vturn_handoff) add({ group: "advanced", key: "vturn_handoff", text: "After a losing V-turn exit, the stock moves to Bot 2", off: { vturn_handoff: false } });

  // 4. Gap mode
  if (f.use_gap_mode && !pattern) {
    add({ group: "gap", key: "use_gap_mode", text: `Enters only once the SMA gap reaches +${n(f.gap_entry_long, 0.05)}% / ${n(f.gap_entry_short, -0.05)}%, closes when it fades back`, off: { use_gap_mode: false } });
    if (f.gap_fade_intrabar) add({ group: "gap", key: "gap_fade_intrabar", text: "Fade checked every second (can exit early)", off: { gap_fade_intrabar: false } });
    if (f.gap_fade_minute) add({ group: "gap", key: "gap_fade_minute", text: "Fade checked every 1-minute candle", off: { gap_fade_minute: false } });
    if (f.gap_fade_confirm_sma) add({ group: "gap", key: "gap_fade_confirm_sma", text: `Rides out pullbacks: fade needs a close past SMA ${slow}`, off: { gap_fade_confirm_sma: false } });
  }

  // 5. Stop-loss and target
  if (f.use_stop === false) {
    add({ group: "stop", key: "use_stop", text: "NO stop-loss on new trades", warn: true });
  } else if (f.stop_type === "TSL") {
    const pct = f.tsl_mode === "PERCENT";
    const u = (v: unknown, fb: number) => (pct ? `${n(v, fb)}%` : `₹${n(v, fb)}`);
    const target = n(pct ? f.tsl_target_pct : f.tsl_target_points, 0);
    add({
      group: "stop",
      key: "use_stop",
      text: `Trailing stop ${u(pct ? f.tsl_sl_pct : f.tsl_sl_points, pct ? 1 : 20)}, moves every ${u(pct ? f.tsl_trail_pct : f.tsl_trail_points, pct ? 0.5 : 10)}${target > 0 ? `, target ${pct ? `${target}%` : `₹${target}`}` : ", no target"}`,
    });
  } else if (f.stop_type === "SMA_GAP") {
    add({ group: "stop", key: "use_stop", text: `Moving stop and target from the SMA gap (PAPER only; LIVE uses ${f.atr_multiplier}× ATR)` });
  } else if (f.stop_type === "SUPERTREND") {
    add({ group: "stop", key: "use_stop", text: `Stop on the Supertrend line (${n(f.st_period, 10)}, ×${n(f.st_mult, 3)}), moved each candle, tighter only` });
  } else if (f.stop_type === "VWAP") {
    add({ group: "stop", key: "use_stop", text: `Stop at VWAP ∓ ${n(f.vwap_stop_atr, 0.5)}× ATR, moved each candle, tighter only` });
  } else {
    add({ group: "stop", key: "use_stop", text: `Stop at ${f.atr_multiplier}× ATR from the entry` });
  }
  if (f.use_stop !== false && Number(f.atr_target_mult) > 0 && f.stop_type !== "TSL" && f.stop_type !== "SMA_GAP")
    add({ group: "stop", key: "atr_target_mult", text: `Target at ${f.atr_target_mult}× ATR from the entry`, off: { atr_target_mult: "0" } });
  if (f.stop_plan && (f.stop_type ?? "ATR") === "ATR")
    add({ group: "stop", key: "stop_plan", text: `Stop plan: breakeven at +${f.plan_be_r ?? 1.5}R, Supertrend trail from +${f.plan_trail_r ?? 2}R`, off: { stop_plan: false } });
  if (f.pivot_target === "R1" || f.pivot_target === "R2")
    add({ group: "stop", key: "pivot_target", text: f.pivot_target === "R1" ? "Target at the R1 / S1 pivot (else R2 / S2)" : "Target at the R2 / S2 pivot", off: { pivot_target: "OFF" } });

  // 7. Advanced
  if (f.recovery_hold) add({ group: "advanced", key: "recovery_hold", text: "Recovery hold: a losing trade is held past the opposite cross", off: { recovery_hold: false }, warn: true });
  if (f.recovery_hold && f.recovery_handoff !== false) add({ group: "advanced", key: "recovery_handoff", text: "After a recovery, the stock moves to Bot 2", off: { recovery_handoff: false } });
  if (f.handoff_now && ((f.recovery_hold && f.recovery_handoff !== false) || (f.vturn_exit && f.vturn_handoff))) add({ group: "advanced", key: "handoff_now", text: "Bot 2 orders at once on a handed-over stock (opposite of this bot's trade, no filters)", off: { handoff_now: false }, warn: true });
  if (f.breakout_handoff)
    add({
      group: "advanced",
      key: "breakout_handoff",
      text: `On ${String(f.breakout_rule ?? "CANDLE").toUpperCase() === "STRONG" ? `a strong trend (${Number(f.breakout_trend_need ?? 6) || 6} checks)` : "a breakout candle"}, the stock moves back where it came from (else to Bot 3); ${String(f.breakout_when ?? "ANY").toUpperCase() === "IN_PROFIT" ? `only while this bot's trade rides it in profit${Number(f.breakout_min_profit_pct ?? 0) > 0 ? ` by at least ${Number(f.breakout_min_profit_pct)}%` : ""}${Number(f.breakout_min_profit_rs ?? 0) > 0 ? `${Number(f.breakout_min_profit_pct ?? 0) > 0 ? " and" : " by at least"} ₹${Number(f.breakout_min_profit_rs)} a share` : ""} (the trade is kept)` : f.breakout_cut ? "a trade against it is cut at once" : "an open trade runs to its own exit first"}`,
      off: { breakout_handoff: false },
    });
  if (Number(f.chop_losses ?? 0) > 0)
    add({
      group: "advanced",
      key: "chop_losses",
      text: `After ${Number(f.chop_losses)} losing trades on a stock today, it moves to Bot 2`,
      off: { chop_losses: "0" },
    });
  if (f.review_on)
    add({
      group: "advanced",
      key: "review_on",
      text:
        f.review_default_answer === "EXIT"
          ? "1-minute review closes the trade by itself when the SMAs get close"
          : f.review_default_answer === "REVERSE"
            ? "1-minute review closes the trade and opens the other side by itself when the SMAs get uncertain"
          : f.review_default_answer === "CONTINUE"
            ? "1-minute review: noted only, no question asked"
            : "1-minute review: asks me EXIT or WAIT when the SMAs get close",
      off: { review_on: false },
      warn: f.review_default_answer === "EXIT" || f.review_default_answer === "REVERSE",
    });
  return out;
}

/** "What's switched on": every active rule in plain words, grouped, with a Turn off button (press Save to keep). */
function ActiveRules({
  form,
  scope,
  onOff,
  notes,
}: {
  form: SmaConfig;
  scope: string;
  onOff: (values: Partial<Record<keyof SmaConfig, string | boolean>>) => void;
  /** Overlapping or lopsided settings (strategyNotes), each with an optional one-click fix. */
  notes: StrategyNote[];
}) {
  const rules = activeRules(form);
  const extraExits = rules.filter((r) => r.group === "exit" && r.key !== "cross_exit").length + (form.use_gap_mode ? 1 : 0);
  const checks = rules.filter((r) => r.group === "check").length;
  const extraEntries = rules.filter((r) => r.group === "enter" && r.off).length;
  return (
    <div className="mt-4 rounded-lg border border-sky-400/25 bg-sky-400/[0.04] p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold text-slate-100">
        What&apos;s switched on{scope ? ` for ${scope}` : ""}
        <span className="text-xs font-normal text-slate-400">
          {rules.length} rule{rules.length === 1 ? "" : "s"} · Turn off changes the form; press Save to keep it
        </span>
      </div>
      {extraExits >= 3 ? (
        <p className="mt-2 rounded-md border border-amber-400/40 bg-amber-400/[0.08] p-2 text-[12px] leading-snug text-amber-100">
          {extraExits} extra exits are on. Whichever fires first closes the trade, so trades are cut earlier and smaller. Keep one or
          two you have checked on Replay.
        </p>
      ) : null}
      {checks >= 4 ? (
        <p className="mt-2 rounded-md border border-amber-400/40 bg-amber-400/[0.08] p-2 text-[12px] leading-snug text-amber-100">
          {checks} entry checks are on. A trade needs every one of them to agree, so many good crosses are skipped.
        </p>
      ) : null}
      {notes.length ? (
        <div className="mt-2 rounded-md border border-amber-400/40 bg-amber-400/[0.06] p-2">
          <div className="text-[12px] font-semibold text-amber-200">
            {notes.length === 1 ? "1 setting overlaps or looks off" : `${notes.length} settings overlap or look off`}
          </div>
          <ul className="mt-1 space-y-1.5">
            {notes.map((note) => (
              <li key={note.id} className="text-[12px] leading-snug text-amber-100">
                {note.text}
                {note.fix ? (
                  <button
                    type="button"
                    onClick={() =>
                      onOff(
                        Object.fromEntries(
                          Object.entries(note.fix!.values).map(([k, v]) => [k, typeof v === "boolean" ? v : String(v)])
                        ) as Partial<Record<keyof SmaConfig, string | boolean>>
                      )
                    }
                    className="ml-2 rounded px-2 py-0.5 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-400/15"
                  >
                    {note.fix.label}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {extraEntries + checks + extraExits === 0 ? (
        <p className="mt-2 text-xs text-slate-400">Plain SMA cross with the stop below: no extra entries, checks or exits are on.</p>
      ) : null}
      <div className="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {RULE_GROUPS.map((g) => {
          const list = rules.filter((r) => r.group === g.id);
          if (!list.length) return null;
          return (
            <div key={g.id} className="min-w-0">
              <a href={`#sma-${g.id}`} className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200">
                {g.title}
              </a>
              <ul className="mt-1 space-y-1">
                {list.map((r) => (
                  <li key={r.key} className="flex items-start gap-2 text-[13px] leading-snug">
                    <span aria-hidden className={clsx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", r.warn ? "bg-amber-400" : "bg-emerald-400")} />
                    <span className={clsx("min-w-0 flex-1", r.warn ? "text-amber-200" : "text-slate-200")}>{r.text}</span>
                    {r.off ? (
                      <button
                        type="button"
                        onClick={() => onOff(r.off!)}
                        className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                      >
                        Turn off
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      <ExitOrder form={form} />
    </div>
  );
}

/** The exits switched on, in the order the bot checks them; the first that fires closes the trade. */
function ExitOrder({ form }: { form: SmaConfig }) {
  const steps = exitOrder(form);
  return (
    <details className="mt-3 rounded-md border border-white/10 bg-black/20 p-2">
      <summary className="cursor-pointer text-[12px] font-semibold text-slate-200">
        Exit order: {steps.length} exit{steps.length === 1 ? "" : "s"} checked in this order
      </summary>
      <p className="mt-1 text-[11px] leading-snug text-slate-400">
        About once a second the bot runs down this list for each open trade. The first one that fires closes the trade; the ones after
        it see no trade. Price exits act on the live price, candle exits wait for the candle to close, so a stop can beat them inside a
        candle. Square-off, the daily loss limit and Panic always win. In LIVE the Groww stop is cancelled before any other exit.
      </p>
      {exitJoinText(form) ? (
        <p className="mt-1 text-[11px] leading-snug text-amber-300">
          {exitJoinText(form)}: those exits only vote when they fire, and the trade closes once enough of them agree. The other exits
          in the list still close on their own.
        </p>
      ) : null}
      <ol className="mt-2 space-y-1">
        {steps.map((s, i) => (
          <li key={s.code + i} className="flex items-baseline gap-2 text-[12px]">
            <span className="w-5 shrink-0 text-right font-mono text-slate-500">{i + 1}.</span>
            <span className="min-w-0 flex-1 text-slate-200">{s.name}</span>
            <span className="shrink-0 rounded bg-white/5 px-1.5 text-[10px] text-slate-400">{s.when}</span>
            <span className="hidden shrink-0 font-mono text-[10px] text-slate-500 sm:inline">{s.code}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

/** A collapsible group of settings, with how many of its rules are on. */
function FieldGroup({
  id,
  title,
  hint,
  wide,
  on,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  hint: string;
  wide: boolean;
  on?: number;
  /** One group is open at a time (the group chips); a search opens every group with a match. */
  open: boolean;
  onToggle: (id: string, open: boolean) => void;
  children: ReactNode;
}) {
  const meta = GROUP_INTRO[id];
  return (
    <details
      id={`sma-${id}`}
      open={open}
      onToggle={(e) => onToggle(id, e.currentTarget.open)}
      className="group/fs mt-4 scroll-mt-24 rounded-lg border border-white/10 bg-white/[0.02]"
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-3 text-sm font-semibold text-slate-100 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 [&::-webkit-details-marker]:hidden">
        <ChevronDown size={16} aria-hidden className="shrink-0 text-slate-400 transition-transform group-[:not([open])]/fs:-rotate-90" />
        {title}
        {on ? (
          <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">{on} on</span>
        ) : null}
        <TierTag tier={meta?.tier} />
        <span className="hidden truncate text-xs font-normal text-slate-400 sm:inline">— {hint}</span>
      </summary>
      {meta?.intro ? (
        <p className="mx-3 mb-1 rounded-md border border-sky-400/20 bg-sky-400/[0.05] px-3 py-2 text-xs leading-relaxed text-slate-200">
          {meta.intro}
        </p>
      ) : null}
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
    <div data-setting-title="" className="mt-5 flex items-center gap-1 border-t border-white/10 pt-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-300 first:mt-3">
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


const SMALL_SIZES = [1, 2, 3, 5, 10] as const;

/** Watch smaller candles while in a trade (small_candles.py): exit on a 1 / 3 / 5-minute turn before the bot's own candle can. */
function SmallCandlesCard({
  form,
  set,
  isOwn,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const own = Number(form.candle_minutes ?? 1) || 1;
  const picked = String(form.small_exit_minutes ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => (SMALL_SIZES as readonly number[]).includes(n));
  const watched = picked.filter((n) => n < own);
  const on = watched.length > 0;
  const toggle = (m: number, next: boolean) => {
    const list = next ? Array.from(new Set([...picked, m])) : picked.filter((n) => n !== m);
    set("small_exit_minutes", list.sort((a, b) => a - b).join(","));
  };
  const counter = Number(form.small_exit_counter ?? 0) || 0;
  return (
    <div className={clsx("mt-3 rounded-md p-2 ring-1 ring-inset", on ? "ring-emerald-400/40 bg-emerald-500/[0.05]" : "ring-white/10")}>
      <div className="flex flex-wrap items-center gap-1 text-sm text-slate-300">
        {isOwn("small_exit_minutes") && <OwnTag />}
        <b className="text-slate-100">Watch smaller candles while in a trade</b>
        <span className={clsx("ml-auto font-mono text-xs", on ? "text-emerald-300" : "text-slate-500")}>{on ? `ON · ${watched.join(" / ")} min` : "off"}</span>
        <InfoTip label="About watching smaller candles">
          A bot on {own}-minute candles only learns a trade has turned when a {own}-minute candle closes. Tick smaller sizes
          here and the bot also reads them, built from the stock&apos;s 1-minute candles, while it holds a trade: the SMA 9/21
          cross against the trade, the SMA 9 slope turning against it (the turn level from the slope exit), or N candles in a
          row against it. One size saying so closes the trade (SMALL_CANDLE_EXIT), or set how many sizes must agree. &quot;Only
          when under water&quot; keeps a winning trade with the bot&apos;s own exits and lets the small candles cut only a losing
          one. No reverse: the next entry waits for the bot&apos;s own signal. Entries are unchanged. Sizes at or above the
          bot&apos;s candle are ignored.
        </InfoTip>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300" role="group" aria-label="Smaller candle sizes to watch">
        <span className="text-slate-400">Read the trade on:</span>
        {SMALL_SIZES.map((m) => (
          <label
            key={m}
            className={clsx(
              "flex items-center gap-1 rounded-full px-2.5 py-1 ring-1 ring-inset",
              m >= own ? "opacity-40" : picked.includes(m) ? "bg-emerald-500/15 text-slate-100 ring-emerald-400/50" : "ring-white/15 hover:bg-white/5"
            )}
            title={m >= own ? `Not smaller than the bot's ${own}-minute candle` : undefined}
          >
            <input type="checkbox" checked={picked.includes(m)} disabled={m >= own} onChange={(e) => toggle(m, e.target.checked)} className="accent-emerald-400" aria-label={`Watch ${m}-minute candles`} />
            {m} min
          </label>
        ))}
      </div>
      <div className={clsx("mt-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-3", !on && "opacity-50")}>
        <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.small_exit_cross !== false} onChange={(e) => set("small_exit_cross", e.target.checked)} className="accent-[#10B981]" />
          {isOwn("small_exit_cross") && <OwnTag />}
          SMA cross against the trade
        </label>
        <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.small_exit_slope !== false} onChange={(e) => set("small_exit_slope", e.target.checked)} className="accent-[#10B981]" />
          {isOwn("small_exit_slope") && <OwnTag />}
          SMA 9 slope turns against it
        </label>
        <Field label="Candles in a row against (0 = off)" value={String(counter)} onChange={(v) => set("small_exit_counter", v)} own={isOwn("small_exit_counter")} />
        <Field label="Only when under water by (%, 0 = always)" value={String(form.small_exit_loss_pct ?? 0)} onChange={(v) => set("small_exit_loss_pct", v)} own={isOwn("small_exit_loss_pct")} />
        <Field label="Sizes that must agree (1 = any)" value={String(form.small_exit_agree ?? 1)} onChange={(v) => set("small_exit_agree", v)} own={isOwn("small_exit_agree")} />
      </div>
    </div>
  );
}


/**
 * Colours on smaller candles (backend small_colour.py): sizes, N candles in a row, how the sizes combine, and two uses:
 * an entry check (green to buy, red to sell) and a confirm for the colour-turn exit. Saves through the normal Save.
 */
function SmallColourCard({
  form,
  set,
  isOwn,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const own = Number(form.candle_minutes ?? 1) || 1;
  const picked = String(form.sc_minutes ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => (SMALL_SIZES as readonly number[]).includes(n));
  const watched = picked.filter((n) => n < own);
  const used = Boolean(form.sc_entry) || Boolean(form.sc_confirm);
  const on = watched.length > 0 && used;
  const toggle = (m: number, next: boolean) => {
    const list = next ? Array.from(new Set([...picked, m])) : picked.filter((n) => n !== m);
    set("sc_minutes", list.sort((a, b) => a - b).join(","));
  };
  const join = String(form.sc_join ?? "ALL");
  return (
    <div id="sma-small-colour" className={clsx("mt-3 rounded-md p-2 ring-1 ring-inset", on ? "ring-emerald-400/40 bg-emerald-500/[0.05]" : "ring-white/10")}>
      <div className="flex flex-wrap items-center gap-1 text-sm text-slate-300">
        {isOwn("sc_minutes") && <OwnTag />}
        <b className="text-slate-100">Colours on smaller candles</b>
        <span className={clsx("ml-auto font-mono text-xs", on ? "text-emerald-300" : "text-slate-500")}>
          {on ? `ON · ${watched.join(" / ")} min` : "off"}
        </span>
        <InfoTip label="About colours on smaller candles">
          The bot reads a candle&apos;s colour once every {own} minutes. Tick smaller sizes and it also reads the last N candles
          of each size, built from the stock&apos;s 1-minute candles (Heikin Ashi colours when that switch is on). Entry check: a
          buy needs them green, a sell red, else the entry is refused like any other check (late entry can wait it out). Confirm
          a colour turn: when a turn trade or a reversed trade would close on a candle of the other colour, it first waits until
          the smaller candles agree; if they turn back to the trade&apos;s colour the trade stays. Sizes at or above the bot&apos;s
          candle are ignored.
        </InfoTip>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300" role="group" aria-label="Smaller candle sizes for colours">
        <span className="text-slate-400">Read colours on:</span>
        {SMALL_SIZES.map((m) => (
          <label
            key={m}
            className={clsx(
              "flex items-center gap-1 rounded-full px-2.5 py-1 ring-1 ring-inset",
              m >= own ? "opacity-40" : picked.includes(m) ? "bg-emerald-500/15 text-slate-100 ring-emerald-400/50" : "ring-white/15 hover:bg-white/5"
            )}
            title={m >= own ? `Not smaller than the bot's ${own}-minute candle` : undefined}
          >
            <input type="checkbox" checked={picked.includes(m)} disabled={m >= own} onChange={(e) => toggle(m, e.target.checked)} className="accent-emerald-400" aria-label={`Read ${m}-minute candle colours`} />
            {m} min
          </label>
        ))}
      </div>
      <div className="mt-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-4">
        <label className="block text-sm text-slate-300">
          <span className="text-xs uppercase tracking-wider text-slate-400">Candles in a row{isOwn("sc_count") ? " (own)" : ""}</span>
          <select
            value={String(form.sc_count ?? 2)}
            onChange={(e) => set("sc_count", e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-slate-300">
          <span className="text-xs uppercase tracking-wider text-slate-400">Sizes{isOwn("sc_join") ? " (own)" : ""}</span>
          <select
            value={join}
            onChange={(e) => set("sc_join", e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            <option value="ALL">All ticked sizes agree</option>
            <option value="ANY">Any one size</option>
            <option value="AT_LEAST">At least N sizes</option>
          </select>
        </label>
        {join === "AT_LEAST" ? (
          <Field label="N sizes" value={String(form.sc_need ?? 1)} onChange={(v) => set("sc_need", v)} own={isOwn("sc_need")} />
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-sm text-slate-300">
        <label className="flex min-h-9 items-center gap-2">
          <input type="checkbox" checked={Boolean(form.sc_entry)} onChange={(e) => set("sc_entry", e.target.checked)} className="accent-[#10B981]" />
          {isOwn("sc_entry") && <OwnTag />}
          Entry check: green to buy, red to sell
        </label>
        <label className="flex min-h-9 items-center gap-2">
          <input type="checkbox" checked={Boolean(form.sc_confirm)} onChange={(e) => set("sc_confirm", e.target.checked)} className="accent-[#10B981]" />
          {isOwn("sc_confirm") && <OwnTag />}
          Confirm a colour-turn exit on them first
        </label>
      </div>
    </div>
  );
}

/**
 * ATR-multiple trailing stop (tsl_mode ATR): sliders and boxes for the stop, trail step and target as multiples of the
 * stock's own ATR at entry. Made for Bot 5, which trades whatever fast mover it is handed: the numbers size themselves
 * to each stock. The calculator below shows what the multiples give in ₹ and % on a chosen stock per candle size.
 */
function TslAtrSliders({
  form,
  set,
  isOwn,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const rows: { key: keyof SmaConfig; label: string; min: number; max: number; step: number; fallback: number; note: string }[] = [
    { key: "tsl_sl_atr", label: "Stop", min: 0.25, max: 6, step: 0.05, fallback: 1.5, note: "under 1 × is hit by noise; 1.5 × leaves one wild candle" },
    { key: "tsl_trail_atr", label: "Trail every", min: 0.1, max: 4, step: 0.05, fallback: 0.5, note: "the stop follows in steps this big; ½ ATR = half a candle" },
    { key: "tsl_target_atr", label: "Target (0 = none)", min: 0, max: 12, step: 0.25, fallback: 3, note: "3 × is two-to-one on a 1.5 × stop; 0 lets the trail or the cross close it" },
  ];
  return (
    <div className="space-y-2">
      {rows.map((r) => {
        const value = Number(form[r.key] ?? r.fallback);
        return (
          <div key={r.key} className="grid grid-cols-[1fr_6rem] items-end gap-3">
            <label className="block text-xs text-slate-300">
              <span className="flex items-center justify-between gap-2">
                <span>
                  {r.label} <span className="text-slate-500">(× ATR)</span>
                  {isOwn(r.key) ? <OwnTag /> : null}
                </span>
                <span className="font-mono text-slate-100">{Number.isFinite(value) ? `${value} × ATR` : "—"}</span>
              </span>
              <input
                type="range"
                min={r.min}
                max={r.max}
                step={r.step}
                value={Math.min(r.max, Math.max(r.min, Number.isFinite(value) ? value : r.fallback))}
                onChange={(e) => set(r.key, String(Number(e.target.value)))}
                className="mt-1 w-full accent-sky-400"
                aria-label={`${r.label} in ATR multiples`}
              />
              <span className="block text-[11px] text-slate-500">{r.note}</span>
            </label>
            <Field label="" suffix="× ATR" value={String(form[r.key] ?? r.fallback)} onChange={(v) => set(r.key, v)} own={isOwn(r.key)} />
          </div>
        );
      })}
    </div>
  );
}

/** Reverse instead of only closing (backend reverse_exit.py): which exits open the other side at once. */
const REVERSE_KEYS = [
  { key: "VTURN", label: "SMA 9 V-turn", on: (f: SmaConfig) => Boolean(f.vturn_exit) },
  { key: "TURN", label: "SMA 9 turn", on: (f: SmaConfig) => Boolean(f.slope_exit) && f.slope_turn !== false },
  { key: "CANDLES", label: "counter-candle", on: (f: SmaConfig) => Number(f.counter_exit_count ?? 0) > 0 },
  {
    key: "SMALL",
    label: "smaller-candle",
    on: (f: SmaConfig) => {
      const own = Number(f.candle_minutes ?? 1) || 1;
      return String(f.small_exit_minutes ?? "")
        .split(",")
        .some((s) => Number(s.trim()) > 0 && Number(s.trim()) < own);
    },
  },
] as const;

function ReverseOnExit({
  form,
  set,
  own,
  ownColour,
}: {
  form: SmaConfig;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  own: boolean;
  ownColour: boolean;
}) {
  const picked = new Set(
    String(form.reverse_on_exit ?? "")
      .split(",")
      .map((k) => k.trim().toUpperCase())
      .filter(Boolean)
  );
  const toggle = (key: string, tick: boolean) => {
    const next = new Set(picked);
    if (tick) next.add(key);
    else next.delete(key);
    set("reverse_on_exit", REVERSE_KEYS.map((r) => r.key).filter((k) => next.has(k)).join(","));
  };
  return (
    <div className="mt-4 rounded-md border border-white/10 p-2">
      <div className="flex items-center gap-1 text-sm text-slate-300">
        {own && <OwnTag />}
        Reverse instead of only closing
        <InfoTip label="About reverse on exit">
          These exits only close the trade. Tick one and, right after it closes the trade, the bot opens the other side at
          once (a buy becomes a sell). Every entry check still applies (filters, the stop, flip, market hours, the cut-off,
          the trade caps, the loss limit, one LIVE bot per stock); if one says no, the stock just stays flat. The exit
          itself must be switched on for its tick to do anything. Entry reason EXIT_REVERSE. Try it on Replay first.
        </InfoTip>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
        {REVERSE_KEYS.map((r) => (
          <label key={r.key} className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={picked.has(r.key)}
              onChange={(e) => toggle(r.key, e.target.checked)}
              className="accent-[#10B981]"
            />
            After the {r.label} exit
            {picked.has(r.key) && !r.on(form) ? <span className="text-xs text-amber-300">(that exit is off)</span> : null}
          </label>
        ))}
      </div>
      <label className={clsx("mt-2 flex items-center gap-2 text-sm text-slate-300", picked.size === 0 && "opacity-50")}>
        <input
          type="checkbox"
          checked={Boolean(form.reverse_colour_exit)}
          onChange={(e) => set("reverse_colour_exit", e.target.checked)}
          className="accent-[#10B981]"
        />
        {ownColour && <OwnTag />}
        Close a reversed trade on the first candle of the other colour
        <InfoTip label="About closing a reversed trade on the candle colour">
          A trade opened by the reverse above (&quot;Reversed on exit&quot;) closes on the first candle that closes the other
          way after its entry: red for a long, green for a short (saved as &quot;Colour turned&quot;), like &quot;Close a turn
          trade on the first candle of the other colour&quot; does for SMA 9 turn trades. No reverse after it. A trade you
          reversed by hand with the Reverse button is not affected. The stop and the other exits still apply.
        </InfoTip>
      </label>
    </div>
  );
}

const REVIEW_CONDITIONS: { key: "GAP" | "SLOPE" | "STEEP"; label: string; hint: string }[] = [
  { key: "GAP", label: "Gap narrowing", hint: "The SMA gap is inside the ± band and getting smaller (heading for a cross)." },
  { key: "SLOPE", label: "Slope flattened", hint: "The fast SMA's slope with the trade has dropped to the flat level or below (0 or less = turned)." },
  { key: "STEEP", label: "Steep against", hint: "The fast SMA is running against the trade at least this fast." },
];

/** The ticked "uncertain" conditions (backend review.conditions): CSV of GAP / SLOPE / STEEP, GAP when none. */
function reviewConditions(raw: unknown): string[] {
  const parts = Array.isArray(raw) ? raw : String(raw ?? "").split(",");
  const out = parts.map((p) => String(p).trim().toUpperCase()).filter((p) => REVIEW_CONDITIONS.some((c) => c.key === p));
  return out.length ? Array.from(new Set(out)) : ["GAP"];
}

/** 1-minute review: which conditions make the SMAs "uncertain", how they combine, and the slope numbers. */
function ReviewConditions({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string | boolean | number | number[]) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const current = reviewConditions(form.review_conditions);
  const join = form.review_join === "AND" ? "AND" : "OR";
  const toggle = (key: string, on: boolean) => {
    const next = on ? [...current, key] : current.filter((c) => c !== key);
    set("review_conditions", (next.length ? next : ["GAP"]).join(","));
  };
  const slopeOn = current.includes("SLOPE") || current.includes("STEEP");
  const fast = String(form.sma_fast ?? 9);
  return (
    <div className="mt-2 rounded-md p-2 ring-1 ring-inset ring-white/10">
      <div className="text-xs text-slate-400">
        Uncertain when{(isOwn("review_conditions") || isOwn("review_join")) && <OwnTag />}
        <span className="ml-2 text-[11px] text-slate-500">Tick one or more, then pick how they combine.</span>
      </div>
      <div className="mt-1 space-y-1">
        {REVIEW_CONDITIONS.map((c) => (
          <label key={c.key} className="flex items-start gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={current.includes(c.key)} onChange={(e) => toggle(c.key, e.target.checked)} className="mt-1 accent-[#10B981]" />
            <span>
              <b className="text-slate-100">{c.label}</b> <span className="text-xs text-slate-400">— {c.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" role="radiogroup" aria-label="How the conditions combine">
        {(["OR", "AND"] as const).map((j) => (
          <button
            key={j}
            type="button"
            role="radio"
            aria-checked={join === j}
            disabled={current.length < 2}
            onClick={() => set("review_join", j)}
            className={clsx(
              "min-h-9 rounded-md px-3 font-semibold ring-1 ring-inset disabled:opacity-40",
              join === j ? "bg-sky-500/15 text-sky-200 ring-sky-400/50" : "bg-black/20 text-slate-300 ring-white/15 hover:bg-white/5"
            )}
          >
            {j === "OR" ? "OR — any one ticked" : "AND — all ticked together"}
          </button>
        ))}
      </div>
      {slopeOn ? (
        <div className="mt-2 grid grid-cols-1 items-end gap-2 sm:grid-cols-3">
          <Field
            label={`Candles to read SMA ${fast} slope over`}
            suffix="candles"
            own={isOwn("review_slope_candles")}
            value={String(form.review_slope_candles ?? 3)}
            onChange={(v) => set("review_slope_candles", v)}
          />
          {current.includes("SLOPE") ? (
            <Field
              label="Flat at or under (with the trade)"
              suffix="%/candle"
              own={isOwn("review_slope_flat")}
              value={String(form.review_slope_flat ?? 0.01)}
              onChange={(v) => set("review_slope_flat", v)}
            />
          ) : null}
          {current.includes("STEEP") ? (
            <Field
              label="Steep at (against the trade)"
              suffix="%/candle"
              own={isOwn("review_steep_pct")}
              value={String(form.review_steep_pct ?? 0.03)}
              onChange={(v) => set("review_steep_pct", v)}
            />
          ) : null}
        </div>
      ) : null}
      <p className="mt-1 text-[11px] text-slate-500">
        Slope = how much SMA {fast} moved per candle, in % of its price, over the candles above, on the bot&apos;s own candle.
        Example: 0.01 flat means it rose 0.01% a candle or less for a buy. The gap band below applies to Gap narrowing.
      </p>
    </div>
  );
}

/** The strong-trend rule's 7 checks (breakout.py / trend_strength.py): each must pass, counts or is off, and how many of
 * the counted ones must pass. Saved as "stack:must,vwap:use,…" plus a number. */
const TREND_CHECKS: { key: string; name: string; what: string }[] = [
  { key: "stack", name: "Lines in order", what: "Price beyond SMA 9, SMA 9 beyond SMA 21" },
  { key: "vwap", name: "Beyond VWAP", what: "Price and SMA 21 on the trend side of VWAP" },
  { key: "slope", name: "Both SMAs sloping", what: "SMA 9 and SMA 21 moved the trend's way over 3 candles" },
  { key: "fan", name: "Gap widening", what: "SMA gap at least 0.10 % and not shrinking by a third" },
  { key: "hold", name: "Holding above SMA 9", what: "At least 75 % of the last 12 closes beyond SMA 9" },
  { key: "structure", name: "Higher highs / lows", what: "Higher highs and lows (lower for a downtrend), last 12 candles" },
  { key: "run", name: "No cross lately", what: "No SMA 9 / 21 cross in the last 12 candles" },
];
const TREND_DEFAULT: Record<string, string> = { stack: "must", vwap: "must", slope: "must", fan: "use", hold: "use", structure: "use", run: "use" };
const TREND_MODES: { mode: string; label: string }[] = [
  { mode: "must", label: "Must pass" },
  { mode: "use", label: "Counts" },
  { mode: "off", label: "Off" },
];

function parseTrend(value: string): Record<string, string> {
  const out = { ...TREND_DEFAULT };
  for (const part of value.split(",")) {
    const [k, m] = part.split(":").map((x) => (x ?? "").trim().toLowerCase());
    if (k in out && ["must", "use", "off"].includes(m)) out[k] = m;
  }
  return out;
}

function TrendChecks({
  value,
  need,
  onChange,
  onNeed,
  own,
}: {
  value: string;
  need: number;
  onChange: (v: string) => void;
  onNeed: (n: number) => void;
  own?: boolean;
}) {
  const rules = parseTrend(value);
  const on = TREND_CHECKS.filter((c) => rules[c.key] !== "off").length;
  const must = TREND_CHECKS.filter((c) => rules[c.key] === "must").length;
  const shown = Math.max(1, Math.min(need, on || 1));
  const setMode = (key: string, mode: string) => {
    const next = { ...rules, [key]: mode };
    if (Object.values(next).every((m) => m === "off")) return; // at least one check stays on
    onChange(TREND_CHECKS.map((c) => `${c.key}:${next[c.key]}`).join(","));
  };
  return (
    <div className="mt-2 rounded-lg border border-white/10 p-2.5">
      <p className="flex items-center gap-1 text-xs font-semibold text-slate-200">
        Strong-trend checks {own && <OwnTag />}
      </p>
      <p className="text-[11px] text-slate-400">
        Read on this bot&apos;s own closed candles. &quot;Must pass&quot; ones always have to pass; &quot;Counts&quot; ones add to the total;
        &quot;Off&quot; ones are not read.
      </p>
      <div className="mt-2 space-y-1.5">
        {TREND_CHECKS.map((c) => (
          <div key={c.key} className="grid grid-cols-1 items-center gap-1 sm:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-slate-200">{c.name}</p>
              <p className="text-[11px] text-slate-500">{c.what}</p>
            </div>
            <div role="group" aria-label={c.name} className="inline-flex overflow-hidden rounded-md ring-1 ring-inset ring-white/15">
              {TREND_MODES.map((m) => (
                <button
                  key={m.mode}
                  type="button"
                  aria-pressed={rules[c.key] === m.mode}
                  onClick={() => setMode(c.key, m.mode)}
                  className={clsx(
                    "min-h-8 px-2.5 text-[11px] font-semibold",
                    rules[c.key] === m.mode
                      ? m.mode === "must"
                        ? "bg-emerald-500/20 text-emerald-200"
                        : m.mode === "use"
                          ? "bg-sky-500/20 text-sky-200"
                          : "bg-white/10 text-slate-300"
                      : "text-slate-400 hover:bg-white/5"
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
        Need at least
        <select
          value={shown}
          onChange={(e) => onNeed(Number(e.target.value))}
          className="min-h-8 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
        >
          {Array.from({ length: Math.max(on, 1) }, (_, i) => i + 1).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        of the {on} checks that are on{must ? ` (the ${must} must-pass ones included)` : ""}.
      </label>
      <p className="mt-1 text-[11px] text-slate-500">
        Fewer checks or a lower number = the stock moves to Bot 3 more often (and on weaker trends). Default: lines in order,
        beyond VWAP and both SMAs sloping must pass, 6 of 7.
      </p>
    </div>
  );
}



const BODY_SIZES = [1, 3, 5, 10, 15, 30, 60] as const;

function parseBodyByTf(text: string | undefined): Record<number, string> {
  const out: Record<number, string> = {};
  for (const part of String(text ?? "").split(",")) {
    const [size, value] = part.split(":");
    const tf = Number(size);
    if ((BODY_SIZES as readonly number[]).includes(tf) && value !== undefined && value.trim() !== "") out[tf] = value.trim();
  }
  return out;
}

/** Candle colour: the smallest body that counts as a colour, in % of the price per candle size, or in ATRs. */
function ColourBodySize({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const unit = form.colour_body_unit === "ATR" ? "ATR" : "PCT";
  const bySize = parseBodyByTf(form.colour_body_by_tf);
  const chosen = Number(form.pattern_tf ?? 1);
  const setSize = (tf: number, value: string) => {
    const next = { ...bySize, [tf]: value };
    if (value.trim() === "") delete next[tf];
    set(
      "colour_body_by_tf",
      BODY_SIZES.filter((s) => next[s] !== undefined)
        .map((s) => `${s}:${next[s]}`)
        .join(",")
    );
  };
  return (
    <div className="col-span-2 rounded-md border border-white/10 p-2">
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-300">
        <span className="text-xs uppercase tracking-wider text-slate-400">
          Smallest candle body{isOwn("colour_body_unit") ? " (own)" : ""}
        </span>
        {(["PCT", "ATR"] as const).map((u) => (
          <label key={u} className="flex min-h-9 items-center gap-1.5">
            <input
              type="radio"
              name="colour-body-unit"
              checked={unit === u}
              onChange={() => set("colour_body_unit", u)}
              className="accent-[#10B981]"
            />
            {u === "PCT" ? "% of price, per candle size" : "× ATR of the colour candles"}
          </label>
        ))}
      </div>
      {unit === "ATR" ? (
        <div className="mt-2 grid grid-cols-2 items-end gap-2">
          <Field
            label="Smallest body (× ATR, 0 = any)"
            value={String(form.colour_body_atr ?? 0)}
            onChange={(v) => set("colour_body_atr", v)}
            own={isOwn("colour_body_atr")}
          />
          <p className="text-xs text-slate-400">
            The ATR is worked out on the colour candles themselves, so one number fits every candle size and stock. 0.3 = a body
            under 30% of a usual candle counts as no colour.
          </p>
        </div>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-4 items-end gap-2 sm:grid-cols-8">
            <Field
              label="Any size (%)"
              value={String(form.colour_min_body ?? 0)}
              onChange={(v) => set("colour_min_body", v)}
              own={isOwn("colour_min_body")}
            />
            {BODY_SIZES.map((tf) => (
              <label key={tf} className="block text-sm text-slate-300">
                <span className={clsx("text-xs uppercase tracking-wider", tf === chosen ? "font-semibold text-emerald-300" : "text-slate-400")}>
                  {tf} min{tf === chosen ? " ✓" : ""}
                </span>
                <input
                  inputMode="decimal"
                  value={bySize[tf] ?? ""}
                  placeholder={String(form.colour_min_body ?? 0)}
                  onChange={(e) => setSize(tf, e.target.value)}
                  aria-label={`Smallest body for ${tf}-minute colour candles, % of price`}
                  className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
                />
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-400">
            The box for the colour candle size you picked (✓) is used; an empty box uses &quot;Any size&quot;. 0 = any body counts.
          </p>
        </>
      )}
      <ColourBodySeq form={form} set={set} isOwn={isOwn} unit={unit} />
      <ColourInside form={form} set={set} isOwn={isOwn} />
      <ColourExit form={form} set={set} isOwn={isOwn} />
      <ColourForce form={form} set={set} isOwn={isOwn} />
      <ColourPull form={form} set={set} isOwn={isOwn} />
    </div>
  );
}

/** Colour entries wait for a pullback (colour_pull / colour_pull_candles / colour_pull_market, backend colour_pull.py). */
function ColourPull({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const tf = Number(form.pattern_tf ?? 1) || 1;
  const rule = String(form.colour_pull ?? "OFF");
  const on = rule !== "OFF";
  const wait = Number(form.colour_pull_candles ?? 1) || 1;
  const own = isOwn("colour_pull") || isOwn("colour_pull_candles") || isOwn("colour_pull_market");
  const where: Record<string, string> = {
    HALF: "half-way back: the middle of that candle",
    LOW: "the candle's low (its high for a sell)",
    SMA9: "the fast SMA when it sits inside the candle on the pullback side, else the candle's low (high for a sell)",
  };
  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <div className="text-xs uppercase tracking-wider text-slate-400">
        Wait for a pullback before entering{own ? " (own)" : ""}{" "}
        <span className="normal-case tracking-normal text-slate-500">({on ? `on · ${rule === "HALF" ? "half-way" : rule === "LOW" ? "candle's low" : "SMA 9 / low"}` : "off"})</span>
      </div>
      <div className="mt-1 grid grid-cols-1 items-end gap-2 sm:grid-cols-3">
        <label className="block text-sm text-slate-300">
          <span className="text-xs text-slate-400">Buy at (sell: mirror)</span>
          <select
            value={rule}
            onChange={(e) => set("colour_pull", e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            <option value="OFF">Off: at the market, next candle</option>
            <option value="HALF">Half-way back</option>
            <option value="LOW">Candle&apos;s low</option>
            <option value="SMA9">SMA 9 or the low</option>
          </select>
        </label>
        <label className={clsx("block text-sm text-slate-300", !on && "opacity-50")}>
          <span className="text-xs text-slate-400">Wait for</span>
          <select
            value={String(wait)}
            onChange={(e) => set("colour_pull_candles", e.target.value)}
            disabled={!on}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            {[1, 2, 3].map((c) => (
              <option key={c} value={String(c)}>
                {c} colour candle{c > 1 ? "s" : ""} ({c * tf} min)
              </option>
            ))}
          </select>
        </label>
        <label className={clsx("flex min-h-11 items-center gap-2 text-sm text-slate-300 sm:min-h-9", !on && "opacity-50")}>
          <input
            type="checkbox"
            checked={Boolean(form.colour_pull_market)}
            onChange={(e) => set("colour_pull_market", e.target.checked)}
            disabled={!on}
            className="h-4 w-4"
          />
          No pullback: buy at the market after the wait
        </label>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Normally a colour entry buys at the market at the start of the next candle, which after a strong green candle is near its
        top. With this on, the order waits at {on ? where[rule] : "a level inside the candle that confirmed the colour"}, for up to{" "}
        {wait} {tf}-minute candle{wait > 1 ? "s" : ""}. If the price never comes back:{" "}
        {form.colour_pull_market ? "it buys at the market once the wait is over." : "no trade; the next colour signal decides again."} The
        same for a sell (mirror) and for the turn after a colour exit. Every entry check, the stop, flip, cut-off and caps still apply.
      </p>
    </div>
  );
}

/** Inside candles agree (colour_inside_minutes / colour_inside_body, candle_patterns.inside_colour). */
function ColourInside({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const tf = Number(form.pattern_tf ?? 1) || 1;
  const size = Number(form.colour_inside_minutes ?? 0) || 0;
  const fits = (m: number) => m < tf && tf % m === 0;
  const on = size > 0 && fits(size);
  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <div className="text-xs uppercase tracking-wider text-slate-400">
        When the smaller candles inside agree{isOwn("colour_inside_minutes") || isOwn("colour_inside_body") ? " (own)" : ""}{" "}
        <span className="normal-case tracking-normal text-slate-500">({on ? `on · ${size} min` : "off"})</span>
      </div>
      <div className="mt-1 grid grid-cols-2 items-end gap-2">
        <label className="block text-sm text-slate-300">
          <span className="text-xs text-slate-400">Inside candle size</span>
          <select
            value={String(size)}
            onChange={(e) => set("colour_inside_minutes", e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            <option value="0">Off</option>
            {[1, 2, 3, 5, 10].map((m) => (
              <option key={m} value={String(m)} disabled={!fits(m)}>
                {m} min{fits(m) ? ` (${tf / m} inside)` : " (does not fit)"}
              </option>
            ))}
          </select>
        </label>
        <label className={clsx("block text-sm text-slate-300", !on && "opacity-50")}>
          <span className="text-xs text-slate-400">Smallest body then (% of price, 0 = any)</span>
          <input
            inputMode="decimal"
            value={String(form.colour_inside_body ?? 0)}
            onChange={(e) => set("colour_inside_body", e.target.value)}
            disabled={!on}
            aria-label="Smallest body when every inside candle agrees, % of price"
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          />
        </label>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        When every {size > 0 ? `${size}-minute` : "smaller"} candle inside the newest {tf}-minute colour candle is that colour (all
        green or all red; Heikin Ashi colours when that switch is on, even while the {tf}-minute HA candle still reads the other
        colour) and the real {tf}-minute candle closed that way too, that one candle is enough: no run of &quot;Candles in a
        row&quot;, and this smallest body (on the real candle) instead of the sizes above. Mixed inside candles: the rules above decide as before.
        Read for turns and for entries while flat.
      </p>
    </div>
  );
}

/** A separate rule for closing a colour trade (colour_exit_on / _count / _body / _inside, backend colour_exit.py). */
function ColourExit({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const tf = Number(form.pattern_tf ?? 1) || 1;
  const on = Boolean(form.colour_exit_on);
  const n = Math.min(5, Math.max(1, Math.round(Number(form.colour_exit_count ?? 2)) || 2));
  const body = Number(form.colour_exit_body ?? 0.2) || 0;
  const own = isOwn("colour_exit_on") || isOwn("colour_exit_count") || isOwn("colour_exit_body") || isOwn("colour_exit_inside");
  const inside = Number(form.colour_inside_minutes ?? 0) > 0;
  return (
    <div id="sma-colour-exit" className="mt-3 border-t border-white/10 pt-2">
      <label className="flex items-center gap-2 text-sm text-slate-200">
        <input type="checkbox" checked={on} onChange={(e) => set("colour_exit_on", e.target.checked)} />
        <span>
          Use a separate rule to close a trade{own ? " (own)" : ""}{" "}
          <span className="text-xs text-slate-500">({on ? "on" : "off: the entry rule above closes trades too"})</span>
        </span>
      </label>
      <div className={clsx("mt-1 grid grid-cols-2 items-end gap-2", !on && "opacity-50")}>
        <label className="block text-sm text-slate-300">
          <span className="text-xs text-slate-400">Candles of the other colour in a row</span>
          <select
            value={String(n)}
            onChange={(e) => set("colour_exit_count", e.target.value)}
            disabled={!on}
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          >
            {[1, 2, 3, 4, 5].map((k) => (
              <option key={k} value={String(k)}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-slate-300">
          <span className="text-xs text-slate-400">Smallest body of each (% of price, 0 = any)</span>
          <input
            inputMode="decimal"
            value={String(form.colour_exit_body ?? 0.2)}
            onChange={(e) => set("colour_exit_body", e.target.value)}
            disabled={!on}
            aria-label="Smallest body of each exit candle, % of price"
            className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
          />
        </label>
      </div>
      <label className={clsx("mt-2 flex items-center gap-2 text-sm text-slate-300", (!on || !inside) && "opacity-50")}>
        <input
          type="checkbox"
          checked={Boolean(form.colour_exit_inside)}
          onChange={(e) => set("colour_exit_inside", e.target.checked)}
          disabled={!on}
        />
        Let &quot;When the smaller candles inside agree&quot; close a trade too
      </label>
      <p className="mt-1 text-xs text-slate-400">
        {on
          ? `An open trade closes only after ${n} ${tf}-minute candle${n > 1 ? "s" : ""} of the other colour in a row${body > 0 ? `, each with a body of at least ${body}%` : ""}${form.colour_exit_inside ? ", or one candle whose smaller candles all agree" : ""}. Entries keep the rules above. After the close, the other side opens only if the entry rule also asks for it on that candle; otherwise the stock waits flat.`
          : "Off: the colour rules above both open and close trades (a candle of the other colour turns the trade). Tick it to give closing its own, stricter rule while entries stay as they are."}
      </p>
    </div>
  );
}

/** Force order follows the last closed colour candle (colour_force, backend colour_force.py). */
function ColourForce({
  form,
  set,
  isOwn,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string | boolean) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
}) {
  const tf = Number(form.pattern_tf ?? 1) || 1;
  const on = Boolean(form.colour_force);
  return (
    <div id="sma-colour-force" className="mt-3 border-t border-white/10 pt-2">
      <label className="flex items-center gap-2 text-sm text-slate-200">
        <input type="checkbox" checked={on} onChange={(e) => set("colour_force", e.target.checked)} />
        <span>
          Force order follows the candle colour{isOwn("colour_force") ? " (own)" : ""}{" "}
          <span className="text-xs text-slate-500">({on ? "on" : "off: Force reads SMA 9 vs SMA 21"})</span>
        </span>
      </label>
      <p className="mt-1 text-xs text-slate-400">
        {on
          ? `Force reads the last closed ${tf}-minute colour candle with the colour rules above (candles in a row, smallest body, Heikin Ashi, smaller candles inside): green buys, red sells. No colour (a doji, a small body or a mixed run) = no order. The candle still forming is never read. Entry checks are skipped as for any Force order; market hours, cut-off, Trade list, caps and flip still apply.`
          : "Off: Force buys when SMA 9 is above SMA 21 on the 1-minute candle forming now (the bot's own candle in colour mode) and sells when below, whatever colour the candles are. Tick it to make Force follow the colour candles instead."}
      </p>
    </div>
  );
}

/** Each candle of the colour run's own smallest body (colour_body_seq, oldest first, in the unit picked above). */
function ColourBodySeq({
  form,
  set,
  isOwn,
  unit,
}: {
  form: Partial<SmaConfig>;
  set: (key: keyof SmaConfig, value: string) => void;
  isOwn: (key: keyof SmaConfig) => boolean;
  unit: "PCT" | "ATR";
}) {
  const count = Math.min(5, Math.max(1, Math.round(Number(form.colour_count ?? 1)) || 1));
  const seq = String(form.colour_body_seq ?? "").split(",");
  const usual = unit === "ATR" ? String(form.colour_body_atr ?? 0) : String(form.colour_min_body ?? 0);
  const setAt = (i: number, value: string) => {
    const next = Array.from({ length: Math.max(count, seq.length) }, (_, k) => (k === i ? value.trim() : (seq[k] ?? "").trim()));
    while (next.length && next[next.length - 1] === "") next.pop();
    set("colour_body_seq", next.join(","));
  };
  const on = seq.slice(0, count).some((v) => v.trim() !== "");
  const ord = (n: number) => (n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`);
  if (count < 2) {
    return (
      <p className="mt-2 text-xs text-slate-500">
        Set &quot;Candles in a row&quot; to 2 or more to give each candle of the run its own smallest size.
      </p>
    );
  }
  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <div className="text-xs uppercase tracking-wider text-slate-400">
        Each candle&apos;s own size{isOwn("colour_body_seq") ? " (own)" : ""}{" "}
        <span className="normal-case tracking-normal text-slate-500">
          ({unit === "ATR" ? "× ATR" : "% of price"}; {on ? "on" : "off, all empty"})
        </span>
      </div>
      <div className="mt-1 grid grid-cols-3 items-end gap-2 sm:grid-cols-5">
        {Array.from({ length: count }, (_, i) => (
          <label key={i} className="block text-sm text-slate-300">
            <span className="text-xs text-slate-400">
              {ord(i + 1)} candle{i === count - 1 ? " (newest)" : i === 0 ? " (oldest)" : ""}
            </span>
            <input
              inputMode="decimal"
              value={(seq[i] ?? "").trim()}
              placeholder={usual}
              onChange={(e) => setAt(i, e.target.value)}
              aria-label={`Smallest body of the ${ord(i + 1)} candle of the run, ${unit === "ATR" ? "times ATR" : "% of price"}`}
              className="mt-1 block min-h-11 w-full rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100 sm:min-h-9"
            />
          </label>
        ))}
      </div>
      <p className="mt-1 text-xs text-slate-400">
        The trade turns only when every candle of the run is the new colour <b>and</b> at least its own size. Example with 2 in a
        row and × ATR: 1st 0.1, 2nd 0.5 = a small then a big candle turns the trade; a big then a small one does not (the 2nd is
        under 0.5). An empty box uses the usual smallest body ({usual}). It is read for entries while flat too.
      </p>
    </div>
  );
}
