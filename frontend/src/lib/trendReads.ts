/**
 * The trend reading a backtest made at each candle close (backend trend_log.py; display only): the Trend quality score,
 * its checks, the phase, and what the bot did with it. Shared by the run detail's table and the chart's why line.
 */
/** HH:MM in IST of a unix time in seconds. */
export function istClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
}

export type TrendRead = {
  ts: number;
  symbol?: string;
  day?: string;
  price: number | null;
  minutes: number;
  side: "BULLISH" | "BEARISH" | null;
  score: number | null;
  strong: string | null;
  mode: string | null;
  checks: Record<string, boolean>;
  label: string | null;
  crosses: number | null;
  phase: "BULL" | "BEAR" | "CHOP" | null;
  phase_since: string | null;
  phase_early: boolean;
  held: "LONG" | "SHORT" | null;
  decision: string;
  /** "live" = kept while the backtest played; "after" = worked out again afterwards (no positions known). */
  source?: "live" | "after";
};

export const CHECK_WORDS: Record<string, string> = {
  stack: "stack",
  vwap: "VWAP",
  slope: "slope",
  fan: "fan",
  hold: "hold",
  structure: "structure",
  run: "run",
  volume: "volume",
};

export function sideWord(side: TrendRead["side"]): string {
  return side === "BULLISH" ? "up" : side === "BEARISH" ? "down" : "—";
}

export function phaseWord(r: Pick<TrendRead, "phase" | "phase_since" | "phase_early">): string {
  if (r.phase_early) return "too few candles yet";
  if (!r.phase) return "—";
  const word = r.phase === "BULL" ? "UP" : r.phase === "BEAR" ? "DOWN" : "CHOP";
  return r.phase_since ? `${word} since ${r.phase_since}` : word;
}

export function checksPassed(r: TrendRead): { passed: number; total: number; failed: string[] } {
  const keys = Object.keys(r.checks ?? {});
  const failed = keys.filter((k) => !r.checks[k]).map((k) => CHECK_WORDS[k] ?? k);
  return { passed: keys.length - failed.length, total: keys.length, failed };
}

/** "Trend reading at 10:45: down, score 86, 6/7 (missed hold), phase CHOP → phase CHOP (needs DOWN) — waiting." */
export function trendReadText(r: TrendRead): string {
  const c = checksPassed(r);
  const checks = c.total ? `, ${c.passed}/${c.total}${c.failed.length ? ` (missed ${c.failed.join(", ")})` : ""}` : "";
  return `Trend reading at ${istClock(r.ts)}: ${sideWord(r.side)}, score ${r.score ?? "—"}${checks}, phase ${phaseWord(r)} → ${r.decision}.`;
}

/** What changed against the stock's previous reading that day (green = stronger, red = weaker), like the History tab. */
export function trendChange(r: TrendRead, prev: TrendRead | undefined): { text: string; tone: "up" | "down" | "none" } {
  if (!prev) return { text: "first reading", tone: "none" };
  const parts: string[] = [];
  let tone: "up" | "down" | "none" = "none";
  if (prev.side !== r.side) {
    parts.push(`trend turned ${sideWord(r.side)}`);
    tone = "down";
  }
  const a = prev.score ?? 0;
  const b = r.score ?? 0;
  if (a !== b && prev.side === r.side) {
    parts.push(`score ${a} → ${b}`);
    tone = b > a ? "up" : "down";
  }
  if (prev.phase !== r.phase || prev.phase_early !== r.phase_early) {
    parts.push(`phase ${phaseWord(prev).split(" since")[0]} → ${phaseWord(r).split(" since")[0]}`);
    if (r.phase && r.phase !== "CHOP" && !r.phase_early) tone = "up";
    else if (tone === "none") tone = "down";
  }
  if ((prev.decision || "").split(" — ")[0] !== (r.decision || "").split(" — ")[0] && /opened|closed|enter/i.test(r.decision)) {
    parts.push("bot acted");
  }
  return parts.length ? { text: parts.join(" · "), tone } : { text: "—", tone: "none" };
}
