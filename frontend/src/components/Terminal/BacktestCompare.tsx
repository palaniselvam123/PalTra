"use client";

/**
 * Compare backtest runs (GET /api/replay/compare, backend/backtest_compare.py): opened from the blotter's Backtests tab
 * (tick 2-8 runs, Compare). One run is the base, every other run is measured against it, and all of them are ranked
 * together on the days and stocks they all played. Read-only: practice results, no order, no setting changed.
 *
 * Each run keeps one colour by its place in the comparison (CSS `--s1`…`--s8`, globals.css `.cmp-series`), never by
 * its rank, so changing the base or the basis never repaints a run.
 */
import clsx from "clsx";
import { ArrowLeft, Download, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { C, money, short, ticks, Tip, useWidth } from "@/components/PnlDashboard/charts";
import { settingLabel } from "@/lib/settingLabels";
import {
  smaApi,
  type BacktestCompare as Compare,
  type CompareCell,
  type CompareMetrics,
  type CompareRank,
} from "@/lib/smaApi";
import { useTitle } from "@/lib/useTitle";
import { strategyLabel, type Settings } from "./BacktestRuns";
import { ThemeToggle } from "./Header";
import { SortTh, useSort } from "./sortable";
import { REASON_SHORT } from "./TradeHistoryTable";
import { Badge, pnlTone } from "./ui";

type Basis = "gross" | "net";
type Run = Compare["runs"][number];
type StockRow = Compare["by_stock"]["rows"][number];
type DayRow = Compare["by_day"]["rows"][number];

const MAX_RUNS = 8;
const colorOf = (i: number) => `var(--s${(i % MAX_RUNS) + 1})`;
// The best value of a column: a neutral ring, never the money colours (green / red keep their meaning).
const BEST = "rounded bg-sky-500/10 px-1 font-semibold ring-1 ring-inset ring-sky-400/40";

const EXTRA_LABELS: Record<string, string> = {
  bot: "bot whose settings were played",
  as_armed: "replayed as armed live",
  play_handoffs: "hand-offs played (Bot 2 / Bot 3)",
  fast_bot: "fast-mover hand-off to Bot 5",
};
const PICK_LABELS: Record<string, string> = {
  kind: "which Scalp backtest picked",
  pick_time: "pick time",
  top_n: "top N",
  direction: "direction",
  minutes: "candle (min)",
  rank: "ranked by",
  bias: "bias",
  min_value_cr: "min money traded (₹ cr)",
  source: "auto-arm source",
  first: "first arm",
  last: "last arm",
  every_min: "read every (min)",
  min_checks: "strong-trend checks",
  side: "side",
  pct: "buyers / sellers %",
  pressure_side: "buyers or sellers",
  max_day: "arms a day",
  price_min: "price from (₹)",
  price_max: "price to (₹)",
  min_atr_pct: "min ATR %",
  require_bias: "needs a bias",
  sma_fast: "SMA fast",
  sma_slow: "SMA slow",
};

function labelFor(key: string): string {
  if (key.startsWith("pick.")) return `pick rule: ${PICK_LABELS[key.slice(5)] ?? key.slice(5)}`;
  return EXTRA_LABELS[key] ?? settingLabel(key);
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "On" : "Off";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v === "" ? "(none)" : v;
  return JSON.stringify(v);
}

function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  if (typeof a === "string" && typeof b === "string") return a.trim().toUpperCase() === b.trim().toUpperCase();
  if ((typeof a === "boolean" || typeof b === "boolean") && a != null && b != null) return Boolean(a) === Boolean(b);
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function dayLabel(iso: string, weekday = true): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", {
    ...(weekday ? { weekday: "short" as const } : {}),
    day: "2-digit",
    month: "short",
    timeZone: "Asia/Kolkata",
  });
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}%`);
const pf = (m: CompareMetrics) => (m.no_loss ? "no loss" : m.profit_factor == null ? "—" : m.profit_factor.toFixed(2));
const cellOf = (cells: Record<string, CompareCell>, id: number) => cells[String(id)] ?? null;

function downloadCsv(name: string, rows: (string | number | null | undefined)[][]) {
  const text = rows
    .map((r) => r.map((v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))).join(","))
    .join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A run's colour swatch, number and bot (the text stays in text colours; the swatch carries the identity). */
function RunTag({ run, i, base }: { run: Run; i: number; base?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap normal-case tracking-normal">
      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorOf(i) }} />
      <span className="font-semibold text-slate-100">#{run.id}</span>
      <span className="text-xs font-normal text-slate-400">{run.bot_name}</span>
      {base ? <span className="rounded bg-white/10 px-1 text-[10px] font-semibold uppercase tracking-wider text-slate-300">base</span> : null}
    </span>
  );
}

function Section({ title, hint, right, children }: { title: string; hint?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-2 flex flex-wrap items-start gap-2">
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

function CsvButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex min-h-8 items-center gap-1 rounded-md border border-white/10 px-2 text-xs text-slate-300 hover:bg-white/5"
    >
      <Download size={12} aria-hidden /> CSV
    </button>
  );
}

export function BacktestCompare() {
  const [ids, setIds] = useState<number[] | null>(null);
  const [base, setBase] = useState<number | null>(null);
  const [basis, setBasis] = useState<Basis>("gross");
  const [data, setData] = useState<Compare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useTitle("Compare backtests · PalTra");

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const list = (q.get("runs") ?? "")
      .split(",")
      .map((x) => Number(x))
      .filter((n) => Number.isInteger(n) && n > 0);
    setIds(Array.from(new Set(list)));
    const b = Number(q.get("base"));
    if (Number.isInteger(b) && b > 0) setBase(b);
    if (q.get("basis") === "net") setBasis("net");
  }, []);

  useEffect(() => {
    if (!ids || ids.length < 2) return;
    let stop = false;
    setLoading(true);
    smaApi
      .replayCompare(ids, base, basis)
      .then((d) => {
        if (stop) return;
        setData(d);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "The comparison did not load");
      })
      .finally(() => {
        if (!stop) setLoading(false);
      });
    // Keep the address in step with the page, so it can be bookmarked or sent.
    const q = new URLSearchParams({ runs: ids.join(",") });
    if (base) q.set("base", String(base));
    if (basis === "net") q.set("basis", "net");
    window.history.replaceState(null, "", `${window.location.pathname}?${q.toString()}`);
    return () => {
      stop = true;
    };
  }, [ids, base, basis]);

  const meta = useMemo(() => new Map((data?.runs ?? []).map((r, i) => [r.id, { run: r, i }])), [data]);
  const tag = (id: number | null | undefined) => {
    const m = id == null ? undefined : meta.get(id);
    return m ? <RunTag run={m.run} i={m.i} base={data?.base === id} /> : <span>#{id}</span>;
  };

  return (
    <main className="cmp-series mx-auto max-w-[1500px] space-y-4 px-3 py-4 sm:px-6">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <a
          href="/terminal/"
          className="inline-flex min-h-9 items-center gap-1 rounded-md border border-white/10 px-2 text-sm text-slate-300 hover:bg-white/5"
        >
          <ArrowLeft size={14} aria-hidden /> Terminal
        </a>
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Compare backtests</h1>
          <p className="text-xs text-slate-400">
            Practice results on Groww&apos;s past candles, P&amp;L {basis === "net" ? "after" : "before"} charges. Nothing here
            changes a setting or places an order.
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {loading ? <Loader2 size={16} aria-label="Loading" className="animate-spin text-slate-400" /> : null}
          {data ? (
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              Base
              <select
                value={data.base}
                onChange={(e) => setBase(Number(e.target.value))}
                className="min-h-9 rounded-md border border-white/10 bg-surface px-2 text-sm text-slate-100"
                title="Every other run is measured against this one"
              >
                {data.runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {r.bot_name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div role="group" aria-label="P&L basis" className="inline-flex overflow-hidden rounded-md border border-white/10 text-xs">
            {(["gross", "net"] as const).map((b) => (
              <button
                key={b}
                type="button"
                aria-pressed={basis === b}
                onClick={() => setBasis(b)}
                className={clsx("min-h-9 px-2.5", basis === b ? "bg-white/10 font-semibold text-slate-100" : "text-slate-400 hover:bg-white/5")}
              >
                {b === "gross" ? "Before charges" : "After charges"}
              </button>
            ))}
          </div>
          <ThemeToggle />
        </div>
      </header>

      {ids !== null && ids.length < 2 ? (
        <p className="rounded-xl border border-slate-800 bg-card p-4 text-sm text-slate-300">
          Tick 2 to {MAX_RUNS} runs in the terminal&apos;s Backtests tab (blotter) and press <b>Compare</b>. The first one you tick
          is the base.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/[0.06] p-3 text-sm text-rose-200">
          {error}
        </p>
      ) : null}
      {!data && !error && ids && ids.length >= 2 ? <p className="text-sm text-slate-400">Comparing {ids.length} runs…</p> : null}

      {data ? (
        <>
          <RunCards data={data} />
          <Answer data={data} tag={tag} />
          <Ranking data={data} meta={meta} />
          <Section
            title="Running total, day by day"
            hint={
              data.ground.days_filtered
                ? `Each run's P&L added up over the ${data.ground.days.length} day(s) every run finished.`
                : "These runs share no day: each line runs over the days that run played."
            }
          >
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
              {data.runs.map((r, i) => (
                <RunTag key={r.id} run={r} i={i} base={r.id === data.base} />
              ))}
            </div>
            <RunningTotals data={data} />
          </Section>
          <Differences data={data} tag={tag} />
          <ByStock data={data} tag={tag} />
          <ByDay data={data} />
          <ByReason data={data} />
        </>
      ) : null}
    </main>
  );
}

/** The runs being compared: dates, days finished, stocks and the strategy in words. */
function RunCards({ data }: { data: Compare }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {data.runs.map((r, i) => (
        <div key={r.id} className="min-w-0 rounded-xl border border-slate-800 bg-card p-3 text-xs">
          <RunTag run={r} i={i} base={r.id === data.base} />
          <div className="mt-1 text-slate-400">
            {dayLabel(r.start_date, false)}
            {r.end_date !== r.start_date ? ` → ${dayLabel(r.end_date, false)}` : ""} · {r.finished_days} day(s) finished
            {r.days_total > r.finished_days ? ` of ${r.days_total}` : ""} · {r.symbols.length} stock(s)
            {r.status !== "FINISHED" ? ` · ${r.status.toLowerCase()}` : ""}
          </div>
          <div className="mt-1 line-clamp-3 text-slate-300" title={strategyLabel(r.settings as Settings)}>
            {strategyLabel(r.settings as Settings)}
          </div>
        </div>
      ))}
    </div>
  );
}

/** The comparison in plain words: the best strategy, how sure, what to watch out for, which stocks and days. */
function Answer({ data, tag }: { data: Compare; tag: (id: number | null | undefined) => ReactNode }) {
  const v = data.verdict;
  const row = (id: number | null | undefined) => data.overall.find((r) => r.run_id === id);
  const lines: { tone: "main" | "good" | "warn" | "info"; body: ReactNode }[] = [];
  const names = (list: string[], cap = 8) => `${list.slice(0, cap).join(", ")}${list.length > cap ? ` and ${list.length - cap} more` : ""}`;

  const best = row(v.best);
  if (v.best == null || !best) {
    lines.push({ tone: "warn", body: "Nothing to compare yet: none of these runs has a finished day." });
  } else {
    const b = best.metrics;
    lines.push({
      tone: "main",
      body: (
        <>
          Best overall: {tag(v.best)}. It made <b className={pnlTone(b.pnl)}>{money(b.pnl)}</b> over {b.days} day(s) and {b.trades}{" "}
          trade(s), green on {b.green_days} of {b.days} days, biggest drop {money(b.max_drawdown)}.
        </>
      ),
    });
    const runner = row(v.runner_up);
    const h = v.head_to_head;
    if (runner && h) {
      const decided = h.won + h.lost;
      const lead = v.lead ?? 0;
      const total = ` (${money(lead)} in total)`;
      const text =
        v.confidence === "clear" ? (
          <>
            It beat {tag(v.runner_up)} on {h.won} of {decided} days head to head{total}: a clear lead.
          </>
        ) : v.confidence === "likely" ? (
          <>
            It beat {tag(v.runner_up)} on {h.won} of {decided} days{total}: a likely lead. Replay more days before you switch for good.
          </>
        ) : v.confidence === "close" ? (
          <>
            It beat {tag(v.runner_up)} on only {h.won} of {decided} days{total}: too close to call, the difference could be luck.
          </>
        ) : (
          <>
            Only {b.days} day(s) and {b.trades} trade(s) to judge against {tag(v.runner_up)}: too few to call. Replay at least 10 days of
            the same stocks.
          </>
        );
      lines.push({ tone: v.confidence === "clear" ? "good" : v.confidence === "likely" ? "info" : "warn", body: text });
      if (lead < 0) {
        lines.push({
          tone: "info",
          body: (
            <>
              It made {money(-lead)} less than {tag(v.runner_up)} but ranks higher on steadiness (green days, smaller drops, per trade).
            </>
          ),
        });
      }
    }
    const most = row(v.most_pnl);
    if (most && v.most_pnl !== v.best) {
      lines.push({
        tone: "info",
        body: (
          <>
            Most money: {tag(v.most_pnl)} made {money(most.metrics.pnl)} ({money(most.metrics.pnl - b.pnl)} against the best), with{" "}
            {most.metrics.red_days} red day(s) and a biggest drop of {money(most.metrics.max_drawdown)}.
          </>
        ),
      });
    }
    const steady = row(v.steadiest);
    if (steady && v.steadiest !== v.best && v.steadiest !== v.most_pnl) {
      lines.push({
        tone: "info",
        body: (
          <>
            Steadiest: {tag(v.steadiest)}, green on {pct(steady.metrics.green_pct)} of days, biggest drop {money(steady.metrics.max_drawdown)}.
          </>
        ),
      });
    }
    const per = row(v.best_per_trade);
    if (per && v.best_per_trade !== v.best) {
      lines.push({
        tone: "info",
        body: (
          <>
            Best per trade: {tag(v.best_per_trade)}, {money(per.metrics.avg_trade)} a trade over {per.metrics.trades} trade(s).
          </>
        ),
      });
    }
    if (v.warnings.includes("all_lost")) {
      lines.push({
        tone: "warn",
        body: "None of these strategies made money on these days. Change more than the stop (the entry or the exits), or test other stocks.",
      });
    }
    if (v.lean?.stock) {
      lines.push({
        tone: "warn",
        body: (
          <>
            Careful: {v.lean.stock.share}% of {tag(v.best)}&apos;s profit came from one stock, <b>{v.lean.stock.symbol}</b> (
            {money(v.lean.stock.pnl)}). Check it still wins without that stock.
          </>
        ),
      });
    }
    if (v.lean?.day) {
      lines.push({
        tone: "warn",
        body: (
          <>
            Careful: {v.lean.day.share}% of its profit came from one day, {dayLabel(v.lean.day.date)} ({money(v.lean.day.pnl)}).
          </>
        ),
      });
    }
  }
  for (const note of data.ground.notes) {
    if (note.code === "different_days")
      lines.push({
        tone: "info",
        body: `Compared on the ${note.days} day(s) every run finished. Some runs played more: switch the table to "Everything each run played" to see their full totals.`,
      });
    if (note.code === "no_common_days")
      lines.push({
        tone: "warn",
        body: "These runs share no day, so their totals cover different days. Read the ranking as a rough guide only: replay the same days for a fair test.",
      });
    if (note.code === "different_stocks")
      lines.push({ tone: "info", body: `Compared on the ${note.stocks} stock(s) every run played; the By stock table lists all of them.` });
    if (note.code === "no_common_stocks")
      lines.push({
        tone: "info",
        body: "These runs traded different stocks (for example Scalp-page pick runs): compared on the common days with each run's own stocks.",
      });
    if (note.code === "no_finished_day")
      lines.push({ tone: "warn", body: `Run ${(note.runs ?? []).map((r) => `#${r}`).join(", ")} has no finished day yet and is left out of the ranking.` });
  }
  if (v.good_on_all?.length) lines.push({ tone: "good", body: `Good on every strategy: ${names(v.good_on_all)}.` });
  if (v.bad_on_all?.length)
    lines.push({
      tone: "warn",
      body: `Lost on every strategy: ${names(v.bad_on_all)}. Worth taking off the Trade list, or a closer look on its own.`,
    });
  const s = data.by_stock;
  for (const sug of s.suggestions.slice(0, 3)) {
    lines.push({
      tone: "info",
      body: (
        <>
          <b>{sug.symbol}</b> did better with {tag(sug.run_id)}&apos;s settings: {money(sug.pnl)} against {money(sug.best_run_pnl)} on{" "}
          {tag(v.best)} ({sug.trades} trades). Try them as {sug.symbol}&apos;s own settings (Settings → Settings for: {sug.symbol}).
        </>
      ),
    });
  }
  if (s.uplift != null && s.uplift > 0 && s.suggestions.length) {
    lines.push({
      tone: "info",
      body: (
        <>
          Each stock on its own best strategy would have made {money(s.each_best_total)} instead of {money(s.best_run_total)}: hindsight,
          so a hint where per-stock settings may help, not a promise.
        </>
      ),
    });
  }
  const border = { main: "border-slate-400/60", good: "border-emerald-400/50", warn: "border-amber-400/60", info: "border-sky-400/40" };
  return (
    <Section title="The answer" hint="Ranked on the days and stocks every run played, so a longer run does not win by playing more.">
      <ul className="space-y-1.5 text-sm text-slate-200">
        {lines.map((l, k) => (
          <li key={k} className={clsx("border-l-2 pl-2.5 leading-relaxed", border[l.tone], l.tone === "main" && "text-base text-slate-100")}>
            {l.body}
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Every run ranked, with the best value of each column marked. */
function Ranking({ data, meta }: { data: Compare; meta: Map<number, { run: Run; i: number }> }) {
  const [all, setAll] = useState(false);
  const rows = data.overall;
  const m = (r: CompareRank) => (all ? r.metrics_all : r.metrics);
  const ranked = rows.filter((r) => r.rank != null);
  const traded = ranked.filter((r) => m(r).trades > 0);
  const top = (list: CompareRank[], f: (r: CompareRank) => number | null): number | null => {
    let best: number | null = null;
    for (const r of list) {
      const v = f(r);
      if (v != null && (best == null || v > best)) best = v;
    }
    return best;
  };
  const pfValue = (r: CompareRank) => (m(r).no_loss ? Number.POSITIVE_INFINITY : m(r).profit_factor);
  const bests = {
    pnl: top(ranked, (r) => m(r).pnl),
    win: top(traded, (r) => m(r).win_rate),
    green: top(ranked, (r) => m(r).green_pct),
    pf: top(traded, pfValue),
    avg: top(traded, (r) => m(r).avg_trade),
    day: top(ranked, (r) => m(r).avg_day),
    dd: top(traded, (r) => m(r).max_drawdown),
    won: top(ranked, (r) => r.days_won),
    stocks: top(ranked, (r) => r.stocks_won),
  };
  const mark = (on: boolean, children: ReactNode) => <span className={clsx(on && BEST)}>{children}</span>;
  const PLACE: Record<string, string> = {
    pnl: "P&L",
    green_pct: "green days",
    max_drawdown: "biggest drop",
    profit_factor: "profit factor",
    avg_trade: "per trade",
    days_won: "days won",
  };
  const placeText = (r: CompareRank) =>
    r.avg_place == null
      ? "No finished day: not ranked"
      : `Average place ${r.avg_place} (P&L counts twice): ${Object.entries(r.places)
          .map(([k, p]) => `${PLACE[k] ?? k} ${p}`)
          .join(", ")}`;
  return (
    <Section
      title="Strategies ranked"
      hint="Rank = average place over P&L (counted twice), green days, biggest drop, profit factor, P&L per trade and days won. The best of each column is ringed."
      right={
        <div role="group" aria-label="Which numbers" className="inline-flex overflow-hidden rounded-md border border-white/10 text-xs">
          {[false, true].map((on) => (
            <button
              key={String(on)}
              type="button"
              aria-pressed={all === on}
              onClick={() => setAll(on)}
              className={clsx("min-h-8 px-2.5", all === on ? "bg-white/10 font-semibold text-slate-100" : "text-slate-400 hover:bg-white/5")}
            >
              {on ? "Everything each run played" : "Common ground"}
            </button>
          ))}
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] whitespace-nowrap text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              {["Rank", "Run", "P&L", "vs base", "Trades", "Win %", "Green days", "Profit factor", "Per trade", "Per day", "Biggest drop", "Days won", "Stocks won", "Best day", "Worst day"].map(
                (h, k) => (
                  <th key={h} scope="col" className={clsx("px-2 py-2", k >= 2 && "text-right")}>
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {rows.map((r) => {
              const x = m(r);
              const info = meta.get(r.run_id);
              return (
                <tr key={r.run_id} className={clsx(r.rank === 1 && "bg-white/[0.03]")}>
                  <td className="px-2 py-2 font-semibold text-slate-100" title={placeText(r)}>
                    {r.rank ?? "—"}
                  </td>
                  <td className="px-2 py-2">{info ? <RunTag run={info.run} i={info.i} base={r.run_id === data.base} /> : `#${r.run_id}`}</td>
                  <td className={clsx("px-2 py-2 text-right font-mono", pnlTone(x.pnl))}>{mark(x.pnl === bests.pnl && r.rank != null, money(x.pnl))}</td>
                  <td
                    className={clsx("px-2 py-2 text-right font-mono", r.run_id === data.base ? "text-slate-500" : pnlTone(r.vs_base.pnl_diff))}
                    title={`Common ground, day by day against the base: won ${r.vs_base.head_to_head.won}, lost ${r.vs_base.head_to_head.lost}, level ${r.vs_base.head_to_head.tied}`}
                  >
                    {r.run_id === data.base ? "base" : money(r.vs_base.pnl_diff)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">{x.trades}</td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">{mark(x.trades > 0 && x.win_rate === bests.win, pct(x.win_rate))}</td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">
                    {mark(r.rank != null && x.green_pct === bests.green, `${x.green_days}/${x.days}`)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">{mark(x.trades > 0 && pfValue(r) === bests.pf, pf(x))}</td>
                  <td className={clsx("px-2 py-2 text-right font-mono", pnlTone(x.avg_trade))}>
                    {mark(x.trades > 0 && x.avg_trade === bests.avg, money(x.avg_trade))}
                  </td>
                  <td className={clsx("px-2 py-2 text-right font-mono", pnlTone(x.avg_day))}>{mark(r.rank != null && x.avg_day === bests.day, money(x.avg_day))}</td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">
                    {mark(x.trades > 0 && x.max_drawdown === bests.dd, money(x.max_drawdown))}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">{mark(r.rank != null && r.days_won === bests.won && r.days_won > 0, r.days_won)}</td>
                  <td className="px-2 py-2 text-right font-mono text-slate-200">
                    {mark(r.rank != null && r.stocks_won === bests.stocks && r.stocks_won > 0, r.stocks_won)}
                  </td>
                  <td className="px-2 py-2 text-right text-slate-300">{x.best_day ? `${dayLabel(x.best_day.date, false)} ${money(x.best_day.pnl)}` : "—"}</td>
                  <td className="px-2 py-2 text-right text-slate-300">{x.worst_day ? `${dayLabel(x.worst_day.date, false)} ${money(x.worst_day.pnl)}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {all ? (
        <p className="mt-2 text-xs text-amber-200">
          Everything each run played: totals over different days or stocks are not like-for-like. The rank and the answer stay on the common
          ground.
        </p>
      ) : null}
    </Section>
  );
}

/** Each run's running total over the days, one line per run; hover a day for every run's value. */
function RunningTotals({ data }: { data: Compare }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const days = data.curve.days;
  const series = data.runs.map((r, i) => {
    const raw = data.curve.runs[String(r.id)] ?? [];
    let last = 0;
    // A day a run did not play keeps its running total where it was.
    const values = days.map((_, k) => {
      const v = raw[k];
      if (v != null) last = v;
      return last;
    });
    return { run: r, i, values };
  });
  if (!days.length) return <p className="text-sm text-slate-400">No finished day to draw yet.</p>;
  const labelled = series.length <= 4;
  const pad = { l: 56, r: labelled ? 70 : 16, t: 14, b: 28 };
  const height = 300;
  const all = series.flatMap((s) => s.values);
  const ys = ticks(Math.min(...all), Math.max(...all));
  const lo = ys[0];
  const hi = ys[ys.length - 1];
  const iw = Math.max(10, w - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const n = days.length;
  const px = (k: number) => pad.l + (n === 1 ? iw / 2 : (k / (n - 1)) * iw);
  const py = (v: number) => pad.t + ih - ((v - lo) / (hi - lo || 1)) * ih;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
  const path = (vals: number[]) => vals.map((v, k) => `${k ? "L" : "M"}${px(k).toFixed(1)},${py(v).toFixed(1)}`).join("");
  // End labels (up to 4 runs), nudged apart so they never sit on each other.
  const ends = labelled
    ? series.map((s) => ({ s, y: py(s.values[n - 1]) })).sort((a, b) => a.y - b.y)
    : [];
  for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const rel = ((e.clientX - box.left) / box.width) * w - pad.l;
    setHover(Math.max(0, Math.min(n - 1, Math.round((rel / iw) * (n - 1)))));
  };
  return (
    <div ref={ref} className="relative w-full min-w-0">
      <svg
        width={w}
        height={height}
        role="img"
        aria-label="Each run's running total, day by day"
        className="block touch-none select-none"
        onPointerMove={move}
        onPointerLeave={() => setHover(null)}
      >
        {ys.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={w - pad.r} y1={py(v)} y2={py(v)} stroke={C.grid} strokeWidth={v === 0 ? 1.5 : 1} />
            <text x={pad.l - 6} y={py(v) + 4} textAnchor="end" fontSize="11" fill={C.axis}>
              {short(v)}
            </text>
          </g>
        ))}
        {days.map((d, k) =>
          (k % every === 0 && n - 1 - k >= every * 0.6) || k === n - 1 ? (
            <text key={d} x={px(k)} y={height - 8} textAnchor="middle" fontSize="11" fill={C.axis}>
              {dayLabel(d, false)}
            </text>
          ) : null
        )}
        {series.map((s) =>
          n > 1 ? (
            <path key={s.run.id} d={path(s.values)} fill="none" stroke={colorOf(s.i)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ) : (
            <circle key={s.run.id} cx={px(0)} cy={py(s.values[0])} r={5} fill={colorOf(s.i)} stroke="rgb(var(--c-surface))" strokeWidth={2} />
          )
        )}
        {ends.map(({ s, y }) => (
          <g key={s.run.id}>
            <line x1={w - pad.r + 4} x2={w - pad.r + 14} y1={y} y2={y} stroke={colorOf(s.i)} strokeWidth={2} />
            <text x={w - pad.r + 17} y={y + 4} fontSize="11" fill="rgb(var(--c-body-text))">
              #{s.run.id}
            </text>
          </g>
        ))}
        {hover != null ? (
          <g>
            <line x1={px(hover)} x2={px(hover)} y1={pad.t} y2={pad.t + ih} stroke={C.axis} strokeWidth={1} />
            {series.map((s) => (
              <circle key={s.run.id} cx={px(hover)} cy={py(s.values[hover])} r={4} fill={colorOf(s.i)} stroke="rgb(var(--c-surface))" strokeWidth={2} />
            ))}
          </g>
        ) : null}
      </svg>
      {hover != null ? (
        <Tip x={px(hover)} w={w}>
          <div className="mb-0.5 font-semibold text-slate-100">{dayLabel(days[hover])}</div>
          {[...series]
            .sort((a, b) => b.values[hover] - a.values[hover])
            .map((s) => (
              <div key={s.run.id} className="flex justify-between gap-3">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: colorOf(s.i) }} />#{s.run.id}
                </span>
                <span className="font-mono tabular-nums">{money(s.values[hover])}</span>
              </div>
            ))}
        </Tip>
      ) : null}
    </div>
  );
}

/** What each run changed against the base, the settings that differ, and (3+ runs) each setting's runs side by side. */
function Differences({ data, tag }: { data: Compare; tag: (id: number | null | undefined) => ReactNode }) {
  const d = data.differences;
  const baseKey = String(data.base);
  return (
    <Section
      title="What each strategy changed"
      hint="Against the base, the settings that were not the same in every run. A change and its result side by side: with one change it is that setting's effect, with several it is all of them together."
    >
      <ul className="mb-3 space-y-1.5 text-sm text-slate-200">
        {d.vs_base.map((v) => {
          const labels = v.changed.map(labelFor);
          const shown = labels.slice(0, 6).join(", ") + (labels.length > 6 ? ` and ${labels.length - 6} more` : "");
          const h = v.head_to_head;
          return (
            <li key={v.run_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              {tag(v.run_id)}
              <span className="text-slate-400">against</span>
              {tag(data.base)}
              <span className="text-slate-300">
                {labels.length || v.changed_stocks.length
                  ? `changed ${shown || "no shared setting"}${v.changed_stocks.length ? `${labels.length ? "," : ""} and the own settings of ${v.changed_stocks.length} stock(s)` : ""}`
                  : "same settings as the base"}
              </span>
              <span className={clsx("font-mono", pnlTone(v.pnl_diff))}>{money(v.pnl_diff)}</span>
              <span className="text-xs text-slate-400">
                won {h.won}, lost {h.lost} of {h.days} day(s)
              </span>
            </li>
          );
        })}
      </ul>
      {d.fields.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th scope="col" className="px-2 py-2">
                  Setting
                </th>
                {data.runs.map((r, i) => (
                  <th key={r.id} scope="col" className="px-2 py-2">
                    <RunTag run={r} i={i} base={r.id === data.base} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {d.fields.map((f) => (
                <tr key={f.key}>
                  <th scope="row" className="px-2 py-1.5 font-normal text-slate-300" title={f.key}>
                    {labelFor(f.key)}
                  </th>
                  {data.runs.map((r) => {
                    const value = f.values[String(r.id)];
                    const idle = (f.unused ?? []).includes(r.id);
                    const baseIdle = (f.unused ?? []).includes(data.base);
                    const changed = String(r.id) !== baseKey && !(idle && baseIdle) && (idle !== baseIdle || !sameValue(value, f.values[baseKey]));
                    return (
                      <td key={r.id} className="px-2 py-1.5 font-mono text-slate-200">
                        {idle ? (
                          <span className="font-sans text-slate-500" title={`Set to ${fmtValue(value)}, but its switch was off in this run, so it did nothing`}>
                            not used
                          </span>
                        ) : (
                          <span
                            className={clsx(changed && "rounded bg-amber-500/10 px-1 ring-1 ring-inset ring-amber-400/40")}
                            title={changed ? "Not the base's value" : undefined}
                          >
                            {fmtValue(value)}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-400">
          Every run used the same shared settings: the differences come from the days or stocks played, each stock&apos;s own settings, or
          app changes between the runs.
        </p>
      )}
      {d.effects.length ? (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Each setting across the runs</h3>
          <p className="mb-1 text-xs text-slate-400">Runs grouped by the value of each setting, their average P&amp;L. A hint, not proof: other settings may differ too.</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <tbody className="divide-y divide-white/5">
                {d.effects.map((e) => (
                  <tr key={e.key}>
                    <th scope="row" className="px-2 py-1.5 font-normal text-slate-300">
                      {labelFor(e.key)}
                    </th>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-wrap gap-x-4 gap-y-1">
                        {e.groups.map((g, k) => (
                          <span key={k} className="inline-flex items-center gap-1.5">
                            <span className="font-mono text-slate-200">{fmtValue(g.value)}</span>
                            <span className={clsx("font-mono", pnlTone(g.avg_pnl))}>{money(g.avg_pnl)}</span>
                            <span className="text-slate-500">avg of {g.runs.map((id) => `#${id}`).join(", ")}</span>
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {d.stock_fields.length ? (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-slate-400">Each stock&apos;s own settings that differ ({d.stock_fields.length})</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <tbody className="divide-y divide-white/5">
                {d.stock_fields.map((f) => (
                  <tr key={`${f.symbol}-${f.key}`}>
                    <th scope="row" className="px-2 py-1.5 font-normal text-slate-300">
                      {f.symbol} · {labelFor(f.key)}
                    </th>
                    {data.runs.map((r) => (
                      <td key={r.id} className="px-2 py-1.5 font-mono text-slate-200">
                        {f.values[String(r.id)] == null ? <span className="text-slate-500">shared</span> : fmtValue(f.values[String(r.id)])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </Section>
  );
}

const STOCK_VERDICT: Record<StockRow["verdict"], { text: string; color: "green" | "red" | "amber" | "slate" }> = {
  ALL_WIN: { text: "Wins on all", color: "green" },
  ALL_LOSE: { text: "Loses on all", color: "red" },
  DEPENDS: { text: "Depends on strategy", color: "amber" },
  MIXED: { text: "Mixed", color: "slate" },
  NO_TRADES: { text: "No trades", color: "slate" },
  ONLY_ONE: { text: "One run only", color: "slate" },
};

function Cell({ cell, best }: { cell: CompareCell; best: boolean }) {
  if (!cell) return <span className="text-slate-500" title="This run did not play it">—</span>;
  return (
    <span className={clsx("inline-flex items-baseline gap-1", best && BEST)}>
      <span className={pnlTone(cell.pnl)}>{money(cell.pnl)}</span>
      <span className="text-slate-500">· {cell.trades}</span>
    </span>
  );
}

/** Each stock under every run: which strategy suits it, the ones that win or lose whatever the strategy. */
function ByStock({ data, tag }: { data: Compare; tag: (id: number | null | undefined) => ReactNode }) {
  const [show, setShow] = useState<"ALL" | StockRow["verdict"]>("ALL");
  const [q, setQ] = useState("");
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of data.by_stock.rows) out[r.verdict] = (out[r.verdict] ?? 0) + 1;
    return out;
  }, [data]);
  const rows = useMemo(
    () => data.by_stock.rows.filter((r) => (show === "ALL" || r.verdict === show) && (!q || r.symbol.includes(q.trim().toUpperCase()))),
    [data, show, q]
  );
  const { sorted, sort, onSort } = useSort<StockRow, string>(rows, (r, k) =>
    k === "symbol" ? r.symbol : k === "spread" ? r.spread : k === "best" ? r.best : cellOf(r.cells, Number(k.slice(1)))?.pnl ?? null
  );
  const csv = () =>
    downloadCsv(`compare-stocks-${data.runs.map((r) => r.id).join("-")}.csv`, [
      ["stock", "verdict", "best run", ...data.runs.flatMap((r) => [`#${r.id} P&L`, `#${r.id} trades`])],
      ...sorted.map((r) => [
        r.symbol,
        STOCK_VERDICT[r.verdict].text,
        r.best ? `#${r.best}` : "",
        ...data.runs.flatMap((run): (string | number)[] => {
          const c = cellOf(r.cells, run.id);
          return c ? [c.pnl, c.trades] : ["", ""];
        }),
      ]),
    ]);
  const FILTERS: ("ALL" | StockRow["verdict"])[] = ["ALL", "DEPENDS", "ALL_WIN", "ALL_LOSE", "MIXED", "NO_TRADES"];
  const s = data.by_stock;
  return (
    <Section
      title="By stock"
      hint={`Each stock's P&L · trades under every run, on the ${data.ground.days_filtered ? "common days" : "days each run played"}. The best run of each stock is ringed.`}
      right={<CsvButton onClick={csv} />}
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={show === f}
            onClick={() => setShow(f)}
            className={clsx(
              "min-h-8 rounded-md border px-2 text-xs",
              show === f ? "border-sky-400/50 bg-sky-500/10 font-semibold text-slate-100" : "border-white/10 text-slate-400 hover:bg-white/5"
            )}
          >
            {f === "ALL" ? "All" : STOCK_VERDICT[f].text} ({f === "ALL" ? s.rows.length : counts[f] ?? 0})
          </button>
        ))}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find a stock"
          aria-label="Find a stock"
          className="ml-auto min-h-8 w-40 rounded-md border border-white/10 bg-surface px-2 text-xs text-slate-100"
        />
      </div>
      {s.suggestions.length ? (
        <div className="mb-2 rounded-lg border border-white/10 p-2 text-xs text-slate-300">
          <div className="mb-1 font-semibold text-slate-200">Stocks another run did clearly better on than the best overall ({tag(data.verdict.best)})</div>
          <ul className="space-y-0.5">
            {s.suggestions.map((x) => (
              <li key={x.symbol} className="flex flex-wrap items-baseline gap-x-2">
                <b className="text-slate-100">{x.symbol}</b>
                {tag(x.run_id)}
                <span className={clsx("font-mono", pnlTone(x.pnl))}>{money(x.pnl)}</span>
                <span className="text-slate-500">against</span>
                <span className={clsx("font-mono", pnlTone(x.best_run_pnl))}>{money(x.best_run_pnl)}</span>
                <span className="text-slate-500">({x.trades} trades, {money(x.gain)} better)</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="max-h-[560px] overflow-auto">
        <table className="w-full min-w-[720px] whitespace-nowrap text-left text-xs">
          <thead className="sticky top-0 z-10 bg-card text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <SortTh label="Stock" k="symbol" text sort={sort} onSort={onSort} />
              <th scope="col" className="px-2 py-1">
                Verdict
              </th>
              {data.runs.map((r, i) => (
                <SortTh key={r.id} label={<RunTag run={r} i={i} base={r.id === data.base} />} k={`r${r.id}`} num sort={sort} onSort={onSort} />
              ))}
              <SortTh label="Spread" k="spread" num title="Best run's P&L minus the worst's on this stock" sort={sort} onSort={onSort} />
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sorted.map((r) => (
              <tr key={r.symbol}>
                <td className="px-2 py-1.5 font-semibold text-slate-100">{r.symbol}</td>
                <td className="px-2 py-1.5">
                  <Badge color={STOCK_VERDICT[r.verdict].color}>{STOCK_VERDICT[r.verdict].text}</Badge>
                </td>
                {data.runs.map((run) => (
                  <td key={run.id} className="px-2 py-1.5 text-right font-mono">
                    <Cell cell={cellOf(r.cells, run.id)} best={r.best === run.id} />
                  </td>
                ))}
                <td className="px-2 py-1.5 text-right font-mono text-slate-300">{r.spread == null ? "—" : money(r.spread)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!sorted.length ? <p className="py-3 text-sm text-slate-400">No stock matches.</p> : null}
      </div>
    </Section>
  );
}

/** Each day under every run: who won the day, and the days every strategy lost (or won). */
function ByDay({ data }: { data: Compare }) {
  const { sorted, sort, onSort } = useSort<DayRow, string>(data.by_day.rows, (r, k) =>
    k === "date" ? r.date : cellOf(r.cells, Number(k.slice(1)))?.pnl ?? null
  );
  const csv = () =>
    downloadCsv(`compare-days-${data.runs.map((r) => r.id).join("-")}.csv`, [
      ["day", "best run", ...data.runs.flatMap((r) => [`#${r.id} P&L`, `#${r.id} trades`])],
      ...sorted.map((r) => [
        r.date,
        r.best ? `#${r.best}` : "",
        ...data.runs.flatMap((run): (string | number)[] => {
          const c = cellOf(r.cells, run.id);
          return c ? [c.pnl, c.trades] : ["", ""];
        }),
      ]),
    ]);
  const won = new Map(data.overall.map((r) => [r.run_id, r.days_won]));
  const { all_red, all_green } = data.by_day;
  return (
    <Section
      title="By day"
      hint={`${data.ground.stocks_filtered ? "On the stocks every run played. " : ""}The run that did best each day is ringed.${all_red.length ? ` Every strategy lost on ${all_red.length} day(s): days to sit out, not a setting to fix.` : ""}${all_green.length ? ` Every strategy made money on ${all_green.length} day(s).` : ""}`}
      right={<CsvButton onClick={csv} />}
    >
      <div className="max-h-[560px] overflow-auto">
        <table className="w-full min-w-[720px] whitespace-nowrap text-left text-xs">
          <thead className="sticky top-0 z-10 bg-card text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <SortTh label="Day" k="date" text sort={sort} onSort={onSort} />
              {data.runs.map((r, i) => (
                <SortTh key={r.id} label={<RunTag run={r} i={i} base={r.id === data.base} />} k={`r${r.id}`} num sort={sort} onSort={onSort} />
              ))}
              <th scope="col" className="px-2 py-1" />
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sorted.map((r) => (
              <tr key={r.date}>
                <td className="px-2 py-1.5 text-slate-200">{dayLabel(r.date)}</td>
                {data.runs.map((run) => (
                  <td key={run.id} className="px-2 py-1.5 text-right font-mono">
                    <Cell cell={cellOf(r.cells, run.id)} best={r.best === run.id} />
                  </td>
                ))}
                <td className="px-2 py-1.5">
                  {r.all_red ? <Badge color="red">All lost</Badge> : r.all_green ? <Badge color="green">All won</Badge> : null}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-white/10 text-slate-300">
            <tr>
              <th scope="row" className="px-2 py-1.5 text-left font-semibold">
                Days won
              </th>
              {data.runs.map((run) => (
                <td key={run.id} className="px-2 py-1.5 text-right font-mono">
                  {won.get(run.id) ?? 0}
                </td>
              ))}
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </Section>
  );
}

/** Why each run's trades closed, and what each way out made. */
function ByReason({ data }: { data: Compare }) {
  if (!data.by_reason.length) return null;
  return (
    <Section title="How the trades closed" hint="Trades · P&L per exit reason, on the common ground. Shows where a stop or exit rule saved or cost money.">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] whitespace-nowrap text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wider text-slate-400">
            <tr>
              <th scope="col" className="px-2 py-2">
                Exit reason
              </th>
              {data.runs.map((r, i) => (
                <th key={r.id} scope="col" className="px-2 py-2 text-right">
                  <RunTag run={r} i={i} base={r.id === data.base} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {data.by_reason.map((row) => (
              <tr key={row.reason}>
                <th scope="row" className="px-2 py-1.5 font-normal text-slate-300" title={row.reason}>
                  {REASON_SHORT[row.reason] ?? row.reason}
                </th>
                {data.runs.map((run) => {
                  const c = row.cells[String(run.id)];
                  return (
                    <td key={run.id} className="px-2 py-1.5 text-right font-mono">
                      {c ? (
                        <span className="inline-flex items-baseline gap-1">
                          <span className="text-slate-300">{c.count}</span>
                          <span className="text-slate-500">·</span>
                          <span className={pnlTone(c.pnl)}>{money(c.pnl)}</span>
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
    </Section>
  );
}
