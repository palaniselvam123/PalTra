/** Shared arithmetic of the Second candle test screens (SecondCandle, CandleCompare): the same readings as the backend
 * second_candle.py, worked out in the browser from the saved rows. */
import type { SecondCandleRow } from "@/lib/smaApi";

export const LEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50];
export const STOPS = [3, 5, 8, 10, 15, 20];
/** The stop levels the backend measures the stop-and-reverse leg for (second_candle.REVERSE_STOPS). */
export const REV_STOPS = STOPS;
export const ordinal = (n: number) => (n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`);
export const fmt = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : v.toFixed(d));
export const signed = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`);
export const nearestLevel = (v: number) => LEVELS.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));
export const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
/** A level's minute from a row's level map: the backend keys them like "10.0" (older saves may use "10"). */
export const levelMin = (m: Record<string, number | null> | undefined, lv: number) => m?.[`${lv}.0`] ?? m?.[String(lv)] ?? null;
/** The same reading as the backend's target_stop_outcome: the target first, else the stop, else the square-off. */
export function outcome(r: SecondCandleRow, target: number, stop: number): { kind: "target" | "stop" | "close"; pts: number } {
  const t = levelMin(r.fav_min, nearestLevel(target));
  const s = levelMin(r.adv_min, nearestLevel(stop));
  if (t != null && (s == null || t < s)) return { kind: "target", pts: nearestLevel(target) };
  if (s != null) return { kind: "stop", pts: -nearestLevel(stop) };
  return { kind: "close", pts: r.close_pts };
}
/** The reverse after the stop (backend reverse_outcome): the other side entered at the stop price, the same target and
 * stop. null = no reverse (the first trade did not stop out first, or the stop is not a measured level). */
export function reverseOutcome(r: SecondCandleRow, target: number, stop: number): { kind: "target" | "stop" | "close"; pts: number; best: number } | null {
  if (outcome(r, target, stop).kind !== "stop") return null;
  const s = nearestLevel(stop);
  const leg = r.rev?.[`${s}.0`] ?? r.rev?.[String(s)];
  if (!leg) return null;
  const t = nearestLevel(target);
  const tMin = leg[4 + LEVELS.indexOf(t)];
  const sMin = leg[4 + LEVELS.length];
  const best = Number(leg[1] ?? 0);
  if (tMin != null && (sMin == null || tMin < sMin)) return { kind: "target", pts: t, best };
  if (sMin != null) return { kind: "stop", pts: -s, best };
  return { kind: "close", pts: Number(leg[3] ?? 0), best };
}
