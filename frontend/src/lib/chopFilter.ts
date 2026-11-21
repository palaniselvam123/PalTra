/**
 * The Trend quality card's filters as plain functions, shared by the live card (ChopScan) and the History tab's
 * "Across days" view (ChopAcrossDays), so a stock passes the same way on both. Display only.
 */
import type { ChopCandles, ChopScanRow, ChopScore } from "@/lib/smaApi";
import { trendRead, type TrendDir, type TrendKind } from "@/lib/trendRank";

export const CANDLE_CHOICES = [1, 2, 3, 5, 10, 15] as const;

// ---- Candle modes (chop_scan.CANDLE_KINDS) ----------------------------------------------------------------------
// NORMAL reads the real candles by the SMA 9 / 21 cross; COLOUR reads the real candles by colour (runs and flips,
// like Heikin Ashi, ha_trend.py); HA reads Heikin Ashi candles by colour. The filters and columns name what they
// count in each mode, so "crosses" become "colour flips" and "SMA side" becomes the candles' colour.
export const CANDLE_KIND_OPTIONS: { value: ChopCandles; label: string }[] = [
  { value: "NORMAL", label: "Normal (SMA cross)" },
  { value: "COLOUR", label: "Normal (colours)" },
  { value: "HA", label: "Heikin Ashi" },
];
export const asCandleKind = (v: unknown): ChopCandles => (v === "COLOUR" || v === "HA" ? v : "NORMAL");
/** The trend is read from the candles' colours (no SMA cross). */
export const colourRead = (k: ChopCandles | undefined | null) => k === "COLOUR" || k === "HA";
/** Words for a candle mode in a sentence ("on the 5-minute Heikin Ashi candle"). */
export const candleWords = (k: ChopCandles | undefined | null) => (k === "HA" ? "Heikin Ashi " : k === "COLOUR" ? "colour-read " : "");
/** Short tag after a candle size ("5m HA"). */
export const candleTag = (k: ChopCandles | undefined | null) => (k === "HA" ? " HA" : k === "COLOUR" ? " colours" : "");
/** The candles' name in the colour read: "candle" or "Heikin Ashi candle". */
const colourCandle = (k: ChopCandles | undefined | null) => (k === "HA" ? "Heikin Ashi candle" : "candle");

/** What each mode counts and calls things: the labels the card's filters and columns use. */
export function modeWords(k: ChopCandles | undefined | null) {
  if (!colourRead(k)) {
    return {
      crosses: "Crosses today",
      crossesShort: "crosses",
      crossesHelp:
        "None: the SMAs have not crossed once today. Exactly 1: one turn since the morning, then a one-way run (↑ up / ↓ down: that cross's direction). At most / or more: a range. Every ticked candle size must match.",
      side: "SMA side (fast above / below slow)",
      sideHelp:
        "SMA side: the fast SMA is above the slow one (bullish) or below (bearish) on the last closed candle of that size. Strong trend: the card's strong-trend read (6 of 7 checks: stack, VWAP, slope, fan, hold, structure, run) on that size. Every ticked size must agree; a size with too few candles never matches.",
      gap: "SMA gap",
      gapHelp: "Hide a trend whose SMA gap on any ticked size has shrunk to under half its widest in this trend",
      score:
        "Score counts today's SMA 9 / 21 crosses on closed candles: Trending = 0-1 crosses with over an hour since the last, Choppy = 4+ crosses or crosses under 15 minutes apart on average, Mixed = the rest.",
      up: "crossed up",
      down: "crossed down",
    };
  }
  const c = colourCandle(k);
  return {
    crosses: "Colour flips today",
    crossesShort: "colour flips",
    crossesHelp: `None: no ${c} has changed colour today (dojis skipped). Exactly 1: one colour change since the morning, then one colour (↑ turned green / ↓ turned red). At most / or more: a range. Every ticked candle size must match.`,
    side: `Candle colour (the current ${c} run)`,
    sideHelp: `Candle colour: the colour of the current run of ${c}s on that size (green = bullish, red = bearish; dojis do not break a run). Strong trend: the card's colour read (6 of 7 checks: a streak of 3+, no wick against it, bodies not shrinking, higher lows / lower highs, price beyond VWAP, a move of 1 ATR, no doji lately) on that size. Every ticked size must agree; a size with too few candles never matches.`,
    gap: "Run move",
    gapHelp: "Hide a trend whose colour run on any ticked size has given back more than half of its widest move",
    score: `Score counts today's ${c} colour flips (dojis skipped): Trending = 0-2 flips with a long run since, Choppy = 5+ flips or flips under 3 candles apart on average, Mixed = the rest.`,
    up: "turned green",
    down: "turned red",
  };
}

/** The "Crosses today" choices worded for the candle mode (the values stay the same). */
export function crossRulesFor(k: ChopCandles | undefined | null): [CrossRule, string][] {
  const w = modeWords(k);
  return CROSS_RULES.map(([v, label]) => [v, label.replace("crossed up", w.up).replace("crossed down", w.down)] as [CrossRule, string]);
}

/** The "Trend on" kinds worded for the candle mode. */
export function trendKindsFor(k: ChopCandles | undefined | null): [TrendKind, string][] {
  return TREND_KINDS.map(([v, label]) => [v, v === "SIDE" ? modeWords(k).side : label] as [TrendKind, string]);
}

// "Crosses today" choices: today's closed-candle SMA crosses (or Heikin Ashi colour flips) on each ticked size.
export const CROSS_RULES = [
  ["0", "None (0)"],
  ["1", "Exactly 1"],
  ["1up", "Exactly 1 ↑ (crossed up)"],
  ["1down", "Exactly 1 ↓ (crossed down)"],
  ["2", "Exactly 2"],
  ["3", "Exactly 3"],
  ["le1", "At most 1"],
  ["le2", "At most 2"],
  ["le3", "At most 3"],
  ["ge2", "2 or more"],
  ["ge3", "3 or more"],
  ["ge5", "5 or more (choppy)"],
] as const;
export type CrossRule = (typeof CROSS_RULES)[number][0];

export function isCrossRule(value: string): value is CrossRule {
  return CROSS_RULES.some(([v]) => v === value);
}

// A size with too few candles has no count (null): it never matches.
export function crossRuleMatches(
  rule: CrossRule,
  count: number | null | undefined,
  lastSide: "BULLISH" | "BEARISH" | null | undefined
): boolean {
  if (count == null) return false;
  if (rule === "1up") return count === 1 && lastSide === "BULLISH";
  if (rule === "1down") return count === 1 && lastSide === "BEARISH";
  if (rule.startsWith("le")) return count <= Number(rule.slice(2));
  if (rule.startsWith("ge")) return count >= Number(rule.slice(2));
  return count === Number(rule);
}

// "Trend on" filter: which way every ticked candle size must point, and how strong.
export const TREND_DIRS = [
  ["UP", "Bullish"],
  ["DOWN", "Bearish"],
  ["SAME", "Same way (either)"],
] as const;
export const TREND_KINDS = [
  ["SIDE", "SMA side (fast above / below slow)"],
  ["STRONG", "Strong trend (6 of 7 checks)"],
] as const;

export type ScoreFilter =
  | "ALL"
  | ChopScore
  | "STRONG"
  | "STRONG_BULLISH"
  | "STRONG_BEARISH"
  | "FRESH"
  | "FRESH_BULLISH"
  | "FRESH_BEARISH"
  | "PHASE_STRONG"
  | "PHASE_BULL"
  | "PHASE_BEAR"
  | "PHASE_CHOP";

export const SCORE_OPTIONS: { value: ScoreFilter; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "STRONG", label: "Strong trend (both)" },
  { value: "STRONG_BEARISH", label: "Strong bearish ↓" },
  { value: "STRONG_BULLISH", label: "Strong bullish ↑" },
  { value: "FRESH", label: "Fresh trend (both)" },
  { value: "FRESH_BULLISH", label: "Fresh trend up ↑" },
  { value: "FRESH_BEARISH", label: "Fresh trend down ↓" },
  { value: "PHASE_STRONG", label: "Phase now: strong (both)" },
  { value: "PHASE_BULL", label: "Phase now: strong up ↑" },
  { value: "PHASE_BEAR", label: "Phase now: strong down ↓" },
  { value: "PHASE_CHOP", label: "Phase now: choppy" },
  { value: "TRENDING", label: "Trending only" },
  { value: "MIXED", label: "Mixed only" },
  { value: "CHOPPY", label: "Choppy only" },
];

export const isStrong = (r: Pick<ChopScanRow, "trend">) => r.trend === "STRONG_BULLISH" || r.trend === "STRONG_BEARISH";

// Inside [min, max] ₹; 0 = no bound. A stock with no price passes only when no bound is set (as chop_scan.in_price_range).
export const inPriceRange = (ltp: number | null | undefined, min: number, max: number) =>
  (!min && !max) || (ltp != null && (!min || ltp >= min) && (!max || ltp <= max));

export type ChopFilter = {
  score: ScoreFilter;
  minSpeed: number;
  priceMin: number;
  priceMax: number;
  crossCount: CrossRule;
  noCrossOn: readonly number[];
  trendOn: readonly number[];
  trendDir: TrendDir;
  trendKind: TrendKind;
  maxAge: number;
  minGap: number;
  hideFading: boolean;
  /** Trend score (0-100) at least; 0 = any. */
  minTrendScore?: number;
  /** Strong-trend checks passed at least (of 7); 0 = any. */
  minChecks?: number;
  /** Phase now: ANY, BULL, BEAR, STRONG (either), CHOP. */
  phase?: PhaseFilter;
};

export type PhaseFilter = "ANY" | "BULL" | "BEAR" | "STRONG" | "CHOP";
export const PHASE_OPTIONS: { value: PhaseFilter; label: string }[] = [
  { value: "ANY", label: "Any" },
  { value: "BULL", label: "Strong up ↑" },
  { value: "BEAR", label: "Strong down ↓" },
  { value: "STRONG", label: "Strong (either)" },
  { value: "CHOP", label: "Choppy" },
];

/** Strong-trend checks passed (true values of `trend_checks`), or null when the row has none. */
export function checksPassed(r: Pick<ChopScanRow, "trend_checks">): number | null {
  const c = r.trend_checks;
  if (!c) return null;
  return Object.values(c).filter((v) => v === true).length;
}

export const NO_FILTER: ChopFilter = {
  score: "ALL",
  minSpeed: 0,
  priceMin: 0,
  priceMax: 0,
  crossCount: "0",
  noCrossOn: [],
  trendOn: [],
  trendDir: "UP",
  trendKind: "SIDE",
  maxAge: 0,
  minGap: 0,
  hideFading: false,
  minTrendScore: 0,
  minChecks: 0,
  phase: "ANY",
};

/** Every filter of the card but the stock search: whether this row is "in the list". */
export function chopPasses(r: ChopScanRow, f: ChopFilter): boolean {
  const score = f.score;
  if (score === "STRONG" && !isStrong(r)) return false;
  if ((score === "STRONG_BULLISH" || score === "STRONG_BEARISH") && r.trend !== score) return false;
  if ((score === "TRENDING" || score === "MIXED" || score === "CHOPPY") && r.score !== score) return false;
  if (score === "FRESH" && !r.fresh) return false;
  if (score === "FRESH_BULLISH" && r.fresh !== "BULLISH") return false;
  if (score === "FRESH_BEARISH" && r.fresh !== "BEARISH") return false;
  if (score === "PHASE_STRONG" && r.phase !== "BULL" && r.phase !== "BEAR") return false;
  if (score === "PHASE_BULL" && r.phase !== "BULL") return false;
  if (score === "PHASE_BEAR" && r.phase !== "BEAR") return false;
  if (score === "PHASE_CHOP" && (r.phase !== "CHOP" || r.phase_early)) return false;
  if ((f.minTrendScore ?? 0) > 0 && (r.trend_score ?? -1) < (f.minTrendScore ?? 0)) return false;
  if ((f.minChecks ?? 0) > 0 && (checksPassed(r) ?? -1) < (f.minChecks ?? 0)) return false;
  const ph = f.phase ?? "ANY";
  if (ph === "BULL" && r.phase !== "BULL") return false;
  if (ph === "BEAR" && r.phase !== "BEAR") return false;
  if (ph === "STRONG" && r.phase !== "BULL" && r.phase !== "BEAR") return false;
  if (ph === "CHOP" && (r.phase !== "CHOP" || r.phase_early)) return false;
  if (f.minSpeed > 0 && Math.abs(r.speed_pct_per_min ?? 0) < f.minSpeed) return false;
  if (!inPriceRange(r.ltp, f.priceMin, f.priceMax)) return false;
  if (
    f.noCrossOn.some(
      (m) => !crossRuleMatches(f.crossCount, r.crosses_by_minutes?.[String(m)], r.last_cross_side_by_minutes?.[String(m)])
    )
  )
    return false;
  if (f.trendOn.length > 0) {
    const t = trendRead(r, [...f.trendOn], f.trendDir, f.trendKind);
    if (t == null) return false;
    if (f.maxAge > 0 && t.ageMin > f.maxAge) return false;
    if (t.gapPct < f.minGap) return false;
    if (f.hideFading && t.fading) return false;
  }
  return true;
}
