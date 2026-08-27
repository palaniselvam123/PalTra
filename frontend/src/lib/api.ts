export type BotConfig = {
  session_mode: "demo" | "market";
  candle_interval_sec: number;
  range_duration_sec: number;
  rvol_threshold: number;
  risk_reward: number;
  trailing_enabled: boolean;
  adx_filter_enabled: boolean;
  adx_period: number;
  adx_threshold: number;
};

export type OpeningRangeRow = {
  symbol: string;
  high: number;
  low: number;
  rvol: number;
  qualifies: boolean;
  adx: number | null;
  adx_trending: boolean | null;
  bb_squeeze: boolean | null;
  bb_bandwidth_pct: number | null;
};

export type BotStatus = {
  enabled: boolean;
  status: "STOPPED" | "WAITING_FOR_OPEN" | "BUILDING_RANGE" | "ARMED" | "NO_RANGE" | "HALTED";
  config: BotConfig;
  range_ready: boolean;
  range_ends_in_sec: number | null;
  opening_ranges: OpeningRangeRow[];
  symbols_traded: string[];
  late_start: boolean;
  range_window: string | null;
};

export type FeedStatus = {
  source: "simulated" | "live";
  session: "OPEN" | "PRE_OPEN" | "CLOSED" | "WEEKEND";
  market_open: boolean;
  connected: boolean;
  stale: boolean;
  error: string | null;
  symbols: number;
  poll_interval_sec: number;
  last_tick_at: string | null;
  seconds_until_open: number | null;
  broker_session: boolean;
};

export type AccountSummary = {
  starting_capital: number;
  balance: number;
  realised_all_time: number;
  realised_today: number;
  unrealised: number;
  equity: number;
  open_exposure: number;
  exposure_ratio: number;
  open_positions: number;
  return_pct: number;
};

export type Transaction = {
  id: number;
  mode: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number;
  target: number;
  status: "OPEN" | "CLOSED" | "CANCELLED";
  strategy: string;
  source: string;
  exit_reason: string | null;
  turnover: number;
  risk_per_share: number;
  planned_risk: number;
  planned_reward: number;
  planned_rr: number | null;
  entry_charges: number;
  exit_charges: number;
  charges: number;
  charges_recorded: boolean;
  opened_at: string | null;
  closed_at: string | null;
  trade_date: string | null;
  entry_hour_ist: number | null;
  weekday: string | null;
  gross_pnl: number | null;
  net_pnl: number | null;
  pnl: number | null;
  charges_drag_pct: number | null;
  return_on_turnover_pct: number | null;
  r_multiple: number | null;
  holding_sec: number | null;
  outcome: "WIN" | "LOSS" | "BREAKEVEN" | "OPEN" | "VOID";
  ltp?: number | null;
  unrealised_pnl: number | null;
};

export type ReportSummary = {
  trades_total: number;
  trades_closed: number;
  trades_open: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate_pct: number;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number;
  expectancy: number;
  expectancy_r: number | null;
  avg_win: number;
  avg_loss: number;
  payoff_ratio: number | null;
  largest_win: number;
  largest_loss: number;
  max_win_streak: number;
  max_loss_streak: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  avg_holding_sec: number | null;
  total_charges: number;
  charges_coverage: string;
  total_turnover: number;
  unrealised_open: number;
};

export type Breakdown = {
  key: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  net_pnl: number;
  avg_pnl: number;
  avg_r: number | null;
  best: number;
  worst: number;
};

export type EquityPoint = {
  trade_id: number;
  at: string | null;
  pnl: number | null;
  cumulative: number;
  drawdown: number;
};

export type FullReport = {
  summary: ReportSummary;
  equity_curve: EquityPoint[];
  daily: (Breakdown & { date: string })[];
  by_symbol: Breakdown[];
  by_side: Breakdown[];
  by_source: Breakdown[];
  by_account: Breakdown[];
  by_strategy: Breakdown[];
  by_exit_reason: Breakdown[];
  by_weekday: Breakdown[];
  by_hour: Breakdown[];
  transactions: Transaction[];
  generated_at: string;
};

export type ReportFilters = {
  date_from?: string;
  date_to?: string;
  symbols?: string;
  side?: string;
  source?: string;
  strategy?: string;
  status?: string;
  outcome?: string;
  search?: string;
  account?: string;
};

export type ExpertView = {
  symbol: string;
  stance: "BULLISH" | "BEARISH" | "NEUTRAL";
  conviction: number;
  sentiment_label: string;
  sentiment_score: number;
  thesis: string;
  catalysts: string[];
  risks: string[];
  invalidation: string;
  recency_note: string;
  sources: { title: string; url: string }[];
  model: string;
  web_search_used: boolean;
  latency_ms: number;
  created_at: string;
  analysis_id: number | null;
  age_sec: number;
};

export type ChatTurn = { role: "user" | "assistant"; content: string };


export type ChartCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Indicator series arrive aligned index-for-index with the candles, using
 *  null during warm-up so "not computable yet" stays visually honest. */
export type IndicatorSeries = (number | null)[];

export type ChartPayload = {
  symbol: string;
  interval: string;
  candles: ChartCandle[];
  indicators: {
    sma?: IndicatorSeries;
    ema_fast?: IndicatorSeries;
    ema_slow?: IndicatorSeries;
    vwap?: IndicatorSeries;
    bollinger?: { upper: IndicatorSeries; middle: IndicatorSeries; lower: IndicatorSeries; bandwidth_pct: IndicatorSeries };
    supertrend?: { value: IndicatorSeries; direction: IndicatorSeries };
    rsi?: IndicatorSeries;
    macd?: { macd: IndicatorSeries; signal: IndicatorSeries; histogram: IndicatorSeries };
    adx?: { adx: IndicatorSeries; plus_di: IndicatorSeries; minus_di: IndicatorSeries };
    atr?: IndicatorSeries;
  };
  note: string | null;
  source: string;
};

export type ChartMarker = {
  time: number;
  kind: "ENTRY" | "EXIT";
  side: "BUY" | "SELL";
  price: number;
  quantity: number;
  account: string;
  source: string;
  trade_id: number;
  pnl: number | null;
  exit_reason?: string | null;
};

export type CandlePattern = {
  time: number;
  name: string;
  label: string;
  bias: "BULLISH" | "BEARISH" | "INDECISION";
  close: number;
  trend: string;
  note: string;
};

export type ChartConfig = {
  intervals: string[];
  overlays: string[];
  oscillators: string[];
};


export type ScanConfig = {
  timeframe: string;
  fast_period: number;
  fast_type: "EMA" | "SMA";
  slow_period: number;
  slow_type: "EMA" | "SMA";
  signal_type: "GOLDEN_CROSS" | "DEATH_CROSS" | "BOTH";
  trend_filter: boolean;
  trend_period: number;
  volume_filter: boolean;
  volume_multiplier: number;
  volume_lookback: number;
  pattern_filter: boolean;
  pattern_lookback: number;
  adx_filter: boolean;
  adx_threshold: number;
  min_price: number;
  max_price: number;
  cooldown_minutes: number;
  once_per_session: boolean;
  universe: "WATCHLIST" | "CORE" | "CUSTOM";
  custom_symbols: string;
};

export type ScanOptions = {
  timeframes: string[];
  ma_types: string[];
  signal_types: string[];
  universes: string[];
  patterns: string[];
};

export type ScanChannel = { provider: string; enabled: boolean; configured: boolean };

export type ScanStatus = {
  running: boolean;
  timeframe: string;
  next_bar_close: string | null;
  universe_size: number;
  scanned_symbols: number;
  last_scan_at: string | null;
  last_error: string | null;
  signals_today: number;
  notes: string[];
  feed_source: string;
  channels: ScanChannel[];
};

export type ScanSignal = {
  id: number;
  symbol: string;
  timeframe: string;
  side: "BUY" | "SELL";
  price: number;
  fast_label: string;
  slow_label: string;
  fast_value: number;
  slow_value: number;
  volume_ratio: number | null;
  adx_value: number | null;
  pattern: string | null;
  candle_pattern: string | null;
  candle_desc: string;
  candle_ohlc: [number, number, number, number] | null;
  plain_english: string[];
  reasons: string[];
  alert_status: "SENT" | "FAILED" | "PENDING" | "SKIPPED";
  alert_error: string | null;
  alert_provider: string | null;
  feed_source: string;
  created_at: string | null;
};

export type WatchRow = {
  symbol: string;
  ltp: number;
  bid: number;
  ask: number;
  spread: number;
  spread_pct: number | null;
  volume: number;
  in_universe: boolean;
  has_position: boolean;
  intraday_allowed: boolean | null;
};

export type InstrumentResult = {
  symbol: string;
  name: string;
  series: string;
  intraday_allowed: boolean;
};

export type WatchlistState = {
  core: string[];
  added: { symbol: string; name: string; added_at: string | null }[];
};

export type DeskAccount = AccountSummary & {
  max_leverage: number;
  buying_power: number;
  margin_available: number;
};

export type DeskPosition = {
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  stop_loss: number;
  target: number;
  order_id: string;
  trade_id: number | null;
  ltp: number;
  value: number;
  unrealised: number;
  unrealised_pct: number;
};

export type OrderPreview = {
  symbol: string;
  side: string;
  quantity: number;
  ltp: number;
  expected_fill: number;
  order_value: number;
  estimated_charges: number;
  estimated_round_trip_charges: number;
  slippage_round_trip: number;
  total_round_trip_cost: number;
  breakeven_move_per_share: number;
  breakeven_move_pct: number | null;
};

export type AiStatus = {
  configured: boolean;
  provider: string;
  model: string;
  web_search_enabled: boolean;
  gate_enabled: boolean;
  min_conviction: number;
  require_agreement: boolean;
  cache_ttl_sec: number;
  cached_symbols: string[];
  in_flight: string[];
  last_error: string | null;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws/live";

/** Drops empty values so an untouched filter never narrows the query. */
function queryString(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => request<{ status: string; mode: string }>("/api/health"),

  getMode: () => request<{ mode: string; kill_switch_active: boolean }>("/api/orders/mode"),
  setMode: (mode: "paper" | "live") =>
    request("/api/orders/mode", { method: "POST", body: JSON.stringify({ mode }) }),

  getPositions: () => request<any[]>("/api/orders/positions"),
  placeOrder: (body: {
    symbol: string;
    side: "BUY" | "SELL";
    entry_price: number;
    stop_loss: number;
    target: number;
  }) => request("/api/orders/place", { method: "POST", body: JSON.stringify(body) }),
  closePosition: (symbol: string) => request(`/api/orders/close/${symbol}`, { method: "POST" }),

  killSwitch: () => request("/api/orders/kill-switch", { method: "POST" }),
  resetKillSwitch: () => request("/api/orders/kill-switch/reset", { method: "POST" }),

  getRiskConfig: () => request<any>("/api/orders/risk-config"),
  setRiskConfig: (body: any) =>
    request("/api/orders/risk-config", { method: "POST", body: JSON.stringify(body) }),

  getSummary: () =>
    request<{
      total_pnl: number;
      trades_closed: number;
      win_rate_pct: number;
      profit_factor: number;
      max_drawdown: number;
    }>("/api/orders/summary"),
  getHistory: () => request<any[]>("/api/orders/history"),
  getAccount: () => request<AccountSummary>("/api/orders/account"),

  getBotStatus: () => request<BotStatus>("/api/bot/status"),
  startBot: () => request<BotStatus>("/api/bot/start", { method: "POST" }),
  stopBot: () => request<BotStatus>("/api/bot/stop", { method: "POST" }),
  setBotConfig: (body: Partial<BotConfig>) =>
    request<BotStatus>("/api/bot/config", { method: "POST", body: JSON.stringify(body) }),

  getFeedStatus: () => request<FeedStatus>("/api/marketdata/status"),
  setFeedSource: (source: "simulated" | "live", poll_interval_sec = 2.0) =>
    request<FeedStatus>("/api/marketdata/source", {
      method: "POST",
      body: JSON.stringify({ source, poll_interval_sec }),
    }),
  probeSdk: () => request<any>("/api/marketdata/probe"),
  diagnoseMarketData: () =>
    request<{
      verdict: string;
      detail: string;
      checks: { name: string; kind: string; ok: boolean; forbidden?: boolean; error: string | null }[];
    }>("/api/marketdata/diagnose"),

  getScanner: () => request<{ universe_size: number; qualifying: any[] }>("/api/scanner/opening-range"),

  saveCredentials: (body: { broker: string; api_key: string; api_secret: string; totp_secret: string }) =>
    request("/api/auth/credentials", { method: "POST", body: JSON.stringify(body) }),
  getCredentialStatus: (broker: string) => request<any>(`/api/auth/credentials/${broker}`),
  login: (broker: string) => request<any>(`/api/auth/login/${broker}`, { method: "POST" }),
  testTotp: (broker: string) => request<any>(`/api/auth/test-totp/${broker}`, { method: "POST" }),

  getReport: (filters: ReportFilters = {}) => request<FullReport>(`/api/reports/full${queryString(filters)}`),
  getReportOptions: () =>
    request<{
      symbols: string[];
      sources: string[];
      strategies: string[];
      exit_reasons: string[];
      date_min: string | null;
      date_max: string | null;
    }>("/api/reports/options"),
  getTransaction: (id: number) =>
    request<{ transaction: Transaction; ai_analyses: any[] }>(`/api/reports/transaction/${id}`),
  // Not a JSON endpoint — the browser downloads it, so hand back a URL.
  reportCsvUrl: (filters: ReportFilters = {}) => `${API_BASE}/api/reports/export.csv${queryString(filters)}`,

  getAiStatus: () => request<AiStatus>("/api/ai/status"),
  saveAiKey: (api_key: string) => request("/api/ai/key", { method: "POST", body: JSON.stringify({ api_key }) }),
  deleteAiKey: () => request("/api/ai/key", { method: "DELETE" }),
  testAiKey: (model?: string) =>
    request<{ ok: boolean; model: string; model_available: boolean; detail: string }>("/api/ai/test", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  setAiConfig: (body: Partial<Omit<AiStatus, "configured" | "provider" | "cached_symbols" | "in_flight" | "last_error">>) =>
    request("/api/ai/config", { method: "POST", body: JSON.stringify(body) }),
  analyzeSymbol: (symbol: string, force = false) =>
    request<{ cached: boolean; view: ExpertView }>("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify({ symbol, force }),
    }),
  getCachedView: (symbol: string) => request<{ view: ExpertView | null }>(`/api/ai/view/${symbol}`),
  // --- Manual trading desk (its own virtual wallet, separate from the bot) ---
  // --- Charting ---
  // --- Automated scanner & alert engine ---
  scanConfig: () => request<{ config: ScanConfig; options: ScanOptions }>("/api/scan/config"),
  setScanConfig: (body: Partial<ScanConfig>) =>
    request<{ config: ScanConfig }>("/api/scan/config", { method: "POST", body: JSON.stringify(body) }),
  scanStatus: () => request<ScanStatus>("/api/scan/status"),
  scanStart: () => request<ScanStatus>("/api/scan/start", { method: "POST" }),
  scanStop: () => request<ScanStatus>("/api/scan/stop", { method: "POST" }),
  scanNow: () => request<any>("/api/scan/scan-now", { method: "POST" }),
  scanSignals: (limit = 50) => request<ScanSignal[]>(`/api/scan/signals?limit=${limit}`),
  scanChannels: () => request<ScanChannel[]>("/api/scan/channels"),
  saveScanChannel: (body: { provider: string; enabled: boolean; target: string; secret: string; extra?: string }) =>
    request<ScanChannel[]>("/api/scan/channels", { method: "POST", body: JSON.stringify(body) }),
  deleteScanChannel: (provider: string) =>
    request<ScanChannel[]>(`/api/scan/channels/${provider}`, { method: "DELETE" }),
  testScanAlert: () => request<{ ok: boolean; provider: string }>("/api/scan/test-alert", { method: "POST" }),

  chartConfig: () => request<ChartConfig>("/api/chart/config"),
  chartCandles: (symbol: string, interval: string, indicators: string[], limit = 500) =>
    request<ChartPayload>(
      `/api/chart/candles?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}` +
        (indicators.length ? `&indicators=${indicators.join(",")}` : "")
    ),
  chartMarkers: (symbol: string) =>
    request<ChartMarker[]>(`/api/chart/markers?symbol=${encodeURIComponent(symbol)}`),
  chartPatterns: (symbol: string, interval: string) =>
    request<{ patterns: CandlePattern[]; note: string | null }>(
      `/api/chart/patterns?symbol=${encodeURIComponent(symbol)}&interval=${interval}`
    ),

  deskWatchlist: () => request<WatchRow[]>("/api/manual/watchlist"),

  // --- Instrument search + editable watchlist (search any NSE stock, not just the bot's core 20) ---
  searchInstruments: (q: string, limit = 15) =>
    request<InstrumentResult[]>(`/api/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getWatchlist: () => request<WatchlistState>("/api/watchlist"),
  addToWatchlist: (symbol: string) =>
    request<{ symbol: string; name: string; was_new: boolean }>("/api/watchlist/add", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  removeFromWatchlist: (symbol: string) =>
    request<{ removed: string }>(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  getInstrumentStats: () =>
    request<{ mainboard_equity: number; intraday_eligible: number }>("/api/instruments/stats"),
  deskAccount: () => request<DeskAccount>("/api/manual/account"),
  deskPositions: () => request<DeskPosition[]>("/api/manual/positions"),
  deskHistory: () => request<any[]>("/api/manual/history"),
  deskSummary: () =>
    request<{
      total_pnl: number;
      trades_closed: number;
      win_rate_pct: number;
      profit_factor: number;
      max_drawdown: number;
    }>("/api/manual/summary"),
  deskPreview: (symbol: string, side: "BUY" | "SELL", quantity: number) =>
    request<OrderPreview>("/api/manual/preview", {
      method: "POST",
      body: JSON.stringify({ symbol, side, quantity }),
    }),
  deskPlace: (body: {
    symbol: string;
    side: "BUY" | "SELL";
    quantity: number;
    stop_loss?: number | null;
    target?: number | null;
  }) => request<any>("/api/manual/place", { method: "POST", body: JSON.stringify(body) }),
  deskClose: (symbol: string) => request<any>(`/api/manual/close/${symbol}`, { method: "POST" }),
  deskSquareOffAll: () => request<any>("/api/manual/square-off-all", { method: "POST" }),
  deskSetCapital: (starting_capital: number) =>
    request<DeskAccount>("/api/manual/capital", {
      method: "POST",
      body: JSON.stringify({ starting_capital }),
    }),

  getChatStatus: () =>
    request<{ configured: boolean; model: string; suggestions: string[] }>("/api/chat/status"),
  askChat: (message: string, history: ChatTurn[]) =>
    request<{ reply: string; model: string; facts_summary: Record<string, unknown> }>("/api/chat/ask", {
      method: "POST",
      body: JSON.stringify({ message, history }),
    }),

  getAiAnalyses: (symbol?: string, limit = 25) =>
    request<any[]>(`/api/ai/analyses${queryString({ symbol, limit: String(limit) })}`),
};
