// "Hide what came after" on the chart (display only): cut a chart payload and its trades at a moment, so a finished
// backtest can be read candle by candle as the bot saw it, without the later candles and lines giving the answer away.
import { parseClock } from "@/lib/format";
import type { ChartPayload, TradeRow } from "@/lib/smaApi";

/** `payload` with everything from `end` (epoch seconds, exclusive) on removed; null `end` = unchanged. */
export function cutChart<T extends ChartPayload>(payload: T | null, end: number | null): T | null {
  if (!payload || end == null) return payload;
  // Trades whose exit lies in the hidden part: their entry marker must not show the result.
  const hiddenExits = new Set(
    payload.markers.filter((m) => m.kind === "EXIT" && m.time >= end && m.trade_ref).map((m) => m.trade_ref as string)
  );
  return {
    ...payload,
    candles: payload.candles.filter((c) => c.time < end),
    markers: payload.markers
      .filter((m) => m.time < end)
      .map((m) => (m.trade_ref && hiddenExits.has(m.trade_ref) ? { ...m, net_pnl: null, gross_pnl: null, open: true } : m)),
    blocked: payload.blocked?.filter((b) => b.time < end),
    pressure: payload.pressure?.filter((p) => p.time < end),
    flow: payload.flow?.filter((p) => p.time < end),
    flow_decisions: payload.flow_decisions?.filter((d) => d.end <= end),
    gate_notes: payload.gate_notes?.filter((n) => n.end <= end),
    trend_reads: payload.trend_reads?.filter((r) => r.ts <= end),
  };
}

function seconds(text: string | null | undefined): number | null {
  const when = text ? parseClock(text) : null;
  return when ? Math.floor(when.getTime() / 1000) : null;
}

/** Trades entered before `end`; one that closed later reads as still open (no exit, no result). */
export function cutTrades(trades: TradeRow[], end: number | null): TradeRow[] {
  if (end == null) return trades;
  const out: TradeRow[] = [];
  for (const t of trades) {
    const entered = seconds(t.entry_time);
    if (entered != null && entered >= end) continue;
    const closed = seconds(t.exit_time);
    if (closed != null && closed >= end) {
      out.push({ ...t, exit_time: null, exit_price: null, exit_reason: null, exit_flow: null, gross_pnl: null, net_pnl: null });
    } else {
      out.push(t);
    }
  }
  return out;
}

/** The next / previous order (entry or exit) around `end` in the uncut payload; null when there is none. */
export function orderAfter(payload: ChartPayload | null, end: number | null): ChartPayload["markers"][number] | null {
  if (!payload || end == null) return null;
  const later = payload.markers.filter((m) => (m.kind === "ENTRY" || m.kind === "EXIT") && m.time >= end);
  return later.length ? later.reduce((a, b) => (b.time < a.time ? b : a)) : null;
}

export function orderBefore(payload: ChartPayload | null, end: number | null): ChartPayload["markers"][number] | null {
  if (!payload) return null;
  const earlier = payload.markers.filter((m) => (m.kind === "ENTRY" || m.kind === "EXIT") && (end == null || m.time < end));
  return earlier.length ? earlier.reduce((a, b) => (b.time > a.time ? b : a)) : null;
}
