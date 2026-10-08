/**
 * The SMA screens show P&L BEFORE charges (the owner's choice). Charges are
 * still recorded and shown on their own; they are just not taken off the P&L
 * figures. Display only: the server's own figures, the daily loss limit and the
 * alerts are unchanged.
 *
 * Trade lists and backtest runs are turned into this basis once, where they are
 * loaded (lib/smaApi.ts), so every table, total and chart agrees. A trade's
 * `net_pnl` then holds its P&L before charges.
 */
import type { ReplayDayRow, ReplayRun, ReplayRunTotals, SmaState, TradeRow } from "./smaApi";

const round2 = (v: number) => Math.round(v * 100) / 100;

/** A closed trade's P&L before charges (null while it is open). */
export function grossOf(t: Pick<TradeRow, "exit_price" | "gross_pnl" | "points" | "qty">): number | null {
  if (t.exit_price == null) return null;
  if (t.gross_pnl != null) return t.gross_pnl;
  return t.points != null ? round2(t.points * (t.qty || 0)) : null;
}

/** One trade with its P&L before charges in `net_pnl`. */
export function tradeBeforeCharges<T extends TradeRow>(t: T): T {
  const gross = grossOf(t);
  return gross == null ? t : { ...t, net_pnl: gross };
}

/** Day rows on P&L before charges: the running total follows the gross. */
function daysBeforeCharges(days: ReplayDayRow[]): ReplayDayRow[] {
  let running = 0;
  return days.map((d) => {
    running += d.gross;
    return { ...d, net: d.gross, cumulative: round2(running) };
  });
}

/** Totals from day rows (same maths as the server's `_totals`, on P&L before charges). */
function totalsFrom(base: ReplayRunTotals, days: ReplayDayRow[]): ReplayRunTotals {
  let peak = 0;
  let worst = 0;
  for (const d of days) {
    peak = Math.max(peak, d.cumulative);
    worst = Math.min(worst, d.cumulative - peak);
  }
  return {
    ...base,
    net: base.gross,
    max_drawdown: round2(worst),
    green_days: days.filter((d) => d.net > 0).length,
    red_days: days.filter((d) => d.net < 0).length,
  };
}

/** A backtest run with its P&L, running totals, drawdown and green/red days before charges. */
export function runBeforeCharges(run: ReplayRun): ReplayRun {
  const days = run.days ? daysBeforeCharges(run.days) : undefined;
  return {
    ...run,
    days,
    totals: days ? totalsFrom(run.totals, days) : { ...run.totals, net: run.totals.gross },
    stocks: run.stocks?.map((s) => {
      const sd = daysBeforeCharges(s.days);
      return { ...s, days: sd, totals: totalsFrom(s.totals, sd) };
    }),
  };
}

type Book = NonNullable<SmaState["books"]>[number];

/** A held stock's open P&L before charges, at its last price; null when flat. */
export function openGross(b: Pick<Book, "direction" | "qty" | "entry_price" | "ltp">): number | null {
  if (b.direction === "FLAT" || b.entry_price == null || b.ltp == null || !b.qty) return null;
  const points = b.direction === "SHORT" ? b.entry_price - b.ltp : b.ltp - b.entry_price;
  return round2(points * b.qty);
}

/** Today's closed P&L before charges per stock, from the trade list (already on this basis). */
export function closedTodayBySymbol(trades: TradeRow[], mode: string | undefined, today: string): Map<string, { pnl: number; trades: number }> {
  const out = new Map<string, { pnl: number; trades: number }>();
  const book = (mode || "PAPER").toUpperCase();
  for (const t of trades) {
    if (t.exit_price == null || t.date !== today || (t.mode || "PAPER").toUpperCase() !== book) continue;
    const key = t.symbol.toUpperCase();
    const cur = out.get(key) ?? { pnl: 0, trades: 0 };
    cur.pnl = round2(cur.pnl + (t.net_pnl ?? 0));
    cur.trades += 1;
    out.set(key, cur);
  }
  return out;
}
