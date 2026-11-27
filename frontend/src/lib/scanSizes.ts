/**
 * Candle sizes and SMA periods the Scalp scans (Trend quality, Cross scan) can read: the bots' own sizes plus
 * 30 minutes and 1 hour, and the main desk's SMA periods or the owner's own (cross_scan.SCAN_MINUTES / pick_smas).
 * Display and request helpers only.
 */
export const SCAN_SIZES = [1, 2, 3, 5, 10, 15, 30, 60] as const;
export type ScanSize = (typeof SCAN_SIZES)[number];
export const isScanSize = (m: unknown): m is ScanSize => SCAN_SIZES.includes(m as ScanSize);
/** "5 min", "1 hour". */
export const sizeLabel = (m: number) => (m === 60 ? "1 hour" : `${m} min`);
/** "5m", "1h" (chips). */
export const sizeShort = (m: number) => (m === 60 ? "1h" : `${m}m`);
/** "5-minute", "1-hour" (in a sentence, before "candle"). */
export const sizeWords = (m: number) => (m === 60 ? "1-hour" : `${m}-minute`);

/** The SMA periods a scan is asked for; 0 / 0 = the main desk's own. */
export type ScanSma = { fast: number; slow: number };
export const DESK_SMA: ScanSma = { fast: 0, slow: 0 };
export const SMA_PRESETS: ScanSma[] = [
  { fast: 9, slow: 21 },
  { fast: 20, slow: 50 },
  { fast: 50, slow: 200 },
];
export const SMA_MIN = 2;
export const SMA_MAX = 200;

/** Why a typed pair cannot be scanned, in plain words; null when it can. */
export function smaError(fast: number, slow: number): string | null {
  if (!Number.isInteger(fast) || !Number.isInteger(slow)) return "Type two whole numbers.";
  if (fast < SMA_MIN || slow < SMA_MIN || fast > SMA_MAX || slow > SMA_MAX) return `Each number must be between ${SMA_MIN} and ${SMA_MAX}.`;
  if (fast >= slow) return "The first (fast) number must be smaller than the second (slow) one.";
  return null;
}

export const cleanSma = (fast: unknown, slow: unknown): ScanSma => {
  const f = Number(fast) || 0;
  const s = Number(slow) || 0;
  return f && s && !smaError(f, s) ? { fast: f, slow: s } : DESK_SMA;
};
export const sameSma = (a: ScanSma, b: ScanSma) => a.fast === b.fast && a.slow === b.slow;
