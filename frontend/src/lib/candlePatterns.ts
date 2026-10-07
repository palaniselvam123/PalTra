/**
 * Candlestick pattern names for the chart's data table.
 *
 * Plain rules on a candle and the ones before it. Three-candle patterns win
 * over two-candle ones, and those over a single-candle shape. A candle that
 * matches none gets a plain size-and-colour name ("Bullish", "Long bearish").
 * The trend before the candle (closes over the last 5 bars) separates shapes
 * that look alike, such as a hammer (after a fall) from a hanging man
 * (after a rise). Labels only: the bot does not trade on them.
 */

export type Ohlc = { open: number; high: number; low: number; close: number };

/** The bias a pattern usually carries. */
export type PatternBias = "bullish" | "bearish" | "neutral";
export type Pattern = { name: string; bias: PatternBias };

type Shape = {
  range: number;
  body: number;
  upper: number;
  lower: number;
  bull: boolean;
  bear: boolean;
  mid: number;
};

function shape(c: Ohlc): Shape {
  const range = Math.max(c.high - c.low, 0);
  const body = Math.abs(c.close - c.open);
  return {
    range,
    body,
    upper: c.high - Math.max(c.open, c.close),
    lower: Math.min(c.open, c.close) - c.low,
    bull: c.close > c.open,
    bear: c.close < c.open,
    mid: (c.open + c.close) / 2,
  };
}

/** +1 rising, -1 falling, 0 flat: the last 5 closes before index `i`. */
function trendBefore(rows: Ohlc[], i: number): number {
  const from = Math.max(0, i - 5);
  if (i - from < 3) return 0;
  const first = rows[from].close;
  const last = rows[i - 1].close;
  const span = Math.max(...rows.slice(from, i).map((r) => r.high)) - Math.min(...rows.slice(from, i).map((r) => r.low));
  if (span <= 0) return 0;
  const move = (last - first) / span;
  return move > 0.3 ? 1 : move < -0.3 ? -1 : 0;
}

/** Typical body size of the bars before `i`, so "long" and "small" are relative to this stock. */
function averageBody(rows: Ohlc[], i: number): number {
  const from = Math.max(0, i - 10);
  const bodies = rows.slice(from, i).map((r) => Math.abs(r.close - r.open));
  if (!bodies.length) return Math.abs(rows[i].close - rows[i].open);
  return bodies.reduce((a, b) => a + b, 0) / bodies.length;
}

function single(c: Ohlc, s: Shape, avg: number, trend: number): Pattern | null {
  if (s.range <= 0) return { name: "Flat", bias: "neutral" };
  const bodyShare = s.body / s.range;
  if (bodyShare <= 0.1) {
    if (s.lower >= s.range * 0.6 && s.upper <= s.range * 0.1) return { name: "Dragonfly doji", bias: "bullish" };
    if (s.upper >= s.range * 0.6 && s.lower <= s.range * 0.1) return { name: "Gravestone doji", bias: "bearish" };
    if (s.upper >= s.range * 0.3 && s.lower >= s.range * 0.3) return { name: "Long-legged doji", bias: "neutral" };
    return { name: "Doji", bias: "neutral" };
  }
  if (s.lower >= s.body * 2 && s.upper <= s.body * 0.5) {
    return trend > 0 ? { name: "Hanging man", bias: "bearish" } : { name: "Hammer", bias: "bullish" };
  }
  if (s.upper >= s.body * 2 && s.lower <= s.body * 0.5) {
    return trend > 0 ? { name: "Shooting star", bias: "bearish" } : { name: "Inverted hammer", bias: "bullish" };
  }
  if (bodyShare >= 0.9 && s.body >= avg) {
    return s.bull ? { name: "Bullish marubozu", bias: "bullish" } : { name: "Bearish marubozu", bias: "bearish" };
  }
  if (bodyShare <= 0.3 && s.upper >= s.body && s.lower >= s.body) return { name: "Spinning top", bias: "neutral" };
  void c;
  return null;
}

function double(p: Ohlc, c: Ohlc, ps: Shape, s: Shape, trend: number): Pattern | null {
  const pTop = Math.max(p.open, p.close);
  const pBot = Math.min(p.open, p.close);
  const top = Math.max(c.open, c.close);
  const bot = Math.min(c.open, c.close);
  if (ps.bear && s.bull && bot <= pBot && top >= pTop && s.body > ps.body) return { name: "Bullish engulfing", bias: "bullish" };
  if (ps.bull && s.bear && bot <= pBot && top >= pTop && s.body > ps.body) return { name: "Bearish engulfing", bias: "bearish" };
  if (ps.bear && s.bull && top < pTop && bot > pBot && ps.body > 0 && s.body < ps.body * 0.6) {
    return { name: "Bullish harami", bias: "bullish" };
  }
  if (ps.bull && s.bear && top < pTop && bot > pBot && ps.body > 0 && s.body < ps.body * 0.6) {
    return { name: "Bearish harami", bias: "bearish" };
  }
  if (ps.bear && s.bull && c.open < p.close && c.close > ps.mid && c.close < p.open) return { name: "Piercing line", bias: "bullish" };
  if (ps.bull && s.bear && c.open > p.close && c.close < ps.mid && c.close > p.open) {
    return { name: "Dark cloud cover", bias: "bearish" };
  }
  const tick = Math.max(ps.range, s.range) * 0.05;
  if (trend < 0 && ps.bear && s.bull && Math.abs(p.low - c.low) <= tick) return { name: "Tweezer bottom", bias: "bullish" };
  if (trend > 0 && ps.bull && s.bear && Math.abs(p.high - c.high) <= tick) return { name: "Tweezer top", bias: "bearish" };
  return null;
}

function triple(a: Ohlc, b: Ohlc, c: Ohlc, as: Shape, bs: Shape, cs: Shape): Pattern | null {
  const smallMiddle = bs.body <= Math.min(as.body, cs.body) * 0.5;
  if (as.bear && smallMiddle && cs.bull && c.close > as.mid && as.body > 0) return { name: "Morning star", bias: "bullish" };
  if (as.bull && smallMiddle && cs.bear && c.close < as.mid && as.body > 0) return { name: "Evening star", bias: "bearish" };
  const rising = as.bull && bs.bull && cs.bull && b.close > a.close && c.close > b.close && b.open > a.open && c.open > b.open;
  const strong = (s: Shape) => s.range > 0 && s.body / s.range >= 0.6;
  if (rising && strong(as) && strong(bs) && strong(cs)) return { name: "Three white soldiers", bias: "bullish" };
  const falling = as.bear && bs.bear && cs.bear && b.close < a.close && c.close < b.close && b.open < a.open && c.open < b.open;
  if (falling && strong(as) && strong(bs) && strong(cs)) return { name: "Three black crows", bias: "bearish" };
  return null;
}

/** The pattern name of candle `i` in `rows`. */
export function candlePattern(rows: Ohlc[], i: number): Pattern {
  const c = rows[i];
  const s = shape(c);
  const trend = trendBefore(rows, i);
  if (i >= 2) {
    const hit = triple(rows[i - 2], rows[i - 1], c, shape(rows[i - 2]), shape(rows[i - 1]), s);
    if (hit) return hit;
  }
  if (i >= 1) {
    const hit = double(rows[i - 1], c, shape(rows[i - 1]), s, trend);
    if (hit) return hit;
  }
  const avg = averageBody(rows, i);
  const one = single(c, s, avg, trend);
  if (one) return one;
  const size = avg > 0 && s.body >= avg * 1.5 ? "Long " : avg > 0 && s.body <= avg * 0.5 ? "Small " : "";
  const word = s.bull ? "bullish" : s.bear ? "bearish" : "flat";
  const name = size ? `${size}${word}` : word.charAt(0).toUpperCase() + word.slice(1);
  return { name, bias: s.bull ? "bullish" : s.bear ? "bearish" : "neutral" };
}
