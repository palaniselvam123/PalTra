/**
 * A worked example of the SMA 9 slope rules on made-up candles, for the Settings slope editor.
 *
 * Same arithmetic as the bot (backend turn_entry.slopes / slope_exit.slope):
 *   SMA 9 slope = (SMA 9 now − SMA 9 `back` candles ago) ÷ SMA 9 then × 100 ÷ back, in % per candle.
 * The candles are invented (flat, then a steady climb, then a steady pull-back) and sized so the slope just reaches
 * the user's "steep run" and then the "turn back" level, so the page can show how big the candles must be in rupees.
 * Display only: nothing here is saved or traded.
 */

export type ExampleCandle = {
  i: number;
  open: number;
  high: number;
  low: number;
  close: number;
  sma: number | null;
  /** % per candle, signed (positive = SMA 9 rising). */
  slope: number | null;
  phase: "flat" | "run" | "back";
};

export type SlopeExample = {
  candles: ExampleCandle[];
  /** Rupees each close moves in the run / the pull-back. */
  runStep: number;
  backStep: number;
  /** Index (in `candles`) of the first candle whose slope reaches the steep run, the V-turn, and the turn exit. */
  steepAt: number | null;
  vturnAt: number | null;
  turnExitAt: number | null;
  runCandles: number;
};

function smaAt(closes: number[], i: number, n: number): number | null {
  if (i + 1 < n) return null;
  let s = 0;
  for (let k = i - n + 1; k <= i; k += 1) s += closes[k];
  return s / n;
}

function slopes(closes: number[], fast: number, back: number): (number | null)[] {
  const sma = closes.map((_, i) => smaAt(closes, i, fast));
  return sma.map((now, i) => {
    const then = i - back >= 0 ? sma[i - back] : null;
    if (now == null || then == null || then === 0) return null;
    return ((now - then) / then) * 100 / back;
  });
}

/** Closes for a LONG picture: `warm` flat at `price`, `run` candles up by `up`, `down` candles down by `dn`. */
function path(price: number, warm: number, run: number, up: number, down: number, dn: number): number[] {
  const out: number[] = [];
  for (let k = 0; k < warm; k += 1) out.push(price);
  let p = price;
  for (let k = 0; k < run; k += 1) out.push((p += up));
  for (let k = 0; k < down; k += 1) out.push((p -= dn));
  return out;
}

/** The smallest step (₹) for which `ok(step)` holds, by bisection; null when even `hi` is not enough. */
function smallest(ok: (step: number) => boolean, hi: number): number | null {
  if (!ok(hi)) return null;
  let lo = 0;
  for (let k = 0; k < 40; k += 1) {
    const mid = (lo + hi) / 2;
    if (ok(mid)) hi = mid;
    else lo = mid;
  }
  return hi;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

export function slopeExample(opts: {
  price: number;
  fast: number;
  back: number;
  move: number;
  turn: number;
  within: number;
  turnExit: number;
  side: "LONG" | "SHORT";
}): SlopeExample {
  const price = opts.price > 0 ? opts.price : 1000;
  const fast = Math.max(1, Math.round(opts.fast));
  const back = Math.max(1, Math.round(opts.back));
  const within = Math.max(1, Math.round(opts.within));
  const move = Math.max(opts.move, 1e-6);
  const turn = Math.max(opts.turn, 1e-6);
  const warm = fast + back;
  const RUN = 6;
  const DOWN = Math.min(within, 4);
  const cap = price * 0.05;

  // Climb: the smallest equal step that lifts the slope to the steep run within RUN candles.
  const up =
    smallest((u) => {
      const s = slopes(path(price, warm, RUN, u, 0, 0), fast, back);
      return s.slice(warm).some((v) => v != null && v >= move);
    }, cap) ?? cap;
  // Pull-back: the smallest equal step that turns the slope down to the turn-back level within DOWN candles.
  const dn =
    smallest((d) => {
      const s = slopes(path(price, warm, RUN, up, DOWN, d), fast, back);
      return s.slice(warm + RUN).some((v) => v != null && v <= -turn);
    }, cap) ?? cap;

  // Show the candles until the turn has fired (or the window ran out), plus one.
  const closesLong = path(price, warm, RUN, up, Math.max(DOWN, within) + 1, dn);
  const s = slopes(closesLong, fast, back);
  const flip = (v: number) => (opts.side === "SHORT" ? 2 * price - v : v);
  const signed = (v: number | null) => (v == null ? null : opts.side === "SHORT" ? -v : v);

  let steepAt: number | null = null;
  let vturnAt: number | null = null;
  let turnExitAt: number | null = null;
  for (let i = warm; i < closesLong.length; i += 1) {
    const v = s[i];
    if (v == null) continue;
    if (steepAt == null && v >= move) steepAt = i;
    if (i >= warm + RUN) {
      if (turnExitAt == null && opts.turnExit > 0 && v <= -opts.turnExit) turnExitAt = i;
      if (vturnAt == null && v <= -turn) {
        const prior = s.slice(Math.max(0, i - within), i).filter((x): x is number => x != null);
        if (prior.length && Math.max(...prior) >= move) vturnAt = i;
      }
    }
  }
  const lastShown = Math.min(closesLong.length - 1, Math.max(vturnAt ?? 0, turnExitAt ?? 0, warm + RUN + DOWN) + 1);
  const first = Math.max(0, warm - 3);
  const wick = Math.max(up, dn) * 0.35 + price * 0.0002;
  const candles: ExampleCandle[] = [];
  for (let i = first; i <= lastShown; i += 1) {
    const close = closesLong[i];
    const open = i > 0 ? closesLong[i - 1] : close;
    const hi = Math.max(open, close) + wick;
    const lo = Math.min(open, close) - wick;
    const sma = smaAt(closesLong, i, fast);
    candles.push({
      i: i - first,
      open: round2(flip(open)),
      close: round2(flip(close)),
      high: round2(opts.side === "SHORT" ? flip(lo) : hi),
      low: round2(opts.side === "SHORT" ? flip(hi) : lo),
      sma: sma == null ? null : round2(flip(sma)),
      slope: signed(s[i]),
      phase: i < warm ? "flat" : i < warm + RUN ? "run" : "back",
    });
  }
  const at = (i: number | null) => (i == null || i > lastShown ? null : i - first);
  return {
    candles,
    runStep: round2(up),
    backStep: round2(dn),
    steepAt: at(steepAt),
    vturnAt: at(vturnAt),
    turnExitAt: at(turnExitAt),
    runCandles: RUN,
  };
}
