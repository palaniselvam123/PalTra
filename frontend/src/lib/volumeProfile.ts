/** Volume profile: how many shares traded at each price level in a session.
 *
 * Each 1-minute candle's volume is spread evenly over the price rows its
 * high–low range covers (a 1-minute bar does not say where inside its range
 * the shares printed). The busiest row is the point of control (POC); the
 * value area is the band around it that holds `valueAreaShare` of the volume,
 * grown one row at a time toward the busier neighbour.
 */

export type ProfileCandle = { time: number; high: number; low: number; volume?: number | null };

export type ProfileRow = { low: number; high: number; volume: number };

export type VolumeProfile = {
  rows: ProfileRow[];
  /** Middle of the busiest row. */
  poc: number;
  /** Value area high / low: edges of the band holding valueAreaShare of the volume. */
  vah: number;
  val: number;
  total: number;
  /** IST date (YYYY-MM-DD) of the session profiled. */
  day: string;
  candles: number;
};

export const PROFILE_ROWS = 40;
export const VALUE_AREA_SHARE = 0.7;

function istDay(sec: number): string {
  return new Date((sec + 19_800) * 1000).toISOString().slice(0, 10);
}

/** Profile of the latest IST session in `candles`. Null when it has no volume. */
export function volumeProfile(
  candles: ProfileCandle[],
  rowsWanted = PROFILE_ROWS,
  valueAreaShare = VALUE_AREA_SHARE
): VolumeProfile | null {
  if (candles.length === 0) return null;
  const day = istDay(candles[candles.length - 1].time);
  const session = candles.filter(
    (c) => istDay(c.time) === day && c.volume != null && c.volume > 0 && Number.isFinite(c.high) && Number.isFinite(c.low)
  );
  if (session.length === 0) return null;
  const lo = Math.min(...session.map((c) => c.low));
  const hi = Math.max(...session.map((c) => c.high));
  const n = Math.max(1, rowsWanted);
  const step = hi > lo ? (hi - lo) / n : 0;
  const vols = new Array<number>(step > 0 ? n : 1).fill(0);
  const rowOf = (price: number) => (step > 0 ? Math.min(n - 1, Math.max(0, Math.floor((price - lo) / step))) : 0);

  for (const c of session) {
    const v = c.volume as number;
    const a = rowOf(Math.min(c.low, c.high));
    const b = rowOf(Math.max(c.low, c.high));
    const share = v / (b - a + 1);
    for (let i = a; i <= b; i += 1) vols[i] += share;
  }

  const total = vols.reduce((s, v) => s + v, 0);
  if (total <= 0) return null;
  let pocRow = 0;
  for (let i = 1; i < vols.length; i += 1) if (vols[i] > vols[pocRow]) pocRow = i;

  // Grow the value area from the POC toward whichever side traded more.
  let down = pocRow;
  let up = pocRow;
  let inside = vols[pocRow];
  while (inside < total * valueAreaShare && (down > 0 || up < vols.length - 1)) {
    const below = down > 0 ? vols[down - 1] : -1;
    const above = up < vols.length - 1 ? vols[up + 1] : -1;
    if (above >= below) {
      up += 1;
      inside += vols[up];
    } else {
      down -= 1;
      inside += vols[down];
    }
  }

  const edge = (i: number) => lo + i * step;
  const rows = vols.map((volume, i) => ({ low: edge(i), high: step > 0 ? edge(i + 1) : hi, volume }));
  return {
    rows,
    poc: step > 0 ? edge(pocRow) + step / 2 : lo,
    vah: step > 0 ? edge(up + 1) : hi,
    val: edge(down),
    total,
    day,
    candles: session.length,
  };
}
