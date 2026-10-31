/**
 * Where the next candle's close would fire an SMA 9 slope rule, as a price (slope_exit.py, turn_entry.py).
 *
 * The bot reads the fast SMA's slope on closed candles:
 *
 *     slope = (SMA now − SMA `back` candles ago) ÷ SMA then × 100 ÷ back      (% per candle)
 *
 * The next candle changes the SMA by (its close − the close that drops out of the window) ÷ fast, and the
 * "then" SMA of that reading is already known, so the close at which the slope reaches a level is one number:
 *
 *     close* = drop + fast × (SMA then × (1 + level × back ÷ 100) − SMA now)
 *
 * Every level here is a minimum the bot compares against ("at least", never "exactly"), so the zone beyond
 * close* is where the rule fires. Display only: the bot's own reading decides.
 */
import type { ChartFilters } from "@/lib/smaApi";

export type ZoneRow = { time: number; close: number; sma9: number | null; sma21?: number | null };

export type Zone = {
  /** Which rule, in the bot's words. */
  rule: "turn" | "vturn" | "bend" | "entry";
  /** The side of the trade the rule closes, or the side a turn entry would open. */
  direction: "LONG" | "SHORT";
  /** The next candle's close at which it fires. */
  level: number;
  /** The zone is at or above the level (true) or at or below it (false). */
  above: boolean;
  /** "SMA 9 turn ≥ 0.02%/c" and the like. */
  text: string;
};

export type ZoneRead = { zones: Zone[]; note: string | null };

export type ZoneArgs = {
  rows: ReadonlyArray<ZoneRow>;
  /** The closed candle the reading is made on (the next candle is the one judged). */
  i: number;
  filters: ChartFilters | undefined;
  /** The chart's candle size and the bot's own, in minutes. */
  bar: number;
  botBar: number;
  /** The trade held after candle `i`, with when it opened (seconds), or null while flat. */
  held: { direction: "LONG" | "SHORT"; entryTime: number } | null;
};

export function slopeAt(rows: ReadonlyArray<{ sma9?: number | null }>, i: number, back: number): number | null {
  if (i - back < 0) return null;
  const now = rows[i]?.sma9;
  const then = rows[i - back]?.sma9;
  if (now == null || then == null || then === 0) return null;
  return (((now - then) / then) * 100) / back;
}

/** The next candle's close at which the slope read over `back` candles equals `level` (% per candle). */
export function closeForSlope(rows: ReadonlyArray<ZoneRow>, i: number, fast: number, back: number, level: number): number | null {
  const now = rows[i]?.sma9;
  const then = rows[i + 1 - back]?.sma9;
  const drop = rows[i - fast + 1]?.close;
  if (now == null || then == null || drop == null || i - fast + 1 < 0) return null;
  const smaNext = then * (1 + (level * back) / 100);
  return drop + fast * (smaNext - now);
}

const fmt = (v: number) => `${v}%/c`;

/** The exit zones of a held trade and the entry zones of a turn entry, for the candle after row `i`. */
export function slopeZones(a: ZoneArgs): ZoneRead {
  const { rows, i, filters: f, bar, botBar, held } = a;
  if (!f || i < 0 || i >= rows.length) return { zones: [], note: null };
  const fast = f.sma_fast ?? 9;
  const back = f.slope_lookback ?? 2;
  const turn = f.slope_turn_pct ?? 0.02;
  const move = f.vturn_move_pct ?? 0.02;
  const turnBack = f.vturn_turn_pct ?? 0.015;
  const within = f.vturn_within ?? 6;
  const bend = f.slope_bend_pct ?? 60;
  const slopeBar = f.slope_minutes || 0;
  const zones: Zone[] = [];
  // Signed slopes (+ = with `side`) of the closed candles up to `i`.
  const signed = (side: "LONG" | "SHORT", j: number) => {
    const v = slopeAt(rows, j, back);
    return v == null ? null : side === "LONG" ? v : -v;
  };
  const sign = (side: "LONG" | "SHORT") => (side === "LONG" ? -1 : 1);
  const push = (rule: Zone["rule"], direction: "LONG" | "SHORT", raw: number, text: string, above: boolean) => {
    const level = closeForSlope(rows, i, fast, back, raw);
    if (level != null && Number.isFinite(level)) zones.push({ rule, direction, level, above, text });
  };

  if (held) {
    const side = held.direction;
    const exitsOn = Boolean(f.slope_rules) || Boolean(f.vturn_exit) || Boolean(f.decel_exit);
    if (!exitsOn) return { zones: [], note: "No SMA 9 slope exit is on, so the slope never closes this trade." };
    if (slopeBar && slopeBar !== bar) {
      return {
        zones: [],
        note: `The bot reads the slope exits on ${slopeBar}-minute candles; set the chart's candle size to ${slopeBar} min to see where they fire.`,
      };
    }
    // Candles closed after the entry: the ones the bot has read for this trade (the peak and the V's history).
    const since: number[] = [];
    for (let j = i; j >= 0 && rows[j].time + bar * 60 > held.entryTime; j -= 1) {
      const v = signed(side, j);
      if (v != null) since.unshift(v);
    }
    // Exits sit against the trade: a long closes on a close at or below the level, a short at or above it.
    const above = side === "SHORT";
    if (f.slope_rules && f.slope_turn) push("turn", side, sign(side) * turn, `SMA ${fast} turn ≥ ${fmt(turn)} against`, above);
    if (f.slope_rules && bend > 0) {
      const peak = since.length ? Math.max(...since) : null;
      if (peak != null && peak >= Math.max(turn, 1e-9)) {
        const at = peak * (1 - bend / 100);
        push("bend", side, -sign(side) * at, `bend: slope ≤ ${Number(at.toFixed(4))}%/c (${bend}% off its steepest ${Number(peak.toFixed(4))}%/c)`, above);
      }
    }
    if (f.vturn_exit) {
      const recent = since.slice(-within);
      const steepest = recent.length ? Math.max(...recent) : null;
      if (steepest != null && steepest >= Math.max(move, 1e-9)) {
        push("vturn", side, sign(side) * turnBack, `V-turn ≥ ${fmt(turnBack)} back (after ${Number(steepest.toFixed(4))}%/c with the trade)`, above);
      }
    }
    const notes: string[] = [];
    if (f.vturn_exit && zones.every((z) => z.rule !== "vturn")) notes.push(`V-turn: no steep run of ${fmt(move)} with the trade in the last ${within} candles yet`);
    if (f.decel_exit) notes.push("the deceleration guard reads the bend in ATR, not one price level");
    return { zones: sortNearest(zones, rows[i].close), note: notes.length ? notes.join("; ") : null };
  }

  // Flat: an SMA 9 turn entry (turn_entry.py) opens on the next candle when its slope turns back after a steep run.
  if (!f.turn_entry) return { zones: [], note: null };
  if (bar !== botBar) return { zones: [], note: `The turn entry reads the bot's own ${botBar}-minute candle; set the chart to it to see where it fires.` };
  const row = rows[i];
  for (const side of ["LONG", "SHORT"] as const) {
    // The last `within` closed slopes, the way the bot reads them before the next candle.
    const prior: number[] = [];
    for (let j = i; j > i - within && j >= 0; j -= 1) {
      const v = signed(side, j);
      if (v != null) prior.push(v);
    }
    const prev = signed(side, i);
    if (!prior.length || (prev != null && prev >= turnBack)) continue;
    // A V needs the run the other way first: at least `move` against the side it will open.
    if (Math.min(...prior) > -Math.max(move, 1e-9)) continue;
    if (f.turn_toward_cross && row.sma9 != null && row.sma21 != null) {
      // Only toward the 9/21 cross: a buy while SMA 9 is under SMA 21, a sell while above.
      if (side === "LONG" ? row.sma9 >= row.sma21 : row.sma9 <= row.sma21) continue;
    }
    const raw = side === "LONG" ? turnBack : -turnBack;
    push("entry", side, raw, `SMA ${fast} turn ${side === "LONG" ? "up" : "down"} ≥ ${fmt(turnBack)}: ${side === "LONG" ? "buys" : "sells"}`, side === "LONG");
  }
  return { zones: sortNearest(zones, row.close), note: zones.length ? null : `Turn entry: no steep run of ${fmt(move)} in the last ${within} candles yet.` };
}

function sortNearest(zones: Zone[], close: number): Zone[] {
  return [...zones].sort((x, y) => Math.abs(x.level - close) - Math.abs(y.level - close));
}

/** One line for the hover: "exits if the next candle closes ≥ ₹1,603.20 (SMA 9 turn ≥ 0.02%/c against)". */
export function zoneWords(read: ZoneRead, px: (v: number) => string): string {
  const parts = read.zones.map((z) => `${z.rule === "entry" ? (z.direction === "LONG" ? "buys" : "sells") : "exits"} if the next candle closes ${z.above ? "≥" : "≤"} ₹${px(z.level)} (${z.text})`);
  return [...parts, read.note].filter(Boolean).join("; ");
}
