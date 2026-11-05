/** Choppy or trending when a trade opened (backend handoff_log.trend_label / trend_text; display only). */
export type TradeRegime = {
  phase: "BULL" | "BEAR" | "CHOP" | null;
  score: "TRENDING" | "MIXED" | "CHOPPY" | null;
  crosses: number | null;
  phase_since: string | null;
  phase_move_pct: number | null;
  minutes: number | null;
};

export type TrendGroup = "UP_WITH" | "DOWN_WITH" | "UP_AGAINST" | "DOWN_AGAINST" | "CHOPPY" | "UNKNOWN";

export const TREND_GROUP_WORDS: Record<TrendGroup, string> = {
  UP_WITH: "Up trend, traded with it",
  DOWN_WITH: "Down trend, traded with it",
  UP_AGAINST: "Up trend, traded against it",
  DOWN_AGAINST: "Down trend, traded against it",
  CHOPPY: "Choppy",
  UNKNOWN: "Not read",
};

/** The filter's choices: with / against a trend, choppy, not read. */
export type TrendFilter = "ALL" | "WITH" | "AGAINST" | "CHOPPY" | "UNKNOWN";

export function trendGroup(regime: TradeRegime | null | undefined, direction: string | null | undefined): TrendGroup {
  if (!regime || !regime.phase) return "UNKNOWN";
  if (regime.phase === "CHOP") return "CHOPPY";
  const long = (direction || "").toUpperCase() === "LONG" || (direction || "").toUpperCase() === "BUY";
  const up = regime.phase === "BULL";
  return `${up ? "UP" : "DOWN"}_${up === long ? "WITH" : "AGAINST"}` as TrendGroup;
}

export function matchesTrend(filter: TrendFilter, group: TrendGroup): boolean {
  if (filter === "ALL") return true;
  if (filter === "WITH") return group.endsWith("_WITH");
  if (filter === "AGAINST") return group.endsWith("_AGAINST");
  return group === filter;
}

/** A short chip label, e.g. "Up ↑ with", "Down ↓ against", "Choppy". */
export function trendShort(regime: TradeRegime | null | undefined, direction: string | null | undefined): string | null {
  const g = trendGroup(regime, direction);
  if (g === "UNKNOWN") return null;
  if (g === "CHOPPY") return "Choppy";
  return `${g.startsWith("UP") ? "Up ↑" : "Down ↓"} ${g.endsWith("WITH") ? "with" : "against"}`;
}

/** The full sentence for a tooltip or CSV / PDF cell. */
export function trendText(regime: TradeRegime | null | undefined, direction: string | null | undefined): string {
  if (!regime || !regime.phase) return "";
  const g = trendGroup(regime, direction);
  const phase = regime.phase === "BULL" ? "Up" : regime.phase === "BEAR" ? "Down" : "Choppy";
  const since = regime.phase_since ? ` since ${regime.phase_since}` : "";
  const move =
    regime.phase_move_pct != null && g !== "CHOPPY" ? ` (${regime.phase_move_pct > 0 ? "+" : ""}${regime.phase_move_pct.toFixed(2)}%)` : "";
  const side = g.endsWith("_WITH") ? ", with it" : g.endsWith("_AGAINST") ? ", against it" : "";
  const crosses = regime.crosses ?? 0;
  const score = (regime.score || "").toLowerCase();
  const candle = regime.minutes ? ` on ${regime.minutes}-min candles` : "";
  return `${phase}${since}${move}${side} · ${score}, ${crosses} cross${crosses === 1 ? "" : "es"} today${candle}`;
}

/** Chip colour: sky = with a trend, violet = against, amber = choppy (the terminal's palette). */
export function trendColor(group: TrendGroup): "sky" | "violet" | "amber" | "slate" {
  if (group.endsWith("_WITH")) return "sky";
  if (group.endsWith("_AGAINST")) return "violet";
  return group === "CHOPPY" ? "amber" : "slate";
}
