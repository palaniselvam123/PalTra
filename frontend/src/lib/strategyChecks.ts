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

/** The SMA cross exit is on unless set off (older servers do not send it). */
export const crossExitOn = (cfg: Partial<SmaConfig>): boolean => cfg.cross_exit !== false;

/**
 * No exit is on: SMA entries with the cross exit, stop-loss, gap mode and
 * Bollinger exit and counter-candle exit all off, so a trade is held until the square-off. (Candle
 * patterns always close at the end of their candle; candle colour on the other colour.)
 */
export function noExitOn(cfg: Partial<SmaConfig> | null | undefined): boolean {
  if (!cfg) return false;
  return (
    (cfg.entry_mode ?? "SMA") === "SMA" &&
    !crossExitOn(cfg) &&
    cfg.use_stop === false &&
    !cfg.use_gap_mode &&
    String(cfg.bb_exit ?? "OFF").toUpperCase() === "OFF" &&
    !(Number(cfg.counter_exit_count ?? 0) > 0) &&
    !cfg.ema_exit &&
    !cfg.st_exit &&
    !cfg.slope_exit &&
    !cfg.vturn_exit &&
    !(cfg.pivot_target === "R1" || cfg.pivot_target === "R2") &&
    !cfg.slow_sma_exit
  );
}

/**
 * Overlaps and slips in the strategy settings (mostly gap mode). Nothing here
 * changes a trade; it only points out settings that do the same job twice, are
 * lopsided, or leave a trade with no exit.
 */
export function strategyNotes(cfg: Partial<SmaConfig> | null | undefined): StrategyNote[] {
  if (!cfg) return [];
  const notes: StrategyNote[] = [];
  if (noExitOn(cfg)) {
    notes.push({
      id: "no-exit",
      text: "No exit is on: the SMA cross exit, stop-loss, gap mode and Bollinger exit are all off. A trade opened on a cross is held, with no stop, until the square-off.",
      fix: { label: "Turn the SMA cross exit on", values: { cross_exit: true } },
    });
  }
  if (!cfg.use_gap_mode) return notes;
  const crossCloses = crossExitOn(cfg);
  const inLong = num(cfg.gap_entry_long, 0.05);
  const outLong = num(cfg.gap_exit_long, 0.02);
  const inShort = num(cfg.gap_entry_short, -0.05);
  const outShort = num(cfg.gap_exit_short, -0.02);

  // With the cross exit off, a fade exit at 0% is the only thing that closes at the cross, so it is not a repeat.
  if (crossCloses && outLong <= 0) {
    notes.push({
      id: "buy-exit-zero",
      text: `Buy exit at ${outLong}% is the SMA cross itself: the gap reaches 0% where SMA 9 crosses back below SMA 21, and that cross already closes the trade. This fade exit never fires first.`,
      fix: inLong > 0.02 ? { label: `Set buy exit to ${round(inLong * 0.4)}%`, values: { gap_exit_long: round(inLong * 0.4) } } : undefined,
    });
  }
  if (crossCloses && outShort >= 0) {
    notes.push({
      id: "sell-exit-zero",
      text: `Sell exit at ${outShort}% is the SMA cross itself: the opposite cross already closes the trade there, so this fade exit never fires first.`,
      fix: inShort < -0.02 ? { label: `Set sell exit to ${round(inShort * 0.4)}%`, values: { gap_exit_short: round(inShort * 0.4) } } : undefined,
    });
  }
  const roomLong = round(inLong - outLong);
  const roomShort = round(outShort - inShort);
  // A lock-in level (exit beyond entry) is chosen on purpose, so "balance both" would undo it.
  const lockIn = roomLong < 0 || roomShort < 0;
  if (!lockIn && (Math.abs(inLong + inShort) > 1e-9 || Math.abs(outLong + outShort) > 1e-9)) {
    notes.push({
      id: "lopsided",
      text: `Buys and sells are not mirror images: a buy enters at ${inLong}% and exits at ${outLong}% (room ${roomLong}), a sell enters at ${inShort}% and exits at ${outShort}% (room ${roomShort}). One side is held much longer than the other.`,
      fix: balanced(inLong, inShort),
    });
  }
  for (const [side, inLevel, outLevel, room] of [
    ["buy", inLong, outLong, roomLong],
    ["sell", inShort, outShort, roomShort],
  ] as const) {
    if (room < 0) {
      notes.push({
        id: `lock-in-${side}`,
        text: `The ${side} exit (${outLevel}%) is beyond the ${side} entry (${inLevel}%): a lock-in level. The gap exit only arms once the gap has reached ${outLevel}%, then closes when it comes back to it${num(cfg.gap_giveback_pct, 0) > 0 ? " (the give-back waits for that too)" : ""}. A ${side} whose gap never gets that wide is closed only by ${crossCloses ? "the stop, the opposite cross or square-off" : "the stop or square-off"}.`,
      });
    }
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
      fix: { label: "Turn the gap range check off", values: { use_gap_long: false, use_gap_short: false } },
    });
  }
  if (num(cfg.gap_giveback_pct, 0) > 0 && (num(cfg.gap_fade_min_candles, 0) > 0 || cfg.gap_fade_confirm_sma)) {
    notes.push({
      id: "exit-mix",
      text: "Give-back makes the fade exit sooner while “narrowing N candles” / “ride out pullbacks” make it wait. They pull the same exit in opposite directions; change one at a time and compare in Replay.",
    });
  }
  if (cfg.slow_sma_exit && cfg.gap_fade_confirm_sma) {
    notes.push({
      id: "slow-sma-fade",
      text: "“Ride out pullbacks” waits for a close past the slow SMA, and the slow-SMA exit closes the trade on that same candle first, so the gap fade never fires. Keep one of the two.",
      fix: { label: "Turn “Ride out pullbacks” off", values: { gap_fade_confirm_sma: false } },
    });
  }
  if (cfg.gap_fade_intrabar && cfg.gap_fade_minute) {
    notes.push({
      id: "fade-two-speeds",
      text: "The fade is checked every second and every 1-minute candle. In market hours the every-second check already covers each minute; only replays and after-hours use the 1-minute one. Keeping just the 1-minute check makes live trading match your backtests.",
      fix: { label: "Keep only the 1-minute check", values: { gap_fade_intrabar: false } },
    });
  }
  if (num(cfg.late_entry_min, 0) > 0) {
    notes.push({
      id: "late-entry-ignored",
      text: "Late entry is ignored in gap mode: gap mode already keeps a refused cross waiting until the checks agree (up to “Give up after the cross”).",
      fix: { label: "Set late entry to 0", values: { late_entry_min: 0 } },
    });
  }
  if (num(cfg.gap_giveback_pct, 0) > 0 && cfg.slope_exit && num(cfg.slope_bend_pct, 60) > 0) {
    notes.push({
      id: "giveback-bend",
      text: "Give-back and the SMA 9 bend exit do the same job (the trend is losing strength), one on the gap and one on the SMA 9 slope. Whichever reacts first closes the trade; keep one.",
    });
  }
  if (num(cfg.gap_fade_min_candles, 0) > 0 && num(cfg.counter_exit_count, 0) > 0) {
    notes.push({
      id: "narrow-counter",
      text: "“Gap narrowing N candles in a row” and “Close after N candles against the trade” both count a run of candles going the wrong way. Keep one.",
    });
  }
  return notes;
}
