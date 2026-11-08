"use client";

import clsx from "clsx";
import { Trophy } from "lucide-react";
import { useMemo } from "react";
import { GrowwLink } from "@/components/ui/growwLink";
import type { SecondCandleRow } from "@/lib/smaApi";
import { REV_STOPS, STOPS, fmt, mean, ordinal, outcome, reverseOutcome, signed } from "./secondCandleMath";

/**
 * "Which candle is best?" (the Second candle tab's third view): with several candles read in one run, each candle's
 * trades side by side under every way of trading them the test measures (held to the square-off, the best trailing
 * stop, the best target + stop, the best stop and reverse), the best of each marked, a plain-words verdict, and each
 * stock's best candle. Worked out in the browser from the run's rows, so the card's filters (place by volume, side, day,
 * reached, time window, search, ticked only; not the Candle filter) change every number. Read-only research.
 */
type Plan = { label: string; total: number };
type CandleResult = {
  no: number;
  trades: number;
  hitPct: number;
  avgBest: number | null;
  avgWorst: number | null;
  close: number;
  closeWin: number;
  trail: { pct: number; total: number } | null;
  stop: { stop: number; total: number; win: number } | null;
  reverse: { stop: number; total: number } | null;
  best: Plan;
};

const th = "px-2 py-1.5 text-left font-medium text-slate-400 whitespace-nowrap";
const td = "px-2 py-1 whitespace-nowrap";
const pos = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "");
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const argmax = <T,>(xs: T[], f: (x: T) => number | null | undefined) =>
  xs.reduce<T | null>((a, b) => ((f(b) ?? -Infinity) > (a == null ? -Infinity : f(a) ?? -Infinity) ? b : a), null);

function candleResult(no: number, part: SecondCandleRow[], target: number): CandleResult {
  const hits = part.filter((r) => r.hit).length;
  const close = sum(part.map((r) => r.close_pts));
  const trailKeys = Array.from(new Set(part.flatMap((r) => Object.keys(r.trail ?? {}))));
  const trails = trailKeys.map((k) => ({ pct: Number(k), total: sum(part.map((r) => r.trail?.[k] ?? r.close_pts)) }));
  const stops = STOPS.map((s) => {
    const pts = part.map((r) => outcome(r, target, s).pts);
    return { stop: s, total: sum(pts), win: pts.length ? (pts.filter((v) => v > 0).length / pts.length) * 100 : 0 };
  });
  const reverses = REV_STOPS.map((s) => ({
    stop: s,
    total: sum(part.map((r) => outcome(r, target, s).pts + (reverseOutcome(r, target, s)?.pts ?? 0))),
  }));
  const trail = argmax(trails, (t) => t.total);
  const stop = argmax(stops, (t) => t.total);
  const reverse = argmax(reverses, (t) => t.total);
  const plans: Plan[] = [{ label: "held to the square-off", total: close }];
  if (trail) plans.push({ label: `a ${trail.pct}% trailing stop`, total: trail.total });
  if (stop) plans.push({ label: `target ₹${target} with a ₹${stop.stop} stop`, total: stop.total });
  if (reverse) plans.push({ label: `target ₹${target}, ₹${reverse.stop} stop and reverse`, total: reverse.total });
  return {
    no,
    trades: part.length,
    hitPct: part.length ? (hits / part.length) * 100 : 0,
    avgBest: mean(part.map((r) => r.best_pts)),
    avgWorst: mean(part.map((r) => r.worst_pts)),
    close,
    closeWin: part.length ? (part.filter((r) => r.close_pts > 0).length / part.length) * 100 : 0,
    trail,
    stop,
    reverse,
    best: argmax(plans, (p) => p.total) ?? plans[0],
  };
}

export function CandleCompare({
  rows,
  candles,
  candleOf,
  target,
  stop,
  sideRule,
}: {
  /** The run's rows after the card's filters, every candle kept. */
  rows: SecondCandleRow[];
  candles: number[];
  candleOf: (r: SecondCandleRow) => number;
  /** The target the target + stop test uses on the card (₹ level). */
  target: number;
  /** The stop picked in the card's target + stop test, used for the per-stock table. */
  stop: number;
  sideRule?: string;
}) {
  const results = useMemo(() => candles.map((no) => candleResult(no, rows.filter((r) => candleOf(r) === no), target)), [rows, candles, candleOf, target]);
  const ranked = useMemo(() => [...results].filter((c) => c.trades).sort((a, b) => b.best.total - a.best.total), [results]);
  const top = ranked[0];
  const bestOf = {
    hitPct: argmax(results, (c) => (c.trades ? c.hitPct : null))?.no,
    avgBest: argmax(results, (c) => c.avgBest)?.no,
    avgWorst: argmax(results, (c) => (c.avgWorst == null ? null : -c.avgWorst))?.no,
    close: argmax(results, (c) => (c.trades ? c.close : null))?.no,
    trail: argmax(results, (c) => c.trail?.total)?.no,
    stop: argmax(results, (c) => c.stop?.total)?.no,
    reverse: argmax(results, (c) => c.reverse?.total)?.no,
    best: argmax(results, (c) => (c.trades ? c.best.total : null))?.no,
  };

  // Each stock's best candle with the card's target + stop (the stop picked in the target + stop test).
  const perStock = useMemo(() => {
    const by = new Map<string, Map<number, number>>();
    for (const r of rows) {
      const m = by.get(r.symbol) ?? new Map<number, number>();
      m.set(candleOf(r), (m.get(candleOf(r)) ?? 0) + outcome(r, target, stop).pts);
      by.set(r.symbol, m);
    }
    return Array.from(by.entries())
      .map(([symbol, m]) => {
        const best = argmax(Array.from(m.entries()), ([, v]) => v)!;
        return { symbol, per: m, bestNo: best[0], bestTotal: best[1] };
      })
      .sort((a, b) => b.bestTotal - a.bestTotal);
  }, [rows, candleOf, target, stop]);
  const wins = useMemo(() => {
    const out = new Map<number, number>();
    for (const s of perStock) if (s.bestTotal > 0) out.set(s.bestNo, (out.get(s.bestNo) ?? 0) + 1);
    return out;
  }, [perStock]);

  if (candles.length < 2) {
    return (
      <p className="rounded-lg border border-slate-800 p-3 text-xs text-slate-400">
        This run read one candle. Tick two or more candles under &quot;Read the candle(s)&quot; (for example the 1st, 2nd and 3rd) and run
        the test again to compare them here.
      </p>
    );
  }
  const mark = (on: boolean) => (on ? "rounded bg-emerald-500/15 px-1 font-semibold ring-1 ring-inset ring-emerald-400/40" : "");

  return (
    <div className="space-y-3">
      {top ? (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs text-slate-200">
          <div className="mb-1 flex items-center gap-1.5 font-semibold text-emerald-200">
            <Trophy className="h-4 w-4" /> Best candle: the {ordinal(top.no)}
          </div>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>
              {ordinal(top.no)} candle, {top.best.label}: ₹{fmt(top.best.total)} a share in all over {top.trades} trades (₹
              {fmt(top.trades ? top.best.total / top.trades : null)} a trade).
            </li>
            {ranked.slice(1).map((c) => (
              <li key={c.no} className="text-slate-400">
                {ordinal(c.no)} candle: best ₹{fmt(c.best.total)} ({c.best.label}, {c.trades} trades, ₹{fmt(c.trades ? c.best.total / c.trades : null)} a
                trade).
              </li>
            ))}
            {wins.size ? (
              <li className="text-slate-400">
                Best candle per stock (target ₹{target}, ₹{stop} stop):{" "}
                {Array.from(wins.entries())
                  .sort((a, b) => b[1] - a[1])
                  .map(([no, n]) => `${ordinal(no)} on ${n} stock${n === 1 ? "" : "s"}`)
                  .join(", ")}
                .
              </li>
            ) : null}
            {sideRule && sideRule !== "NTH" ? (
              <li className="text-slate-400">The side was picked by {sideRule === "MAJORITY" ? "most of the opening candles" : "the candle when most opening candles agree"}.</li>
            ) : null}
            <li className="text-slate-500">Past days only: a rule that won here can lose next week. More days and more stocks make it surer.</li>
          </ul>
        </div>
      ) : (
        <p className="text-xs text-slate-500">No trades match these filters.</p>
      )}

      <div className="overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="bg-surface">
            <tr>
              <th className={th}>Candle</th>
              <th className={clsx(th, "text-right")}>Trades</th>
              <th className={clsx(th, "text-right")}>Reached ₹{target}</th>
              <th className={clsx(th, "text-right")}>Avg with</th>
              <th className={clsx(th, "text-right")}>Avg against</th>
              <th className={clsx(th, "text-right")}>Held to square-off</th>
              <th className={clsx(th, "text-right")}>Best trailing stop</th>
              <th className={clsx(th, "text-right")}>Best target + stop</th>
              <th className={clsx(th, "text-right")}>Best stop and reverse</th>
              <th className={clsx(th, "text-right")}>Best way · a trade</th>
            </tr>
          </thead>
          <tbody>
            {results.map((c) => (
              <tr key={c.no} className={clsx("border-t border-white/5", top?.no === c.no && "bg-emerald-500/5")}>
                <td className={clsx(td, "font-semibold text-slate-100")}>
                  {ordinal(c.no)}
                  {top?.no === c.no ? <Trophy className="ml-1 inline h-3 w-3 text-emerald-300" aria-label="best" /> : null}
                </td>
                <td className={clsx(td, "text-right")}>{c.trades}</td>
                <td className={clsx(td, "text-right")}>
                  <span className={mark(bestOf.hitPct === c.no)}>{fmt(c.hitPct, 0)}%</span>
                </td>
                <td className={clsx(td, "text-right text-emerald-300")}>
                  <span className={mark(bestOf.avgBest === c.no)}>{c.avgBest == null ? "—" : `+${fmt(c.avgBest)}`}</span>
                </td>
                <td className={clsx(td, "text-right text-rose-300")}>
                  <span className={mark(bestOf.avgWorst === c.no)}>{c.avgWorst == null ? "—" : `−${fmt(c.avgWorst)}`}</span>
                </td>
                <td className={clsx(td, "text-right", pos(c.close))}>
                  <span className={mark(bestOf.close === c.no)}>{signed(c.close)}</span>
                  <span className="ml-1 text-slate-500">{fmt(c.closeWin, 0)}% won</span>
                </td>
                <td className={clsx(td, "text-right", pos(c.trail?.total))}>
                  <span className={mark(bestOf.trail === c.no)}>{signed(c.trail?.total)}</span>
                  {c.trail ? <span className="ml-1 text-slate-500">{c.trail.pct}%</span> : null}
                </td>
                <td className={clsx(td, "text-right", pos(c.stop?.total))}>
                  <span className={mark(bestOf.stop === c.no)}>{signed(c.stop?.total)}</span>
                  {c.stop ? <span className="ml-1 text-slate-500">₹{c.stop.stop} stop</span> : null}
                </td>
                <td className={clsx(td, "text-right", pos(c.reverse?.total))}>
                  <span className={mark(bestOf.reverse === c.no)}>{signed(c.reverse?.total)}</span>
                  {c.reverse ? <span className="ml-1 text-slate-500">₹{c.reverse.stop} stop</span> : null}
                </td>
                <td className={clsx(td, "text-right", pos(c.best.total))}>
                  <span className={mark(bestOf.best === c.no)}>{signed(c.trades ? c.best.total / c.trades : null)}</span>
                  <span className="ml-1 text-slate-500">{c.best.label}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-500">
        ₹ a share, before charges. Each column&apos;s best candle is boxed in green. Target ₹{target} (change it in the target + stop test above);
        the stops tried are ₹{STOPS.join(", ₹")}.
      </p>

      <div className="max-h-[28rem] overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-surface">
            <tr>
              <th className={th}>Stock</th>
              <th className={th}>Best candle</th>
              {candles.map((no) => (
                <th key={no} className={clsx(th, "text-right")}>
                  {ordinal(no)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {perStock.map((s) => (
              <tr key={s.symbol} className="border-t border-white/5">
                <td className={clsx(td, "font-semibold text-slate-100")}>
                  <GrowwLink symbol={s.symbol} />
                </td>
                <td className={clsx(td, s.bestTotal > 0 ? "text-emerald-300" : "text-slate-500")}>{s.bestTotal > 0 ? ordinal(s.bestNo) : "none made money"}</td>
                {candles.map((no) => {
                  const v = s.per.get(no);
                  return (
                    <td key={no} className={clsx(td, "text-right", pos(v))}>
                      <span className={mark(no === s.bestNo && (v ?? 0) > 0)}>{signed(v)}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {!perStock.length && <p className="p-3 text-xs text-slate-500">No trades match these filters.</p>}
      </div>
      <p className="text-[11px] text-slate-500">Per stock: ₹ a share over its days with target ₹{target} and a ₹{stop} stop (the stop picked in the target + stop test above).</p>
    </div>
  );
}
