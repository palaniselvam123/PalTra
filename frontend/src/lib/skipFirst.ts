/** "Skip today's first candles" (backend chop_scan `skip_candles`): the Trend quality card and the pick backtests
 * leave today's first N candles out of the trend read. 0 = none; the server allows up to 12. */
export const SKIP_FIRST_CHOICES = [0, 1, 2, 3, 4, 5, 6, 12] as const;

export const skipFirstLabel = (n: number, minutes: number) => (n === 0 ? "None" : `First ${n} (${n * minutes} min)`);

export const SKIP_FIRST_HELP =
  "Leave today's first N candles out of the trend read: crosses, phases, the strong-trend checks and VWAP start after them, and a strong trend must also have moved 1 ATR its way since then. For stocks whose big opening candles made a sideways day look like a trend.";
