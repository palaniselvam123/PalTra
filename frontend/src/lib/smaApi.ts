/** Client for the SMA + ATR terminal.

Locally that is `uvicorn main:app --port 8001`. On the deployed site the same
routes are mounted at `/sma` on this host, so the browser must not call
127.0.0.1.
*/

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
  max_daily_loss: number;
  max_trades_per_day: number;
  square_off_time: string;
  /** HH:MM. No new entries from this time. */
  entry_cutoff_time?: string;
  trading_mode: "PAPER" | "LIVE";
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

export type SmaState = {
  bot_status: string;
  halt_reason: string;
  /** REPLAY while a past day is replaying (practice only). */
  mode: "PAPER" | "LIVE" | "REPLAY";
  data_source: string;
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
    /** Groww's last refusal on this stock today (its own words), until an order fills. */
    last_reject?: string | null;
    last_reject_at?: string | null;
  }[];
  /** Unrealized net of every held stock, not only the chart's. */
  open_net_total?: number;
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

export type ChartFilters = {
  use_vwap: boolean;
  use_rsi: boolean;
  rsi_long_min: number;
  rsi_long_max: number;
  rsi_short_min: number;
  rsi_short_max: number;
  /** The ATR stop is the one in use, so the ATR line matters. */
  atr_stop: boolean;
};

export type ChartPayload = {
  candles: Candle[];
  markers: {
    time: number;
    direction: "LONG" | "SHORT";
    price: number;
    kind: "ENTRY" | "EXIT" | string;
    /** The trade's net P&L once it has closed, when the API sends it. */
    net_pnl?: number | null;
    /** Only on EXIT markers from /api/history: why the trade closed. */
    reason?: string | null;
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
  direction: "LONG" | "SHORT";
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

export type TradeBookMode = "PAPER" | "LIVE" | "REPLAY";

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

export type ReplayRun = {
  id: number;
  created_at: string | null;
  start_date: string;
  end_date: string;
  start_time: string;
  symbols: string[];
  /** Snapshot of the strategy settings the run used. */
  settings: Record<string, string | number | boolean | null>;
  status: "RUNNING" | "FINISHED" | "STOPPED";
  days_total: number;
  days_done: number;
  totals: ReplayRunTotals;
  days?: ReplayDayRow[];
  /** Per-stock split, on the run detail only. */
  stocks?: ReplayStockRow[];
};

export function replayActive(info: ReplayInfo | null | undefined): boolean {
  return !!info && ["LOADING", "PLAYING", "PAUSED", "FINISHED"].includes(info.status);
}

/** While a replay plays, state, chart and bot buttons talk to the replay engine. */
let replayRouting = false;
export function setReplayRouting(on: boolean): void {
  replayRouting = on;
}
function route(path: string): string {
  if (!replayRouting) return path;
  if (path.startsWith("/api/state")) return path.replace("/api/state", "/api/replay/state");
  if (path.startsWith("/api/chart")) return path.replace("/api/chart", "/api/replay/chart");
  if (path.startsWith("/api/bot/")) return path.replace("/api/bot/", "/api/replay/bot/");
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

async function request<T>(path: string, init?: RequestInit, timeoutMs = 12000): Promise<T> {
  path = route(path);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${SMA_API}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
    if (!res.ok) {
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

export const smaApi = {
  state: () => request<SmaState>("/api/state"),
  chart: (limit = 240) => request<ChartPayload>(limit === 240 ? "/api/chart" : `/api/chart?limit=${limit}`),
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
  closePosition: (symbol: string) =>
    request<{ bot_status: string; last_signal: string }>("/api/bot/close", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  /** Newest 200 trades across all books; the page polls this. */
  trades: () => request<TradeRow[]>("/api/trades"),
  /** One whole book, newest first, up to 20,000 trades. */
  tradeBook: (mode: TradeBookMode) =>
    request<TradeBookPayload>(`/api/trades/book?mode=${mode}`, undefined, 30000).then(
      (body): TradeBook => ({
        total: body.total,
        rows: body.rows.map(({ strategy_ref, ...row }) => ({
          ...row,
          strategy: strategy_ref == null ? null : body.strategies[strategy_ref] ?? null,
        })),
      })
    ),
  tradeCounts: () => request<Record<TradeBookMode, number>>("/api/trades/counts"),
  replayInfo: () => request<ReplayInfo>("/api/replay"),
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
  /** `symbols` replays those stocks instead of the armed ones (they are not armed). */
  replayStart: (date: string, start: string, speed: number, endDate?: string, symbols?: string[]) =>
    request<ReplayInfo>(
      "/api/replay/start",
      {
        method: "POST",
        body: JSON.stringify({ date, end_date: endDate || null, start, speed, symbols: symbols?.length ? symbols : null }),
      },
      20000
    ),
  replayRuns: () => request<ReplayRun[]>("/api/replay/runs"),
  replayRun: (id: number) => request<ReplayRun>(`/api/replay/runs/${id}`),
  deleteReplayRun: (id: number) => request<{ deleted: number }>(`/api/replay/runs/${id}`, { method: "DELETE" }),
  replayControl: (action: "play" | "pause" | "stop" | "speed", speed?: number) =>
    request<ReplayInfo>("/api/replay/control", { method: "POST", body: JSON.stringify({ action, speed }) }, 20000),
  csvUrl: (mode?: "PAPER" | "LIVE" | "REPLAY") =>
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
