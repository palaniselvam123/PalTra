/**
 * Candle colour mode: a candle's body against the colour rules' floors, in ₹, % of the close and ATRs (display only).
 *
 * The bot (candle_patterns.colour_call) reads the body of each closed colour candle: the Heikin Ashi body when
 * `ha_colours` is on (heikin_ashi.view: the real close with the open moved, so close − open is the HA body), else the
 * real one. A body under the floor counts as no colour, like a doji, and the trade is held. The floor to close a trade
 * is the exit rule's % of the close when it is on (colour_exit.exit_cfg), else the entry rule's own floor. The chart's
 * readings line showed the candle's size (high − low) in ATRs, which neither rule reads; this puts the body next to
 * the floor in the same units.
 */
import type { ChartFilters } from "./smaApi";
import { heikinAshi } from "./chartStyle";

type Row = { time: number; open: number; high: number; low: number; close: number; atr14?: number | null };

export type BodyFloor = {
  /** Smallest body in ₹ (0 = any body counts). */
  rs: number;
  pct: number;
  /** In ATRs of this candle (null when the ATR is not formed). */
  atr: number | null;
  /** How the setting is written ("0.5% of the price", "1× ATR"). */
  setting: string;
  ok: boolean;
};

export type ColourBodyRead = {
  /** Heikin Ashi body (true) or the real candle's. */
  ha: boolean;
  colour: "green" | "red" | "doji";
  rs: number;
  pct: number;
  atr: number | null;
  /** The floor that closes a trade, and whether that is the separate exit rule. */
  close: BodyFloor;
  closeByExitRule: boolean;
  closeCount: number;
  /** The entry rule's floor for the newest candle of a run (null when it equals `close`). */
  entry: BodyFloor | null;
  entryCount: number;
};

/** Null outside candle colour mode or when the chart is not on the colour candle size. */
export function colourBodyRead(
  rows: Row[],
  i: number,
  filters: ChartFilters | null | undefined,
  bar: number,
  /** The ATR the reading line shows (the forming candle has none: the last closed one's). */
  atrShown?: number | null,
): ColourBodyRead | null {
  if (!filters || filters.entry_mode !== "COLOUR") return null;
  const tf = Number(filters.pattern_tf ?? 0);
  if (!tf || tf !== bar || i < 0 || i >= rows.length) return null;
  const ha = Boolean(filters.ha_colours);
  // Heikin Ashi from the first loaded candle; the bot starts from its own tape, so the body can differ by a little.
  const drawn = ha ? heikinAshi(rows.slice(0, i + 1)) : rows;
  const c = drawn[i];
  const close = rows[i].close;
  if (!close) return null;
  const body = c.close - c.open;
  const rs = Math.abs(body);
  const atrRaw = rows[i].atr14 ?? atrShown;
  const atrNow = atrRaw != null && atrRaw > 0 ? atrRaw : null;
  const inAtr = (v: number) => (atrNow ? v / atrNow : null);
  const floorFrom = (floorRs: number, setting: string): BodyFloor => ({
    rs: floorRs,
    pct: (floorRs / close) * 100,
    atr: inAtr(floorRs),
    setting,
    ok: floorRs <= 0 || rs >= floorRs,
  });

  // The entry rule: the newest candle of a run of `colour_count` (2+ reads the per-candle sizes), else one floor.
  const entryCount = Math.max(1, Number(filters.colour_count ?? 1));
  const unit = filters.colour_body_unit === "ATR" ? "ATR" : "PCT";
  const seq = entryCount > 1 ? filters.colour_body_seq ?? [] : [];
  const own = seq.length ? seq[Math.min(seq.length, entryCount) - 1] : null;
  const usual = unit === "ATR" ? Number(filters.colour_body_atr ?? 0) : Number(filters.colour_body_pct ?? filters.colour_min_body ?? 0);
  const value = own ?? usual;
  const entry =
    unit === "ATR"
      ? atrNow
        ? floorFrom(value * atrNow, `${value}× ATR`)
        : floorFrom(0, `${value}× ATR (ATR not formed: any body counts)`)
      : floorFrom((close * value) / 100, `${value}% of the price`);

  const byExit = Boolean(filters.colour_exit_on);
  const exitPct = Number(filters.colour_exit_body ?? 0);
  const closeFloor = byExit ? floorFrom((close * exitPct) / 100, `${exitPct}% of the price`) : entry;
  return {
    ha,
    colour: body > 0 ? "green" : body < 0 ? "red" : "doji",
    rs,
    pct: (rs / close) * 100,
    atr: inAtr(rs),
    close: closeFloor,
    closeByExitRule: byExit,
    closeCount: byExit ? Math.max(1, Number(filters.colour_exit_count ?? 1)) : entryCount,
    entry: byExit ? entry : null,
    entryCount,
  };
}

const money = (v: number) => `₹${v.toFixed(2)}`;
const atrText = (v: number | null) => (v == null ? "" : ` = ${v.toFixed(2)}× ATR`);

/** "₹12.50 = 0.20% = 0.23× ATR" */
export function bodyText(rs: number, pct: number, atr: number | null): string {
  return `${money(rs)} = ${pct.toFixed(2)}%${atrText(atr)}`;
}

/** The hover text: every number and what the bot does with this candle. */
export function colourBodyTip(r: ColourBodyRead): string {
  const kind = r.ha ? "Heikin Ashi" : "real";
  const floorLine = (name: string, f: BodyFloor, count: number) =>
    f.rs <= 0
      ? `${name}: any body counts (${f.setting}).`
      : `${name}: body at least ${f.setting} = ${bodyText(f.rs, f.pct, f.atr)}, ${count} candle${count === 1 ? "" : "s"} in a row: ${f.ok ? "this one is big enough" : "this one is too small, so it counts as no colour and the trade is held"}.`;
  return [
    `This candle's ${kind} body (open to close${r.ha ? " of the smoothed candle" : ""}): ${bodyText(r.rs, r.pct, r.atr)}, ${r.colour}.`,
    "Size (high to low, wicks included) is not what the colour rules read; the body is.",
    floorLine(r.closeByExitRule ? "To close a trade (exit rule)" : "To close or turn a trade (entry rule)", r.close, r.closeCount),
    r.entry ? floorLine("To open a trade (entry rule)", r.entry, r.entryCount) : "",
    r.ha ? "The chart works Heikin Ashi out from the first loaded candle and the bot from its own tape, so the body can differ by a little." : "",
  ]
    .filter(Boolean)
    .join(" ");
}
