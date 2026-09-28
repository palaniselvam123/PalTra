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

export type SmaConfig = {
  symbol: string;
  exchange: string;
  qty: number;
  sma_fast: number;
  sma_slow: number;
  atr_period: number;
  atr_multiplier: number;
  use_adx_filter: boolean;
  adx_threshold: number;
  max_daily_loss: number;
  max_trades_per_day: number;
  square_off_time: string;
  trading_mode: "PAPER" | "LIVE";
};

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
  mode: "PAPER" | "LIVE";
  data_source: string;
  last_error: string;
  last_signal: string;
  symbol: string;
  exchange: string;
  ltp: number;
  day_open?: number | null;
  day_change_pct?: number | null;
  sma9: number | null;
  sma21: number | null;
  atr14: number | null;
  adx14: number | null;
  position: null | {
    direction: "LONG" | "SHORT";
    qty: number;
    entry_price: number;
    ma_cross_price: number;
    atr_at_entry: number;
    sl_trigger: number;
    sl_order_id: string;
    entry_time: string;
    mode: string;
  };
  active_sl_trigger: number | null;
  unrealized_gross_pnl: number;
  estimated_charges: number;
  unrealized_net_pnl: number;
  sl_room: number | null;
  sl_room_pct: number | null;
  realized_net_pnl: number;
  trades_today: number;
  max_trades: number;
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
};

export type ChartPayload = {
  candles: Candle[];
  markers: { time: number; direction: "LONG" | "SHORT"; price: number; kind: string }[];
  entry_price: number | null;
  sl_trigger: number | null;
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
  atr_at_entry: number;
  sl_trigger_price: number;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  gross_pnl: number | null;
  brokerage_and_taxes: number | null;
  net_pnl: number | null;
  points: number | null;
  mode: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
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
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json() as Promise<T>;
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
  chart: () => request<ChartPayload>("/api/chart"),
  config: () => request<SmaConfig>("/api/config"),
  saveConfig: (body: Partial<SmaConfig>) =>
    request<SmaConfig>("/api/config", { method: "PUT", body: JSON.stringify(body) }),
  setMode: (mode: "PAPER" | "LIVE", confirmLive = false) =>
    request<{ trading_mode: string }>("/api/mode", {
      method: "POST",
      body: JSON.stringify({ mode, confirm_live: confirmLive }),
    }),
  start: () => request<{ bot_status: string }>("/api/bot/start", { method: "POST" }),
  pause: () => request<{ bot_status: string }>("/api/bot/pause", { method: "POST" }),
  kill: () => request<{ bot_status: string; halt_reason: string }>("/api/bot/kill", { method: "POST" }),
  trades: () => request<TradeRow[]>("/api/trades"),
  csvUrl: () => `${SMA_API}/api/trades.csv`,
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
