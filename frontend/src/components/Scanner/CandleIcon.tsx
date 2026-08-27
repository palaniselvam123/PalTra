"use client";

/** Draws the ACTUAL candle from its OHLC, not a generic per-pattern icon.
 *
 *  A stock "hammer" glyph would show a textbook shape regardless of what the
 *  bar really looked like — which is precisely the kind of prettified fiction
 *  the rest of this app avoids. Here the wick and body are drawn to true
 *  proportion, so a marginal hammer looks marginal.
 *
 *  Proportions are normalised to the candle's own high-low range, since an
 *  absolute rupee scale would render a ₹150 stock's bar invisible next to a
 *  ₹13,000 one. */
export function CandleIcon({
  ohlc,
  size = 26,
  title,
}: {
  ohlc: [number, number, number, number] | null;
  size?: number;
  title?: string;
}) {
  if (!ohlc) return null;
  const [open, high, low, close] = ohlc;
  const range = high - low;

  const W = size * 0.62;
  const H = size;
  const cx = W / 2;
  const pad = 1.5;
  const usable = H - pad * 2;

  // A zero-range bar (frozen feed, or a genuine no-trade bar) has no shape to
  // draw — show a flat line rather than dividing by zero.
  if (range <= 0) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title ?? "flat bar"}>
        {title && <title>{title}</title>}
        <line x1={1} y1={H / 2} x2={W - 1} y2={H / 2} stroke="currentColor" strokeWidth={1.5} opacity={0.45} />
      </svg>
    );
  }

  const y = (price: number) => pad + ((high - price) / range) * usable;

  const up = close >= open;
  const colour = up ? "#10b981" : "#f43f5e";
  const bodyTop = y(Math.max(open, close));
  const bodyBottom = y(Math.min(open, close));
  // A doji's body is zero-height; give it a visible hairline so the candle
  // does not disappear into its own wick.
  const bodyHeight = Math.max(bodyBottom - bodyTop, 1.2);

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title ?? "candle"}>
      {title && <title>{title}</title>}
      <line x1={cx} y1={y(high)} x2={cx} y2={y(low)} stroke={colour} strokeWidth={1.2} />
      <rect x={1} y={bodyTop} width={W - 2} height={bodyHeight} fill={colour} rx={0.5} />
    </svg>
  );
}
