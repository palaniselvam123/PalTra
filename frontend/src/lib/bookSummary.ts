/**
 * A trade book (NSE live or Simulation) summed up the way a backtest run is:
 * day-wise P&L, totals with max drawdown, and the split by stock. The maths
 * mirrors the server's replay summary (`_day_rows`, `_totals`, `_stock_rows`
 * in backend/replay.py) so a live summary and a backtest compare like for like.
 */
import type { ReplayDayRow, ReplayRun, ReplayRunTotals, ReplayStockRow, TradeRow } from "@/lib/smaApi";

const round2 = (v: number) => Math.round(v * 100) / 100;

export function dayRows(trades: TradeRow[]): ReplayDayRow[] {
  const days = new Map<string, Omit<ReplayDayRow, "gross" | "cumulative">>();
  for (const t of trades) {
    if (t.exit_price == null) continue;
    const d = days.get(t.date) ?? { date: t.date, trades: 0, wins: 0, losses: 0, profit: 0, loss: 0, charges: 0, net: 0 };
    const gross = t.gross_pnl ?? 0;
    d.trades += 1;
    if (gross > 0) {
      d.wins += 1;
      d.profit += gross;
    } else if (gross < 0) {
      d.losses += 1;
      d.loss += gross;
    }
    d.charges += t.brokerage_and_taxes ?? 0;
    d.net += t.net_pnl ?? gross;
    days.set(t.date, d);
  }
  let running = 0;
  return [...days.keys()].sort().map((key) => {
    const d = days.get(key)!;
    running += d.net;
    return {
      date: d.date,
      trades: d.trades,
      wins: d.wins,
      losses: d.losses,
      profit: round2(d.profit),
      loss: round2(d.loss),
      charges: round2(d.charges),
      net: round2(d.net),
      gross: round2(d.profit + d.loss),
      cumulative: round2(running),
    };
  });
}

export function totalsOf(days: ReplayDayRow[]): ReplayRunTotals {
  const sum = (k: "trades" | "wins" | "losses" | "profit" | "loss" | "charges" | "net") =>
    days.reduce((acc, d) => acc + d[k], 0);
  const trades = sum("trades");
  const wins = sum("wins");
  let peak = 0;
  let worst = 0;
  for (const d of days) {
    peak = Math.max(peak, d.cumulative);
    worst = Math.min(worst, d.cumulative - peak);
  }
  return {
    trades,
    wins,
    losses: sum("losses"),
    profit: round2(sum("profit")),
    loss: round2(sum("loss")),
    charges: round2(sum("charges")),
    net: round2(sum("net")),
    gross: round2(sum("profit") + sum("loss")),
    win_rate: trades ? Math.round((1000 * wins) / trades) / 10 : 0,
    max_drawdown: round2(worst),
    green_days: days.filter((d) => d.net > 0).length,
    red_days: days.filter((d) => d.net < 0).length,
  };
}

export function stockRows(trades: TradeRow[]): ReplayStockRow[] {
  const bySymbol = new Map<string, TradeRow[]>();
  for (const t of trades) {
    const key = (t.symbol || "").toUpperCase();
    bySymbol.set(key, [...(bySymbol.get(key) ?? []), t]);
  }
  const out: ReplayStockRow[] = [...bySymbol.entries()].map(([symbol, rows]) => {
    const days = dayRows(rows);
    const first = [...rows].filter((r) => r.strategy).sort((a, b) => a.id - b.id)[0];
    return { symbol, totals: totalsOf(days), days, strategy: first?.strategy ?? null };
  });
  out.sort((a, b) => (b.totals.trades > 0 ? 1 : 0) - (a.totals.trades > 0 ? 1 : 0) || b.totals.net - a.totals.net);
  return out;
}

/** Closed trades as a backtest-shaped summary. Open trades are left out. */
export function summarizeBook(trades: TradeRow[]): ReplayRun {
  const closed = trades.filter((t) => t.exit_price != null);
  const days = dayRows(closed);
  const latest = [...closed].filter((t) => t.strategy).sort((a, b) => b.id - a.id)[0];
  return {
    id: 0,
    created_at: null,
    start_date: days[0]?.date ?? "",
    end_date: days[days.length - 1]?.date ?? "",
    start_time: "09:15",
    symbols: [...new Set(closed.map((t) => t.symbol.toUpperCase()))].sort(),
    settings: latest?.strategy ?? {},
    status: "FINISHED",
    days_total: days.length,
    days_done: days.length,
    totals: totalsOf(days),
    days,
    stocks: stockRows(closed),
  };
}

/** Each distinct strategy the trades used, with how many trades used it. */
export function strategiesUsed(trades: TradeRow[], label: (s: NonNullable<TradeRow["strategy"]>) => string): [string, string][] {
  const counts = new Map<string, number>();
  for (const t of trades) {
    if (t.exit_price == null) continue;
    const key = t.strategy ? label(t.strategy) : "Not recorded";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => [name, `${n} trade${n === 1 ? "" : "s"}`]);
}
