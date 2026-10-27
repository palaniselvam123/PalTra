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

export type StopType = "ATR" | "SMA_GAP" | "TSL" | "SUPERTREND";

export type SmaConfig = {
  /** This bot's name, e.g. "Scalper". */
  bot_name?: string | null;
  /** "SMA" (cross, default), "PATTERN" (candle-pattern entries closed at the candle's end) or
   * "COLOUR" (ride the candle colour: green buys, red sells, reverse on the other colour). */
  entry_mode?: "SMA" | "PATTERN" | "COLOUR";
  /** Pattern / colour candle size in minutes. */
  pattern_tf?: 1 | 3 | 5 | 10 | 15;
  /** Candle interval in minutes the SMA strategy trades on (built from the 1-minute tape). */
  candle_minutes?: CandleMinutes;
  /** Mixed candles: the SMA cross exit read on candles of this size (0 = the entry candle). */
  exit_candle_minutes?: number;
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
  tsl_mode?: "POINTS" | "PERCENT";
  tsl_sl_pct?: number;
  tsl_trail_pct?: number;
  tsl_target_pct?: number;
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
  /** Supertrend: entry only on its side; exit when it flips against the trade. */
  use_supertrend?: boolean;
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
  slope_exit?: boolean;
  slope_lookback?: number;
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
  uturn_run?: number;
  uturn_min_pct?: number;
  vturn_move_pct?: number;
  vturn_turn_pct?: number;
  vturn_within?: number;
  /** After a V-turn exit at a loss, move the stock to Bot 2 (flipped, gap mode). */
  vturn_handoff?: boolean;
  /** After a hand-off to Bot 2, Bot 2 orders at once the opposite way of the closed trade (no filters). */
  handoff_now?: boolean;
  /** Press Start by itself while STOPPED in PAPER and NSE is open (auto_start.py). Bots 1-4 only. */
  auto_start?: boolean;
  /** Book the profit at a daily pivot level: R1 (S1 for a short; R2 / S2 when the entry is already past it) or R2. */
  pivot_target?: "OFF" | "R1" | "R2";
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
  review_default_answer?: "PROMPT" | "EXIT" | "CONTINUE";
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
  bot: 1 | 2 | 3 | 4;
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
};

export type OpenPnlReplayRun = { run_id: number; first: string; last: string; days: number; points: number; saved_at: string };
export type OpenPnlReplayLog = Omit<OpenPnlLog, "desk" | "every_sec"> & { run_id: number };

/** One saved Cross scan / Trend quality pass (scan history), without its rows. */
export type ScanKind = "cross" | "chop";
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
export type ReviewStatus = "PENDING" | "USER_REVIEW_EXIT" | "USER_REVIEW_WAIT" | "NO_RESPONSE" | "ALREADY_CLOSED";

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

export type SmaState = {
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
    tsl_mode?: "POINTS" | "PERCENT";
    tsl_sl_points: number;
    tsl_trail_points: number;
    tsl_target_points: number;
    tsl_sl_pct?: number;
    tsl_trail_pct?: number;
    tsl_target_pct?: number;
    gap_sl_mult: number;
    gap_tp_mult: number;
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
  use_vwap: boolean;
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
export type CandleMinutes = 1 | 2 | 3 | 5 | 10 | 15;
export const CANDLE_MINUTES: CandleMinutes[] = [1, 2, 3, 5, 10, 15];
/** Exit candle sizes for mixed candles (exit_candle.py); 0 = the entry candle. */
export const EXIT_CANDLE_MINUTES = [0, 1, 2, 3, 5, 10, 15, 30, 60];

export type ArmTarget = 1 | 2 | 3 | 4 | "research";

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

export type ReplayInfo = {
  status: ReplayStatus;
  date: string | null;
  start: string;
  /** ISO time on the replayed day. */
  clock: string | null;
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
  /** Scalp-pick runs: the rule and each day's picks (date -> picks, best first). */
  pick_rule?: ScalpPickRule | null;
  picks?: Record<string, ScalpPick[]>;
};

export type ScalpPickRule = {
  pick_time: string;
  top_n: number;
  min_atr_pct: number;
  min_value_cr: number;
  require_bias: boolean;
};

export type ScalpPick = {
  symbol: string;
  score: number;
  bias: "LONG" | "SHORT" | "NONE";
  atr_pct: number | null;
  value_cr: number | null;
  move_5m_pct: number | null;
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
  days?: ReplayDayRow[];
  /** Per-stock split, on the run detail only. */
  stocks?: ReplayStockRow[];
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
 * Which bot this page drives: "live" is the main desk (bot 1), "bot2"…"bot4"
 * the extra bots (each PAPER or LIVE, own settings and book), "research" the
 * paper-only research desk.
 */
export type Desk = "live" | "bot2" | "bot3" | "bot4" | "research";
export const DESKS: Desk[] = ["live", "bot2", "bot3", "bot4", "research"];
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
  up_sec: number;
};

export const smaApi = {
  backtestStatus: () => request<BacktestServerStatus>("/api/backtest/status", undefined, 12000, false),
  backtestSync: () => request<BacktestServerStatus>("/api/backtest/sync", { method: "POST" }, 30000, false),
  backtestIdle: (minutes: number) =>
    request<BacktestServerStatus>("/api/backtest/idle", { method: "PUT", body: JSON.stringify({ minutes }) }, 12000, false),
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
  saveConfig: (body: Partial<SmaConfig>) =>
    request<SmaConfig>("/api/config", { method: "PUT", body: JSON.stringify(body) }),
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
  /** Copy stocks onto another bot's Trade list. The source bot keeps them. */
  copyTradeSymbols: (fromBot: 1 | 2 | 3 | 4, toBot: 1 | 2 | 3 | 4, symbols: string[], source?: string | null) =>
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
  configOn: (bot: 1 | 2 | 3 | 4) =>
    request<SmaConfig>(bot === 1 ? "/api/config" : `/api/bots/${bot}/config`, undefined, undefined, false),
  saveConfigOn: (bot: 1 | 2 | 3 | 4, body: Partial<SmaConfig>) =>
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
  /** Saved scans: the days, and one day's runs (latest day when none is given). */
  /** A desk's open P&L through a day, one point a minute ("1"-"4" or "research"; latest day when none is given). */
  openPnl: (desk: string, day?: string) =>
    request<OpenPnlLog>(`/api/open-pnl?desk=${encodeURIComponent(desk)}${day ? `&day=${day}` : ""}`, undefined, undefined, false),
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
  replayStart: (date: string, start: string, speed: number, endDate?: string, symbols?: string[], bot = 1, asArmed = false) =>
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
        }),
      },
      20000
    ),
  /** When each stock was armed on / taken off a desk's Trade list on one IST day (arm_log.py). */
  armLog: (desk: string, day?: string) =>
    request<ArmLogDay>(`/api/arm-log?desk=${encodeURIComponent(desk)}${day ? `&day=${day}` : ""}`),
  /** The newest `limit` runs (server default 50, at most 500). */
  replayRuns: (limit?: number): Promise<ReplayRun[]> =>
    request<ReplayRun[]>(limit ? `/api/replay/runs?limit=${limit}` : "/api/replay/runs").then((runs) => runs.map((r) => runBeforeCharges(r))),
  replayRun: (id: number): Promise<ReplayRun> =>
    request<ReplayRun>(`/api/replay/runs/${id}`).then((r) => runBeforeCharges(r)),
  deleteReplayRun: (id: number) => request<{ deleted: number }>(`/api/replay/runs/${id}`, { method: "DELETE" }),
  replayControl: (action: "play" | "pause" | "stop" | "speed", speed?: number) =>
    request<ReplayInfo>(replayPath("/api/replay/control"), { method: "POST", body: JSON.stringify({ action, speed }) }, 20000),
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
