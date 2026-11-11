/** Client for the SMA + ATR terminal.

Locally that is `uvicorn main:app --port 8001`. On the deployed site the same
routes are mounted at `/sma` on this host, so the browser must not call
127.0.0.1.
*/

import { runBeforeCharges, tradeBeforeCharges } from "./pnlBasis";
import { onSignedOut } from "./session";

function resolveSmaApi(): string {
  const fromEnv = process.env.NEXT_PUBLIC_SMA_API_URL;
  if (fromEnv) return fromEnv.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host !== "localhost" && host !== "127.0.0.1") {
      return `${window.location.origin}/sma`;
    }
  }
  return "http://127.0.0.1:8001";
}

export const SMA_API = resolveSmaApi();

export type StopType = "ATR" | "SMA_GAP" | "TSL" | "SUPERTREND" | "VWAP";

export type SmaConfig = {
  /** This bot's name, e.g. "Scalper". */
  bot_name?: string | null;
  /** "SMA" (cross, default), "PATTERN" (candle-pattern entries closed at the candle's end),
   * "COLOUR" (ride the candle colour: green buys, red sells, reverse on the other colour) or
   * "NTH_CANDLE" (the day's Nth candle colour, once a day: green buys, red sells, then the stop /
   * target / exits run the trade; backend nth_candle.py). */
  entry_mode?: EntryMode;
  /** Second candle entries: which candle of the day (1-6), its size in minutes (the bot's own candle in
   * that mode), the smallest body in % of the close that orders (0 = any), the least volume vs usual (0 = off). */
  nth_candle_no?: number;
  nth_candle_minutes?: NthCandleMinutes;
  nth_min_body_pct?: number;
  nth_min_rvol?: number;
  /** Second candle entry: which way (that candle / most of candles 1..N / that candle when most agree). */
  nth_side_rule?: "NTH" | "MAJORITY" | "AGREE";
  nth_majority_by?: "COUNT" | "SIZE";
  /** What makes a candle up / down: COLOUR green / red, CLOSES close vs the previous close, BOTH. */
  nth_dir_rule?: CandleDirRule;
  /** Colours on smaller candles (backend small_colour.py). */
  sc_minutes?: string;
  sc_count?: number;
  sc_join?: "ALL" | "ANY" | "AT_LEAST";
  sc_need?: number;
  sc_entry?: boolean;
  sc_confirm?: boolean;
  /** Second candle entry: enter at once without the entry checks. */
  nth_skip_checks?: boolean;
  /** Pattern / colour candle size in minutes. */
  pattern_tf?: 1 | 3 | 5 | 10 | 15 | 30 | 60;
  /** Candle colour: candles of one colour in a row needed to enter or turn (1-5). */
  colour_count?: number;
  /** Candle colour: smallest body in % of the price that counts as a colour (0 = any). */
  colour_min_body?: number;
  /** Smallest body set in % of price (per candle size) or in ATRs of the colour candles. */
  colour_body_unit?: "PCT" | "ATR";
  /** Smallest body % per colour candle size, e.g. "5:0.05,15:0.15"; other sizes use colour_min_body. */
  colour_body_by_tf?: string;
  /** Each candle of the colour run's own smallest body, oldest first, in colour_body_unit ("0.1,0.5"; "" = off). */
  colour_body_seq?: string;
  /** Inside candles agree (candle_patterns.inside_colour): size in minutes, 0 = off. */
  colour_inside_minutes?: 0 | 1 | 2 | 3 | 5 | 10;
  /** Smallest body (% of price) for a colour candle whose inside candles all agree; 0 = any. */
  colour_inside_body?: number;
  /** Colour entries wait for a pullback inside the confirming candle (backend colour_pull.py). */
  colour_pull?: "OFF" | "HALF" | "LOW" | "SMA9";
  /** Colour candles to wait for the pullback (1-3). */
  colour_pull_candles?: number;
  /** No pullback within the wait: buy / sell at the market. */
  colour_pull_market?: boolean;
  /** Smallest body in ATRs of the colour candles (0 = any), with colour_body_unit ATR. */
  colour_body_atr?: number;
  /** Candle interval in minutes the SMA strategy trades on (built from the 1-minute tape). */
  candle_minutes?: CandleMinutes;
  /** Mixed candles: the SMA cross exit read on candles of this size (0 = the entry candle). */
  exit_candle_minutes?: number;
  /** Watch smaller candles while in a trade (small_candles.py): CSV of sizes ("1,3,5"; "" = off) and its rules. */
  small_exit_minutes?: string;
  small_exit_cross?: boolean;
  small_exit_slope?: boolean;
  small_exit_counter?: number;
  small_exit_loss_pct?: number;
  small_exit_agree?: number;
  /** Patterns only with the SMA trend. */
  pattern_trend?: boolean;
  pattern_set?: "STRONG" | "ALL";
  /** Skip candles whose usual range is under this many times the round-trip charges (0 = off). */
  pattern_min_edge?: number;
  symbol: string;
  trade_symbols?: string[];
  /** Where each armed stock came from, keyed by symbol (information only). */
  trade_sources?: Record<string, string>;
  exchange: string;
  qty: number;
  sma_fast: number;
  /** Optional third SMA between fast and slow. null / undefined = standard two-SMA mode. */
  sma_medium?: number | null;
  sma_slow: number;
  atr_period: number;
  atr_multiplier: number;
  use_adx_filter: boolean;
  use_stop?: boolean;
  /**
   * ATR = fixed stop at atr_multiplier × ATR. SMA_GAP = moving stop and target (PAPER only).
   * TSL = trailing stop in ₹ steps, like Groww (PAPER and LIVE).
   */
  stop_type?: StopType;
  /** TSL: stop ₹ from entry, trail step ₹, target ₹ (0 = none). */
  tsl_sl_points?: number;
  tsl_trail_points?: number;
  tsl_target_points?: number;
  /** Units for the TSL settings: POINTS (₹) or PERCENT (of entry price). Defaults to POINTS. */
  tsl_mode?: "POINTS" | "PERCENT" | "ATR";
  tsl_sl_pct?: number;
  tsl_trail_pct?: number;
  tsl_target_pct?: number;
  /** ATR multiples, read when tsl_mode is "ATR": the stop / trail / target sized from the stock's own ATR at entry. */
  tsl_sl_atr?: number;
  tsl_trail_atr?: number;
  tsl_target_atr?: number;
  /** VWAP + ATR buffer stop (stop_type "VWAP"): stop = VWAP ∓ this × ATR, tighter only each candle. */
  vwap_stop_atr?: number;
  gap_sl_mult?: number;
  gap_tp_mult?: number;
  gap_min_pct?: number;
  adx_threshold: number;
  use_vwap?: boolean;
  use_volume?: boolean;
  volume_min_ratio?: number;
  use_density?: boolean;
  density_min_pct?: number;
  use_rsi?: boolean;
  rsi_long_min?: number;
  rsi_long_max?: number;
  rsi_short_min?: number;
  rsi_short_max?: number;
  /** Bollinger entry filter: skip a stretched cross or a squeeze. */
  use_bollinger?: boolean;
  bb_period?: number;
  bb_std?: number;
  /** Bands narrower than this % of price count as a squeeze. 0 = check off. */
  bb_min_width_pct?: number;
  /** Bollinger exit: band target, middle-band fade, both, or off. */
  bb_exit?: BbExit;
  /** SMA gap range entry filter, signed % ((SMA9 − SMA21) / SMA21 × 100); each side ticked separately. */
  use_gap_long?: boolean;
  gap_long_min?: number;
  gap_long_max?: number;
  use_gap_short?: boolean;
  gap_short_min?: number;
  gap_short_max?: number;
  /** Candle direction filter: the last N closed candles move the trade's way. */
  use_candle_dir?: boolean;
  candle_dir_count?: number;
  /** CLOSES: each close beyond the last; COLOUR: green for a buy, red for a sell; BOTH. */
  candle_dir_rule?: "CLOSES" | "COLOUR" | "BOTH";
  /** SMA gap mode: enter on a widening gap, exit when it fades (gap_mode.py). */
  use_gap_mode?: boolean;
  gap_entry_long?: number;
  gap_exit_long?: number;
  gap_entry_short?: number;
  gap_exit_short?: number;
  gap_giveback_pct?: number;
  /** Fade exit only on a close beyond the slow SMA (rides out a pullback). */
  gap_fade_confirm_sma?: boolean;
  /** Fade exit only after the gap narrowed this many candles in a row (0 = off). */
  gap_fade_min_candles?: number;
  /** Judge the fade on the live price about once a second, not only on closed candles. */
  gap_fade_intrabar?: boolean;
  /** Judge the fade on each closed 1-minute candle inside a longer bot candle (replays too). */
  gap_fade_minute?: boolean;
  /** SMA cross exit (on unless false): an opposite cross closes the trade. Off: other exits or square-off. */
  cross_exit?: boolean;
  /** Late entry: minutes a filter-refused cross stays armed (0 = off). */
  late_entry_min?: number;
  /** Join the morning trend once a day, join_after_min minutes after 09:15. */
  join_trend?: boolean;
  /** Always in a trade (always_in.py): a flat armed stock is entered at once on the SMAs' side, again after every exit. */
  always_in?: boolean;
  /** With always_in: after a losing trade the next entry goes the opposite way. */
  always_in_reverse?: boolean;
  join_after_min?: number;
  /** Counter candles: close after N closed candles against the trade (0 = off). */
  counter_exit_count?: number;
  /** Enter early when N candles run against the side the SMAs are on (once per SMA run). */
  counter_entry?: boolean;
  counter_rule?: "CLOSES" | "COLOUR" | "BOTH";
  /** The last counter candle must close past the fast SMA (on unless false). */
  counter_need_fast?: boolean;
  /** Exit only: each counter candle's body |close − open| must be at least this × its ATR (0 = any size). */
  counter_min_body?: number;
  /** After a counter-candle exit, re-enter when a later candle breaks the exit candle the trade's way. */
  counter_reenter?: boolean;
  /** Most re-entries per SMA run (1-10, default 3). */
  counter_reenter_max?: number;
  /** EMA cross check: entry only on the fast/slow EMA side; exit on an EMA cross against the trade. */
  use_ema?: boolean;
  ema_exit?: boolean;
  ema_fast?: number;
  ema_slow?: number;
  /** MACD check (macd.py): entry only on the MACD / signal side; exit on a MACD cross against the trade. */
  use_macd?: boolean;
  macd_exit?: boolean;
  macd_fast?: number;
  macd_slow?: number;
  macd_signal?: number;
  /** Cumulative delta (cum_delta.py): entry only while it moves the trade's way; exit on a price / delta divergence. */
  use_cvd?: boolean;
  cvd_lookback?: number;
  cvd_div_exit?: boolean;
  cvd_div_lookback?: number;
  /** Reverse instead of only closing (reverse_exit.py): CSV of VTURN / TURN / CANDLES / SMALL, "" = off. */
  reverse_on_exit?: string;
  reverse_colour_exit?: boolean;
  /** Reversed trades' own entry checks / exits / stop (backend reverse_exit.FIELDS); {} = the bot's own. */
  reverse_settings?: Record<string, string | number | boolean>;
  /** How exits combine (backend exit_join.py): OR = any one closes; AND = the ticked group must agree. */
  exit_join?: "OR" | "AND";
  exit_group?: string;
  exit_need?: number | string;
  exit_within?: number | string;
  /** Heikin Ashi colours (heikin_ashi.py): the candle-colour rules read Heikin Ashi candles. */
  ha_colours?: boolean;
  /** Supertrend: entry only on its side; exit when it flips against the trade. */
  use_supertrend?: boolean;
  /** Price level check (level_check.py): enter only beyond a Bollinger band or a pivot level. */
  use_level?: boolean;
  level_line?: "BB_MIDDLE" | "BB_UPPER" | "PIVOT_P" | "PIVOT_R1";
  /** 52-week high check (high52.py): a buy only on a close at the 52-week high (a sell at the low), from Groww daily candles. */
  use_high52?: boolean;
  high52_side?: "LONG" | "SHORT" | "BOTH";
  /** 0 = the close must reach the level; N = within N % of it counts. */
  high52_pct?: number;
  st_exit?: boolean;
  st_period?: number;
  st_mult?: number;
  /** Tall-candle pullback: after a tall signal candle, wait for the price to come back before entering. */
  tall_pullback?: boolean;
  tall_atr_mult?: number;
  tall_expiry_candles?: number;
  extreme_entry?: boolean;
  extreme_anchor?: "CANDLE_EXTREME" | "SMA_9";
  extreme_invalid_pct?: number;
  extreme_expiry_candles?: number;
  extreme_cancel_on_cross?: boolean;
  /** Entry-candle check: the order waits until its own candle is past its open the trade's way (entry_candle.py). */
  entry_candle_confirm?: boolean;
  entry_candle_pct?: number;
  entry_candle_max?: number;
  handoff_candle?: "OFF" | "WAIT" | "FOLLOW" | string;
  /** Day high/low guard: a buy near today's high (a sell near its low) waits for a pullback (day_extreme.py). */
  day_extreme_guard?: boolean;
  day_extreme_pct?: number;
  day_extreme_pull_pct?: number;
  day_extreme_candles?: number;
  slope_exit?: boolean;
  slope_lookback?: number;
  /** Slope candle in minutes (slope_exit.py); 0 = the bot's own candle. */
  slope_minutes?: number;
  slope_bend_pct?: number;
  slope_turn?: boolean;
  slope_turn_pct?: number;
  /** SMA 9 V-turn exit: a steep slope with the trade, then a steep one against it within N candles. */
  vturn_exit?: boolean;
  /** SMA 9 turn (turn_entry.py): a Telegram, and / or an order at once, on the candle the fast SMA makes a V. */
  turn_alert?: boolean;
  turn_entry?: boolean;
  /** Also count a rounded U turn: a run of N candles one way, then the slope at least uturn_min_pct the other way. */
  uturn_on?: boolean;
  /** Turn entry: only turns toward the 9/21 cross open trades; a turn closes a trade on a candle of the other colour only. */
  turn_toward_cross?: boolean;
  turn_exit_candle?: boolean;
  /** A trade opened by an SMA 9 turn closes on the first candle of the other colour. */
  turn_colour_exit?: boolean;
  uturn_run?: number;
  uturn_min_pct?: number;
  vturn_move_pct?: number;
  vturn_turn_pct?: number;
  vturn_within?: number;
  /** After a V-turn exit at a loss, move the stock to Bot 2 (flipped, gap mode). */
  vturn_handoff?: boolean;
  /** After a stop-loss exit, move the stock: back to the bot it came from, Bot 2 or Bot 3 (stop_handoff.py). */
  stop_handoff?: "OFF" | "BACK" | "BOT2" | "BOT3";
  /** ... only when that stop closed at a loss. */
  stop_handoff_loss?: boolean;
  /** After a hand-off to Bot 2, Bot 2 orders at once the opposite way of the closed trade (no filters). */
  handoff_now?: boolean;
  /** Press Start by itself while STOPPED in PAPER and NSE is open (auto_start.py). Bots 1-4 only. */
  auto_start?: boolean;
  /** Book the profit at a daily pivot level: R1 (S1 for a short; R2 / S2 when the entry is already past it) or R2. */
  pivot_target?: "OFF" | "R1" | "R2";
  /** Staged stop plan (stop_plan.py): breakeven at +plan_be_r R, Supertrend trail from +plan_trail_r R. */
  stop_plan?: boolean;
  /** SMA 9 deceleration guard (decel_guard.py): slope in ATR per candle. */
  decel_exit?: boolean;
  decel_lookback?: number;
  decel_arm_atr?: number;
  decel_drop_pct?: number;
  decel_min_atr?: number;
  decel_candles?: number;
  decel_confirm?: "OFF" | "CLOSE";
  decel_pierce_atr?: number;
  /** Where the peak slope is counted from: the entry (default) or the start of the current run. */
  decel_peak_from?: "ENTRY" | "RUN";
  plan_be_r?: number;
  plan_trail_r?: number;
  /** Structural exit, recovery hold and the Bot 2 handoff (recovery.py). */
  slow_sma_exit?: boolean;
  recovery_hold?: boolean;
  recovery_tolerance_pct?: number;
  recovery_emergency_mult?: number;
  recovery_atr_mult?: number;
  recovery_max_candles?: number;
  recovery_handoff?: boolean;
  /** Trend breakout: cut the trade and hand the stock back to Bot 3 (breakout.py). */
  breakout_handoff?: boolean;
  breakout_atr_mult?: number;
  breakout_gap_pct?: number;
  breakout_volume_mult?: number;
  /** CANDLE: one tall candle; STRONG: the 7-check strong trend (breakout.py). */
  breakout_rule?: "CANDLE" | "STRONG";
  /** Close an open trade against the breakout at once; off = it runs to its own exits, then the stock moves. */
  breakout_cut?: boolean;
  breakout_when?: "ANY" | "IN_PROFIT" | string;
  breakout_min_profit_pct?: number;
  breakout_min_profit_rs?: number;
  chop_losses?: number;
  /** The strong-trend rule's checks ("stack:must,vwap:use,run:off,…") and how many must pass (1-7). */
  breakout_trend_checks?: string;
  breakout_trend_need?: number;
  /** Opening range (09:15–09:30), each check its own tick box; all ticked must agree. */
  use_or_volume?: boolean;
  use_or_vwap?: boolean;
  use_or_spread?: boolean;
  use_or_break?: boolean;
  or_volume_ratio?: number;
  or_spread_pct?: number;
  /** 1-minute human review (off by default): ask EXIT or WAIT when an open trade's SMA gap narrows into this band. */
  review_on?: boolean;
  /** One or more check candle sizes (minutes). The smart review reads each in turn. */
  review_check_minutes?: number[] | number;
  review_check_mode?: "ALWAYS" | "ONLY_IF_AGAINST" | "ONLY_IF_NOT_WITH";
  review_default_answer?: "PROMPT" | "EXIT" | "REVERSE" | "CONTINUE";
  /** What counts as uncertain: CSV of GAP (gap narrowing in the band), SLOPE (fast SMA flattened), STEEP (fast SMA running against). */
  review_conditions?: string;
  /** OR: any ticked condition raises the review; AND: all of them. */
  review_join?: "OR" | "AND";
  /** Closed candles the fast SMA's slope is read over. */
  review_slope_candles?: number;
  /** SLOPE fires when the slope with the trade is at or under this (% per candle). */
  review_slope_flat?: number;
  /** STEEP fires when the slope against the trade is at least this (% per candle). */
  review_steep_pct?: number;
  /** The band, in % of the slow SMA, on the bot's own candle. */
  review_gap_pct?: number;
  /** No second review on the same trade within this many minutes. */
  review_cooldown_min?: number;
  /** Flip strategy: a buy signal sells, a sell signal buys. */
  flip_orders?: boolean;
  gap_entry_delay_min?: number;
  gap_entry_window_min?: number;
  max_daily_loss: number;
  max_trades_per_day: number;
  /** Entries one stock may take per day on this bot (0 = no cap); also a per-stock override. */
  max_trades_per_stock?: number;
  square_off_time: string;
  /** HH:MM. No new entries from this time. */
  entry_cutoff_time?: string;
  /** RESEARCH on the research desk (paper only, its own book). */
  trading_mode: "PAPER" | "LIVE" | "RESEARCH";
  /** Each stock's own strategy settings over the shared ones: {TCS: {qty: 50}}. */
  stock_settings?: Record<string, Partial<SmaConfig>>;
  /** The fields a stock may set for itself. */
  stock_fields?: string[];
};

/** One stock's settings as the bot uses them, plus the ones it sets itself. */
export type StockConfig = SmaConfig & { own: Partial<SmaConfig> };

export type ChargeBreakdown = {
  brokerage: number;
  stt: number;
  exchange_charge: number;
  sebi_fee: number;
  stamp_duty: number;
  gst: number;
  total_charges?: number;
};

export type CrossScanMinutes = 1 | 2 | 3 | 5 | 10 | 15;

/** One stock whose SMA fast / slow are about to cross (or just did) on the scan's candle. */
export type CrossScanRow = {
  symbol: string;
  state: "APPROACHING" | "CROSSED";
  /** BULLISH: the fast SMA is (or will be) above the slow one. */
  side: "BULLISH" | "BEARISH";
  ltp: number | null;
  sma_fast: number;
  sma_slow: number;
  /** (fast − slow) / slow × 100 on the last closed candle. */
  gap_pct: number;
  /** Gap change per candle. */
  slope_pct: number;
  candles_to_cross: number | null;
  minutes_to_cross: number | null;
  /** CROSSED only: 0 = on the last closed candle. */
  crossed_candles_ago: number | null;
  candle_ts: number;
  /** Shares traded today so far (Groww's running total at the last 1-minute candle); null when not reported. */
  volume?: number | null;
  /** Shares traded in the last `window_min` minutes; null when the running total reset. */
  volume_window?: number | null;
  /** Price change over the last `window_min` minutes, signed %. */
  move_pct?: number | null;
  /** `move_pct` per minute, signed. */
  speed_pct_per_min?: number | null;
  window_min?: number;
};

export type CrossScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  minutes: CrossScanMinutes;
  sma_fast: number;
  sma_slow: number;
  as_of: string | null;
  /** When the last pass ended (IST); older servers leave it out. */
  finished_at?: string | null;
  market_open: boolean;
  rows: CrossScanRow[];
};

/** The 09:30 morning checklist (morning_scan.py): five opening checks per stock — information only. */
export type MorningCheck = "orb" | "rvol" | "vwap" | "gap" | "candles";
export type MorningScanRow = {
  symbol: string;
  /** The 09:15-09:30 window has closed. */
  ready: boolean;
  side: "LONG" | "SHORT" | null;
  /** Checks passed on `side`, 0-5. */
  score: number;
  runner: boolean;
  checks: Partial<Record<MorningCheck, boolean>>;
  notes: Partial<Record<MorningCheck, string>>;
  long_score: number;
  short_score: number;
  or_high: number | null;
  or_low: number | null;
  price: number | null;
  price_ts: number | null;
  rvol: number | null;
  rvol_sessions: number;
  open_volume: number | null;
  gap_pct: number | null;
  gap_ts: number | null;
  vwap: number | null;
  greens: number;
  reds: number;
  ltp: number | null;
  day_move_pct: number | null;
};
export type MorningScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  minutes: number;
  sma_fast: number;
  sma_slow: number;
  as_of: string | null;
  finished_at?: string | null;
  market_open: boolean;
  rules?: { rvol_min: number; gap_min_pct: number; runner_min: number; sessions: number };
  rows: MorningScanRow[];
  paused?: boolean;
};

/** The 52-week high scan (high52.py): each stock's distance from its 52-week high / low — information only. */
export type High52ScanRow = {
  symbol: string;
  ltp: number | null;
  high52: number | null;
  high52_date: string | null;
  low52: number | null;
  low52_date: string | null;
  /** How far under the 52-week high, % (0 = at it, negative = above it today). */
  dist_high_pct: number | null;
  dist_low_pct: number | null;
  broke_high: boolean;
  broke_low: boolean;
  days_since_high: number | null;
  day_move_pct: number | null;
  year_move_pct: number | null;
  sessions: number;
  ts: number | null;
};
export type High52Scan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  weeks: number;
  as_of: string | null;
  finished_at?: string | null;
  market_open: boolean;
  rows: High52ScanRow[];
  paused?: boolean;
};

/** How choppy or trending a stock has looked today — information only, never traded on. */
export type ChopScore = "TRENDING" | "MIXED" | "CHOPPY";
export type ChopScanRow = {
  symbol: string;
  minutes: CrossScanMinutes;
  crosses_today: number;
  minutes_since_last_cross: number | null;
  avg_minutes_between: number | null;
  avg_move_pct: number | null;
  last_cross_direction: "BULLISH" | "BEARISH" | null;
  score: ChopScore;
  /** Had crosses earlier today and has been quiet for ≥ quiet_min minutes — possible strong trend forming. */
  quiet_runner: boolean;
  /** When a scan first read this stock as quiet today, and when its Telegram went (IST; older servers leave them out). */
  first_quiet_at?: string | null;
  alerted_at?: string | null;
  ltp: number | null;
  candle_ts: number;
  volume?: number | null;
  volume_window?: number | null;
  move_pct?: number | null;
  speed_pct_per_min?: number | null;
  window_min?: number;
  /** Strong one-way trend (stacked SMAs + VWAP, sloping, fanned, holding, no recent cross). */
  trend?: ChopTrend | null;
  trend_side?: "BULLISH" | "BEARISH" | null;
  /** Share of the seven trend checks that passed, 0-100. */
  trend_score?: number;
  trend_checks?: Partial<Record<ChopTrendCheck, boolean>>;
  /** MORNING before 10:00 IST (today's candles), MIDDAY from 10:00. */
  trend_mode?: "MORNING" | "MIDDAY";
  /** The server's one-line reading of each check (names the morning / midday rule used). */
  trend_notes?: Partial<Record<ChopTrendCheck, string>>;
  /** Morning volume since 09:15 against the usual for the same minutes (x), and the earlier sessions used. */
  rvol?: number | null;
  trend_days?: number;
  sma_gap_pct?: number | null;
  vwap?: number | null;
  /** Last close vs today's first open, %. */
  day_move_pct?: number | null;
  /** Today's closed-candle SMA crosses per candle size in minutes (null = not enough candles yet). */
  crosses_by_minutes?: Record<string, number | null>;
  /** Side of the fast SMA per candle size. */
  side_by_minutes?: Record<string, "BULLISH" | "BEARISH" | null>;
  /** Start of today's last cross candle per size (epoch seconds), and that cross's direction. */
  last_cross_ts_by_minutes?: Record<string, number | null>;
  last_cross_side_by_minutes?: Record<string, "BULLISH" | "BEARISH" | null>;
  /** Strong-trend read on every candle size (6 of 7 checks): STRONG_BULLISH / STRONG_BEARISH, or null. */
  trend_by_minutes?: Record<string, "STRONG_BULLISH" | "STRONG_BEARISH" | null>;
  /** How long the current SMA side has run per size (older servers leave them out): start of its first candle
   * (epoch seconds), closed candles in the run, the signed SMA gap % now and the widest |gap| % in the run. */
  run_since_ts_by_minutes?: Record<string, number | null>;
  run_candles_by_minutes?: Record<string, number | null>;
  gap_pct_by_minutes?: Record<string, number | null>;
  gap_peak_pct_by_minutes?: Record<string, number | null>;
  /** Today's trend phases on the scan's candle (older servers leave them out): the phase now and every phase, oldest first. */
  phase?: ChopPhaseSide | null;
  phase_since_ts?: number | null;
  phase_move_pct?: number | null;
  phase_candles?: number;
  phases?: ChopPhase[];
  strong_phases?: number;
};
/** Trend-rank alert (backend trend_rank.py): Telegram / call the top ranks after each Trend quality pass. */
export type TrendAlerts = {
  on: boolean;
  sizes: number[];
  direction: "UP" | "DOWN" | "SAME";
  kind: "SIDE" | "STRONG";
  top_n: number;
  max_age_min: number;
  min_gap_pct: number;
  skip_fading: boolean;
  telegram: boolean;
  call: boolean;
  recent: { at: string; symbol: string; side: "BULLISH" | "BEARISH"; rank: number; age_min: number; gap_pct: number }[];
};
/** A Scan & arm preset (scan_arm.py): a Trend quality scan, then the matches armed on one bot, now or at set IST times. */
export type ScanArmPreset = {
  id: string;
  name: string;
  bot: 1 | 2 | 3 | 4 | 5;
  lists: ("armed" | "fno" | "nifty50" | "nifty100" | "niftybank")[];
  minutes: number;
  sizes: number[];
  direction: "UP" | "DOWN" | "SAME";
  kind: "SIDE" | "STRONG";
  max_age_min: number;
  min_gap_pct: number;
  skip_fading: boolean;
  top_n: number;
  times: string[];
  on: boolean;
  fired?: Record<string, string>;
  last?: ScanArmRun | null;
  rule?: string;
};
export type ScanArmRun = {
  at: string;
  finished_at?: string;
  trigger: "manual" | "schedule" | "mcp";
  time: string | null;
  preset: string;
  name: string;
  bot: number;
  bot_name?: string;
  mode?: "PAPER" | "LIVE";
  rule: string;
  scanned: number;
  matched: { symbol: string; side: "BULLISH" | "BEARISH"; rank: number; age_min: number; gap_pct: number; ltp: number | null }[];
  armed: string[];
  already: string[];
  skipped: { symbol: string; why: string }[];
  error: string | null;
};
export type ScanArmState = {
  presets: ScanArmPreset[];
  running: { name: string | null; stage: string | null } | null;
  recent: ScanArmRun[];
  scans_enabled: boolean;
  paused: boolean;
  /** True on the scanner / backtest server: the bots armed are the main desk's, over the link. */
  arms_desk: boolean;
  grace_min: number;
};
export type ChopPhaseSide = "BULL" | "BEAR" | "CHOP";
export type ChopPhase = { side: ChopPhaseSide; start_ts: number; end_ts: number; candles: number; move_pct: number | null };
export type ChopTrend = "STRONG_BULLISH" | "STRONG_BEARISH";
export type ChopTrendCheck = "stack" | "vwap" | "slope" | "fan" | "hold" | "structure" | "run" | "volume";

export type ChopScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  minutes: CrossScanMinutes;
  sma_fast: number;
  sma_slow: number;
  quiet_min: number;
  as_of: string | null;
  /** When the last pass ended (IST); older servers leave it out. */
  finished_at?: string | null;
  market_open: boolean;
  rows: ChopScanRow[];
  /** The server's own re-scan (older servers leave it out). */
  auto?: ChopAuto;
};

/** One minute of a desk's open P&L (the Stocks bar's "Open" figure), from the open P&L log. */
/** What makes a candle up / down (backend nth_candle.DIR_RULES): its colour, its close vs the previous close, or both. */
export type CandleDirRule = "COLOUR" | "CLOSES" | "BOTH";
/** First candles (backend first_candles.py): the day's first N candles and the Second candle call. */
export type FirstCandleRule = {
  minutes: number;
  candle_no: number;
  side_rule: SecondCandleSideRule;
  majority_by: "COUNT" | "SIZE";
  min_body_pct: number;
  dir_rule: CandleDirRule;
  /** Read Heikin Ashi candles (colour / body); prices stay real. */
  ha: boolean;
  /** Also the last N closed candles before the read time (0 = off). */
  last_n: number;
};
export type FirstCandleRow = {
  symbol: string;
  day: string;
  /** G green / R red / D doji, candle 1 first. */
  colours: ("G" | "R" | "D")[];
  /** Up / down / neither by the rule's dir_rule, candle 1 first. */
  dirs?: ("U" | "D" | "0")[];
  dir_rule?: CandleDirRule;
  ha?: boolean;
  /** Each candle as drawn: [open, high, low, close] (Heikin Ashi when `ha`). */
  ohlc?: [number, number, number, number][];
  /** The last N closed candles before the read time, as drawn, and when the last one closed (HH:MM). */
  last_ohlc?: [number, number, number, number][];
  last_at?: string | null;
  bodies: number[];
  closed: number;
  greens?: number;
  reds?: number;
  net_body?: number;
  call: "LONG" | "SHORT" | null;
  why: string | null;
  matched: number | null;
  entry_at?: string;
  last?: number | null;
};
export type FirstCandleScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  as_of: string | null;
  finished_at: string | null;
  rule: Partial<FirstCandleRule>;
  rows: FirstCandleRow[];
  paused?: boolean;
};

export type OpenPnlStock = { symbol: string; side: string; qty: number; entry: number | null; ltp: number | null; gross: number; net: number };
/** One minute of the P&L log: open (gross / net), today's closed P&L, and the day total (closed + open). */
export type OpenPnlPoint = {
  t: string;
  mode: string;
  gross: number;
  net: number;
  closed_gross?: number;
  closed_net?: number;
  total_gross?: number;
  total_net?: number;
  held: number;
  stocks: OpenPnlStock[];
};
export type OpenPnlLog = {
  desk: string;
  day: string | null;
  days: { day: string; points: number; held_points: number }[];
  points: OpenPnlPoint[];
  /** High / low / last of the day's P&L (closed + open, before charges; `basis` "total_gross"; older servers: open only). */
  summary: { high: number; high_at: string; low: number; low_at: string; last: number; last_at: string; basis?: string } | null;
  every_sec: number;
  keep_days: number;
  /** Each stock's day P&L at every logged minute: its closed trades so far plus its open P&L (before charges). */
  by_stock?: Record<string, OpenPnlStockPoint[]>;
};
export type FastMoveSettings = {
  on: boolean;
  pct: number;
  minutes: number;
  scan_on: boolean;
  scan_speed: number;
  cooldown_min: number;
  call: boolean;
  recent?: {
    at: string;
    source: "armed" | "scan";
    symbol: string;
    move_pct?: number | null;
    speed?: number;
    price?: number | null;
    from?: number;
    secs?: number;
    minutes?: number | null;
  }[];
  watches_armed?: boolean;
  scans_enabled?: boolean;
};
/** Fast-mover hand-off to Bot 5 (fast_bot.py). */
export type FastBotRule = "MOVE" | "SPEED" | "EITHER";
export type FastBotSettings = {
  on: boolean;
  rule: FastBotRule;
  pct: number;
  minutes: number;
  speed: number;
  speed_minutes: number;
  sources: number[];
  max_day: number;
  /** Only a trade already this much in profit moves (% of its entry); 0 = any trade. */
  min_profit_pct?: number;
  /** Bot 5 trades on a moved stock before it goes back to its own bot; 0 = it stays on Bot 5 all day. */
  bot5_trades?: number;
  /** Bot 5's own ticked entry checks (VWAP, RSI, ADX, …) are read before the hand-off; a refusal leaves the trade. */
  entry_checks?: boolean;
  /** Keep the trade on its bot; Bot 5 opens its own order next to it and the stock is armed on both. */
  keep_trade?: boolean;
  /** Practice bots watch, LIVE Bot 5 trades: a PAPER source trade may hand off to a LIVE Bot 5 (real order). */
  paper_to_live?: boolean;
  /** Flip: Bot 5 opens the other side of the trade that moved fast (bets the spike fades). */
  flip?: boolean;
  on_bot5?: Record<string, { from_bot: number; trades: number; kept?: boolean }>;
  target_bot?: number;
  target_mode?: "PAPER" | "LIVE";
  target_status?: string | null;
  target_qty?: number;
  day?: string | null;
  moved_today?: string[];
  recent?: { at: string; symbol: string; from_bot: number; why: string; side?: "LONG" | "SHORT"; result: string }[];
};
export type OpenPnlStockPoint = { t: string; closed: number; open: number; total: number; side: "LONG" | "SHORT" | null };

/** Carry-over scan (backend carry_scan.py): yesterday's SMA side and how today went. Read-only. */
export type CarryStatus = "CONTINUED" | "REVERSED" | "BACK";
export type CarryRow = {
  symbol: string;
  prev_day: string;
  prev_side: "LONG" | "SHORT";
  prev_since: string | null;
  prev_close: number;
  prev_sma_fast: number;
  prev_sma_slow: number;
  prev_gap_pct: number;
  day: string;
  open: number | null;
  gap_pct: number | null;
  gap_with: boolean | null;
  status: CarryStatus;
  reversed_at: string | null;
  back_at: string | null;
  side_now: "LONG" | "SHORT" | null;
  last: number;
  last_at: string;
  dev_rs: number;
  dev_pct: number;
  dev_slow_pct: number;
  move_with_pct: number;
  best_pct: number;
  best_at: string | null;
  worst_pct: number;
  worst_at: string | null;
  candles_today: number;
};
export type CarryScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  minutes: number;
  minutes_choices: number[];
  sma_fast: number;
  sma_slow: number;
  as_of: string | null;
  finished_at: string | null;
  market_open: boolean;
  rows: CarryRow[];
  paused?: boolean;
};

/** Morning test (backend morning_study.py): a bullish / bearish / sideways morning, then the rest of the day. Read-only. */
export type MorningLabel = "BULLISH" | "BEARISH" | "SIDEWAYS";
export type MorningTimes = { at: string; count: number; pct: number }[];
export type MorningGroup = {
  days: number;
  stocks: number;
  closed_higher_pct: number | null;
  avg_rest_pct: number | null;
  median_rest_pct: number | null;
  avg_low_after_pct: number | null;
  avg_high_after_pct: number | null;
  avg_bounce_pct: number | null;
  bounce_big_pct: number | null;
  avg_giveback_pct: number | null;
  low_times: MorningTimes;
  high_times: MorningTimes;
  avg_next_gap_pct: number | null;
  next_gap_up_pct: number | null;
  avg_next_day_pct: number | null;
  next_day_up_pct: number | null;
  next_days: number;
  avg_deliv_pct: number | null;
  deliv_days: number;
  deliv_split?: {
    at_pct: number;
    high: { days: number; closed_higher_pct: number | null; avg_rest_pct: number | null };
    low: { days: number; closed_higher_pct: number | null; avg_rest_pct: number | null };
  };
};
export type MorningStockPart = { days: number; closed_higher_pct: number | null; avg_rest_pct: number | null };
export type MorningStock = {
  symbol: string;
  days: number;
  bullish: MorningStockPart;
  bearish: MorningStockPart;
  sideways: MorningStockPart;
  avg_deliv_pct: number | null;
};
/** Second candle test (backend second_candle.py). One stock on one day: the Nth candle's call and the path after it. */
/** How the Second candle test picks the side: the Nth candle's colour, most of candles 1..N, or the Nth when most agree. */
export type SecondCandleSideRule = "NTH" | "MAJORITY" | "AGREE";

export type SecondCandleRow = {
  symbol: string;
  day: string;
  /** Which candle of the day this trade read (several can be read in one run). */
  candle_no?: number;
  side: "LONG" | "SHORT" | "DOJI" | "TIE";
  skipped?: string;
  /** Green / red candles among candles 1..N, and their bodies added up in ₹ (the majority side rules). */
  greens?: number;
  reds?: number;
  net_body?: number;
  rank?: number;
  candidates?: number;
  rvol: number | null;
  change_pct: number | null;
  body_pct: number;
  signal_at: string;
  entry_at: string;
  entry: number;
  best_pts: number;
  best_pct: number;
  best_at: string;
  worst_pts: number;
  worst_pct: number;
  worst_at: string;
  hit: boolean;
  target_at: string | null;
  minutes_to_target: number | null;
  against_before_target: number | null;
  close_pts: number;
  close_pct: number;
  close_at: string;
  /** Trailing-stop distance (% of the entry, as a string key) → ₹ per share it made. */
  trail: Record<string, number>;
  /** ₹ level (string key) → minutes after the entry it was first reached with / against the trade (null = never). */
  fav_min: Record<string, number | null>;
  adv_min: Record<string, number | null>;
  /** Stop and reverse, per stop ₹ (string key) the first trade reached: [minute of the reverse entry, best ₹, worst ₹,
   * ₹ at the square-off, then the minutes each LEVELS target was reached with the reverse, then the minute the same stop
   * went against it] (null = never). */
  rev?: Record<string, (number | null)[]>;
};
export type SecondCandleGroup = {
  trades: number;
  hits: number;
  hit_pct: number;
  avg_best: number | null;
  median_best: number | null;
  avg_worst: number | null;
  avg_against_before_target: number | null;
  max_against_before_target: number;
  avg_minutes_to_target: number | null;
  avg_close: number | null;
  close_win_pct: number;
  total_close: number;
  went_against_target_pct: number;
};
export type SecondCandleTrail = { pct: number; total: number; avg: number | null; win_pct: number; worst: number; best: number; trades: number };
export type SecondCandleTargetStop = { target: number; stop: number; trades: number; hit_target: number; hit_stop: number; neither: number; total: number; avg: number | null; win_pct: number };
export type SecondCandleReport = {
  rule: { minutes: number; candle_no: number; top_n: number; target_points: number; square_off: string; trails: number[]; min_body_pct: number };
  trades: number;
  ranked: number;
  skipped: number;
  stocks: number;
  days: number;
  all: SecondCandleGroup;
  by_side: Record<"LONG" | "SHORT", SecondCandleGroup>;
  by_rank: ({ label: string } & SecondCandleGroup)[];
  by_day: ({ day: string } & SecondCandleGroup)[];
  trail: SecondCandleTrail[];
  best_trail: SecondCandleTrail | null;
  target_stop: SecondCandleTargetStop[];
  best_stop: SecondCandleTargetStop | null;
  by_half_hour: { slot: string; target: number; best: number; worst: number }[];
  levels: number[];
  stops: number[];
  answer: string[];
  stopped?: boolean;
};
export type SecondCandleStock = { symbol: string; best_trail_pct: number | null; best_trail_total: number | null } & SecondCandleGroup;
export type SecondCandleView = {
  status: "IDLE" | "RUNNING" | "DONE" | "FAILED";
  done: number;
  total: number;
  errors: { symbol: string; error: string }[];
  params: { symbols?: number; days?: number; minutes?: number; candle_no?: number; candle_nos?: number[]; top_n?: number; target_points?: number; square_off?: string; trails?: number[]; min_body_pct?: number; side_rule?: SecondCandleSideRule; majority_by?: "COUNT" | "SIZE"; dir_rule?: CandleDirRule; ha?: boolean };
  started_at: string | null;
  finished_at: string | null;
  report: SecondCandleReport | null;
  stocks: SecondCandleStock[];
  rows: SecondCandleRow[];
  sizes: number[];
  default_trails: number[];
};

export type MorningStudy = {
  status: "IDLE" | "RUNNING" | "DONE" | "FAILED";
  done: number;
  total: number;
  errors: { symbol: string; error: string }[];
  params: { symbols?: number; days?: number; at?: string; minutes?: number; min_move?: number; delivery?: boolean };
  started_at: string | null;
  finished_at: string | null;
  cut_times: string[];
  sizes: number[];
  report: null | {
    samples: number;
    stocks: number;
    stopped?: boolean;
    groups: Record<MorningLabel, MorningGroup>;
    answer: string[];
    by_stock: MorningStock[];
  };
};

/** Strong-trend pick backtest and its daily automatic run (backend trend_picks.py). */
/** Stock ranking (backend stock_rank.py): a bot's strategy backtested on many stocks, each stock ranked. */
export type StockRankVerdict = "STRONG" | "GOOD" | "WEAK" | "AVOID" | "FEW";
export type StockRankRow = {
  rank: number;
  symbol: string;
  trades: number;
  wins: number;
  win_rate: number;
  days: number;
  green_days: number;
  green_pct: number;
  gross: number;
  net: number;
  per_trade: number;
  per_day: number;
  avg_return_pct: number | null;
  profit_factor: number | null;
  best_trade: number;
  worst_trade: number;
  best_day: number;
  worst_day: number;
  verdict: StockRankVerdict;
};
export type StockRankJob = {
  id: number;
  created_at: string;
  status: "RUNNING" | "DONE" | "STOPPED" | "INTERRUPTED" | "ERROR";
  start_date: string;
  end_date: string;
  bot: number;
  batch: number;
  play_handoffs: boolean;
  lists: string[];
  universe: number;
  runs: { time: string; symbols: string[]; run_id: number | null; status: string; note: string }[];
  note: string;
};
export type StockRankView = StockRankJob & {
  earlier_days: number;
  runs_used: number;
  rows: StockRankRow[];
  counts: Record<StockRankVerdict, number>;
  answer: string[];
};
export type TrendPickDirection = "BULLISH" | "BEARISH" | "BOTH";
/** Most active pick backtest (backend active_picks.py): how the day's picks are ranked and filtered. */
export type ActivePickRank = "value" | "rvol" | "change" | "vwap" | "speed" | "range";
export type ActivePickBias = "BOTH" | "LONG" | "SHORT" | "ANY";
export type ActivePickParams = { rank: ActivePickRank; bias: ActivePickBias; min_value_cr: number };
/** A pick-time comparison (backend trend_compare.py): one strong-trend pick backtest per time, side by side. */
export type TrendCompareRun = { time: string; run_id: number | null; status: string; note: string };
export type TrendCompare = {
  id: number;
  created_at: string;
  status: "RUNNING" | "DONE" | "STOPPED" | "INTERRUPTED" | "ERROR";
  start_date: string;
  end_date: string;
  times: string[];
  top_n: number;
  direction: TrendPickDirection;
  minutes: number;
  bot: number;
  universe: number;
  lists: string[];
  play_handoffs?: boolean;
  source?: "TREND" | "ACTIVE";
  active?: ActivePickParams;
  runs: TrendCompareRun[];
  note: string;
};
export type TrendCompareRow = {
  time: string;
  run_id: number | null;
  status: string;
  note: string;
  has_result: boolean;
  rank?: number;
  days_played?: number;
  days_done?: number;
  days_total?: number;
  trades?: number;
  wins?: number;
  win_rate?: number;
  gross?: number;
  net?: number;
  charges?: number;
  per_trade?: number;
  per_day?: number;
  green_days?: number;
  red_days?: number;
  best_day?: number;
  worst_day?: number;
  drawdown?: number;
  stocks_picked?: number;
  best_days?: number;
};
export type TrendCompareResult = {
  rows: TrendCompareRow[];
  days: { date: string; by_time: Record<string, number>; best: string | null }[];
  best: Record<"gross" | "net" | "win_rate" | "green_days" | "drawdown" | "best_days", string | null>;
  settings_differ: boolean;
  answer: string[];
};
export type TrendPicksAuto = {
  on: boolean;
  run_at: string;
  pick_time: string;
  direction: TrendPickDirection;
  top_n: number;
  minutes: number;
  bot: number;
  lists: ("armed" | "fno" | "nifty50" | "nifty100" | "niftybank")[];
  /** Play the bot's hand-offs to Bot 2 / Bot 3 inside the run. */
  play_handoffs: boolean;
  last_day: string | null;
  last_run_id: number | null;
  last_note: string | null;
};

/** Turning scan (backend turn_scan.py): candles turning against the SMA trend before the cross. Read-only. */
export type TurnRule = "COLOUR" | "CLOSES" | "BOTH";
export type TurnRow = {
  symbol: string;
  trend: "BULLISH" | "BEARISH";
  turn: "LONG" | "SHORT";
  trend_since: string | null;
  trend_candles: number;
  counter_candles: number;
  counter_since: string;
  close: number;
  sma_fast: number;
  sma_slow: number;
  gap_pct: number;
  gap_at_turn_pct: number | null;
  gap_closed_pct: number | null;
  candles_to_cross: number | null;
  minutes_to_cross: number | null;
  move_since_turn_pct: number | null;
  day_move_pct: number | null;
  candle_ts: number;
  volume: number | null;
  speed_pct_per_min: number | null;
};
export type TurnScan = {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  error: string | null;
  minutes: number;
  minutes_choices: number[];
  count: number;
  rule: TurnRule;
  need_fast: boolean;
  alert?: boolean;
  sma_fast: number;
  sma_slow: number;
  as_of: string | null;
  finished_at: string | null;
  market_open: boolean;
  rows: TurnRow[];
  paused?: boolean;
};

/** P&L dashboard (backend pnl_dashboard.py): the bots' P&L by time of day, across days or backtest runs. */
export type PnlDashRun = {
  run_id: number;
  bot: number;
  first: string;
  last: string;
  days: number;
  points: number;
  status?: string;
  start?: string;
  end?: string;
  created_at?: string | null;
};
export type PnlDashOptions = { runs: PnlDashRun[]; market_days: string[]; desks: string[]; steps: number[]; keep_days: number };
export type PnlDashDay = {
  key: string;
  label: string;
  first: string;
  last: string;
  high: number;
  high_at: string;
  low: number;
  low_at: string;
  close: number;
  giveback: number;
  curve: number[];
};
export type PnlDashTime = {
  t: string;
  avg: number;
  median: number;
  min: number;
  max: number;
  sum: number;
  pos_pct: number;
  beat_close_pct: number;
  vs_close: number;
};
export type PnlDashStock = {
  symbol: string;
  days: number;
  sum_close: number;
  avg_close: number;
  avg_high: number;
  avg_low: number;
  win_days: number;
  best_exit: string | null;
  avg_at_best: number | null;
  gain_vs_close: number | null;
};
export type PnlDashboard = {
  source: "replay" | "market";
  runs?: number[];
  desks?: string[];
  basis: "gross" | "net";
  stocks_picked: string[];
  /** Shares every trade was restated as (0 = as traded). */
  qty?: number;
  all_stocks: string[];
  grid: string[];
  step: number;
  days: PnlDashDay[];
  by_time: PnlDashTime[];
  best_exit: {
    by_avg: string;
    avg_at_best: number;
    sum_at_best: number;
    beat_close_pct: number;
    by_median: string;
    median_at_best: number;
    avg_close: number;
    sum_close: number;
    gain_vs_close: number;
  } | null;
  high_when: { bucket: string; days: number }[];
  low_when: { bucket: string; days: number }[];
  totals: {
    days: number;
    sum_close: number;
    avg_close: number;
    win_days: number;
    loss_days: number;
    avg_high: number;
    avg_low: number;
    best_day: string;
    best_close: number;
    worst_day: string;
    worst_close: number;
    avg_giveback: number;
  } | null;
  stocks: PnlDashStock[];
};
export type PnlDashQuery = {
  source: "replay" | "market";
  runs?: number[];
  desks?: string[];
  start?: string;
  end?: string;
  stocks?: string[];
  basis?: "gross" | "net";
  step?: number;
  weekdays?: number[];
  /** Restate every trade as this many shares (0 / unset = as traded), so runs with different sizes compare. */
  qty?: number;
};

export type OpenPnlReplayRun = { run_id: number; first: string; last: string; days: number; points: number; saved_at: string };
export type OpenPnlReplayLog = Omit<OpenPnlLog, "desk" | "every_sec"> & { run_id: number };

/** One saved Cross scan / Trend quality pass (scan history), without its rows. */
export type ScanKind = "cross" | "chop" | "active";
export type ScanRun = {
  id: number;
  kind: ScanKind;
  label: string;
  day: string;
  as_of: string;
  finished_at: string | null;
  minutes: CrossScanMinutes | null;
  total: number | null;
  failed: number | null;
  row_count: number;
  error: string | null;
  meta: {
    sma_fast?: number;
    sma_slow?: number;
    quiet_min?: number;
    stocks?: number;
    source?: "manual" | "auto";
    alert_on_quiet?: boolean;
    price_min?: number;
    price_max?: number;
  };
  /** When its accuracy check was made (UTC), null when never. */
  checked_at: string | null;
};
export type ScanCheckStatus = {
  running: boolean;
  run_id: number | null;
  done: number;
  total: number;
  failed: number;
  error: string | null;
};
export type ScanHistory = {
  days: { day: string; runs: number }[];
  day: string | null;
  runs: ScanRun[];
  keep_days: number;
  checking: ScanCheckStatus;
};
/** What one stock did after a saved scan, % with the scan's call (a fall is + for a bearish call). */
export type ScanCheckRow = {
  symbol: string;
  side: "BULLISH" | "BEARISH" | null;
  entry: number;
  after_15_pct: number | null;
  after_30_pct: number | null;
  after_60_pct: number | null;
  close_price: number | null;
  close_pct: number | null;
  best_pct: number | null;
  worst_pct: number | null;
  next_cross_after_min: number | null;
  next_cross_side: "BULLISH" | "BEARISH" | null;
  /** Minutes the trend held after the scan (to the next cross, else to the close). */
  held_min: number | null;
  state?: "APPROACHING" | "CROSSED";
  predicted_min?: number | null;
  crossed?: boolean;
  on_time?: boolean;
  quiet_runner?: boolean;
  trend?: ChopTrend | null;
  score?: ChopScore;
};
export type ScanCheckGroup = {
  key: string;
  label: string;
  count: number;
  with_call: number;
  right_after_15_pct: number | null;
  right_after_30_pct: number | null;
  right_after_60_pct: number | null;
  right_close_pct: number | null;
  avg_after_15_pct: number | null;
  avg_after_30_pct: number | null;
  avg_after_60_pct: number | null;
  avg_close_pct: number | null;
  avg_best_pct: number | null;
  avg_worst_pct: number | null;
  avg_held_min: number | null;
  crossed_pct?: number | null;
  on_time_pct?: number | null;
};
export type ScanCheck = {
  run_id: number;
  kind: ScanKind;
  as_of: string;
  minutes: number;
  checked: number;
  failed: number;
  error: string | null;
  summary: ScanCheckGroup[];
  rows: ScanCheckRow[];
};
export type ScanRunDetail = ScanRun & {
  rows: (CrossScanRow | ChopScanRow)[];
  check: ScanCheck | null;
  can_check: boolean;
  checking: ScanCheckStatus;
};

/** Trend quality re-scan run by the server every `every_min` minutes while NSE is open (no browser needed). */
export type ChopAuto = {
  on: boolean;
  every_min: number;
  minutes: CrossScanMinutes;
  armed_only: boolean;
  /** The card's scan lists (armed, fno, nifty50, nifty100, niftybank); empty = armed_only decides. */
  lists?: string[];
  quiet_min: number;
  alert_on_quiet: boolean;
  price_min: number;
  price_max: number;
  every_choices: number[];
  last_run_at: string | null;
  next_at: string | null;
  error: string | null;
  scans_enabled: boolean;
};

export type ChopAutoUpdate = Partial<
  Pick<ChopAuto, "on" | "every_min" | "minutes" | "armed_only" | "quiet_min" | "alert_on_quiet" | "price_min" | "price_max" | "lists">
>;

/** One 1-minute review of an uncertain SMA position. Only EXIT ever closes the trade. */
export type ReviewStatus =
  | "PENDING"
  | "USER_REVIEW_EXIT"
  | "USER_REVIEW_WAIT"
  | "NO_RESPONSE"
  | "ALREADY_CLOSED"
  | "AUTO_REVIEW_EXIT"
  | "AUTO_REVIEW_REVERSE"
  | "AUTO_REVIEW_CONTINUE";

export type ReviewCandle = { ts: number; open: number; high: number; low: number; close: number; colour: "GREEN" | "RED" | "DOJI" };

export type ReviewItem = {
  id: number;
  created_at: string | null;
  mode: string;
  run_id: number | null;
  trade_id: number;
  symbol: string;
  direction: "LONG" | "SHORT";
  candle_minutes: number;
  entry_price?: number;
  qty?: number;
  five_min: {
    candle_ts?: number;
    sma_fast?: number;
    sma_slow?: number;
    gap_pct?: number;
    prev_gap_pct?: number;
    narrowing?: boolean;
    crossed?: boolean;
    band_pct?: number;
    sma_fast_len?: number;
    sma_slow_len?: number;
    /** Which ticked conditions made it uncertain (gap / slope / steep), in plain words. */
    reasons?: string[];
    slope_pct?: number | null;
    slope_candles?: number | null;
  };
  one_min: {
    candle_ts?: number;
    candles?: ReviewCandle[];
    close?: number | null;
    sma_fast?: number | null;
    sma_slow?: number | null;
    gap_pct?: number | null;
    gap_trend?: string;
    fast_slope?: string;
    slow_slope?: string;
    vs_fast?: string | null;
    vs_slow?: string | null;
    vwap?: number | null;
    vs_vwap?: string | null;
    rsi14?: number | null;
    volume_ratio?: number | null;
    /** How the closed 1-minute candles read for the open trade. Information only; null when too little data. */
    verdict?: {
      label: "WITH" | "AGAINST" | "MIXED";
      score: number;
      of: number;
      with: number;
      against: number;
      flat: number;
      /** One check candle. */
      checks?: Record<string, "with" | "against" | "flat">;
      /** Several check candles (combined verdict): each size's own read. */
      by_candle?: { minutes: number; label: string; checks?: Record<string, "with" | "against" | "flat"> }[];
    } | null;
  };
  message: string;
  status: ReviewStatus;
  action_at: string | null;
  note: string;
};

/** One fast move in a replay (replay.bot5_view): when, which stock and side, why, and what happened. */
export type FastLogRow = {
  at: string;
  symbol: string;
  side: "LONG" | "SHORT";
  why: string;
  result: string;
  moved: boolean;
  from_bot: number;
};

export type SmaState = {
  /** Bot 5's view of a Bot 1-4 replay: the bot it follows and the day's fast moves. */
  follows_bot?: number;
  fast_log?: FastLogRow[];
  /** Recent 1-minute reviews of this desk's open trades (pending first to answer). */
  reviews?: ReviewItem[];
  /** Which SMA bot this is (1 = main desk) and its name. */
  bot?: number;
  bot_name?: string;
  bot_status: string;
  halt_reason: string;
  /** REPLAY while a past day is replaying, RESEARCH on the research desk (both practice only). */
  mode: "PAPER" | "LIVE" | "REPLAY" | "RESEARCH";
  /** "research" on the research desk's state. */
  desk?: "research";
  data_source: string;
  /** The per-second Groww price fetch and record is on (one switch for both desks). */
  second_ticks?: boolean;
  /** The chart stock's flip strategy is on (buy signals sell, sell signals buy). */
  flip_orders?: boolean;
  /** Candle interval in minutes the chart stock trades on (1 for candle patterns). */
  candle_minutes?: number;
  /** Present only on /api/replay/state. */
  replay?: ReplayInfo;
  last_error: string;
  last_signal: string;
  symbol: string;
  trade_symbols?: string[];
  books?: {
    symbol: string;
    direction: "LONG" | "SHORT" | "FLAT";
    qty: number;
    entry_price: number | null;
    sl_trigger: number | null;
    stop_active?: boolean | null;
    target?: number | null;
    trailing?: boolean | null;
    tsl_step?: number | null;
    ltp: number | null;
    note?: string;
    /** Unrealized net at this stock's last price; null when flat. */
    open_net?: number | null;
    /** Today's closed trades of this stock in the current book. */
    closed_net?: number;
    closed_trades?: number;
    /** closed_net + open_net. */
    day_net?: number;
    /** The same three before charges (what the screens show). */
    open_gross?: number | null;
    closed_gross?: number;
    day_gross?: number;
    /** Choppy or trending on the bot's own candle (stock_regime.py); null while the tape is too short. */
    regime?: StockRegime | null;
    /** Groww's last refusal on this stock today (its own words), until an order fills. */
    last_reject?: string | null;
    last_reject_at?: string | null;
  }[];
  /** Unrealized net of every held stock, not only the chart's. */
  open_net_total?: number;
  open_gross_total?: number;
  /** Stocks with their own strategy settings, and the fields each sets. */
  stock_settings?: Record<string, Record<string, unknown>>;
  /** use_stop in the config. False means new entries get no stop order. */
  stop_enabled?: boolean;
  atr_multiplier?: number;
  stop_type?: StopType;
  /** The chart stock's own stop numbers (its overrides over the shared settings). */
  stop_points?: {
    tsl_mode?: "POINTS" | "PERCENT" | "ATR";
    tsl_sl_points: number;
    tsl_trail_points: number;
    tsl_target_points: number;
    tsl_sl_pct?: number;
    tsl_trail_pct?: number;
    tsl_target_pct?: number;
    gap_sl_mult: number;
    gap_tp_mult: number;
    vwap_stop_atr?: number;
  } | null;
  exchange: string;
  ltp: number;
  day_open?: number | null;
  day_change_pct?: number | null;
  sma9: number | null;
  sma21: number | null;
  sma_gap?: number | null;
  sma_gap_pct?: number | null;
  vwap?: number | null;
  minute_volume?: number | null;
  avg_minute_volume?: number | null;
  volume_ratio?: number | null;
  rsi14?: number | null;
  atr14: number | null;
  adx14: number | null;
  position: null | {
    direction: "LONG" | "SHORT";
    flipped?: boolean;
    qty: number;
    entry_price: number;
    ma_cross_price: number;
    atr_at_entry: number;
    /** null when this position was entered with no stop. */
    sl_trigger: number | null;
    sl_order_id: string;
    entry_time: string;
    mode: string;
    stop_active?: boolean;
    /** True when this practice position uses the SMA-gap moving stop and target. */
    trailing?: boolean;
    target?: number | null;
    /** Set when this position uses the ₹-step trailing stop. */
    tsl_step?: number | null;
    tsl_points?: number | null;
    /** The best price since entry the trail is measured from: the stop moves once the price is a step past it. */
    tsl_best?: number | null;
  };
  active_sl_trigger: number | null;
  unrealized_gross_pnl: number;
  estimated_charges: number;
  unrealized_net_pnl: number;
  sl_room: number | null;
  sl_room_pct: number | null;
  realized_net_pnl: number;
  trades_today: number;
  /** null during a replay: replays have no daily trade cap. */
  max_trades: number | null;
  max_daily_loss: number;
  kpis: {
    theoretical_gross: number;
    actual_gross: number;
    total_charges: number;
    charge_breakdown: ChargeBreakdown;
    net: number;
    win_rate: number;
    trades: number;
    wins: number;
    /** Wins counted before charges. */
    gross_wins?: number;
  };
};

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  sma9: number | null;
  sma21: number | null;
  atr14: number | null;
  /** Session VWAP after this candle (what the VWAP filter reads). */
  vwap?: number | null;
  /** RSI 14 on 1-minute closes (what the RSI filter reads). */
  rsi14?: number | null;
  /** EMA cross lines (fast, slow), when the EMA check or exit is on. */
  emaFast?: number | null;
  emaSlow?: number | null;
  /** Supertrend line while it is up (green) / down (red), when its check or exit is on. */
  stUp?: number | null;
  stDown?: number | null;
  /** Shares traded in this candle (null when Groww's running total cannot give it). */
  volume?: number | null;
};

/** An SMA cross the entry filters refused, with the bot's own reason. */
export type BlockedCross = { time: number; direction: "LONG" | "SHORT"; reason: string; label: string };

export const BB_EXITS = ["OFF", "BAND", "MIDDLE", "BOTH"] as const;
export type BbExit = (typeof BB_EXITS)[number];

export type ChartFilters = {
  /** Breakout hand-off on this bot / stock, and the candle height it needs in ATRs (breakout.py). */
  breakout_handoff?: boolean;
  breakout_atr_mult?: number;
  use_vwap: boolean;
  /** Cumulative delta strip (cum_delta.py): drawn on Auto while its check or divergence exit is on. */
  use_cvd?: boolean;
  cvd_div_exit?: boolean;
  cvd_lookback?: number;
  use_rsi: boolean;
  rsi_long_min: number;
  rsi_long_max: number;
  rsi_short_min: number;
  rsi_short_max: number;
  use_bollinger?: boolean;
  bb_period?: number;
  bb_std?: number;
  bb_exit?: BbExit;
  /** The ATR stop is the one in use, so the ATR line matters. */
  atr_stop: boolean;
  /** Draw the EMA cross lines (EMA check or EMA exit on) and their lengths. */
  ema_lines?: boolean;
  ema_fast?: number;
  ema_slow?: number;
  /** Draw the Supertrend line (its check or exit on) and its settings. */
  st_line?: boolean;
  st_period?: number;
  st_mult?: number;
  /** Opening range (09:15–09:30), each check its own tick box; all ticked must agree. */
  use_or_volume?: boolean;
  use_or_vwap?: boolean;
  use_or_spread?: boolean;
  use_or_break?: boolean;
  or_volume_ratio?: number;
  or_spread_pct?: number;
  /** SMA 9 slope exit (slope_exit.py): the strip is always drawn while it is on. */
  slope_exit?: boolean;
  slope_lookback?: number;
  slope_bend_pct?: number;
  slope_turn_pct?: number;
  /** The V-turn's steep / turn-back levels (%/candle) and window, and the slope candle (0 = the bot's own). */
  vturn_exit?: boolean;
  vturn_move_pct?: number;
  vturn_turn_pct?: number;
  vturn_within?: number;
  slope_minutes?: number;
  /** Which slope rules are on and the fast SMA length, for the chart's exit / entry zones (slopeZone.ts). */
  sma_fast?: number;
  slope_rules?: boolean;
  slope_turn?: boolean;
  decel_exit?: boolean;
  turn_entry?: boolean;
  turn_toward_cross?: boolean;
  /** For the chart's "why no trade here" line. */
  entry_cutoff_time?: string;
  square_off_time?: string;
  entry_mode?: EntryMode;
  use_gap_mode?: boolean;
  /** Always in a trade (always_in.py) and its reverse-after-a-loss option, for the chart's "why" line. */
  always_in?: boolean;
  always_in_reverse?: boolean;
};

/** A loan the practice wallet took because the free balance was short of an entry's margin. */
export type WalletLoan = {
  id?: number;
  bot?: number;
  /** True when a replay took it. */
  replay?: boolean;
  symbol: string;
  qty: number;
  price: number;
  need: number;
  available: number;
  borrowed: number;
  /** Total owed after this loan. */
  loan: number;
  at: string;
  text: string;
};

/** Practice wallet of the PAPER bots (like one Groww account); inactive until money is loaded. */
export type Wallet = {
  active: boolean;
  /** Money loaded and borrowed, net of withdrawals. */
  funds: number;
  /** Borrowed to cover margin, still to pay back. */
  loan: number;
  /** P&L before charges of PAPER trades closed since the wallet started. */
  realized: number;
  /** Margin held by open PAPER trades. */
  blocked: number;
  /** Free balance. */
  available: number;
  margin_pct: number;
  since: string | null;
  open: { symbol: string; bot: number; direction: string; qty: number; entry_price: number; margin: number }[];
  last_loan: WalletLoan | null;
  /** Loans not fully repaid. */
  open_loans?: number;
};

/** One line of the wallet's statement; a LOAN line is also that loan's record. */
export type WalletEntry = {
  id: number;
  at: string;
  kind: "ADD" | "WITHDRAW" | "LOAN" | "REPAY" | "RESET" | "MARGIN";
  amount: number;
  /** Free balance and total loan owed right after this line. */
  balance_after: number | null;
  loan_after: number | null;
  note: string;
  bot: number | null;
  symbol: string | null;
  qty: number | null;
  price: number | null;
  /** LOAN: margin the entry needed. */
  need: number | null;
  /** LOAN only. */
  repaid?: number;
  due?: number;
  status?: "OPEN" | "REPAID";
};

/** Where a stock can be armed: an SMA bot (1 = main desk, 2-4) or the research desk. */
export type EntryMode = "SMA" | "PATTERN" | "COLOUR" | "NTH_CANDLE";
export type NthCandleMinutes = 1 | 3 | 5 | 10 | 15;
export const NTH_CANDLE_MINUTES: NthCandleMinutes[] = [1, 3, 5, 10, 15];
export type CandleMinutes = 1 | 2 | 3 | 5 | 10 | 15;
export const CANDLE_MINUTES: CandleMinutes[] = [1, 2, 3, 5, 10, 15];
/** Exit candle sizes for mixed candles (exit_candle.py); 0 = the entry candle. */
export const EXIT_CANDLE_MINUTES = [0, 1, 2, 3, 5, 10, 15, 30, 60];
/** Slope candle sizes for the SMA 9 line exits (slope_exit.py); 0 = the bot's own candle. */
export const SLOPE_MINUTES = [0, 1, 2, 3, 5, 10, 15];

export type ArmTarget = 1 | 2 | 3 | 4 | 5 | "research";

/** The all-bots day target / stop loss (all_bots_guard.py). */
export interface AllBotsGuard {
  target: number;
  stop: number;
  /** "HH:MM" IST square-off time for every bot, "" = off. */
  squareoff: string;
  total: number;
  hit: "target" | "stop" | null;
  halted_bots: number[];
  reason: string;
}

/** All-bots P&L alert levels (pnl_alerts.py): a message / call only, never a trade. */
export interface PnlAlertSettings {
  profit: number;
  loss: number;
  telegram: boolean;
  call: boolean;
  basis: "GROSS" | "NET";
  /** Today's P&L of bots 1-4 on that basis. */
  total?: number;
  /** Levels already alerted today ("profit" / "loss"), with when and the total then. */
  sent?: Record<string, { at: string; total: number }>;
}

/** News entries (news_entry.py): one bot enters at once on a strong, fresh headline. */
export interface NewsEntrySettings {
  /** 0 = off, else the bot (1-4) that takes news entries. */
  bot: number;
  impact: "LOW" | "MEDIUM" | "HIGH";
  /** FNO: any F&O stock (armed on the bot as "News"); ARMED: only stocks already armed there. */
  scope: "FNO" | "ARMED";
  max_age_min: number;
  max_day: number;
  /** The picked bot's PAPER / LIVE mode. */
  mode?: "PAPER" | "LIVE" | null;
  /** The latest headlines that qualified and what each did. */
  recent?: { at: string; bot: number; symbol: string; side: "BUY" | "SHORT"; impact: string; title: string; url?: string; result: string }[];
}

/** A settings file from Settings → Export (backend main.export_settings). */
export type SettingsFile = {
  kind: "paltra-settings";
  version: number;
  exported_at: string;
  server?: string;
  app_version?: string;
  desks: Record<
    string,
    {
      name: string;
      mode: "PAPER" | "LIVE";
      settings: Record<string, unknown>;
      stock_settings: Record<string, Record<string, unknown>>;
      trade_symbols: string[];
    }
  >;
  fast_bot?: Record<string, unknown>;
};

export type BotSummary = {
  bot: number;
  name: string;
  mode: "PAPER" | "LIVE";
  status: string;
  armed: string[];
  held: string[];
  trades_today: number;
  net_today: number;
  /** Today's P&L before charges (what the screens show). */
  gross_today?: number;
};

export type ChartPayload = {
  /** The stock drawn (the chart focus, or the one asked for). */
  symbol?: string;
  /** Minutes per candle the server sends (the bot's candle interval); 1 when absent. */
  candle_minutes?: number;
  /** Classic daily pivots from the previous session (pivots.py); null until that day is loaded. */
  pivots?: { day: string; p: number; r1: number; r2: number; s1: number; s2: number } | null;
  pivot_target?: "OFF" | "R1" | "R2";
  candles: Candle[];
  markers: {
    time: number;
    direction: "LONG" | "SHORT";
    price: number;
    kind: "ENTRY" | "EXIT" | string;
    /** The trade's net P&L once it has closed, when the API sends it. */
    net_pnl?: number | null;
    /** The same before charges (what the screens show). */
    gross_pnl?: number | null;
    /** Only on EXIT markers: why the trade closed. */
    reason?: string | null;
    /** The trade's id inside its book: N-12 (NSE live), P-12 (simulation), R7-12 (replay run 7). */
    trade_ref?: string | null;
    /** True while the trade is still open. */
    open?: boolean;
  }[];
  entry_price: number | null;
  sl_trigger: number | null;
  target?: number | null;
  trailing?: boolean;
  tsl_step?: number | null;
  atr_multiplier?: number;
  blocked?: BlockedCross[];
  filters?: ChartFilters;
};

export type TradeRow = {
  id: number;
  date: string;
  symbol: string;
  /** The SMA bot that placed it (1 = main desk, 2-4 the extra bots). */
  bot?: number;
  direction: "LONG" | "SHORT";
  /** Flip strategy: the order went against the signal. */
  flipped?: boolean;
  qty: number;
  entry_time: string | null;
  entry_price: number;
  ma_cross_price: number;
  /** Entry versus the cross price, in points. Positive means a worse fill. */
  fill_lag_points?: number | null;
  atr_at_entry: number;
  /** null when the trade had no stop. */
  sl_trigger_price: number | null;
  stop_active?: boolean;
  /** Why the trade opened (SMA_CROSS, GAP_MODE, "JOIN_TREND:EXTREME_LIMIT", ...); null on older trades. */
  entry_reason?: string | null;
  /** Choppy or trending when it opened (stock_regime.py at entry); null on older trades. */
  entry_regime?: import("./trendAtEntry").TradeRegime | null;
  /** Staged stop plan moves, "; " separated (stop_plan.py). */
  stop_log?: string | null;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  market_price?: number | null;
  mark_pnl?: number | null;
  gross_pnl: number | null;
  brokerage_and_taxes: number | null;
  net_pnl: number | null;
  points: number | null;
  mode: string;
  /** The replay run a REPLAY trade belongs to. */
  run_id?: number | null;
  /** Number inside its own book (NSE live, simulation, or one replay run). */
  book_seq?: number | null;
  /** The id people see: N-12 (NSE live), P-12 (simulation), R7-12 (replay run 7). */
  trade_ref?: string | null;
  /** Settings the bot used for this trade, when the API records them. */
  strategy?: Record<string, string | number | boolean | null> | null;
  /** Highest / lowest price while the trade was open (so far, while it is). */
  max_high?: number | null;
  max_low?: number | null;
};

/**
 * Rupee P&L the trade would have shown at `price` (before charges): the
 * points in its favour times its quantity. Used for the max high / max low.
 */
export function pnlAtPrice(trade: Pick<TradeRow, "direction" | "entry_price" | "qty">, price: number | null | undefined): number | null {
  if (price == null || !Number.isFinite(price) || !Number.isFinite(trade.entry_price)) return null;
  const points = trade.direction === "SHORT" ? trade.entry_price - price : price - trade.entry_price;
  return points * (trade.qty || 0);
}

export type TradeBookMode = "PAPER" | "LIVE" | "REPLAY" | "RESEARCH";

/** Each distinct strategy is sent once; rows point at it by index. */
type TradeBookPayload = {
  mode: TradeBookMode;
  total: number;
  rows: (Omit<TradeRow, "strategy"> & { strategy_ref: number | null })[];
  strategies: NonNullable<TradeRow["strategy"]>[];
};

export type TradeBook = { total: number; rows: TradeRow[] };

export type ReplayStatus = "IDLE" | "LOADING" | "PLAYING" | "PAUSED" | "FINISHED" | "ERROR";

/** One candle size's read from the trailing stop calculator (tsl_calc.py). */
export type TslCalcRow = {
  minutes: number;
  candles: number;
  atr: number | null;
  avg_range: number | null;
  atr_pct?: number;
  stop?: number | null;
  trail?: number | null;
  target?: number | null;
  stop_pct?: number | null;
  trail_pct?: number | null;
  target_pct?: number | null;
};
export type TslCalcResult = {
  symbol: string;
  price: number | null;
  as_of?: string | null;
  atr_period?: number;
  multiples?: { stop: number; trail: number; target: number };
  sizes: TslCalcRow[];
  source?: "bot tape" | "groww";
  error?: string;
};

export type HandoffReturn = {
  on: boolean;
  at: string;
  /** Stocks handed off and not yet sent back: where they came from and which bot has them now. */
  moved: Record<string, { origin: number; on: number; at: string }>;
  last: { at: string; lines: string[] } | [];
};

export type ReplayInfo = {
  status: ReplayStatus;
  date: string | null;
  start: string;
  /** ISO time on the replayed day. */
  clock: string | null;
  /** Where a seek is heading (ISO), while the replay fast-forwards or replays the day to it; else null. */
  seeking?: string | null;
  speed: number;
  effective_speed: number;
  speeds: number[];
  symbols: string[];
  skipped: string[];
  loaded: number;
  total: number;
  error: string;
  /** Last day of the run (same as `date` for a one-day replay). */
  end_date?: string | null;
  /** Trading days the run plays, in order. */
  days?: string[];
  day_index?: number;
  days_total?: number;
  run_id?: number | null;
  /** The SMA bot whose settings the replay plays (1 = main desk, 2-4). */
  bot?: number;
  bot_name?: string;
  /** Bot 5 only: the Bot 1-4 replay this tab follows (its fast-mover hand-offs to Bot 5). */
  follows?: number | null;
  /** Played hand-offs (handoff_chain.py): which bot each handed-off stock plays as now, and each hand-off. */
  chain?: {
    on_bot: Record<string, number>;
    log: { at: string; day: string; symbol: string; from_bot: number; to_bot: number }[];
    return_eod: boolean;
  } | null;
  /** Scalp-pick runs: the rule and each day's picks (date -> picks, best first). */
  pick_rule?: ScalpPickRule | null;
  picks?: Record<string, ScalpPick[]>;
};

/** The rule a pick run recorded (`scalp_pick` in the run's settings). `kind` says which backtest picked: absent =
 * the Scalp page's scalp picks (scalp_picks.py), `TREND` = strong-trend picks (trend_picks.py), `ACTIVE` = Most active
 * picks (active_picks.py); each kind keeps its own fields, so read them by kind. */
export type ScalpPickRule = {
  kind?: "TREND" | "ACTIVE";
  pick_time: string;
  top_n: number;
  /** Scalp picks only. */
  min_atr_pct?: number;
  min_value_cr?: number;
  require_bias?: boolean;
  /** Strong-trend picks only. */
  direction?: TrendPickDirection;
  minutes?: number;
  /** Most active picks only. */
  rank?: ActivePickRank;
  bias?: ActivePickBias;
};

/** One day's pick. The fields differ by `ScalpPickRule.kind`, so every number is optional. */
export type ScalpPick = {
  symbol: string;
  /** Scalp picks: the scalp score and the bias. */
  score?: number | null;
  bias?: "LONG" | "SHORT" | "NONE" | null;
  atr_pct?: number | null;
  value_cr?: number | null;
  move_5m_pct?: number | null;
  /** Strong-trend and Most active picks: the side traded. */
  side?: "LONG" | "SHORT" | null;
  /** Strong-trend picks. */
  trend?: string | null;
  trend_score?: number | null;
  day_move_pct?: number | null;
  /** Most active picks. */
  rvol?: number | null;
  change_pct?: number | null;
  speed_pct_min?: number | null;
  ltp?: number | null;
};

export type ReplayDayRow = {
  date: string;
  trades: number;
  wins: number;
  losses: number;
  profit: number;
  loss: number;
  gross: number;
  charges: number;
  net: number;
  cumulative: number;
};

export type ReplayRunTotals = {
  trades: number;
  wins: number;
  losses: number;
  profit: number;
  loss: number;
  gross: number;
  charges: number;
  net: number;
  win_rate: number;
  max_drawdown: number;
  green_days: number;
  red_days: number;
};

/** One stock's share of a run: its totals and its own day-wise P&L. */
export type ReplayStockRow = {
  symbol: string;
  totals: ReplayRunTotals;
  days: ReplayDayRow[];
  /** Settings on the stock's first trade (it may have its own); null if it never traded. */
  strategy: Record<string, string | number | boolean | null> | null;
};

/** The most money a run had in open trades at one moment (entry price × qty, full value). */
export type ReplayCapitalPeak = {
  peak_value: number;
  /** ISO time of the peak (replay clock), null when the run never traded. */
  peak_at: string | null;
  /** Positions open at the peak. */
  peak_positions: number;
};

export type ArmLogRow = {
  id: number;
  at: string;
  symbol: string;
  /** ARM / UNARM, or ARMED (already armed when the app started). */
  action: "ARM" | "UNARM" | "ARMED";
  source: string | null;
  mode: string | null;
};

export type ArmLogDay = {
  desk: string;
  day: string;
  rows: ArmLogRow[];
  /** Each stock's armed stretches that day (HH:MM:SS IST). */
  stocks: { symbol: string; spans: { from: string; to: string }[] }[];
};

export type ReplayRun = {
  id: number;
  created_at: string | null;
  start_date: string;
  end_date: string;
  start_time: string;
  symbols: string[];
  /** Snapshot of the strategy settings the run used. */
  settings: Record<string, string | number | boolean | null>;
  status: "RUNNING" | "FINISHED" | "STOPPED" | "INTERRUPTED";
  days_total: number;
  days_done: number;
  totals: ReplayRunTotals;
  /** Money in open trades at the run's busiest moment, and each day's own peak. */
  capital?: ReplayCapitalPeak & { days: (ReplayCapitalPeak & { date: string })[] };
  /** Played hand-offs (handoff_chain.py): closed trades per bot they were played as (Bot 2, Bot 3). */
  handoffs?: { bot: number; trades: number; stocks: number; gross: number; net: number; wins: number }[] | null;
  /** The fast-mover hand-off's part (fast_bot.py): trades cut on this bot, and the ones played on with Bot 5's settings. */
  fast_move?: { cut: number; cut_gross: number; trades: number; gross: number; net: number; wins: number } | null;
  days?: ReplayDayRow[];
  /** Per-stock split, on the run detail only. */
  stocks?: ReplayStockRow[];
  /** Closed trades by the trend at entry (handoff_log.by_regime), on the run detail only. */
  by_regime?: TrendGroupRow[];
  /** Where each stock was handed during the run (handoff_log.rows), on the run detail only. */
  handoff_trail?: HandoffRow[];
};

/** One group of closed trades by the trend at entry. */
export type TrendGroupRow = {
  group: import("./trendAtEntry").TrendGroup;
  label: string;
  trades: number;
  wins: number;
  win_pct: number | null;
  gross: number;
  net: number;
};

/** One hand-off of a stock between the SMA bots (handoff_log.py). */
export type HandoffRow = {
  id: number;
  at: string | null;
  day: string;
  mode: string;
  run_id: number | null;
  from_bot: number;
  to_bot: number;
  symbol: string;
  kind: "RECOVERY" | "VTURN_LOSS" | "BREAKOUT" | "FAST_MOVE" | "FAST_BACK" | string;
  kind_words: string;
  why: string | null;
  result: string | null;
  moved: boolean;
  trade_id: number | null;
  regime: import("./trendAtEntry").TradeRegime | null;
};

/** A replay run cut short with days still to play (GET /api/replay/resumable). */
export type ResumableRun = {
  id: number;
  bot: number;
  /** INTERRUPTED: a deploy or restart cut it; STOPPED: Stop was pressed. */
  status: "INTERRUPTED" | "STOPPED";
  start_date: string;
  end_date: string;
  days_total: number;
  days_done: number;
  next_day: string | null;
  symbols: string[];
};

export function replayActive(info: ReplayInfo | null | undefined): boolean {
  return !!info && ["LOADING", "PLAYING", "PAUSED", "FINISHED"].includes(info.status);
}

/** While a replay plays, state, chart and bot buttons talk to the replay engine. */
let replayRouting = false;
export function setReplayRouting(on: boolean): void {
  replayRouting = on;
}
/**
 * Which bot this page drives: "live" is the main desk (bot 1), "bot2"…"bot5"
 * the extra bots (each PAPER or LIVE, own settings and book), "research" the
 * paper-only research desk.
 */
export type Desk = "live" | "bot2" | "bot3" | "bot4" | "bot5" | "research";
export const DESKS: Desk[] = ["live", "bot2", "bot3", "bot4", "bot5", "research"];
const DESK_KEY = "sma.desk";
let desk: Desk = "live";

function asDesk(value: string | null | undefined): Desk | null {
  return value && (DESKS as string[]).includes(value) ? (value as Desk) : null;
}

/** The SMA bot number of a desk: 1 for the main desk, 2-4 for the extra bots, null for research. */
/** The copy-settings route's name for a desk: "1"-"4" or "research". */
function deskKey(d: Desk): string {
  return d === "research" ? "research" : d === "live" ? "1" : d.slice(3);
}

export function deskBot(d: Desk = desk): number | null {
  if (d === "live") return 1;
  if (d === "research") return null;
  return Number(d.slice(3));
}

/** The desk for this page: `?desk=` in the address wins, then this browser's last choice. */
export function initialDesk(): Desk {
  if (typeof window === "undefined") return "live";
  const asked = asDesk(new URLSearchParams(window.location.search).get("desk"));
  if (asked) return asked;
  try {
    return asDesk(localStorage.getItem(DESK_KEY)) ?? "live";
  } catch {
    return "live";
  }
}

export function setDesk(next: Desk): void {
  desk = next;
  try {
    localStorage.setItem(DESK_KEY, next);
  } catch {
    /* private mode: the address still says which desk */
  }
}

export function currentDesk(): Desk {
  return desk;
}

/** The research desk has its own copy of these routes; the mode switch and replay are live-desk only. */
const RESEARCH_PREFIXES = ["/api/state", "/api/chart", "/api/history", "/api/config", "/api/trade-symbols", "/api/bot/"];
/** Bots 2-4 have the same routes under /api/bots/{n}/, their own PAPER/LIVE switch included. */
const BOT_PREFIXES = [...RESEARCH_PREFIXES, "/api/mode"];

/** Each bot has its own replay (they can play together): this desk's bot, 1 for the research desk. */
function replayPath(path: string): string {
  const bot = deskBot() ?? 1;
  return `${path}${path.includes("?") ? "&" : "?"}bot=${bot}`;
}

function route(path: string): string {
  if (desk === "research") {
    const hit = RESEARCH_PREFIXES.find((prefix) => path.startsWith(prefix));
    return hit ? path.replace("/api/", "/api/research/") : path;
  }
  // A desk following its own bot's replay reads that bot's replay engine (bots 2-4 too).
  if (replayRouting) {
    if (path.startsWith("/api/state")) return replayPath(path.replace("/api/state", "/api/replay/state"));
    if (path.startsWith("/api/chart")) return replayPath(path.replace("/api/chart", "/api/replay/chart"));
    if (path.startsWith("/api/bot/")) return replayPath(path.replace("/api/bot/", "/api/replay/bot/"));
  }
  const bot = deskBot();
  if (bot != null && bot > 1) {
    const hit = BOT_PREFIXES.find((prefix) => path.startsWith(prefix));
    return hit ? path.replace("/api/", `/api/bots/${bot}/`) : path;
  }
  return path;
}

/** FastAPI validation errors arrive as a list; say which field and why. */
function readableDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const loc = Array.isArray((item as { loc?: unknown[] }).loc) ? (item as { loc: unknown[] }).loc : [];
          const field = String(loc[loc.length - 1] ?? "").replace(/_/g, " ");
          return `${field ? `${field}: ` : ""}${String((item as { msg: unknown }).msg)}`;
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

/** The terminal answered, but with an error status: it is reachable. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/** True when the error means the terminal answered (an HTTP error), not that it is down. */
export function answered(err: unknown): boolean {
  return err instanceof ApiError;
}

/** `routed` false sends `path` as given, not to this page's desk (a call that names its own desk). */
async function request<T>(path: string, init?: RequestInit, timeoutMs = 12000, routed = true): Promise<T> {
  if (routed) path = route(path);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${SMA_API}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
    if (!res.ok) {
      onSignedOut(res.status);
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch {
        /* plain text */
      }
      throw new ApiError(typeof detail === "string" ? detail : readableDetail(detail), res.status);
    }
    // Awaited here so the timeout also covers a body that stalls.
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("The terminal did not answer. This is not a flat book.");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function stockPath(target: ArmTarget, symbol: string): string {
  const name = encodeURIComponent(symbol);
  if (target === "research") return `/api/research/config/stock/${name}`;
  return target === 1 ? `/api/config/stock/${name}` : `/api/bots/${target}/config/stock/${name}`;
}

/** Backtest server (backend/backtest_server.py): replays only, stops itself when idle. */
export type BacktestServerStatus = {
  role: string;
  desk_url: string;
  link: boolean;
  idle_min: number;
  idle_left_sec: number;
  busy: boolean;
  groww: boolean;
  groww_error: string | null;
  synced_at: number | null;
  synced: string[];
  sync_error: string | null;
  /** Stocks the scanner armed here, kept on top of the desk's Trade lists at start-up. */
  scanner_armed?: number;
  /** Market hours only: after `stop_at` (and on weekends) it stops once nothing is running. */
  market_hours_only?: boolean;
  /** Copy the desk's settings at every start (off by default: only Sync from desk copies them). */
  auto_sync?: boolean;
  stop_at?: string;
  after_hours?: boolean;
  up_sec: number;
};

/** A named copy of a desk's strategy settings, or one automatic snapshot of a Save (strategy_presets.py). */
export type StrategyPreset = { id: number; name: string; desk: string; saved_at: string; settings: Partial<SmaConfig> };
export type StrategySnapshot = { saved_at: string; settings: Partial<SmaConfig> };
export type StrategyPresets = { desk: string; presets: StrategyPreset[]; history: StrategySnapshot[] };

/** The presets routes live on the main desk only and take the desk as a query, so they are not desk-routed. */
function presetDesk(): string {
  const bot = deskBot();
  return bot == null ? "research" : String(bot);
}

export const smaApi = {
  backtestStatus: () => request<BacktestServerStatus>("/api/backtest/status", undefined, 12000, false),
  backtestSync: () => request<BacktestServerStatus>("/api/backtest/sync", { method: "POST" }, 30000, false),
  backtestIdle: (minutes: number) =>
    request<BacktestServerStatus>("/api/backtest/idle", { method: "PUT", body: JSON.stringify({ minutes }) }, 12000, false),
  backtestHours: (on: boolean, stop_at?: string) =>
    request<BacktestServerStatus>("/api/backtest/hours", { method: "PUT", body: JSON.stringify({ on, stop_at }) }, 12000, false),
  backtestAutoSync: (on: boolean) =>
    request<BacktestServerStatus>("/api/backtest/auto-sync", { method: "PUT", body: JSON.stringify({ on }) }, 12000, false),
  backtestStop: (force = false) =>
    request<{ stopping: boolean }>(`/api/backtest/stop${force ? "?force=true" : ""}`, { method: "POST" }, 12000, false),
  state: () => request<SmaState>("/api/state"),
  /** `symbol` draws that watched stock without moving the chart focus (another tab, a held view). */
  chart: (limit = 240, symbol?: string | null, interval?: number | null) => {
    const q = new URLSearchParams();
    if (limit !== 240) q.set("limit", String(limit));
    if (symbol) q.set("symbol", symbol);
    // Candles of this many minutes drawn from the 1-minute tape (finer than the bot's own, or a size it does not divide into).
    if (interval) q.set("interval", String(interval));
    const qs = q.toString();
    return request<ChartPayload>(qs ? `/api/chart?${qs}` : "/api/chart");
  },
  /** Past 1-minute candles from Groww. Times are IST wall clock, YYYY-MM-DDTHH:MM. */
  /** runId marks one replay run's trades; without it, the practice/real book plus the latest run. */
  history: (symbol: string, start: string, end: string, interval = 1, runId?: number | null) =>
    request<ChartPayload & { symbol: string; from: string; to: string; interval?: number }>(
      `/api/history?${new URLSearchParams({
        symbol,
        start,
        end,
        interval: String(interval),
        ...(runId != null ? { run_id: String(runId) } : {}),
      }).toString()}`,
      undefined,
      70000
    ),
  config: () => request<SmaConfig>("/api/config"),
  /** Trailing stop / trail / target sized from the stock's own candles, per candle size (tsl_calc.py). Read-only. */
  tslCalc: (symbol: string, sizes: number[], atrPeriod = 14, stopMult = 1.5, trailMult = 0.5, targetMult = 3) =>
    request<TslCalcResult>(
      `/api/tsl-calc?${new URLSearchParams({
        symbol,
        sizes: sizes.join(","),
        atr_period: String(atrPeriod),
        stop_mult: String(stopMult),
        trail_mult: String(trailMult),
        target_mult: String(targetMult),
      }).toString()}`,
      undefined,
      60000,
      false
    ),
  strategyPresets: () => request<StrategyPresets>(`/api/config/presets?desk=${presetDesk()}`, undefined, 12000, false),
  saveStrategyPreset: (name: string, settings: Partial<SmaConfig>) =>
    request<StrategyPresets>("/api/config/presets", { method: "POST", body: JSON.stringify({ name, desk: presetDesk(), settings }) }, 12000, false),
  deleteStrategyPreset: (id: number) =>
    request<StrategyPresets>(`/api/config/presets/${id}?desk=${presetDesk()}`, { method: "DELETE" }, 12000, false),
  saveConfig: (body: Partial<SmaConfig>) =>
    request<SmaConfig>("/api/config", { method: "PUT", body: JSON.stringify(body) }),
  /** Put this desk's strategy settings back to the defaults (Trade list, mode and name stay). */
  resetConfig: (confirmLive = false, withStocks = false) =>
    request<{ changed: string[]; config: SmaConfig }>("/api/config/reset", {
      method: "POST",
      body: JSON.stringify({ confirm_live: confirmLive, stock_settings: withStocks }),
    }),
  stockConfig: (symbol: string) => request<StockConfig>(`/api/config/stock/${encodeURIComponent(symbol)}`),
  saveStockConfig: (symbol: string, body: Partial<SmaConfig>) =>
    request<StockConfig>(`/api/config/stock/${encodeURIComponent(symbol)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  resetStockConfig: (symbol: string) =>
    request<StockConfig>(`/api/config/stock/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  setTradeSymbol: (symbol: string, armed: boolean, source?: string | null) =>
    request<SmaConfig>("/api/trade-symbols", {
      method: "POST",
      body: JSON.stringify({ symbol, armed, source: source ?? undefined }),
    }),
  /**
   * Arm or unarm a stock on a chosen desk, whatever desk this page is on:
   * 1 = main desk, 2-4 = bots 2-4, "research" = the practice research desk.
   */
  setTradeSymbolOn: (target: ArmTarget, symbol: string, armed: boolean, source?: string | null) =>
    request<SmaConfig>(
      target === "research" ? "/api/research/trade-symbols" : target === 1 ? "/api/trade-symbols" : `/api/bots/${target}/trade-symbols`,
      { method: "POST", body: JSON.stringify({ symbol, armed, source: source ?? undefined }) },
      undefined,
      false
    ),
  /** Replace one desk's strategy settings with another's. Trade list, mode and name stay. */
  copySettings: (from: Desk, to: Desk, withStocks: boolean, confirmLive = false) =>
    request<{ changed: string[]; config: SmaConfig }>(
      "/api/bots/copy-settings",
      {
        method: "POST",
        body: JSON.stringify({
          from_desk: deskKey(from),
          to_desk: deskKey(to),
          stock_settings: withStocks,
          confirm_live: confirmLive,
        }),
      },
      undefined,
      false
    ),
  /** Settings file of the chosen desks ("1"-"5", "research") and the fast-mover card. Read-only. */
  exportSettings: (desks: string[], fast = true) =>
    request<SettingsFile>(
      `/api/settings/export?desks=${encodeURIComponent(desks.join(","))}&fast=${fast}`,
      undefined,
      30000,
      false
    ),
  /** Write a settings file onto the chosen desks. Mode and names are never written; a LIVE bot needs confirmLive. */
  importSettings: (body: {
    file: SettingsFile;
    desks: string[];
    stock_settings: boolean;
    trade_lists: boolean;
    fast_bot: boolean;
    confirm_live: boolean;
  }) =>
    request<{ desks: { desk: string; changed: string[]; added: string[]; notes: string[] }[]; fast_bot: boolean | null }>(
      "/api/settings/import",
      { method: "POST", body: JSON.stringify(body) },
      30000,
      false
    ),
  /** Copy stocks onto another bot's Trade list. The source bot keeps them. */
  copyTradeSymbols: (fromBot: 1 | 2 | 3 | 4 | 5, toBot: 1 | 2 | 3 | 4 | 5, symbols: string[], source?: string | null) =>
    request<{ added: string[]; skipped: { symbol: string; why: string }[]; config: SmaConfig }>(
      "/api/trade-symbols/copy",
      {
        method: "POST",
        body: JSON.stringify({ from_bot: fromBot, to_bot: toBot, symbols, source: source ?? undefined }),
      },
      undefined,
      false
    ),
  /** A chosen bot's settings, whatever desk this page is on. */
  configOn: (bot: 1 | 2 | 3 | 4 | 5) =>
    request<SmaConfig>(bot === 1 ? "/api/config" : `/api/bots/${bot}/config`, undefined, undefined, false),
  saveConfigOn: (bot: 1 | 2 | 3 | 4 | 5, body: Partial<SmaConfig>) =>
    request<SmaConfig>(
      bot === 1 ? "/api/config" : `/api/bots/${bot}/config`,
      { method: "PUT", body: JSON.stringify(body) },
      undefined,
      false
    ),
  /** One stock's settings on a chosen desk, whatever desk this page is on. */
  stockConfigOn: (target: ArmTarget, symbol: string) =>
    request<StockConfig>(stockPath(target, symbol), undefined, undefined, false),
  saveStockConfigOn: (target: ArmTarget, symbol: string, body: Partial<SmaConfig>) =>
    request<StockConfig>(stockPath(target, symbol), { method: "PUT", body: JSON.stringify(body) }, undefined, false),
  /** The research desk's settings (its Trade list), whatever desk this page is on. */
  researchConfig: () => request<SmaConfig>("/api/research/config", undefined, undefined, false),
  /** The PAPER bots' practice wallet (shared by bots 1-4, whatever desk this page is on). */
  wallet: () => request<Wallet>("/api/wallet", undefined, undefined, false),
  walletAdd: (amount: number) =>
    request<Wallet>("/api/wallet/add", { method: "POST", body: JSON.stringify({ amount }) }, undefined, false),
  walletWithdraw: (amount: number) =>
    request<Wallet>("/api/wallet/withdraw", { method: "POST", body: JSON.stringify({ amount }) }, undefined, false),
  walletRepay: (amount?: number) =>
    request<Wallet>("/api/wallet/repay", { method: "POST", body: JSON.stringify(amount ? { amount } : {}) }, undefined, false),
  walletStatement: () => request<WalletEntry[]>("/api/wallet/statement", undefined, undefined, false),
  walletLoans: () => request<WalletEntry[]>("/api/wallet/loans", undefined, undefined, false),
  walletReset: () => request<Wallet>("/api/wallet/reset", { method: "POST" }, undefined, false),
  walletMargin: (margin_pct: number) =>
    request<Wallet>("/api/wallet/margin", { method: "PUT", body: JSON.stringify({ margin_pct }) }, undefined, false),
  /** Start a cross scan (read-only; candles only). Returns at once; poll `crossScan`. */
  crossScanStart: (symbols: string[], minutes: CrossScanMinutes = 5, force = false) =>
    request<CrossScan>(
      "/api/cross-scan/start",
      { method: "POST", body: JSON.stringify({ symbols, minutes, force }) },
      20000,
      false
    ),
  crossScan: () => request<CrossScan>("/api/cross-scan", undefined, undefined, false),
  /** Start a chop/trend scan (read-only; today's SMA crosses, TRENDING / MIXED / CHOPPY per stock). */
  chopScanStart: (
    symbols: string[],
    minutes: CrossScanMinutes = 5,
    force = false,
    opts: { quiet_min?: number; alert_on_quiet?: boolean; price_min?: number; price_max?: number } = {}
  ) =>
    request<ChopScan>(
      "/api/chop-scan/start",
      {
        method: "POST",
        body: JSON.stringify({
          symbols,
          minutes,
          force,
          quiet_min: opts.quiet_min ?? 60,
          alert_on_quiet: opts.alert_on_quiet ?? false,
          price_min: opts.price_min ?? 0,
          price_max: opts.price_max ?? 0,
        }),
      },
      20000,
      false
    ),
  chopScan: () => request<ChopScan>("/api/chop-scan", undefined, undefined, false),
  /** Start the 09:30 morning checklist (read-only; candles only). Returns at once; poll `morningScan`. */
  morningScanStart: (symbols: string[], force = false) =>
    request<MorningScan>("/api/morning-scan/start", { method: "POST", body: JSON.stringify({ symbols, force }) }, 20000, false),
  morningScan: () => request<MorningScan>("/api/morning-scan", undefined, undefined, false),
  /** Read the day's first N candles of each stock (Most active "First candles"; read-only). Poll `firstCandles`. */
  firstCandlesStart: (body: FirstCandleRule & { symbols: string[]; at?: string }) =>
    request<FirstCandleScan>("/api/first-candles/start", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  firstCandles: () => request<FirstCandleScan>("/api/first-candles", undefined, undefined, false),
  /** Start the carry-over scan (read-only; candles only). Returns at once; poll `carryScan`. */
  carryScanStart: (symbols: string[], minutes: number, force = false) =>
    request<CarryScan>("/api/carry-scan/start", { method: "POST", body: JSON.stringify({ symbols, minutes, force }) }, 20000, false),
  carryScan: () => request<CarryScan>("/api/carry-scan", undefined, undefined, false),
  /** Start the Turning scan (read-only; candles only). Returns at once; poll `turnScan`. */
  turnScanStart: (body: { symbols: string[]; minutes: number; count: number; rule: TurnRule; need_fast: boolean; alert: boolean; force?: boolean }) =>
    request<TurnScan>("/api/turn-scan/start", { method: "POST", body: JSON.stringify({ force: true, ...body }) }, 20000, false),
  turnScan: () => request<TurnScan>("/api/turn-scan", undefined, undefined, false),
  /** Stop one running Scalp scan; the last finished rows stay (backend main.scan_stop). */
  scanStop: (kind: "cross" | "chop" | "morning" | "high52" | "carry" | "turn" | "first") =>
    request<{ stopped: boolean; running: boolean }>(`/api/scan-stop/${kind}`, { method: "POST" }, 20000, false),
  /** Start the 52-week high scan (read-only; Groww daily candles). Returns at once; poll `high52Scan`. */
  high52ScanStart: (symbols: string[], force = false) =>
    request<High52Scan>("/api/high52-scan/start", { method: "POST", body: JSON.stringify({ symbols, force }) }, 20000, false),
  high52Scan: () => request<High52Scan>("/api/high52-scan", undefined, undefined, false),
  /** Sideways test (read-only research on past candles; backend regime_study.py). */
  regimeStudy: () => request<RegimeStudy>("/api/regime-study", undefined, undefined, false),
  morningStudy: () => request<MorningStudy>("/api/morning-study", undefined, undefined, false),
  morningStudyStart: (body: { symbols: string[]; days: number; at: string; minutes: number; min_move: number; delivery: boolean }) =>
    request<MorningStudy>("/api/morning-study/start", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  morningStudyStop: () => request<MorningStudy>("/api/morning-study/stop", { method: "POST" }, 20000, false),
  /** Second candle test (backend second_candle.py): the Most active routine measured on every stock and day. */
  secondCandle: (rows = true) => request<SecondCandleView>(`/api/second-candle${rows ? "" : "?rows=false"}`, undefined, 30000, false),
  secondCandleStart: (body: {
    symbols: string[];
    days: number;
    minutes: number;
    candle_no: number;
    candle_nos?: number[];
    top_n: number;
    target_points: number;
    square_off: string;
    trails: number[];
    min_body_pct: number;
    side_rule?: SecondCandleSideRule;
    majority_by?: "COUNT" | "SIZE";
    dir_rule?: CandleDirRule;
    ha?: boolean;
  }) => request<SecondCandleView>("/api/second-candle/start", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  secondCandleStop: () => request<SecondCandleView>("/api/second-candle/stop", { method: "POST" }, 20000, false),
  regimeStudyStart: (body: { symbols: string[]; days: number; minutes: number; window: number }) =>
    request<RegimeStudy>("/api/regime-study/start", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  regimeStudyStop: () => request<RegimeStudy>("/api/regime-study/stop", { method: "POST" }, 20000, false),
  /** Strategy lab (backend strategy_lab.py): base trades with every rule's answer, any choice evaluated in seconds. */
  strategyLab: () => request<StrategyLabView>("/api/strategy-lab", undefined, undefined, false),
  strategyLabStart: (body: { symbols: string[]; date: string; end_date?: string; bot: number; label?: string; minutes?: number }) =>
    request<StrategyLabView & { job: StrategyLabJob }>("/api/strategy-lab/start", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  strategyLabStop: () => request<StrategyLabView>("/api/strategy-lab/stop", { method: "POST" }, 20000, false),
  strategyLabJob: (id: string) =>
    request<{ job: StrategyLabJob; result: StrategyLabResult; rules: StrategyLabRules }>(`/api/strategy-lab/${encodeURIComponent(id)}`, undefined, 30000, false),
  strategyLabEvaluate: (id: string, choice: StrategyLabChoice) =>
    request<StrategyLabResult>(`/api/strategy-lab/${encodeURIComponent(id)}/evaluate`, { method: "POST", body: JSON.stringify(choice) }, 30000, false),
  strategyLabRecommend: (id: string, choice: StrategyLabChoice) =>
    request<StrategyLabRecommend>(`/api/strategy-lab/${encodeURIComponent(id)}/recommend`, { method: "POST", body: JSON.stringify(choice) }, 60000, false),
  strategyLabDelete: (id: string) => request<StrategyLabView>(`/api/strategy-lab/${encodeURIComponent(id)}`, { method: "DELETE" }, 20000, false),
  /** Saved scans: the days, and one day's runs (latest day when none is given). */
  /** A desk's open P&L through a day, one point a minute ("1"-"4" or "research"; latest day when none is given). */
  openPnl: (desk: string, day?: string) =>
    request<OpenPnlLog>(`/api/open-pnl?desk=${encodeURIComponent(desk)}${day ? `&day=${day}` : ""}`, undefined, undefined, false),
  /** P&L dashboard: the backtest runs and market days it can read. */
  pnlDashOptions: () => request<PnlDashOptions>("/api/pnl-dashboard/options", undefined, 30000, false),
  /** P&L dashboard: curves, highs / lows / closes and the P&L by time of day for the picked runs or days. */
  pnlDashboard: (q: PnlDashQuery) => {
    const p = new URLSearchParams({ source: q.source });
    if (q.runs?.length) p.set("runs", q.runs.join(","));
    if (q.desks?.length) p.set("desks", q.desks.join(","));
    if (q.start) p.set("start", q.start);
    if (q.end) p.set("end", q.end);
    if (q.stocks?.length) p.set("stocks", q.stocks.join(","));
    if (q.basis) p.set("basis", q.basis);
    if (q.step) p.set("step", String(q.step));
    if (q.weekdays?.length) p.set("weekdays", q.weekdays.join(","));
    if (q.qty && q.qty > 0) p.set("qty", String(Math.round(q.qty)));
    return request<PnlDashboard>(`/api/pnl-dashboard?${p.toString()}`, undefined, 60000, false);
  },
  /** A bot's replay runs that have an open P&L log, newest first. */
  openPnlReplays: (bot: number) => request<{ bot: number; runs: OpenPnlReplayRun[] }>(`/api/open-pnl/replays?bot=${bot}`, undefined, undefined, false),
  /** One replay run's open P&L through one of its days (its last day when none is given). */
  openPnlReplay: (runId: number, day?: string) =>
    request<OpenPnlReplayLog>(`/api/open-pnl/replay/${runId}${day ? `?day=${day}` : ""}`, undefined, undefined, false),
  scanHistory: (day?: string, kind?: ScanKind) => {
    const q = new URLSearchParams();
    if (day) q.set("day", day);
    if (kind) q.set("kind", kind);
    const qs = q.toString();
    return request<ScanHistory>(`/api/scan-history${qs ? `?${qs}` : ""}`, undefined, undefined, false);
  },
  scanHistoryRun: (id: number) => request<ScanRunDetail>(`/api/scan-history/${id}`, undefined, 20000, false),
  /** Desk only: copy the backtest server's saved scans this desk lacks (scan_sync.py). */
  scanHistoryPull: (days = 14) =>
    request<{ copied: number; checks: number; already: number; errors: string[] }>(`/api/scan-history/pull?days=${days}`, { method: "POST" }, 120000, false),
  /** Check how a saved scan turned out (after its day closed). Returns at once; poll scanHistoryRun. */
  scanHistoryCheck: (id: number) =>
    request<ScanCheckStatus>(`/api/scan-history/${id}/check`, { method: "POST" }, undefined, false),
  /** Turn the server's Trend quality re-scan on/off, how often, and the choices it scans with. */
  chopScanAuto: (body: ChopAutoUpdate) =>
    request<ChopAuto>("/api/chop-scan/auto", { method: "PUT", body: JSON.stringify(body) }, undefined, false),
  trendAlerts: () => request<TrendAlerts>("/api/chop-scan/trend-alerts", undefined, undefined, false),
  saveTrendAlerts: (body: Partial<Omit<TrendAlerts, "recent">>) =>
    request<TrendAlerts>("/api/chop-scan/trend-alerts", { method: "PUT", body: JSON.stringify(body) }, undefined, false),
  /** Scan & arm: presets, the run in progress, the last runs. */
  scanArm: () => request<ScanArmState>("/api/scan-arm", undefined, undefined, false),
  scanArmSave: (body: Partial<ScanArmPreset>) =>
    request<ScanArmPreset>("/api/scan-arm/presets", { method: "PUT", body: JSON.stringify(body) }, undefined, false),
  scanArmDelete: (id: string) =>
    request<{ deleted: string }>(`/api/scan-arm/presets/${encodeURIComponent(id)}`, { method: "DELETE" }, undefined, false),
  /** Run a preset now (scan, then arm the matches; arming only). A LIVE bot needs confirmLive. */
  scanArmRun: (presetId: string, confirmLive = false) =>
    request<{ started: boolean }>(
      "/api/scan-arm/run",
      { method: "POST", body: JSON.stringify({ preset_id: presetId, confirm_live: confirmLive }) },
      undefined,
      false
    ),
  /** Pause / resume the terminal's Scalp scans (Cross scan, Trend quality). */
  scansPause: (paused: boolean) =>
    request<{ paused: boolean }>("/api/scans/pause", { method: "PUT", body: JSON.stringify({ paused }) }, undefined, false),
  scansPauseStatus: () => request<{ paused: boolean }>("/api/scans/pause", undefined, undefined, false),
  /** Every SMA bot: name, PAPER/LIVE, status, armed and held stocks, today's net. */
  bots: () => request<BotSummary[]>("/api/bots"),
  /** Panic on every SMA bot at once (main desk and bots 2-4). */
  /** All-bots day target / stop loss (₹, 0 = off) and today's combined P&L before charges. */
  allBotsGuard: () => request<AllBotsGuard>("/api/bots/guard"),
  pnlAlerts: () => request<PnlAlertSettings>("/api/bots/pnl-alerts"),
  /** Fast-move alerts (fast_move.py): armed / held stocks moving fast, and fast movers in the Trend quality scans. */
  /** Send handed-off stocks back to the bot they came from at the end of the day (handoff_chain.py). */
  handoffReturn: () => request<HandoffReturn>("/api/bots/handoff-return"),
  handoffReturnSave: (on: boolean, at?: string) =>
    request<HandoffReturn>("/api/bots/handoff-return", { method: "PUT", body: JSON.stringify({ on, at }) }),
  handoffReturnNow: () =>
    request<HandoffReturn & { lines: string[] }>("/api/bots/handoff-return/now", { method: "POST" }, 20000),
  fastBot: () => request<FastBotSettings>("/api/bots/fast-mover"),
  saveFastBot: (body: Partial<Omit<FastBotSettings, "recent" | "moved_today" | "day">> & { confirm_live?: boolean }) =>
    request<FastBotSettings>("/api/bots/fast-mover", { method: "PUT", body: JSON.stringify(body) }),
  fastMoves: () => request<FastMoveSettings>("/api/fast-moves"),
  saveFastMoves: (body: Omit<FastMoveSettings, "recent" | "watches_armed" | "scans_enabled">) =>
    request<FastMoveSettings>("/api/fast-moves", { method: "PUT", body: JSON.stringify(body) }),
  savePnlAlerts: (body: PnlAlertSettings) =>
    request<PnlAlertSettings>("/api/bots/pnl-alerts", {
      method: "PUT",
      body: JSON.stringify({ profit: body.profit, loss: body.loss, telegram: body.telegram, call: body.call, basis: body.basis }),
    }),
  newsEntry: () => request<NewsEntrySettings>("/api/bots/news-entry"),
  saveNewsEntry: (body: NewsEntrySettings, confirmLive = false) =>
    request<NewsEntrySettings>("/api/bots/news-entry", {
      method: "PUT",
      body: JSON.stringify({
        bot: body.bot,
        impact: body.impact,
        scope: body.scope,
        max_age_min: body.max_age_min,
        max_day: body.max_day,
        confirm_live: confirmLive,
      }),
    }),
  testPnlAlerts: () => request<Record<string, string>>("/api/bots/pnl-alerts/test", { method: "POST" }),
  saveAllBotsGuard: (target: number, stop: number, squareoff: string) =>
    request<AllBotsGuard>("/api/bots/guard", { method: "PUT", body: JSON.stringify({ target, stop, squareoff }) }),
  killAllBots: () => request<{ bot: number; bot_status: string }[]>("/api/bots/kill-all", { method: "POST" }),
  setMode: (mode: "PAPER" | "LIVE", confirmLive = false) =>
    request<{ trading_mode: string }>("/api/mode", {
      method: "POST",
      body: JSON.stringify({ mode, confirm_live: confirmLive }),
    }),
  start: () => request<{ bot_status: string }>("/api/bot/start", { method: "POST" }),
  forceOrder: (symbol: string) =>
    request<{ bot_status: string; last_signal: string }>("/api/bot/force", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  pause: () => request<{ bot_status: string }>("/api/bot/pause", { method: "POST" }),
  kill: () => request<{ bot_status: string; halt_reason: string }>("/api/bot/kill", { method: "POST" }),
  /** Today's trade count back to 0; the daily cap counts again from here. Not during a replay. */
  resetTrades: () =>
    request<{ bot_status: string; trades_today: number; was: number }>("/api/bot/reset-trades", { method: "POST" }),
  /** Answer a 1-minute review. EXIT closes only the reviewed trade; WAIT only records the answer. */
  answerReview: (id: number, action: "exit" | "wait") =>
    request<ReviewItem & { result: string }>(`/api/bot/review/${id}/${action}`, { method: "POST" }),
  closePosition: (symbol: string) =>
    request<{ bot_status: string; last_signal: string }>("/api/bot/close", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  /** Close the open trade and open the same stock the other way at once (reverse_exit.py). LIVE needs confirmLive. */
  reversePosition: (symbol: string, confirmLive = false) =>
    request<{ bot_status: string; last_signal: string }>("/api/bot/reverse", {
      method: "POST",
      body: JSON.stringify({ symbol, confirm_live: confirmLive }),
    }),
  /** Newest 200 trades across all books; the page polls this. */
  trades: (): Promise<TradeRow[]> => request<TradeRow[]>("/api/trades").then((rows) => rows.map((r) => tradeBeforeCharges(r))),
  /** Recorded second-by-second prices, [[epoch seconds, price], ...]. Live market minutes only. */
  ticks: (symbol: string, start: number, end: number) =>
    request<{ symbol: string; ticks: [number, number][] }>(
      `/api/ticks?${new URLSearchParams({ symbol, start: String(start), end: String(end) })}`
    ),
  /** Turn the per-second Groww price fetch (and its record) on or off, for both desks. */
  setTickFeed: (on: boolean) =>
    request<{ on: boolean }>("/api/ticks/feed", { method: "PUT", body: JSON.stringify({ on }) }),
  /** One whole book, newest first, up to 20,000 trades. */
  /** One book; on a bot's desk only that bot's PAPER / LIVE trades. */
  tradeBook: (mode: TradeBookMode) =>
    request<TradeBookPayload>(
      `/api/trades/book?mode=${mode}${deskBot() != null && (mode === "PAPER" || mode === "LIVE") ? `&bot=${deskBot()}` : ""}`,
      undefined,
      30000
    ).then(
      (body): TradeBook => ({
        total: body.total,
        rows: body.rows.map(({ strategy_ref, ...row }) =>
          tradeBeforeCharges({
            ...row,
            strategy: strategy_ref == null ? null : body.strategies[strategy_ref] ?? null,
          })
        ),
      })
    ),
  /** One whole book for the Reports page: every SMA bot's trades (rows carry `bot`), whatever desk is open. */
  reportBook: (mode: TradeBookMode) =>
    request<TradeBookPayload>(`/api/trades/book?mode=${mode}`, undefined, 30000).then(
      (body): TradeBook => ({
        total: body.total,
        rows: body.rows.map(({ strategy_ref, ...row }) =>
          tradeBeforeCharges({
            ...row,
            strategy: strategy_ref == null ? null : body.strategies[strategy_ref] ?? null,
          })
        ),
      })
    ),
  tradeCounts: () =>
    request<Record<TradeBookMode, number>>(deskBot() != null ? `/api/trades/counts?bot=${deskBot()}` : "/api/trades/counts"),
  /** This desk's bot's replay (each bot has its own; they can play together). */
  replayInfo: () => request<ReplayInfo>(replayPath("/api/replay")),
  /** Every bot's replay, keyed "1"-"4". */
  replayAll: () => request<Record<string, ReplayInfo>>("/api/replay/all"),
  /** Backtest the Scalp page's own picks: each day, the top N at the pick time. */
  replayScalpPicks: (body: {
    date: string;
    end_date: string;
    universe: string[];
    pick_time: string;
    top_n: number;
    min_atr_pct: number;
    min_value_cr: number;
    require_bias: boolean;
    speed?: number;
  }) =>
    request<ReplayInfo>("/api/replay/scalp-picks", { method: "POST", body: JSON.stringify({ speed: 300, ...body }) }, 20000),
  /** Backtest the strong-trend picks (backend trend_picks.py): each day, the top N strong trends at the pick time,
   * traded by `bot` from that minute. Practice money; nothing is armed. */
  replayTrendPicks: (body: {
    date: string;
    end_date: string;
    universe: string[];
    pick_time: string;
    top_n: number;
    direction: TrendPickDirection;
    minutes: number;
    bot: number;
    speed?: number;
    /** Play the bot's hand-offs to Bot 2 / Bot 3 inside the run (on by default, like the replay bar). */
    play_handoffs?: boolean;
  }) => request<ReplayInfo>("/api/replay/trend-picks", { method: "POST", body: JSON.stringify({ speed: 300, ...body }) }, 20000),
  /** Backtest the Most active picks (backend active_picks.py): each day, the top N by the chosen column at the pick
   * time, traded by `bot` from that minute. Practice money; nothing is armed. */
  replayActivePicks: (body: {
    date: string;
    end_date: string;
    universe: string[];
    pick_time: string;
    top_n: number;
    rank: ActivePickRank;
    bias: ActivePickBias;
    min_value_cr: number;
    bot: number;
    play_handoffs: boolean;
    speed?: number;
  }) => request<ReplayInfo>("/api/replay/active-picks", { method: "POST", body: JSON.stringify({ speed: 300, ...body }) }, 20000),
  /** Stock ranking: backtest a bot's strategy on many stocks in batches, then rank the stocks (stock_rank.py). */
  stockRankStart: (body: {
    date: string;
    end_date: string;
    universe: string[];
    bot: number;
    batch: number;
    play_handoffs: boolean;
    lists?: string[];
  }) => request<StockRankJob>("/api/stock-rank", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  stockRanks: () => request<{ running: number | null; items: StockRankJob[] }>("/api/stock-rank", undefined, undefined, false),
  stockRank: (id: number, earlierDays = 0) =>
    request<StockRankView>(`/api/stock-rank/${id}?earlier_days=${earlierDays}`, undefined, 30000, false),
  stockRankStop: () => request<{ stopped: boolean }>("/api/stock-rank/stop", { method: "POST" }, 20000, false),
  stockRankDelete: (id: number) => request<{ deleted: number }>(`/api/stock-rank/${id}`, { method: "DELETE" }, 20000, false),
  trendPicksAuto: () => request<TrendPicksAuto>("/api/trend-picks/auto", undefined, undefined, false),
  /** Compare pick times: the same strong-trend pick backtest once per time, one after another (trend_compare.py). */
  trendCompareStart: (body: {
    date: string;
    end_date: string;
    universe: string[];
    times: string[];
    top_n: number;
    direction: TrendPickDirection;
    minutes: number;
    bot: number;
    lists?: string[];
    play_handoffs?: boolean;
    source?: "TREND" | "ACTIVE";
    rank?: ActivePickRank;
    bias?: ActivePickBias;
    min_value_cr?: number;
  }) => request<TrendCompare>("/api/trend-picks/compare", { method: "POST", body: JSON.stringify(body) }, 20000, false),
  trendCompares: () =>
    request<{ running: number | null; items: TrendCompare[] }>("/api/trend-picks/compare", undefined, undefined, false),
  trendCompare: (id: number) =>
    request<TrendCompare & { result: TrendCompareResult }>(`/api/trend-picks/compare/${id}`, undefined, 30000, false),
  trendCompareStop: () => request<{ stopped: boolean }>("/api/trend-picks/compare/stop", { method: "POST" }, 20000, false),
  trendCompareDelete: (id: number) =>
    request<{ deleted: number }>(`/api/trend-picks/compare/${id}`, { method: "DELETE" }, 20000, false),
  saveTrendPicksAuto: (body: Partial<Omit<TrendPicksAuto, "last_day" | "last_run_id" | "last_note">>) =>
    request<TrendPicksAuto>("/api/trend-picks/auto", { method: "PUT", body: JSON.stringify(body) }, 20000, false),
  /** `symbols` replays those stocks instead of the armed ones (they are not armed); `bot` whose settings (default 1). */
  /** This desk's bot's replay runs cut short (a deploy/restart, or Stop) with days left, newest first. */
  replayResumable: (bot?: number) =>
    request<ResumableRun[]>(bot ? `/api/replay/resumable?bot=${bot}` : replayPath("/api/replay/resumable"), undefined, undefined, false),
  /** Carry on a cut-short run from its first unfinished day, on the bot it belongs to. */
  replayResume: (runId: number, speed: number, bot: number) =>
    request<ReplayInfo>(
      `/api/replay/resume?bot=${bot}`,
      { method: "POST", body: JSON.stringify({ run_id: runId, speed }) },
      20000,
      false
    ),
  /** `asArmed`: the stocks the bot had armed on those days, each traded only while it was armed live (arm log). */
  /** `playHandoffs`: a stock handed to Bot 2 / Bot 3 stays in the run on that bot's settings (handoff_chain.py). */
  replayStart: (
    date: string,
    start: string,
    speed: number,
    endDate?: string,
    symbols?: string[],
    bot = 1,
    asArmed = false,
    playHandoffs = true
  ) =>
    request<ReplayInfo>(
      "/api/replay/start",
      {
        method: "POST",
        body: JSON.stringify({
          date,
          end_date: endDate || null,
          start,
          speed,
          symbols: symbols?.length ? symbols : null,
          bot,
          ...(asArmed ? { as_armed: true } : {}),
          play_handoffs: playHandoffs,
        }),
      },
      20000
    ),
  /** When each stock was armed on / taken off a desk's Trade list on one IST day (arm_log.py). */
  /** Stocks handed between the bots: a backtest run's (`runId`), else a market day's (handoff_log.py). */
  handoffs: (q: { runId?: number; day?: string; bot?: number } = {}) => {
    const p = new URLSearchParams();
    if (q.runId != null) p.set("run_id", String(q.runId));
    if (q.day) p.set("day", q.day);
    if (q.bot != null) p.set("bot", String(q.bot));
    return request<{ run_id: number | null; day: string | null; rows: HandoffRow[] }>(`/api/handoffs?${p.toString()}`);
  },
  armLog: (desk: string, day?: string) =>
    request<ArmLogDay>(`/api/arm-log?desk=${encodeURIComponent(desk)}${day ? `&day=${day}` : ""}`),
  /** The newest `limit` runs (server default 50, at most 500). */
  replayRuns: (limit?: number): Promise<ReplayRun[]> =>
    request<ReplayRun[]>(limit ? `/api/replay/runs?limit=${limit}` : "/api/replay/runs").then((runs) => runs.map((r) => runBeforeCharges(r))),
  replayRun: (id: number): Promise<ReplayRun> =>
    request<ReplayRun>(`/api/replay/runs/${id}`).then((r) => runBeforeCharges(r)),
  deleteReplayRun: (id: number) => request<{ deleted: number }>(`/api/replay/runs/${id}`, { method: "DELETE" }),
  /** `seek` moves the replayed day's clock to `at` (IST HH:MM): forward = fast-forward, back = the day replays to there. */
  replayControl: (action: "play" | "pause" | "stop" | "speed" | "seek", speed?: number, at?: string) =>
    request<ReplayInfo>(replayPath("/api/replay/control"), { method: "POST", body: JSON.stringify({ action, speed, at }) }, 20000),
  csvUrl: (mode?: "PAPER" | "LIVE" | "REPLAY" | "RESEARCH") =>
    `${SMA_API}/api/trades.csv${mode ? `?mode=${mode}` : ""}`,
  streamUrl: () => SMA_API.replace(/^http/, "ws") + "/ws/stream",
};

export function inr(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  return sign + "₹" + Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function px(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}


/**
 * The state as seen from one stock: its last price and open position come
 * from its book, so a chart of another watched stock draws its own entry and
 * stop. The chart focus's own state is returned as it is.
 */
export function stateForSymbol(state: SmaState | null, symbol: string | null | undefined): SmaState | null {
  const name = (symbol ?? "").toUpperCase();
  if (!state || !name || (state.symbol ?? "").toUpperCase() === name) return state;
  const book = state.books?.find((b) => b.symbol.toUpperCase() === name);
  const held = book && book.direction !== "FLAT" && book.entry_price != null;
  return {
    ...state,
    symbol: name,
    ltp: book?.ltp ?? 0,
    day_open: null,
    day_change_pct: null,
    position: held
      ? {
          direction: book.direction as "LONG" | "SHORT",
          qty: book.qty,
          entry_price: book.entry_price as number,
          ma_cross_price: book.entry_price as number,
          atr_at_entry: 0,
          sl_trigger: book.stop_active === false ? null : book.sl_trigger,
          sl_order_id: "",
          entry_time: "",
          mode: state.mode,
          stop_active: book.stop_active ?? true,
          trailing: book.trailing ?? false,
          target: book.target ?? null,
        }
      : null,
  };
}


/** Sideways test (backend regime_study.py): how often the next window trended after each signal's reading. */
export type RegimeBucket = {
  bucket: string;
  samples: number;
  trended_pct?: number;
  vs_base?: number;
  next_crosses?: number;
  move_atr?: number;
  cross_pnl_pct?: number;
  win_pct?: number;
};
export type RegimeSignal = { key: string; label: string; buckets: RegimeBucket[]; spread: number; pnl_spread: number; verdict: string };
/** Strategy lab (backend strategy_lab.py). A rule's name and its label on the screen. */
export type StrategyLabRule = { name: string; label: string };
export type StrategyLabRules = { entries: StrategyLabRule[]; exits: StrategyLabRule[]; stops: StrategyLabRule[]; targets: StrategyLabRule[] };
export type StrategyLabStop = "NONE" | "ATR" | "TSL" | "SUPERTREND" | "VWAP";
export type StrategyLabChoice = {
  entries: string[];
  exits: string[];
  stop: StrategyLabStop;
  targets: string[];
  entry_from?: string;
  entry_to?: string;
  qty?: number;
};
export type StrategyLabJob = {
  id: string;
  status: "RUNNING" | "DONE" | "STOPPED" | "FAILED" | "INTERRUPTED";
  label: string;
  bot: number;
  /** The candle the run read every stock on (null = the bot's own). */
  minutes?: number | null;
  symbols: string[];
  first: string;
  last: string;
  days: string[];
  done: number;
  total: number;
  errors: { symbol: string; error: string }[];
  trades: number;
  started_at: string | null;
  finished_at: string | null;
  settings: Record<string, unknown> | null;
  choice: StrategyLabChoice | null;
};
export type StrategyLabView = { running: boolean; current: StrategyLabJob | null; jobs: StrategyLabJob[]; rules: StrategyLabRules };
export type StrategyLabTotals = {
  trades: number;
  gross: number;
  net: number;
  charges: number;
  wins: number;
  win_pct: number;
  per_trade: number;
  profit_factor: number | null;
  days: number;
  green_days: number;
  green_pct: number;
  per_day: number;
  best_trade: number;
  worst_trade: number;
  best_day: number;
  worst_day: number;
  drawdown: number;
  stocks: number;
};
export type StrategyLabTrade = {
  symbol: string;
  day: string;
  side: "LONG" | "SHORT";
  entry_ts: number;
  entry_price: number;
  exit_ts: number;
  exit_price: number;
  reason: string;
  qty: number;
  gross: number;
  net: number;
  charges: number;
  best: number;
  worst: number;
};
export type StrategyLabResult = {
  totals: StrategyLabTotals;
  per_day: { day: string; trades: number; gross: number; net: number; cum_gross: number }[];
  per_stock: { symbol: string; trades: number; win_pct: number; gross: number; net: number; days: number; green_days: number; per_trade: number }[];
  by_reason: { reason: string; trades: number; gross: number; wins: number }[];
  skipped: { refused: number; in_position: number; outside_window: number; stock_cap: number };
  base_trades: number;
  choice: Required<StrategyLabChoice>;
  settings: Record<string, unknown>;
  trades?: StrategyLabTrade[];
  trades_cut?: number;
};
export type StrategyLabRecommendRow = {
  kind: "entry" | "exit" | "stop" | "target";
  name: string;
  label: string;
  on: boolean;
  gross_now: number;
  gross_changed: number;
  net_now: number;
  net_changed: number;
  delta_gross: number;
  delta_net: number;
  trades_now: number;
  trades_changed: number;
  win_pct_now: number;
  win_pct_changed: number;
  profit_factor_changed: number | null;
  green_pct_changed: number;
};
export type StrategyLabRecommend = {
  now: StrategyLabTotals;
  rows: StrategyLabRecommendRow[];
  best: {
    choice: Required<StrategyLabChoice>;
    totals: StrategyLabTotals;
    steps: { kind: string; name: string; net_before: number; net_after: number; trades: number }[];
    settings: Record<string, unknown>;
  };
};

export type RegimeReport = {
  samples: number;
  stocks?: number;
  days?: number;
  base_trended_pct?: number;
  base_next_crosses?: number;
  base_cross_pnl_pct?: number;
  min_bucket?: number;
  signals: RegimeSignal[];
  best: string | null;
  stopped?: boolean;
  /** Each stock's own scores and the bot it suits (older saved reports have none). */
  by_stock?: RegimeStock[];
};
/** One stock in the Sideways test: trend / choppy score and where it belongs (backend regime_study.suggest). */
export type RegimeStock = {
  symbol: string;
  windows: number;
  days: number;
  trend_score: number;
  chop_score: number;
  avg_crosses: number;
  follow_pct: number;
  follow_win_pct: number;
  efficiency: number;
  adx: number | null;
  move_atr: number;
  by_time: { at: string; windows: number; trended_pct: number }[];
  bot: 1 | 2 | 3 | null;
  verdict: "TRENDING" | "CHOPPY" | "MIXED" | "NOT_ENOUGH";
  headline: string;
  why: string[];
};
export type RegimeStudy = {
  status: "IDLE" | "RUNNING" | "DONE" | "FAILED";
  done: number;
  total: number;
  errors: { symbol: string; error: string }[];
  params: { symbols?: number; days?: number; minutes?: number; window?: number; sma_fast?: number; sma_slow?: number };
  started_at: string | null;
  finished_at: string | null;
  report: RegimeReport | null;
};

/** Choppy or trending read of one armed stock (backend stock_regime.py, the Trend quality card's arithmetic). */
export type StockRegime = {
  minutes: number;
  score: "TRENDING" | "MIXED" | "CHOPPY";
  crosses: number;
  phase: "BULL" | "BEAR" | "CHOP" | null;
  phase_since: string | null;
  phase_move_pct: number | null;
  phase_candles: number;
  strong_phases: number;
  phases: { side: "BULL" | "BEAR" | "CHOP"; start: number; end: number }[];
};
