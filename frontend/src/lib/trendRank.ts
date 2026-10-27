import type { ChopScanRow } from "@/lib/smaApi";

/**
 * Trend age, SMA gap and rank for the Trend quality card's "Trend on" filter. The same arithmetic as the
 * server's `backend/trend_rank.py` (which sends the alerts), so the table and the alerts agree.
 *
 * age: market minutes all ticked sizes have pointed the same way (the shortest `run candles x size`);
 * gap: average |SMA fast − slow| / slow % over the sizes; fading: a size's gap is under half its widest in
 * this run; rank: average of the stock's place by age (youngest first) and by gap (widest first).
 */
export const FADE_SHARE = 0.5;

export type TrendDir = "UP" | "DOWN" | "SAME";
export type TrendKind = "SIDE" | "STRONG";
export type TrendRead = {
  side: "BULLISH" | "BEARISH";
  ageMin: number;
  sinceTs: number;
  gapPct: number;
  fading: boolean;
};
export type RankedTrend = TrendRead & { rank: number; of: number };

function sideOn(r: ChopScanRow, m: number, kind: TrendKind): "BULLISH" | "BEARISH" | null {
  const k = String(m);
  if (kind === "SIDE") return r.side_by_minutes?.[k] ?? null;
  const t = r.trend_by_minutes?.[k];
  return t === "STRONG_BULLISH" ? "BULLISH" : t === "STRONG_BEARISH" ? "BEARISH" : null;
}

/** The stock's aligned trend on these sizes, or null (not aligned, wrong way, or an older server's row). */
export function trendRead(r: ChopScanRow, sizes: number[], dir: TrendDir, kind: TrendKind): TrendRead | null {
  if (sizes.length === 0) return null;
  const sides = sizes.map((m) => sideOn(r, m, kind));
  const side = sides[0];
  if (side == null || sides.some((s) => s !== side)) return null;
  if ((dir === "UP" && side !== "BULLISH") || (dir === "DOWN" && side !== "BEARISH")) return null;
  const ages: number[] = [];
  const starts: number[] = [];
  const gaps: number[] = [];
  let fading = false;
  for (const m of sizes) {
    const k = String(m);
    const candles = r.run_candles_by_minutes?.[k];
    const since = r.run_since_ts_by_minutes?.[k];
    const gap = r.gap_pct_by_minutes?.[k];
    const peak = r.gap_peak_pct_by_minutes?.[k];
    if (candles == null || since == null || gap == null) return null;
    ages.push(candles * m);
    starts.push(since);
    gaps.push(Math.abs(gap));
    if (peak && Math.abs(gap) < FADE_SHARE * peak) fading = true;
  }
  return {
    side,
    ageMin: Math.min(...ages),
    sinceTs: Math.max(...starts),
    gapPct: gaps.reduce((a, b) => a + b, 0) / gaps.length,
    fading,
  };
}

/** Rank the given reads (1 = fresh and wide). Ties: the wider gap, then the younger. */
export function rankTrends(reads: Map<string, TrendRead>): Map<string, RankedTrend> {
  const items = [...reads.entries()];
  const place = new Map<string, number>(items.map(([s]) => [s, 0]));
  [...items].sort((a, b) => a[1].ageMin - b[1].ageMin).forEach(([s], i) => place.set(s, (place.get(s) ?? 0) + i + 1));
  [...items].sort((a, b) => b[1].gapPct - a[1].gapPct).forEach(([s], i) => place.set(s, (place.get(s) ?? 0) + i + 1));
  const order = [...items].sort(
    (a, b) => (place.get(a[0]) ?? 0) - (place.get(b[0]) ?? 0) || b[1].gapPct - a[1].gapPct || a[1].ageMin - b[1].ageMin
  );
  return new Map(order.map(([s, t], i) => [s, { ...t, rank: i + 1, of: order.length }]));
}

/** "45m" / "2h 05m". */
export function ageText(min: number): string {
  if (min < 60) return `${min}m`;
  return `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m`;
}
