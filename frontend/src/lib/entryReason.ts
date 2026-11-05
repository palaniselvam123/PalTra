/** Why a trade opened (TradeLog.entry_reason, strategy_engine.ENTRY_REASONS). */
const ENTRY_SHORT: Record<string, string> = {
  SMA_CROSS: "SMA cross",
  GAP_MODE: "Gap mode",
  JOIN_TREND: "Joined trend",
  ALWAYS_IN: "Always in",
  ALWAYS_IN_REVERSE: "Reversed after loss",
  LATE_ENTRY: "Late entry",
  EARLY_REVERSAL: "Early reversal",
  COUNTER_REENTRY: "Re-entry",
  CANDLE_PATTERN: "Candle pattern",
  CANDLE_COLOUR: "Candle colour",
  BREAKOUT_ARRIVAL: "Breakout",
  FORCE_ORDER: "Force order",
  NEWS: "News",
  HANDOFF: "Hand-off",
  SMA9_TURN: "SMA 9 turn",
  FAST_MOVE: "Fast mover",
  EXIT_REVERSE: "Reversed on exit",
  MANUAL_REVERSE: "Reversed by hand",
};

const ENTRY_LONG: Record<string, string> = {
  SMA_CROSS: "SMA fast crossed the slow SMA on a closed candle",
  GAP_MODE: "Gap mode: the SMA gap reached the entry level after the cross",
  JOIN_TREND: "Join the morning trend: the SMAs were already on this side",
  ALWAYS_IN: "Always in a trade: flat, so it entered on the side the SMAs point to (no cross needed)",
  ALWAYS_IN_REVERSE: "Always in a trade, reverse after a loss: the trade before lost money, so this one went the other way",
  LATE_ENTRY: "Late entry: a filter refused the cross, then agreed on a later candle",
  EARLY_REVERSAL: "Counter candles: a run against the SMA side before the cross",
  COUNTER_REENTRY: "Re-entry: a candle broke the counter-candle exit candle while the SMAs stayed on this side",
  CANDLE_PATTERN: "Candle pattern entry",
  CANDLE_COLOUR: "Ride the candle colour: a green candle buys, a red one sells",
  BREAKOUT_ARRIVAL: "Breakout handed over from Bot 2",
  FORCE_ORDER: "Force order pressed on the terminal",
  NEWS: "News entry: a strong, fresh headline (good news buys, bad news shorts), no SMA cross or pattern read",
  HANDOFF: "Handed over from another bot: ordered at once, the opposite of that bot's closed trade",
  SMA9_TURN: "SMA 9 turn: the fast SMA made a sharp V (or a rounded U), ordered before the cross",
  FAST_MOVE: "Fast mover: moved from another bot while running its way, ordered at once at this bot's size",
  EXIT_REVERSE: "Reverse on exit: a V-turn, turn or counter-candle exit closed the other side and this one opened at once",
  MANUAL_REVERSE: "Reverse button: the open trade was closed and this side opened at once",
};

/** How a waiting limit was filled (the part after ":"). */
const FILL: Record<string, [string, string]> = {
  EXTREME_LIMIT: ["limit", "filled at the signal candle's extreme limit"],
  PULLBACK: ["pullback", "filled when the price came back to the pullback level"],
  CANDLE_CONFIRM: ["candle confirmed", "filled once the entry candle went the trade's way (past its open)"],
};

/** A backtest trade played as another bot after a hand-off ends "@B2" / "@B3" (handoff_chain.tag). */
function playedAs(code: string): [string, number | null] {
  const m = /^(.*)@B(\d)$/.exec(code);
  return m ? [m[1], Number(m[2])] : [code, null];
}

/** The bot a backtest trade was played as after a hand-off ("…@B2" → 2), else null. */
export function entryPlayedAs(code: string | null | undefined): number | null {
  return code ? playedAs(code)[1] : null;
}

/** Short label, e.g. "SMA cross · limit"; null for older trades with no reason saved. */
export function entryReasonShort(code: string | null | undefined): string | null {
  if (!code) return null;
  const [plain, bot] = playedAs(code);
  const [base, how] = plain.split(":");
  const label = ENTRY_SHORT[base] ?? base.replace(/_/g, " ").toLowerCase();
  const text = how ? `${label} · ${FILL[how]?.[0] ?? how.toLowerCase()}` : label;
  return bot ? `${text} · as Bot ${bot}` : text;
}

/** Full sentence for a tooltip. */
export function entryReasonLong(code: string | null | undefined): string | null {
  if (!code) return null;
  const [plain, bot] = playedAs(code);
  const [base, how] = plain.split(":");
  const text = ENTRY_LONG[base] ?? base.replace(/_/g, " ").toLowerCase();
  const out = how ? `${text}; ${FILL[how]?.[1] ?? how.toLowerCase()}` : text;
  return bot ? `${out}. Played as Bot ${bot} in this backtest: the stock had been handed to Bot ${bot}, so its settings were used` : out;
}
