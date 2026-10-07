import type { SmaConfig } from "./smaApi";

/** A setting that repeats another one or looks like a slip, with an optional one-click fix. */
export type StrategyNote = {
  id: string;
  text: string;
  fix?: { label: string; values: Partial<SmaConfig> };
};

const num = (v: unknown, fallback: number): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};
const round = (v: number) => Math.round(v * 1000) / 1000;

/** Both sides on one level: enter at ±in, exit at ±(40% of in), from the wider of the two entries. */
function balanced(inLong: number, inShort: number): StrategyNote["fix"] {
  const enter = round(Math.max(Math.abs(inLong), Math.abs(inShort)) || 0.05);
  const out = round(enter * 0.4);
  return {
    label: `Balance both: enter ±${enter}%, exit ±${out}%`,
    values: { gap_entry_long: enter, gap_exit_long: out, gap_entry_short: -enter, gap_exit_short: -out },
  };
}

/**
 * Overlaps and slips in the gap-mode settings. Nothing here changes a trade;
 * it only points out settings that do the same job twice or are lopsided.
 */
export function strategyNotes(cfg: Partial<SmaConfig> | null | undefined): StrategyNote[] {
  if (!cfg || !cfg.use_gap_mode) return [];
  const notes: StrategyNote[] = [];
  const inLong = num(cfg.gap_entry_long, 0.05);
  const outLong = num(cfg.gap_exit_long, 0.02);
  const inShort = num(cfg.gap_entry_short, -0.05);
  const outShort = num(cfg.gap_exit_short, -0.02);

  if (outLong <= 0) {
    notes.push({
      id: "buy-exit-zero",
      text: `Buy exit at ${outLong}% is the SMA cross itself: the gap reaches 0% where SMA 9 crosses back below SMA 21, and that cross already closes the trade. This fade exit never fires first.`,
      fix: inLong > 0.02 ? { label: `Set buy exit to ${round(inLong * 0.4)}%`, values: { gap_exit_long: round(inLong * 0.4) } } : undefined,
    });
  }
  if (outShort >= 0) {
    notes.push({
      id: "sell-exit-zero",
      text: `Sell exit at ${outShort}% is the SMA cross itself: the opposite cross already closes the trade there, so this fade exit never fires first.`,
      fix: inShort < -0.02 ? { label: `Set sell exit to ${round(inShort * 0.4)}%`, values: { gap_exit_short: round(inShort * 0.4) } } : undefined,
    });
  }
  const roomLong = round(inLong - outLong);
  const roomShort = round(outShort - inShort);
  if (Math.abs(inLong + inShort) > 1e-9 || Math.abs(outLong + outShort) > 1e-9) {
    notes.push({
      id: "lopsided",
      text: `Buys and sells are not mirror images: a buy enters at ${inLong}% and exits at ${outLong}% (room ${roomLong}), a sell enters at ${inShort}% and exits at ${outShort}% (room ${roomShort}). One side is held much longer than the other.`,
      fix: balanced(inLong, inShort),
    });
  }
  for (const [side, room] of [
    ["buy", roomLong],
    ["sell", roomShort],
  ] as const) {
    if (room > 0 && room < 0.02) {
      notes.push({
        id: `tight-${side}`,
        text: `The ${side} exit is only ${room} from its entry level, so a ${side} closes on the first small narrowing of the gap.`,
      });
    }
  }
  if (cfg.use_gap_long || cfg.use_gap_short) {
    notes.push({
      id: "gap-range",
      text: "The SMA gap range filter is on too. In gap mode it is read on the candle where the order goes, so its minimum repeats the entry level; only its maximum (don't chase a gap that is already wide) adds anything.",
    });
  }
  if (num(cfg.gap_giveback_pct, 0) > 0 && (num(cfg.gap_fade_min_candles, 0) > 0 || cfg.gap_fade_confirm_sma)) {
    notes.push({
      id: "exit-mix",
      text: "Give-back makes the fade exit sooner while “narrowing N candles” / “ride out pullbacks” make it wait. They pull the same exit in opposite directions; change one at a time and compare in Replay.",
    });
  }
  return notes;
}
