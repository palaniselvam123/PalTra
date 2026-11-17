"use client";

/**
 * The Compare page's trade-by-trade part (GET /api/replay/compare/trades and /compare/stock,
 * backend/backtest_detail.py): key takeaways, each run's wins and losers, each run's set-up (Heikin Ashi included,
 * also when the runs do not differ), how far the runs took the same trades, one stock's trades and P&L line per
 * run, scatter plots over every trade, and the entry / exit reasons. Read-only.
 */
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Download, Info, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { C, money, short, ticks, useWidth } from "@/components/PnlDashboard/charts";
import { NewTag } from "@/components/ui/NewTag";
import { entryReasonLong, entryReasonShort } from "@/lib/entryReason";
import {
  smaApi,
  type BacktestCompare as Compare,
  type CmpBrief,
  type CmpOverlap,
  type CmpPair,
  type CmpReasonRow,
  type CmpStats,
  type CmpTrade,
  type CompareDetail as Detail,
  type CompareStockDay,
} from "@/lib/smaApi";
import { strategyLabel, type Settings } from "./BacktestRuns";
import { REASON_SHORT } from "./TradeHistoryTable";
import { pnlTone } from "./ui";

type Run = Compare["runs"][number];
type Basis = "gross" | "net";

const colorOf = (i: number) => `var(--s${(i % 8) + 1})`;
const OPEN_MIN = 9 * 60 + 15;
const CLOSE_MIN = 15 * 60 + 30;

const mins = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
const held = (m: number | null | undefined) =>
  m == null ? "—" : m < 1 ? "<1 min" : m < 120 ? `${Math.round(m)} min` : `${Math.floor(m / 60)} h ${Math.round(m % 60)} min`;
const exitWord = (code: string) => REASON_SHORT[code] ?? (code ? code.replace(/_/g, " ").toLowerCase() : "—");
const entryWord = (code: string) => entryReasonShort(code) ?? "—";
const dayShort = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", weekday: "short" });
};
/** The share of the waiting orders on the trade's own side at its entry (buyers for a buy, sellers for a sell). */
const ownSide = (t: CmpTrade) => (t.buy_pct == null ? null : t.side === "LONG" ? t.buy_pct : 100 - t.buy_pct);

function csv(name: string, rows: (string | number | null | undefined)[][]) {
  const text = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Card({
  title,
  hint,
  right,
  accent = "sky",
  children,
  id,
}: {
  title: ReactNode;
  hint?: ReactNode;
  right?: ReactNode;
  accent?: "sky" | "emerald" | "amber" | "rose";
  children: ReactNode;
  id?: string;
}) {
  const bar = { sky: "bg-sky-400", emerald: "bg-emerald-400", amber: "bg-amber-400", rose: "bg-rose-400" }[accent];
  return (
    <section id={id} className="cmp-card relative overflow-hidden rounded-xl border border-slate-800 bg-card p-4 pl-5">
      <span aria-hidden className={clsx("absolute inset-y-0 left-0 w-1", bar)} />
      <div className="mb-3 flex flex-wrap items-start gap-2">
        <div className="min-w-[14rem] flex-1">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          {hint ? <p className="mt-0.5 text-xs text-slate-400">{hint}</p> : null}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function Tag({ run, i, base }: { run: Run; i: number; base?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorOf(i) }} />
      <span className="font-semibold text-slate-100">#{run.id}</span>
      <span className="text-xs font-normal text-slate-400">{run.bot_name}</span>
      {base ? <span className="rounded bg-white/10 px-1 text-[10px] font-semibold uppercase tracking-wider text-slate-300">base</span> : null}
    </span>
  );
}

type Loaded = { detail: Detail | null; error: string | null };

/** Loads the trade-by-trade comparison once for the page (both parts below read it). */
export function useCompareDetail(data: Compare | null, basis: Basis): Loaded {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = data ? data.runs.map((r) => r.id).join(",") : "";
  const base = data?.base ?? null;

  useEffect(() => {
    if (!key) return;
    let stop = false;
    smaApi
      .replayCompareTrades(key.split(",").map(Number), base, basis)
      .then((d) => {
        if (stop) return;
        setDetail(d);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "The trades did not load");
      });
    return () => {
      stop = true;
    };
  }, [key, base, basis]);
  return { detail, error };
}

/** Top of the page: key takeaways and each run's winners, losers and stocks. */
export function CompareDetailTop({ data, loaded }: { data: Compare; loaded: Loaded }) {
  if (loaded.error) {
    return (
      <p role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/[0.06] p-3 text-sm text-rose-200">
        Trade-by-trade comparison: {loaded.error}
      </p>
    );
  }
  if (!loaded.detail) {
    return (
      <p className="flex items-center gap-2 rounded-xl border border-slate-800 bg-card p-4 text-sm text-slate-400">
        <Loader2 size={14} className="animate-spin" aria-hidden /> Reading every trade of these runs…
      </p>
    );
  }
  return (
    <>
      <Takeaways detail={loaded.detail} />
      <Scoreboard detail={loaded.detail} runs={data.runs} />
    </>
  );
}

/** The rest: set-up, overlap, stock by stock, scatter plots, entry / exit reasons. */
export function CompareDetailRest({ data, loaded }: { data: Compare; loaded: Loaded }) {
  const place = useMemo(() => new Map(data.runs.map((r, i) => [r.id, i])), [data.runs]);
  const runOf = useMemo(() => new Map(data.runs.map((r) => [r.id, r])), [data.runs]);
  const detail = loaded.detail;
  if (!detail) return null;
  const tag = (id: number) => {
    const run = runOf.get(id);
    return run ? <Tag run={run} i={place.get(id) ?? 0} base={id === detail.base} /> : <span>#{id}</span>;
  };
  return (
    <>
      <Setup runs={data.runs} base={detail.base} />
      <Overlap detail={detail} tag={tag} />
      <StockByStock detail={detail} runs={data.runs} tag={tag} />
      <Scatters detail={detail} runs={data.runs} />
      <Reasons detail={detail} runs={data.runs} />
    </>
  );
}

// ---- key takeaways --------------------------------------------------------------------------------------------------

const TONE = {
  good: { box: "border-emerald-500/40 bg-emerald-500/[0.07]", icon: <CheckCircle2 size={16} className="text-emerald-400" aria-hidden /> },
  bad: { box: "border-rose-500/40 bg-rose-500/[0.07]", icon: <AlertTriangle size={16} className="text-rose-400" aria-hidden /> },
  info: { box: "border-sky-500/40 bg-sky-500/[0.07]", icon: <Info size={16} className="text-sky-400" aria-hidden /> },
} as const;

function Takeaways({ detail }: { detail: Detail }) {
  if (!detail.takeaways.length) return null;
  return (
    <Card
      id="cmp-takeaways"
      accent="emerald"
      title={
        <span className="inline-flex items-center gap-2">
          Key takeaways <NewTag id="term-compare-detail" />
        </span>
      }
      hint="Worked out from every closed trade of these runs. Green = worth keeping, red = costing money, blue = good to know."
    >
      <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {detail.takeaways.map((t, i) => (
          <li key={i} className={clsx("flex gap-2 rounded-lg border p-3", TONE[t.tone].box)}>
            <span className="mt-0.5 shrink-0">{TONE[t.tone].icon}</span>
            <span>
              <span className="block text-sm font-semibold text-slate-100">{t.title}</span>
              <span className="mt-0.5 block text-xs leading-5 text-slate-300">{t.text}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ---- wins, losers, stocks, time in the trade ------------------------------------------------------------------------

function Split({ a, b, flat = 0, labelA, labelB }: { a: number; b: number; flat?: number; labelA: string; labelB: string }) {
  const total = a + b + flat || 1;
  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-white/5" role="img" aria-label={`${a} ${labelA}, ${b} ${labelB}`}>
        <span style={{ width: `${(100 * a) / total}%`, background: C.profit }} />
        <span style={{ width: `${(100 * flat) / total}%`, background: C.faint }} />
        <span style={{ width: `${(100 * b) / total}%`, background: C.loss }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px]">
        <span className="text-emerald-400">
          {a} {labelA}
        </span>
        <span className="text-rose-400">
          {b} {labelB}
        </span>
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-slate-400">{label}</span>
      <span className="text-right font-mono text-slate-200">{children}</span>
    </div>
  );
}

function Scoreboard({ detail, runs }: { detail: Detail; runs: Run[] }) {
  const best = Math.max(...runs.map((r) => detail.stats[String(r.id)]?.pnl ?? -Infinity));
  return (
    <Card
      title="Winners, losers and stocks"
      hint={`Every closed trade each run made, on all the days it finished. P&L ${detail.basis === "net" ? "after" : "before"} charges.`}
    >
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {runs.map((run, i) => {
          const s: CmpStats | undefined = detail.stats[String(run.id)];
          if (!s) return null;
          return (
            <div key={run.id} className="rounded-lg border border-white/10 bg-white/[0.02] p-3" style={{ borderTop: `3px solid ${colorOf(i)}` }}>
              <div className="flex items-center justify-between gap-2">
                <Tag run={run} i={i} base={run.id === detail.base} />
                {s.pnl === best && runs.length > 1 && s.trades ? (
                  <span className="rounded bg-emerald-500/15 px-1.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-400">most money</span>
                ) : null}
              </div>
              <div className={clsx("mt-2 font-mono text-2xl font-semibold", pnlTone(s.pnl))}>{money(s.pnl)}</div>
              <div className="text-xs text-slate-400">
                {s.trades} trades · {s.win_rate == null ? "—" : `${s.win_rate.toFixed(1)}%`} won
              </div>
              <div className="mt-3 space-y-3">
                <Split a={s.wins} b={s.losses} flat={s.flat} labelA="winners" labelB="losers" />
                <Split a={s.stocks_won} b={s.stocks_lost} flat={s.stocks - s.stocks_won - s.stocks_lost} labelA={`of ${s.stocks} stocks up`} labelB="down" />
              </div>
              <div className="mt-3 space-y-1 border-t border-white/10 pt-2">
                <Fact label="Average winner / loser">
                  <span className="text-emerald-400">{money(s.avg_win)}</span> / <span className="text-rose-400">{money(s.avg_loss)}</span>
                </Fact>
                <Fact label="Time in a trade (average)">{held(s.avg_hold)}</Fact>
                <Fact label="Winners held / losers held">
                  {held(s.avg_hold_win)} / {held(s.avg_hold_loss)}
                </Fact>
                <Fact label={`Buys (${s.longs}, ${s.long_wins} won)`}>
                  <span className={pnlTone(s.long_pnl)}>{money(s.long_pnl)}</span>
                </Fact>
                <Fact label={`Sells (${s.shorts}, ${s.short_wins} won)`}>
                  <span className={pnlTone(s.short_pnl)}>{money(s.short_pnl)}</span>
                </Fact>
                {s.best_trade ? (
                  <Fact label="Best trade">
                    {s.best_trade.symbol} <span className="text-emerald-400">{money(s.best_trade.pnl)}</span>
                  </Fact>
                ) : null}
                {s.worst_trade ? (
                  <Fact label="Worst trade">
                    {s.worst_trade.symbol} <span className="text-rose-400">{money(s.worst_trade.pnl)}</span>
                  </Fact>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ---- each run's set-up (shown even when the runs do not differ) ------------------------------------------------------

const ENTRY_MODE: Record<string, string> = { SMA: "SMA cross", PATTERN: "Candle patterns", COLOUR: "Candle colour", NTH_CANDLE: "Second candle" };
const STOP_TYPE: Record<string, string> = {
  ATR: "ATR stop",
  TSL: "Trailing stop",
  SMA_GAP: "SMA-gap stop",
  SUPERTREND: "Supertrend",
  VWAP: "VWAP + ATR buffer",
  SMART: "Smart stop",
};

function setupRows(run: Run): [string, string][] {
  const s = run.settings as Record<string, unknown>;
  const pick = (s.scalp_pick ?? null) as Record<string, unknown> | null;
  const mode = String(s.entry_mode ?? "SMA").toUpperCase();
  const perCandle = mode === "COLOUR" || mode === "PATTERN";
  const candle = mode === "NTH_CANDLE" ? s.nth_candle_minutes : perCandle ? s.pattern_tf : s.candle_minutes;
  const onOff = (v: unknown) => (v ? "On" : "Off");
  const rows: [string, string][] = [
    ["Strategy", strategyLabel(run.settings as unknown as Settings)],
    ["Enters on", ENTRY_MODE[mode] ?? mode],
    ["Candle size", candle ? `${candle} min` : "1 min"],
    ["Heikin Ashi colours (bot)", onOff(s.ha_colours)],
    ["SMA fast / slow", `${s.sma_fast ?? 9} / ${s.sma_slow ?? 21}`],
    ["Stop-loss", s.use_stop === false ? "Off" : STOP_TYPE[String(s.stop_type ?? "ATR").toUpperCase()] ?? String(s.stop_type)],
    ["Quantity", s.qty == null ? "—" : String(s.qty)],
    ["Flip orders", onOff(s.flip_orders)],
    ["Hand-offs played", onOff(s.play_handoffs)],
  ];
  if (pick) {
    rows.push(["Stocks picked by", String(pick.kind ?? "—")]);
    rows.push(["Pick candles", String(pick.candles ?? "NORMAL").toUpperCase() === "HA" ? "Heikin Ashi" : "Normal"]);
    if (pick.pick_time) rows.push(["Pick time", String(pick.pick_time)]);
    if (pick.until) rows.push(["Trade until", String(pick.until)]);
  }
  return rows;
}

function Setup({ runs, base }: { runs: Run[]; base: number }) {
  const per = runs.map((r) => new Map(setupRows(r)));
  const labels: string[] = [];
  per.forEach((m) => m.forEach((_, k) => (labels.includes(k) ? null : labels.push(k))));
  return (
    <Card
      accent="amber"
      title="Each run's set-up"
      hint="The main settings of every run, also where the runs are the same. A row the runs differ on is marked amber; the full list of differences is further down."
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <th scope="col" className="px-2 py-2">
                Setting
              </th>
              {runs.map((r, i) => (
                <th key={r.id} scope="col" className="px-2 py-2 normal-case tracking-normal">
                  <Tag run={r} i={i} base={r.id === base} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {labels.map((label) => {
              const values = per.map((m) => m.get(label) ?? "—");
              const differs = new Set(values).size > 1;
              return (
                <tr key={label} className={differs ? "bg-amber-500/[0.06]" : undefined}>
                  <th scope="row" className="whitespace-nowrap px-2 py-1.5 font-normal text-slate-300">
                    {differs ? <span aria-hidden className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-400 align-middle" /> : null}
                    {label}
                    {differs ? <span className="sr-only"> (differs)</span> : null}
                  </th>
                  {values.map((v, i) => (
                    <td key={runs[i].id} className={clsx("px-2 py-1.5", differs ? "font-semibold text-slate-100" : "text-slate-300")}>
                      {v}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---- did the runs take the same trades? -----------------------------------------------------------------------------

const BUCKET = {
  same: "#199e70",
  apart: "#c98500",
  opposite: "#d55181",
  onlyA: "#3987e5",
  onlyB: "#9085e9",
};

function BriefCell({ t }: { t: CmpBrief }) {
  return (
    <span className="whitespace-nowrap">
      <span className={t.side === "LONG" ? "text-emerald-400" : "text-rose-400"}>{t.side === "LONG" ? "Buy" : "Sell"}</span>{" "}
      {t.entry_at}→{t.exit_at} <span className="text-slate-400">({held(t.hold_min)})</span>
      <span className="block text-[11px] text-slate-400">
        in: {entryWord(t.entry_reason)} · out: <span title={exitWord(t.exit_reason)}>{exitWord(t.exit_reason).slice(0, 40)}</span>
      </span>
    </span>
  );
}

function PairTable({ rows, total, a, b }: { rows: CmpPair[]; total: number; a: ReactNode; b: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-400">
          <tr>
            <th scope="col" className="px-2 py-1.5">Day · stock</th>
            <th scope="col" className="px-2 py-1.5 normal-case tracking-normal">{a}</th>
            <th scope="col" className="px-2 py-1.5 text-right">P&amp;L</th>
            <th scope="col" className="px-2 py-1.5 normal-case tracking-normal">{b}</th>
            <th scope="col" className="px-2 py-1.5 text-right">P&amp;L</th>
            <th scope="col" className="px-2 py-1.5 text-right">Difference</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((p, i) => (
            <tr key={i}>
              <th scope="row" className="whitespace-nowrap px-2 py-1.5 font-normal text-slate-300">
                {dayShort(p.a.date)} · <span className="font-semibold text-slate-100">{p.a.symbol}</span>
              </th>
              <td className="px-2 py-1.5 text-slate-200"><BriefCell t={p.a} /></td>
              <td className={clsx("px-2 py-1.5 text-right font-mono", pnlTone(p.a.pnl))}>{money(p.a.pnl)}</td>
              <td className="px-2 py-1.5 text-slate-200"><BriefCell t={p.b} /></td>
              <td className={clsx("px-2 py-1.5 text-right font-mono", pnlTone(p.b.pnl))}>{money(p.b.pnl)}</td>
              <td className={clsx("px-2 py-1.5 text-right font-mono font-semibold", pnlTone(p.diff))}>{money(p.diff)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length ? <p className="px-2 pt-1 text-[11px] text-slate-400">The {rows.length} biggest differences of {total}.</p> : null}
    </div>
  );
}

function SoloTable({ rows, total }: { rows: CmpBrief[]; total: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-400">
          <tr>
            <th scope="col" className="px-2 py-1.5">Day · stock</th>
            <th scope="col" className="px-2 py-1.5">Trade</th>
            <th scope="col" className="px-2 py-1.5 text-right">P&amp;L</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((t, i) => (
            <tr key={i}>
              <th scope="row" className="whitespace-nowrap px-2 py-1.5 font-normal text-slate-300">
                {dayShort(t.date)} · <span className="font-semibold text-slate-100">{t.symbol}</span>
              </th>
              <td className="px-2 py-1.5 text-slate-200"><BriefCell t={t} /></td>
              <td className={clsx("px-2 py-1.5 text-right font-mono", pnlTone(t.pnl))}>{money(t.pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length ? <p className="px-2 pt-1 text-[11px] text-slate-400">The {rows.length} biggest of {total}.</p> : null}
    </div>
  );
}

function Fold({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <details className="cmp-fold rounded-lg border border-white/10 bg-white/[0.02]">
      <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-white/5">{title}</summary>
      <div className="border-t border-white/10 p-2">{children}</div>
    </details>
  );
}

function OverlapOne({ o, baseTag, otherTag }: { o: CmpOverlap; baseTag: ReactNode; otherTag: ReactNode }) {
  const apart = o.same - o.same_exit;
  const parts = [
    { key: "same", n: o.same_exit, color: BUCKET.same, label: "Same trade, same exit" },
    { key: "apart", n: apart, color: BUCKET.apart, label: "Same entry, different exit" },
    { key: "opposite", n: o.opposite, color: BUCKET.opposite, label: "Same moment, other side" },
    { key: "onlyA", n: o.only_a, color: BUCKET.onlyA, label: "Only the base took it" },
    { key: "onlyB", n: o.only_b, color: BUCKET.onlyB, label: "Only this run took it" },
  ];
  const total = o.union || 1;
  return (
    <div className="rounded-lg border border-white/10 p-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-xs text-slate-400">
          {baseTag} <span className="mx-1">vs</span> {otherTag}
        </span>
        <span className="ml-auto text-right">
          <span className="font-mono text-2xl font-semibold text-slate-100">{o.rate == null ? "—" : `${o.rate.toFixed(0)}%`}</span>
          <span className="ml-1.5 text-xs text-slate-400">overlap ({o.same} of {o.union} trades)</span>
        </span>
      </div>
      <div className="mt-2 flex h-4 overflow-hidden rounded-md bg-white/5" role="img" aria-label={parts.map((p) => `${p.label}: ${p.n}`).join(", ")}>
        {parts.map((p) => (p.n ? <span key={p.key} title={`${p.label}: ${p.n}`} style={{ width: `${(100 * p.n) / total}%`, background: p.color }} /> : null))}
      </div>
      <div className="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-5">
        {parts.map((p) => (
          <span key={p.key} className="flex items-center gap-1.5 text-slate-300">
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: p.color }} />
            {p.label}: <b className="font-mono text-slate-100">{p.n}</b>
          </span>
        ))}
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <th scope="col" className="px-2 py-1.5">Where the money came from</th>
              <th scope="col" className="px-2 py-1.5 text-right">Trades</th>
              <th scope="col" className="px-2 py-1.5 text-right">Base</th>
              <th scope="col" className="px-2 py-1.5 text-right">This run</th>
              <th scope="col" className="px-2 py-1.5 text-right">Difference</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 font-mono">
            {(
              [
                ["Trades both took (same side)", o.same, o.same_pnl_a, o.same_pnl_b],
                ["Same moment, other side", o.opposite, o.opposite_pnl_a, o.opposite_pnl_b],
                ["Only the base took", o.only_a, o.only_a_pnl, null],
                ["Only this run took", o.only_b, null, o.only_b_pnl],
              ] as [string, number, number | null, number | null][]
            ).map(([label, n, a, b]) => (
              <tr key={label}>
                <th scope="row" className="px-2 py-1.5 font-sans font-normal text-slate-300">{label}</th>
                <td className="px-2 py-1.5 text-right text-slate-200">{n}</td>
                <td className={clsx("px-2 py-1.5 text-right", pnlTone(a))}>{a == null ? "—" : money(a)}</td>
                <td className={clsx("px-2 py-1.5 text-right", pnlTone(b))}>{b == null ? "—" : money(b)}</td>
                <td className={clsx("px-2 py-1.5 text-right font-semibold", pnlTone((b ?? 0) - (a ?? 0)))}>{money((b ?? 0) - (a ?? 0))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        Of the {o.same} trades both took: {o.same_exit} closed the same way, {o.diff_exit_reason} closed for a different reason and{" "}
        {o.diff_exit_time} at a different time. Stocks both traded: {o.stocks_both}.
        {o.stocks_only_a.length ? ` Only the base traded: ${o.stocks_only_a.slice(0, 12).join(", ")}${o.stocks_only_a.length > 12 ? ` +${o.stocks_only_a.length - 12}` : ""}.` : ""}
        {o.stocks_only_b.length ? ` Only this run traded: ${o.stocks_only_b.slice(0, 12).join(", ")}${o.stocks_only_b.length > 12 ? ` +${o.stocks_only_b.length - 12}` : ""}.` : ""}
      </p>
      <div className="mt-2 space-y-1.5">
        {o.apart.length ? (
          <Fold title={`Same entry, different exit (${o.apart_total})`}>
            <PairTable rows={o.apart} total={o.apart_total} a={baseTag} b={otherTag} />
          </Fold>
        ) : null}
        {o.opposite_rows.length ? (
          <Fold title={`Same moment, other side (${o.opposite})`}>
            <PairTable rows={o.opposite_rows} total={o.opposite} a={baseTag} b={otherTag} />
          </Fold>
        ) : null}
        {o.only_a_rows.length ? (
          <Fold title={`Trades only the base took (${o.only_a})`}>
            <SoloTable rows={o.only_a_rows} total={o.only_a} />
          </Fold>
        ) : null}
        {o.only_b_rows.length ? (
          <Fold title={`Trades only this run took (${o.only_b})`}>
            <SoloTable rows={o.only_b_rows} total={o.only_b} />
          </Fold>
        ) : null}
      </div>
    </div>
  );
}

function Overlap({ detail, tag }: { detail: Detail; tag: (id: number) => ReactNode }) {
  const others = detail.runs.filter((id) => id !== detail.base && detail.overlap[String(id)]);
  if (!others.length) return null;
  return (
    <Card
      accent="sky"
      title="Did the runs take the same trades?"
      hint={`Two trades are the same when they are on the same stock, day and side and entered within ${detail.match_min} minutes of each other. Each run is set against the base.`}
    >
      <div className="space-y-3">
        {others.map((id) => (
          <OverlapOne key={id} o={detail.overlap[String(id)]} baseTag={tag(detail.base)} otherTag={tag(id)} />
        ))}
      </div>
    </Card>
  );
}

// ---- one stock, trade by trade, with its P&L line -------------------------------------------------------------------

type Line = { id: number; color: string; pts: { m: number; v: number }[]; trades: CmpTrade[]; logged: boolean };

/** A run's P&L line for the stock: the replay's own minute log, else steps at each exit from its trades. */
function lineOf(run: CompareStockDay["runs"][number], color: string): Line {
  const trades = [...run.trades].sort((a, b) => a.entry_ts - b.entry_ts);
  if (run.curve.length >= 2) {
    return { id: run.run_id, color, trades, logged: true, pts: run.curve.map((p) => ({ m: mins(p.t), v: p.pnl })) };
  }
  const pts: { m: number; v: number }[] = [{ m: OPEN_MIN, v: 0 }];
  let total = 0;
  for (const t of trades) {
    pts.push({ m: mins(t.entry_at), v: total });
    total += t.gross;
    pts.push({ m: mins(t.exit_at), v: total });
  }
  pts.push({ m: CLOSE_MIN, v: total });
  return { id: run.run_id, color, trades, logged: false, pts };
}

const valueAt = (pts: { m: number; v: number }[], m: number) => {
  let v = 0;
  for (const p of pts) {
    if (p.m > m) break;
    v = p.v;
  }
  return v;
};

function StockChart({ day, lines }: { day: CompareStockDay; lines: Line[] }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 56, r: 14, t: 14, b: 24 };
  const strip = day.pressure.length ? 70 : 0;
  const h = 280;
  const H = h + strip;
  const all = lines.flatMap((l) => l.pts.map((p) => p.v));
  const yt = ticks(Math.min(0, ...all), Math.max(0, ...all), 5);
  const lo = yt[0];
  const hi = yt[yt.length - 1];
  const X = (m: number) => pad.l + ((Math.min(CLOSE_MIN, Math.max(OPEN_MIN, m)) - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN)) * (w - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (h - pad.t - pad.b);
  const SY = (p: number) => h + 8 + (1 - p / 100) * (strip - 20);
  const hours = [555, 600, 660, 720, 780, 840, 900, 930];
  const pressure = day.pressure.map((p) => ({ m: mins(p.t), v: p.buy_pct }));
  const readAt = (m: number) => {
    let got: number | null = null;
    for (const p of pressure) {
      if (p.m > m) break;
      got = p.v;
    }
    return got;
  };
  return (
    <div ref={ref} className="relative">
      <svg
        width={w}
        height={H}
        viewBox={`0 0 ${w} ${H}`}
        role="img"
        aria-label={`${day.symbol} on ${day.day}: profit or loss through the day for each run, with entries and exits`}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const x = ((e.clientX - box.left) / box.width) * w;
          const m = OPEN_MIN + ((x - pad.l) / (w - pad.l - pad.r)) * (CLOSE_MIN - OPEN_MIN);
          setHover(m < OPEN_MIN || m > CLOSE_MIN ? null : Math.round(m));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {yt.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={Y(t)} y2={Y(t)} stroke={t === 0 ? C.axis : C.grid} strokeWidth={t === 0 ? 1 : 0.6} strokeDasharray={t === 0 ? undefined : "3 4"} />
            <text x={pad.l - 6} y={Y(t) + 3.5} textAnchor="end" fontSize="10.5" fill={C.axis}>
              ₹{short(t)}
            </text>
          </g>
        ))}
        {hours.map((m) => (
          <text key={m} x={X(m)} y={h - 6} textAnchor="middle" fontSize="10.5" fill={C.axis}>
            {clock(m)}
          </text>
        ))}
        {lines.map((l) => (
          <g key={l.id}>
            {/* While a trade is open the stretch is shaded in the run's colour. */}
            {l.trades.map((t) => (
              <rect key={t.id} x={X(mins(t.entry_at))} width={Math.max(1, X(mins(t.exit_at)) - X(mins(t.entry_at)))} y={pad.t} height={h - pad.t - pad.b} fill={l.color} opacity={0.06} />
            ))}
            <path d={l.pts.map((p, i) => `${i ? "L" : "M"}${X(p.m).toFixed(1)} ${Y(p.v).toFixed(1)}`).join(" ")} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" />
            {l.trades.map((t) => {
              const em = mins(t.entry_at);
              const xm = mins(t.exit_at);
              const ex = X(em);
              const ey = Y(valueAt(l.pts, em));
              const up = t.side === "LONG";
              const tri = up ? `${ex},${ey - 7} ${ex - 6},${ey + 4} ${ex + 6},${ey + 4}` : `${ex},${ey + 7} ${ex - 6},${ey - 4} ${ex + 6},${ey - 4}`;
              return (
                <g key={t.id}>
                  <polygon points={tri} fill={l.color} stroke="rgb(var(--c-surface))" strokeWidth={1}>
                    <title>{`#${l.id} ${up ? "Buy" : "Sell"} ${t.entry_at.slice(0, 5)} at ₹${t.entry_price} · ${entryWord(t.entry_reason)}`}</title>
                  </polygon>
                  <circle cx={X(xm)} cy={Y(valueAt(l.pts, xm))} r={5} fill={t.gross >= 0 ? C.profit : C.loss} stroke={l.color} strokeWidth={2}>
                    <title>{`#${l.id} exit ${t.exit_at.slice(0, 5)} at ₹${t.exit_price} · ${exitWord(t.exit_reason)} · ${money(t.gross)}`}</title>
                  </circle>
                </g>
              );
            })}
          </g>
        ))}
        {strip ? (
          <g>
            <line x1={pad.l} x2={w - pad.r} y1={SY(50)} y2={SY(50)} stroke={C.grid} strokeDasharray="3 4" />
            <text x={pad.l - 6} y={SY(100) + 4} textAnchor="end" fontSize="10" fill={C.axis}>100%</text>
            <text x={pad.l - 6} y={SY(50) + 3.5} textAnchor="end" fontSize="10" fill={C.axis}>50%</text>
            <text x={pad.l - 6} y={SY(0) + 3} textAnchor="end" fontSize="10" fill={C.axis}>0%</text>
            <path d={pressure.map((p, i) => `${i ? "L" : "M"}${X(p.m).toFixed(1)} ${SY(p.v).toFixed(1)}`).join(" ")} fill="none" stroke={C.profit} strokeWidth={1.6} />
            <path d={pressure.map((p, i) => `${i ? "L" : "M"}${X(p.m).toFixed(1)} ${SY(100 - p.v).toFixed(1)}`).join(" ")} fill="none" stroke={C.loss} strokeWidth={1.6} strokeDasharray="4 3" />
          </g>
        ) : null}
        {hover != null ? <line x1={X(hover)} x2={X(hover)} y1={pad.t} y2={H - 10} stroke={C.axis} strokeWidth={0.8} /> : null}
      </svg>
      {hover != null ? (
        <div
          className="pointer-events-none absolute top-2 z-10 min-w-[170px] rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-slate-200 shadow-card"
          style={X(hover) > w * 0.6 ? { right: w - X(hover) + 10 } : { left: X(hover) + 10 }}
        >
          <div className="font-semibold">{clock(hover)}</div>
          {lines.map((l) => {
            const v = valueAt(l.pts, hover);
            const open = l.trades.find((t) => mins(t.entry_at) <= hover && hover <= mins(t.exit_at));
            return (
              <div key={l.id} className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: l.color }} />#{l.id}
                  {open ? <span className="text-slate-400">{open.side === "LONG" ? "in a buy" : "in a sell"}</span> : null}
                </span>
                <span className={clsx("font-mono", pnlTone(v))}>{money(v)}</span>
              </div>
            );
          })}
          {readAt(hover) != null ? (
            <div className="mt-1 border-t border-white/10 pt-1 text-slate-300">
              Buyers {readAt(hover)!.toFixed(0)}% · Sellers {(100 - readAt(hover)!).toFixed(0)}%
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function StockByStock({ detail, runs, tag }: { detail: Detail; runs: Run[]; tag: (id: number) => ReactNode }) {
  const ids = useMemo(() => runs.map((r) => r.id), [runs]);
  const place = useMemo(() => new Map(runs.map((r, i) => [r.id, i])), [runs]);
  const stocks = useMemo(() => {
    const by = new Map<string, { symbol: string; trades: number; pnl: Record<number, number>; spread: number }>();
    for (const t of detail.trades) {
      const row = by.get(t.symbol) ?? { symbol: t.symbol, trades: 0, pnl: {}, spread: 0 };
      row.trades += 1;
      row.pnl[t.run_id] = (row.pnl[t.run_id] ?? 0) + t.pnl;
      by.set(t.symbol, row);
    }
    const rows = Array.from(by.values());
    for (const r of rows) {
      const vals = ids.map((id) => r.pnl[id] ?? 0);
      r.spread = Math.max(...vals) - Math.min(...vals);
    }
    return rows.sort((a, b) => b.spread - a.spread);
  }, [detail.trades, ids]);
  const [symbol, setSymbol] = useState<string>("");
  const [find, setFind] = useState("");
  const [day, setDay] = useState<string>("");
  const [chart, setChart] = useState<CompareStockDay | null>(null);
  const [busy, setBusy] = useState(false);

  const picked = symbol || stocks[0]?.symbol || "";
  const mine = useMemo(() => detail.trades.filter((t) => t.symbol === picked), [detail.trades, picked]);
  const days = useMemo(() => Array.from(new Set(mine.map((t) => t.date))).sort(), [mine]);
  const theDay = days.includes(day) ? day : days[0] ?? "";
  const key = ids.join(",");

  useEffect(() => {
    if (!picked || !theDay) return;
    let stop = false;
    setBusy(true);
    smaApi
      .replayCompareStock(ids, picked, theDay)
      .then((d) => !stop && setChart(d))
      .catch(() => !stop && setChart(null))
      .finally(() => !stop && setBusy(false));
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, picked, theDay]);

  if (!stocks.length) return null;
  const shown = find.trim() ? stocks.filter((s) => s.symbol.includes(find.trim().toUpperCase())) : stocks;
  const rows = mine.filter((t) => t.date === theDay).sort((a, b) => a.entry_ts - b.entry_ts || a.run_id - b.run_id);
  const lines = chart && chart.symbol === picked && chart.day === theDay ? chart.runs.map((r) => lineOf(r, colorOf(place.get(r.run_id) ?? 0))) : [];
  const anyLogged = lines.some((l) => l.logged);
  const totals = stocks.find((s) => s.symbol === picked);

  return (
    <Card
      accent="emerald"
      title="Stock by stock: entries, exits and the P&L through the day"
      hint="Pick a stock and a day. The table sets each run's trades side by side; the chart draws each run's profit or loss on that stock through the day."
      right={
        <button
          type="button"
          onClick={() =>
            csv(`compare-trades-${key.replace(/,/g, "-")}.csv`, [
              ["run", "day", "stock", "side", "qty", "entry_time", "entry_price", "entry_reason", "exit_time", "exit_price", "exit_reason", "minutes_in_trade", "pnl", "buyers_pct_at_entry", "sellers_pct_at_entry", "vol_vs_usual", "trend_at_entry"],
              ...detail.trades.map((t) => [t.run_id, t.date, t.symbol, t.side, t.qty, t.entry_at, t.entry_price, t.entry_reason, t.exit_at, t.exit_price, t.exit_reason, t.hold_min, t.pnl, t.buy_pct, t.buy_pct == null ? null : Math.round((100 - t.buy_pct) * 10) / 10, t.rvol, t.trend]),
            ])
          }
          className="cmp-noprint inline-flex min-h-8 items-center gap-1 rounded-md border border-white/10 px-2 text-xs text-slate-300 hover:bg-white/5"
        >
          <Download size={12} aria-hidden /> All trades (CSV)
        </button>
      }
    >
      <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
        <div className="cmp-noprint">
          <input
            value={find}
            onChange={(e) => setFind(e.target.value)}
            placeholder="Find a stock"
            aria-label="Find a stock"
            className="mb-2 min-h-9 w-full rounded-md border border-white/10 bg-surface px-2 text-sm text-slate-100 placeholder:text-slate-500"
          />
          <ul className="max-h-[420px] space-y-1 overflow-y-auto pr-1" aria-label="Stocks, biggest difference between the runs first">
            {shown.map((s) => (
              <li key={s.symbol}>
                <button
                  type="button"
                  aria-pressed={s.symbol === picked}
                  onClick={() => setSymbol(s.symbol)}
                  className={clsx(
                    "w-full rounded-md border px-2 py-1.5 text-left text-xs",
                    s.symbol === picked ? "border-sky-400/60 bg-sky-500/10" : "border-white/10 hover:bg-white/5"
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-100">{s.symbol}</span>
                    <span className="text-slate-400">{s.trades} trades</span>
                  </span>
                  <span className="mt-0.5 flex flex-wrap gap-x-2 font-mono">
                    {ids.map((id) => (
                      <span key={id} className="inline-flex items-center gap-1">
                        <span aria-hidden className="h-1.5 w-1.5 rounded-sm" style={{ background: colorOf(place.get(id) ?? 0) }} />
                        <span className={s.pnl[id] == null ? "text-slate-500" : pnlTone(s.pnl[id])}>{s.pnl[id] == null ? "—" : money(s.pnl[id])}</span>
                      </span>
                    ))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-slate-100">{picked}</h3>
            {totals ? (
              <span className="flex flex-wrap gap-x-3 text-xs">
                {ids.map((id) => (
                  <span key={id} className="inline-flex items-center gap-1">
                    <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: colorOf(place.get(id) ?? 0) }} />#{id}{" "}
                    <span className={clsx("font-mono", totals.pnl[id] == null ? "text-slate-500" : pnlTone(totals.pnl[id]))}>
                      {totals.pnl[id] == null ? "no trade" : money(totals.pnl[id])}
                    </span>
                  </span>
                ))}
              </span>
            ) : null}
            {busy ? <Loader2 size={14} className="animate-spin text-slate-400" aria-label="Loading" /> : null}
          </div>
          <div role="group" aria-label="Day" className="cmp-noprint mb-2 flex flex-wrap gap-1">
            {days.map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={d === theDay}
                onClick={() => setDay(d)}
                className={clsx("min-h-8 rounded-md border px-2 text-xs", d === theDay ? "border-sky-400/60 bg-sky-500/10 font-semibold text-slate-100" : "border-white/10 text-slate-300 hover:bg-white/5")}
              >
                {dayShort(d)}
              </button>
            ))}
          </div>
          <p className="mb-1 hidden text-xs text-slate-400 print:block">{dayShort(theDay)}</p>
          {lines.length ? (
            <>
              <StockChart day={chart!} lines={lines} />
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                <span>▲ buy entry · ▼ sell entry · ● exit (green = profit, red = loss) · shaded = in the trade</span>
                {chart!.pressure.length ? (
                  <span>
                    Strip: <span className="text-emerald-400">— buyers %</span> · <span className="text-rose-400">- - sellers %</span> of the waiting orders (saved Most active scans)
                  </span>
                ) : (
                  <span>No Most active scan was saved for this stock that day, so there is no buyers / sellers strip.</span>
                )}
                {!anyLogged ? <span>No minute-by-minute log for this day: the line steps at each exit.</span> : null}
              </div>
            </>
          ) : (
            <div className="h-[280px] rounded-lg border border-dashed border-white/10" />
          )}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[980px] whitespace-nowrap text-left text-xs">
              <thead className="text-[11px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th scope="col" className="px-2 py-1.5">Run</th>
                  <th scope="col" className="px-2 py-1.5">Side</th>
                  <th scope="col" className="px-2 py-1.5">Entered</th>
                  <th scope="col" className="px-2 py-1.5">Why it entered</th>
                  <th scope="col" className="px-2 py-1.5">Exited</th>
                  <th scope="col" className="px-2 py-1.5">Why it exited</th>
                  <th scope="col" className="px-2 py-1.5 text-right">In the trade</th>
                  <th scope="col" className="px-2 py-1.5 text-right">Qty</th>
                  <th scope="col" className="px-2 py-1.5 text-right">P&amp;L</th>
                  <th scope="col" className="px-2 py-1.5 text-right" title="Share of the waiting orders at the latest saved Most active read before the entry">Buyers % / Sellers %</th>
                  <th scope="col" className="px-2 py-1.5 text-right">Vol vs usual</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {rows.map((t) => (
                  <tr key={t.id}>
                    <th scope="row" className="px-2 py-1.5 font-normal">{tag(t.run_id)}</th>
                    <td className={clsx("px-2 py-1.5 font-semibold", t.side === "LONG" ? "text-emerald-400" : "text-rose-400")}>{t.side === "LONG" ? "Buy" : "Sell"}</td>
                    <td className="px-2 py-1.5 font-mono text-slate-200">
                      {t.entry_at.slice(0, 5)} <span className="text-slate-400">₹{t.entry_price}</span>
                    </td>
                    <td className="px-2 py-1.5 text-slate-300" title={entryReasonLong(t.entry_reason) ?? undefined}>{entryWord(t.entry_reason)}</td>
                    <td className="px-2 py-1.5 font-mono text-slate-200">
                      {t.exit_at.slice(0, 5)} <span className="text-slate-400">₹{t.exit_price}</span>
                    </td>
                    <td className="max-w-[220px] truncate px-2 py-1.5 text-slate-300" title={`${exitWord(t.exit_reason)} (${t.exit_reason})`}>{exitWord(t.exit_reason)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-slate-200">{held(t.hold_min)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-slate-300">{t.qty}</td>
                    <td className={clsx("px-2 py-1.5 text-right font-mono font-semibold", pnlTone(t.pnl))}>{money(t.pnl)}</td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {t.buy_pct == null ? (
                        <span className="text-slate-500">—</span>
                      ) : (
                        <span title={t.read_at ? `read ${t.read_at}` : undefined}>
                          <span className="text-emerald-400">{t.buy_pct.toFixed(0)}%</span> / <span className="text-rose-400">{(100 - t.buy_pct).toFixed(0)}%</span>
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-slate-300">{t.rvol == null ? "—" : `${t.rvol.toFixed(1)}×`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ---- scatter plots over every trade ---------------------------------------------------------------------------------

type Dot = { x: number; y: number; color: string; t: CmpTrade };

function Scatter({
  title,
  xLabel,
  dots,
  xFmt,
  xDomain,
  split,
}: {
  title: string;
  xLabel: string;
  dots: Dot[];
  xFmt: (x: number) => string;
  xDomain?: [number, number];
  /** Words for the two halves of the x axis in the reading under the chart. */
  split: [string, string];
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Dot | null>(null);
  const h = 250;
  const pad = { l: 52, r: 12, t: 10, b: 34 };
  // At most 2,500 dots are drawn (evenly thinned); the reading under the chart uses them all.
  const drawn = useMemo(() => {
    const step = Math.ceil(dots.length / 2500);
    return step <= 1 ? dots : dots.filter((_, i) => i % step === 0);
  }, [dots]);
  if (dots.length < 3) {
    return (
      <div className="rounded-lg border border-white/10 p-3">
        <h3 className="text-xs font-semibold text-slate-200">{title}</h3>
        <p className="mt-2 text-xs text-slate-400">Not enough trades with this reading to draw.</p>
      </div>
    );
  }
  const xs = dots.map((d) => d.x).sort((a, b) => a - b);
  // The x axis stops at the 98th percentile, so one all-day trade does not squeeze the rest.
  const x0 = xDomain ? xDomain[0] : Math.min(0, xs[0]);
  const x1 = xDomain ? xDomain[1] : xs[Math.min(xs.length - 1, Math.floor(xs.length * 0.98))] || 1;
  const ys = dots.map((d) => d.y);
  const yt = ticks(Math.min(...ys), Math.max(...ys), 5);
  const lo = yt[0];
  const hi = yt[yt.length - 1];
  const X = (x: number) => pad.l + ((Math.min(x1, Math.max(x0, x)) - x0) / (x1 - x0 || 1)) * (w - pad.l - pad.r);
  const Y = (y: number) => pad.t + (1 - (y - lo) / (hi - lo || 1)) * (h - pad.t - pad.b);
  const xt = [0, 0.25, 0.5, 0.75, 1].map((f) => x0 + f * (x1 - x0));
  const mid = xs[Math.floor(xs.length / 2)];
  const low = dots.filter((d) => d.x <= mid);
  const high = dots.filter((d) => d.x > mid);
  const avg = (list: Dot[]) => (list.length ? list.reduce((s, d) => s + d.y, 0) / list.length : null);
  const won = (list: Dot[]) => (list.length ? (100 * list.filter((d) => d.y > 0).length) / list.length : null);
  return (
    <div className="rounded-lg border border-white/10 p-3">
      <h3 className="text-xs font-semibold text-slate-200">{title}</h3>
      <div ref={ref} className="relative mt-1">
        <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${title}: one dot per trade`} onMouseLeave={() => setHover(null)}>
          {yt.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={w - pad.r} y1={Y(t)} y2={Y(t)} stroke={t === 0 ? C.axis : C.grid} strokeWidth={t === 0 ? 1 : 0.6} strokeDasharray={t === 0 ? undefined : "3 4"} />
              <text x={pad.l - 6} y={Y(t) + 3.5} textAnchor="end" fontSize="10.5" fill={C.axis}>
                ₹{short(t)}
              </text>
            </g>
          ))}
          {xt.map((t, i) => (
            <text key={i} x={X(t)} y={h - 18} textAnchor="middle" fontSize="10.5" fill={C.axis}>
              {xFmt(t)}
            </text>
          ))}
          <text x={(pad.l + w - pad.r) / 2} y={h - 3} textAnchor="middle" fontSize="10.5" fill={C.axis}>
            {xLabel}
          </text>
          {drawn.map((d) => (
            <circle
              key={d.t.id}
              cx={X(d.x)}
              cy={Y(d.y)}
              r={hover?.t.id === d.t.id ? 6 : 3.5}
              fill={d.color}
              fillOpacity={0.6}
              stroke={d.color}
              strokeWidth={1}
              onMouseEnter={() => setHover(d)}
            />
          ))}
        </svg>
        {hover ? (
          <div
            className="pointer-events-none absolute top-1 z-10 min-w-[180px] rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-slate-200 shadow-card"
            style={X(hover.x) > w * 0.55 ? { right: w - X(hover.x) + 10 } : { left: X(hover.x) + 10 }}
          >
            <div className="font-semibold">
              #{hover.t.run_id} · {hover.t.symbol} · {hover.t.side === "LONG" ? "Buy" : "Sell"}
            </div>
            <div className="text-slate-400">
              {dayShort(hover.t.date)} {hover.t.entry_at.slice(0, 5)}→{hover.t.exit_at.slice(0, 5)} ({held(hover.t.hold_min)})
            </div>
            <div className="text-slate-400">
              in: {entryWord(hover.t.entry_reason)} · out: {exitWord(hover.t.exit_reason)}
            </div>
            <div className={clsx("font-mono font-semibold", pnlTone(hover.y))}>{money(hover.y)}</div>
          </div>
        ) : null}
      </div>
      <p className="mt-1 text-xs leading-5 text-slate-300">
        {split[0]} ({low.length} trades): {won(low)?.toFixed(0)}% won, average <span className={clsx("whitespace-nowrap font-mono", pnlTone(avg(low)))}>{money(avg(low))}</span>.{" "}
        {split[1]} ({high.length}): {won(high)?.toFixed(0) ?? "—"}% won, average <span className={clsx("whitespace-nowrap font-mono", pnlTone(avg(high)))}>{money(avg(high))}</span>.
      </p>
    </div>
  );
}

function Scatters({ detail, runs }: { detail: Detail; runs: Run[] }) {
  const place = useMemo(() => new Map(runs.map((r, i) => [r.id, i])), [runs]);
  const [only, setOnly] = useState<number | null>(null);
  const trades = useMemo(() => (only == null ? detail.trades : detail.trades.filter((t) => t.run_id === only)), [detail.trades, only]);
  const dot = (t: CmpTrade, x: number): Dot => ({ x, y: t.pnl, color: colorOf(place.get(t.run_id) ?? 0), t });
  const byHold = useMemo(() => trades.map((t) => dot(t, t.hold_min)), [trades]); // eslint-disable-line react-hooks/exhaustive-deps
  const byTime = useMemo(() => trades.map((t) => dot(t, mins(t.entry_at))), [trades]); // eslint-disable-line react-hooks/exhaustive-deps
  const bySide = useMemo(() => trades.filter((t) => ownSide(t) != null).map((t) => dot(t, ownSide(t)!)), [trades]); // eslint-disable-line react-hooks/exhaustive-deps
  const byBest = useMemo(() => trades.filter((t) => t.best_pts != null).map((t) => dot(t, Math.max(0, t.best_pts! * t.qty))), [trades]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!detail.trades.length) return null;
  const midOf = (list: Dot[]) => {
    const xs = list.map((d) => d.x).sort((a, b) => a - b);
    return xs.length ? xs[Math.floor(xs.length / 2)] : 0;
  };
  return (
    <Card
      accent="rose"
      title="Every trade as a dot"
      hint="One dot per trade, in its run's colour; above the zero line it made money. Hover a dot for the trade. The line under each chart splits the trades in two halves and says which half did better."
      right={
        <div role="group" aria-label="Runs shown" className="cmp-noprint inline-flex flex-wrap overflow-hidden rounded-md border border-white/10 text-xs">
          <button type="button" aria-pressed={only == null} onClick={() => setOnly(null)} className={clsx("min-h-8 px-2.5", only == null ? "bg-white/10 font-semibold text-slate-100" : "text-slate-400 hover:bg-white/5")}>
            All runs
          </button>
          {runs.map((r, i) => (
            <button key={r.id} type="button" aria-pressed={only === r.id} onClick={() => setOnly(r.id)} className={clsx("inline-flex min-h-8 items-center gap-1 px-2.5", only === r.id ? "bg-white/10 font-semibold text-slate-100" : "text-slate-400 hover:bg-white/5")}>
              <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: colorOf(i) }} />#{r.id}
            </button>
          ))}
        </div>
      }
    >
      <div className="grid gap-3 lg:grid-cols-2">
        <Scatter
          title="Time in the trade vs result"
          xLabel="minutes in the trade"
          dots={byHold}
          xFmt={(x) => `${Math.round(x)}m`}
          split={[`Shorter trades (up to ${held(midOf(byHold))})`, "Longer trades"]}
        />
        <Scatter
          title="Entry time vs result"
          xLabel="time of the entry"
          dots={byTime}
          xFmt={clock}
          xDomain={[OPEN_MIN, CLOSE_MIN]}
          split={[`Entered by ${clock(midOf(byTime))}`, "Entered later"]}
        />
        <Scatter
          title="Buyers / sellers on the trade's side at entry vs result"
          xLabel="% of waiting orders on the trade's side (buyers for a buy, sellers for a sell)"
          dots={bySide}
          xFmt={(x) => `${Math.round(x)}%`}
          xDomain={[0, 100]}
          split={[`Up to ${midOf(bySide).toFixed(0)}% on its side`, "More on its side"]}
        />
        <Scatter
          title="Most it was up vs where it ended"
          xLabel="best the trade showed while open (₹)"
          dots={byBest}
          xFmt={(x) => `₹${short(x)}`}
          split={[`Was up at most ₹${short(midOf(byBest))}`, "Was up more"]}
        />
      </div>
      {detail.reads < detail.trades.length ? (
        <p className="mt-2 text-xs text-slate-400">
          Buyers / sellers % is known for {detail.reads} of {detail.trades.length} trades: it needs a Most active scan saved on this server before the entry.
        </p>
      ) : null}
    </Card>
  );
}

// ---- why trades opened and closed -----------------------------------------------------------------------------------

function ReasonTable({ title, pick, word, detail, runs }: { title: string; pick: (s: CmpStats) => CmpReasonRow[]; word: (code: string) => string; detail: Detail; runs: Run[] }) {
  const names: string[] = [];
  const count = new Map<string, number>();
  for (const r of runs) for (const g of pick(detail.stats[String(r.id)]) ?? []) count.set(g.name, (count.get(g.name) ?? 0) + g.trades);
  Array.from(count.entries())
    .sort((a, b) => b[1] - a[1])
    .forEach(([n]) => names.push(n));
  if (!names.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] whitespace-nowrap text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-400">
          <tr>
            <th scope="col" className="px-2 py-2">{title}</th>
            {runs.map((r, i) => (
              <th key={r.id} scope="col" className="px-2 py-2 text-right normal-case tracking-normal">
                <Tag run={r} i={i} base={r.id === detail.base} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {names.map((name) => (
            <tr key={name}>
              <th scope="row" className="px-2 py-1.5 font-normal text-slate-300" title={name}>{word(name)}</th>
              {runs.map((r) => {
                const g = pick(detail.stats[String(r.id)])?.find((x) => x.name === name);
                return (
                  <td key={r.id} className="px-2 py-1.5 text-right font-mono">
                    {g ? (
                      <span title={`${g.wins} won, ${g.losses} lost, held ${held(g.avg_hold)} on average`}>
                        <span className="text-slate-300">{g.trades}</span>{" "}
                        <span className="text-slate-400">
                          (<span className="text-emerald-400">{g.wins}</span>/<span className="text-rose-400">{g.losses}</span>)
                        </span>{" "}
                        · <span className={pnlTone(g.pnl)}>{money(g.pnl)}</span> <span className="text-slate-400">· {held(g.avg_hold)}</span>
                      </span>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reasons({ detail, runs }: { detail: Detail; runs: Run[] }) {
  return (
    <Card
      accent="amber"
      title="Why trades opened and why they closed"
      hint="Per run: trades (won / lost) · P&L · average time in the trade, on all the days each run finished."
    >
      <div className="space-y-4">
        <ReasonTable title="Entry reason" pick={(s) => s?.by_entry} word={entryWord} detail={detail} runs={runs} />
        <ReasonTable title="Exit reason" pick={(s) => s?.by_exit} word={exitWord} detail={detail} runs={runs} />
      </div>
    </Card>
  );
}
