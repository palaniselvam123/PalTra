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

export type StopType = "ATR" | "SMA_GAP" | "TSL";

export type SmaConfig = {
  /** This bot's name, e.g. "Scalper". */
  bot_name?: string | null;
  /** "SMA" (cross, default) or "PATTERN": candle-pattern entries closed at the candle's end. */
  entry_mode?: "SMA" | "PATTERN";
  /** Pattern candle size in minutes. */
  pattern_tf?: 1 | 3 | 5;
  /** Candle interval in minutes the SMA strategy trades on (built from the 1-minute tape). */
  candle_minutes?: CandleMinutes;
  /** Patterns only with the SMA trend. */
  pattern_trend?: boolean;
  pattern_set?: "STRONG" | "ALL";
  /** Skip candles whose usual range is under this many times the round-trip charges (0 = off). */
  pattern_min_edge?: number;
  symbol: string;
  trade_symbols?: string[];
  exchange: string;
  qty: number;
  sma_fast: number;
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
  /** SMA cross exit (on unless false): an opposite cross closes the trade. Off: other exits or square-off. */
  cross_exit?: boolean;
  /** 1-minute human review (off by default): ask EXIT or WAIT when an open trade's SMA gap narrows into this band. */
  review_on?: boolean;
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

export type ArmTarget = 1 | 2 | 3 | 4 | "research";

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

export const smaApi = {
  state: () => request<SmaState>("/api/state"),
  /** `symbol` draws that watched stock without moving the chart focus (another tab, a held view). */
  chart: (limit = 240, symbol?: string | null) => {
    const q = new URLSearchParams();
    if (limit !== 240) q.set("limit", String(limit));
    if (symbol) q.set("symbol", symbol);
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
  setTradeSymbol: (symbol: string, armed: boolean) =>
    request<SmaConfig>("/api/trade-symbols", {
      method: "POST",
      body: JSON.stringify({ symbol, armed }),
    }),
  /**
   * Arm or unarm a stock on a chosen desk, whatever desk this page is on:
   * 1 = main desk, 2-4 = bots 2-4, "research" = the practice research desk.
   */
  setTradeSymbolOn: (target: ArmTarget, symbol: string, armed: boolean) =>
    request<SmaConfig>(
      target === "research" ? "/api/research/trade-symbols" : target === 1 ? "/api/trade-symbols" : `/api/bots/${target}/trade-symbols`,
      { method: "POST", body: JSON.stringify({ symbol, armed }) },
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
  /** Every SMA bot: name, PAPER/LIVE, status, armed and held stocks, today's net. */
  bots: () => request<BotSummary[]>("/api/bots"),
  /** Panic on every SMA bot at once (main desk and bots 2-4). */
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
  replayStart: (date: string, start: string, speed: number, endDate?: string, symbols?: string[], bot = 1) =>
    request<ReplayInfo>(
      "/api/replay/start",
      {
        method: "POST",
        body: JSON.stringify({ date, end_date: endDate || null, start, speed, symbols: symbols?.length ? symbols : null, bot }),
      },
      20000
    ),
  replayRuns: (): Promise<ReplayRun[]> =>
    request<ReplayRun[]>("/api/replay/runs").then((runs) => runs.map((r) => runBeforeCharges(r))),
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
