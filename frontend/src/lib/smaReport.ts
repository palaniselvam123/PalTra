/**
 * The Reports page for the SMA bots' books, built in the browser from the
 * terminal's trade book (`/api/trades/book`). The figures follow the ORB
 * desk's report (`backend/app/services/reports.py`): closed trades only,
 * outcome by net P&L, equity curve and streaks in close order, breakdowns
 * best first.
 */
import type { Breakdown, EquityPoint, FullReport, ReportFilters, ReportSummary, Transaction } from "./api";
import { parseClock } from "./format";
import type { TradeRow } from "./smaApi";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const round = (v: number, digits = 2) => {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
};

/** IST wall-clock parts of a terminal time ("2026-10-07 09:29:00", naive IST). */
function istParts(text: string | null | undefined): { date: string; hour: number; weekday: string } | null {
  const when = text ? parseClock(text) : null;
  if (!when) return null;
  const ist = new Date(when.getTime() + 5.5 * 3600 * 1000);
  return {
    date: ist.toISOString().slice(0, 10),
    hour: ist.getUTCHours(),
    weekday: WEEKDAYS[(ist.getUTCDay() + 6) % 7],
  };
}

function strategyName(s: TradeRow["strategy"]): string {
  if (!s) return "SMA cross";
  const parts: string[] = [];
  if (String(s.entry_mode ?? "SMA") === "PATTERN") parts.push(`Candle patterns ${s.pattern_tf ?? 1}m`);
  else if (s.use_gap_mode) parts.push(`SMA ${s.sma_fast ?? 9}/${s.sma_slow ?? 21} gap mode`);
  else parts.push(`SMA ${s.sma_fast ?? 9}/${s.sma_slow ?? 21} cross`);
  if (s.flip_orders) parts.push("flipped");
  return parts.join(" · ");
}

/** One SMA trade row as a report transaction. `botName` names the bot that placed it. */
export function smaTransaction(t: TradeRow, botName: (bot: number) => string): Transaction {
  const entry = Number(t.entry_price) || 0;
  const qty = Number(t.qty) || 0;
  const turnover = entry * qty;
  const stop = t.sl_trigger_price ?? 0;
  const riskPerShare = stop ? Math.abs(entry - stop) : 0;
  const plannedRisk = riskPerShare * qty;
  const closed = t.exit_time != null && t.exit_price != null;
  const opened = istParts(t.entry_time);
  const shut = istParts(t.exit_time);
  const chargesRecorded = t.brokerage_and_taxes != null;
  const charges = round(Number(t.brokerage_and_taxes ?? 0));
  const gross = closed ? (t.gross_pnl ?? (t.direction === "SHORT" ? entry - Number(t.exit_price) : Number(t.exit_price) - entry) * qty) : null;
  const net = closed ? (t.net_pnl ?? gross) : null;
  const holding =
    closed && t.entry_time && t.exit_time
      ? Math.round(((parseClock(t.exit_time)?.getTime() ?? 0) - (parseClock(t.entry_time)?.getTime() ?? 0)) / 1000)
      : null;
  const bot = t.mode === "PAPER" || t.mode === "LIVE" ? Number(t.bot ?? 1) : null;
  return {
    id: t.id,
    ref: t.trade_ref ?? null,
    mode: t.mode,
    symbol: t.symbol.toUpperCase(),
    side: t.direction === "SHORT" ? "SELL" : "BUY",
    quantity: qty,
    entry_price: round(entry),
    exit_price: t.exit_price == null ? null : round(Number(t.exit_price)),
    stop_loss: round(stop),
    target: 0,
    status: closed ? "CLOSED" : "OPEN",
    strategy: strategyName(t.strategy),
    source: bot != null ? botName(bot) : t.mode === "REPLAY" ? `Replay${t.run_id ? ` run ${t.run_id}` : ""}` : "Research desk",
    account: t.mode,
    exit_reason: t.exit_reason,
    turnover: round(turnover),
    risk_per_share: round(riskPerShare),
    planned_risk: round(plannedRisk),
    planned_reward: 0,
    planned_rr: null,
    entry_charges: 0,
    exit_charges: charges,
    charges,
    charges_recorded: chargesRecorded,
    opened_at: t.entry_time,
    closed_at: closed ? t.exit_time : null,
    trade_date: (shut ?? opened)?.date ?? t.date ?? null,
    entry_hour_ist: opened?.hour ?? null,
    weekday: opened?.weekday ?? null,
    gross_pnl: gross == null ? null : round(gross),
    net_pnl: net == null ? null : round(net),
    pnl: net == null ? null : round(net),
    charges_drag_pct: gross && chargesRecorded ? round((Math.abs(charges) / Math.abs(gross)) * 100, 1) : null,
    return_on_turnover_pct: net != null && turnover ? round((net / turnover) * 100, 3) : null,
    r_multiple: net != null && plannedRisk ? round(net / plannedRisk, 2) : null,
    holding_sec: holding,
    outcome: !closed ? "OPEN" : (net ?? 0) > 0 ? "WIN" : (net ?? 0) < 0 ? "LOSS" : "BREAKEVEN",
    ltp: closed ? null : t.market_price ?? null,
    unrealised_pnl: closed ? null : t.mark_pnl ?? null,
  };
}

/** The same filters the ORB report applies, on transactions already in hand. */
export function matches(r: Transaction, f: ReportFilters): boolean {
  if (f.symbols && !f.symbols.split(",").includes(r.symbol)) return false;
  if (f.side && r.side !== f.side) return false;
  if (f.source && r.source !== f.source) return false;
  if (f.strategy && r.strategy !== f.strategy) return false;
  if (f.status && r.status !== f.status) return false;
  if (f.outcome && r.outcome !== f.outcome) return false;
  if (f.search) {
    const needle = f.search.toLowerCase();
    const hay = [r.symbol, r.side, r.source, r.strategy, r.exit_reason, r.ref].join(" ").toLowerCase();
    if (!hay.includes(needle)) return false;
  }
  if (f.date_from || f.date_to) {
    if (!r.trade_date) return false;
    if (f.date_from && r.trade_date < f.date_from) return false;
    if (f.date_to && r.trade_date > f.date_to) return false;
  }
  return true;
}

function streaks(outcomes: string[]): [number, number] {
  let bestWin = 0;
  let bestLoss = 0;
  let win = 0;
  let loss = 0;
  for (const o of outcomes) {
    if (o === "WIN") {
      win += 1;
      loss = 0;
    } else if (o === "LOSS") {
      loss += 1;
      win = 0;
    } else {
      win = 0;
      loss = 0;
    }
    bestWin = Math.max(bestWin, win);
    bestLoss = Math.max(bestLoss, loss);
  }
  return [bestWin, bestLoss];
}

function group(rows: Transaction[], key: (r: Transaction) => string | number | null): Breakdown[] {
  const buckets = new Map<string, Transaction[]>();
  for (const r of rows) {
    if (r.status !== "CLOSED") continue;
    const name = String(key(r) ?? "—");
    buckets.set(name, [...(buckets.get(name) ?? []), r]);
  }
  const out: Breakdown[] = [];
  for (const [name, list] of buckets) {
    const wins = list.filter((g) => g.outcome === "WIN").length;
    const net = list.reduce((sum, g) => sum + (g.net_pnl ?? 0), 0);
    const rs = list.map((g) => g.r_multiple).filter((v): v is number => v != null);
    out.push({
      key: name,
      trades: list.length,
      wins,
      losses: list.length - wins,
      win_rate_pct: round((wins / list.length) * 100, 1),
      net_pnl: round(net),
      avg_pnl: round(net / list.length),
      avg_r: rs.length ? round(rs.reduce((a, b) => a + b, 0) / rs.length) : null,
      best: round(Math.max(...list.map((g) => g.net_pnl ?? 0))),
      worst: round(Math.min(...list.map((g) => g.net_pnl ?? 0))),
    });
  }
  return out.sort((a, b) => b.net_pnl - a.net_pnl);
}

function summarise(rows: Transaction[]): { summary: ReportSummary; curve: EquityPoint[] } {
  const closed = rows
    .filter((r) => r.status === "CLOSED")
    .sort((a, b) => (parseClock(a.closed_at ?? "")?.getTime() ?? 0) - (parseClock(b.closed_at ?? "")?.getTime() ?? 0));
  const open = rows.filter((r) => r.status === "OPEN");
  const wins = closed.filter((r) => r.outcome === "WIN");
  const losses = closed.filter((r) => r.outcome === "LOSS");
  const sum = (list: Transaction[]) => list.reduce((s, r) => s + (r.net_pnl ?? 0), 0);
  const net = sum(closed);
  const grossProfit = sum(wins);
  const grossLoss = Math.abs(sum(losses));
  const avgWin = wins.length ? grossProfit / wins.length : 0;
  const avgLoss = losses.length ? grossLoss / losses.length : 0;
  const winRate = closed.length ? wins.length / closed.length : 0;
  const rs = closed.map((r) => r.r_multiple).filter((v): v is number => v != null);
  const holding = closed.map((r) => r.holding_sec).filter((v): v is number => v != null);
  const costed = closed.filter((r) => r.charges_recorded);

  let equity = 0;
  let peak = 0;
  let maxDd = 0;
  const curve: EquityPoint[] = closed.map((r) => {
    equity += r.net_pnl ?? 0;
    peak = Math.max(peak, equity);
    maxDd = Math.max(maxDd, peak - equity);
    return { trade_id: r.id, at: r.closed_at, pnl: r.net_pnl, cumulative: round(equity), drawdown: round(peak - equity) };
  });
  const [winStreak, lossStreak] = streaks(closed.map((r) => r.outcome));
  const winsOnly = closed.map((r) => r.net_pnl ?? 0).filter((v) => v > 0);
  return {
    curve,
    summary: {
      trades_total: rows.length,
      trades_closed: closed.length,
      trades_open: open.length,
      wins: wins.length,
      losses: losses.length,
      breakeven: closed.length - wins.length - losses.length,
      win_rate_pct: round(winRate * 100, 1),
      net_pnl: round(net),
      gross_profit: round(grossProfit),
      gross_loss: round(grossLoss),
      profit_factor: grossLoss ? round(grossProfit / grossLoss) : grossProfit ? round(grossProfit) : 0,
      expectancy: closed.length ? round(winRate * avgWin - (1 - winRate) * avgLoss) : 0,
      expectancy_r: rs.length ? round(rs.reduce((a, b) => a + b, 0) / rs.length) : null,
      avg_win: round(avgWin),
      avg_loss: round(avgLoss),
      payoff_ratio: avgLoss ? round(avgWin / avgLoss) : null,
      largest_win: winsOnly.length ? round(Math.max(...winsOnly)) : null,
      largest_loss: round(Math.min(0, ...closed.map((r) => r.net_pnl ?? 0))),
      max_win_streak: winStreak,
      max_loss_streak: lossStreak,
      max_drawdown: round(maxDd),
      // The SMA books have no wallet capital to measure against.
      max_drawdown_pct: 0,
      avg_holding_sec: holding.length ? Math.round(holding.reduce((a, b) => a + b, 0) / holding.length) : null,
      total_charges: round(costed.reduce((s, r) => s + r.charges, 0)),
      charges_coverage: `${costed.length}/${closed.length}`,
      total_turnover: round(rows.reduce((s, r) => s + (r.turnover ?? 0), 0)),
      unrealised_open: round(open.reduce((s, r) => s + (r.unrealised_pnl ?? 0), 0)),
    },
  };
}

/** The full report over the transactions that pass the filters. */
export function buildSmaReport(all: Transaction[], f: ReportFilters): FullReport {
  const rows = all.filter((r) => matches(r, f));
  const { summary, curve } = summarise(rows);
  const daily = group(rows, (r) => r.trade_date)
    .map((d) => ({ ...d, date: d.key }))
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  return {
    summary,
    equity_curve: curve,
    daily,
    by_symbol: group(rows, (r) => r.symbol),
    by_side: group(rows, (r) => r.side),
    by_source: group(rows, (r) => r.source),
    by_account: group(rows, (r) => r.account ?? r.mode),
    by_strategy: group(rows, (r) => r.strategy),
    by_exit_reason: group(rows, (r) => r.exit_reason),
    by_weekday: group(rows, (r) => r.weekday),
    by_hour: group(rows, (r) => r.entry_hour_ist).sort((a, b) => Number(a.key) - Number(b.key)),
    transactions: [...rows].sort(
      (a, b) =>
        (parseClock(b.closed_at ?? b.opened_at ?? "")?.getTime() ?? 0) -
        (parseClock(a.closed_at ?? a.opened_at ?? "")?.getTime() ?? 0)
    ),
    generated_at: new Date().toISOString(),
  };
}

/** Filter choices from the transactions themselves; the date range runs to the latest trade. */
export function smaOptions(all: Transaction[]) {
  const dates = all.map((r) => r.trade_date).filter((d): d is string => !!d).sort();
  const uniq = (list: (string | null | undefined)[]) => Array.from(new Set(list.filter((v): v is string => !!v))).sort();
  return {
    symbols: uniq(all.map((r) => r.symbol)),
    sources: uniq(all.map((r) => r.source)),
    strategies: uniq(all.map((r) => r.strategy)),
    exit_reasons: uniq(all.map((r) => r.exit_reason)),
    date_min: dates[0] ?? null,
    date_max: dates[dates.length - 1] ?? null,
  };
}
