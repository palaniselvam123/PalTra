"use client";

/**
 * P&L dashboard (the home page): when in the day is the bots' P&L at its best, so all trades can be closed then?
 * Reads the minute-by-minute P&L log (backend pnl_dashboard.py) for backtest runs or market days. Read-only: nothing
 * here places, changes or closes an order. Filters are kept per browser.
 */

import clsx from "clsx";
import { BarChart3, Download, Loader2, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Explain } from "@/components/ui/Explain";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { saveFile } from "@/lib/chartTable";
import { smaApi, type PnlDashboard as Dash, type PnlDashOptions, type PnlDashQuery } from "@/lib/smaApi";
import { Bars, C, DayRanges, DivergingBars, Legend, TimeChart, money } from "./charts";

const STORE = "pnlDash.filters";
const DESK_LABEL: Record<string, string> = { "1": "Bot 1", "2": "Bot 2", "3": "Bot 3", "4": "Bot 4", "5": "Bot 5", research: "Research" };
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
const STEPS = [1, 5, 10, 15, 30];

const QTY_PICKS = [100, 500, 1000];

type Filters = Required<Pick<PnlDashQuery, "source" | "runs" | "desks" | "stocks" | "basis" | "step" | "weekdays" | "qty">> & {
  start: string;
  end: string;
};
const DEFAULTS: Filters = { source: "replay", runs: [], desks: ["1"], start: "", end: "", stocks: [], basis: "gross", step: 5, weekdays: [], qty: 0 };

function loadFilters(): Filters {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || "{}");
    return { ...DEFAULTS, ...raw };
  } catch {
    return DEFAULTS;
  }
}

const cls = (v: number | null | undefined) => (v == null ? "text-slate-300" : v > 0 ? "text-profit" : v < 0 ? "text-loss" : "text-slate-300");

export function PnlDashboard() {
  const [f, setF] = useState<Filters>(DEFAULTS);
  const [ready, setReady] = useState(false);
  const [opts, setOpts] = useState<PnlDashOptions | null>(null);
  const [data, setData] = useState<Dash | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pickedDay, setPickedDay] = useState<string | null>(null);

  useEffect(() => {
    setF(loadFilters());
    setReady(true);
    smaApi
      .pnlDashOptions()
      .then(setOpts)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : "Could not load the runs"));
  }, []);
  useEffect(() => {
    if (!ready) return;
    try {
      localStorage.setItem(STORE, JSON.stringify(f));
    } catch {
      /* private mode */
    }
  }, [f, ready]);

  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => setF((cur) => ({ ...cur, [k]: v }));

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const out = await smaApi.pnlDashboard({
        source: f.source,
        runs: f.source === "replay" ? f.runs : undefined,
        desks: f.source === "market" ? f.desks : undefined,
        start: f.source === "market" ? f.start || undefined : undefined,
        end: f.source === "market" ? f.end || undefined : undefined,
        stocks: f.stocks,
        basis: f.basis,
        step: f.step,
        weekdays: f.weekdays,
        qty: f.qty,
      });
      setData(out);
      setPickedDay((p) => (p && out.days.some((d) => d.label === p) ? p : null));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not load the dashboard");
    } finally {
      setBusy(false);
    }
  }, [f]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const t = data?.totals;
  const best = data?.best_exit;
  const times = data?.by_time ?? [];
  const x = data?.grid ?? [];
  const avgCurve = times.map((r) => r.avg);
  const pickedIdx = data?.days.findIndex((d) => d.label === pickedDay) ?? -1;
  const picked = pickedIdx >= 0 ? data!.days[pickedIdx] : null;

  const csv = () => {
    if (!data) return;
    const head = ["Day", "High", "High at", "Low", "Low at", "Close", "Given back", ...data.grid];
    const lines = data.days.map((d) => [d.label, d.high, d.high_at, d.low, d.low_at, d.close, d.giveback, ...d.curve].join(","));
    const tt = ["Time", "Average", "Median", "Worst day", "Best day", "All days", "Days above 0 %", "Beats close %"];
    const tl = times.map((r) => [r.t, r.avg, r.median, r.min, r.max, r.sum, r.pos_pct, r.beat_close_pct].join(","));
    saveFile(`pnl-dashboard-${new Date().toISOString().slice(0, 10)}.csv`, [head.join(","), ...lines, "", tt.join(","), ...tl].join("\n"), "text/csv");
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
            <BarChart3 size={20} className="text-accentSky" aria-hidden /> P&amp;L dashboard: when to close everything
          </h1>
          <Explain
            className="mt-1 max-w-4xl"
            lead="The bots' P&L minute by minute: each day's high, low and close, and what closing every trade at each time of day would have made."
          >
            Built from the P&amp;L log (closed trades plus open P&amp;L, recorded every minute for market days and every backtest).
            The top chart answers the question directly: the line is the average P&amp;L across the picked days if every trade
            had been closed at that time, the shaded band runs from the worst to the best day, and the dashed line is where the
            days actually finished. The green mark is the time with the highest average. Set it as the all-bots square-off time
            in Settings to close everything then. Read-only: nothing here places or closes an order.
          </Explain>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void load()} disabled={busy} className={btn}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Refresh
          </button>
          <button type="button" onClick={csv} disabled={!data?.days.length} className={btn}>
            <Download size={14} /> CSV
          </button>
        </div>
      </div>

      <Filters f={f} set={set} toggle={toggle} opts={opts} allStocks={data?.all_stocks ?? []} />
      {err ? <p className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">{err}</p> : null}

      {data && !data.days.length && !busy ? (
        <div className="rounded-xl border border-border bg-surface p-6 text-sm text-slate-400 shadow-card">
          {f.source === "replay"
            ? "No P&L log for the picked backtest runs. Each backtest records one point a minute from now on; pick a run above or start one from the SMA Terminal's replay bar."
            : "No market days with a P&L log for these filters. Each desk records one point a minute while the market is open."}
        </div>
      ) : null}

      {t ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Tile label="Days" value={String(t.days)} sub={`${t.win_days} up · ${t.loss_days} down`} />
          <Tile label="P&L at the close" value={money(t.sum_close)} tone={t.sum_close} sub={`${money(t.avg_close)} a day`} />
          <Tile label="Average day high" value={money(t.avg_high)} tone={t.avg_high} sub={`best close ${money(t.best_close)}`} />
          <Tile label="Average day low" value={money(t.avg_low)} tone={t.avg_low} sub={`worst close ${money(t.worst_close)}`} />
          <Tile label="Given back from the high" value={money(-t.avg_giveback)} tone={-t.avg_giveback} sub="a day, high to close" />
          <Tile
            label="Best time to close all"
            value={best?.by_avg ?? "—"}
            accent
            sub={best ? `${money(best.avg_at_best)} a day · ${money(best.gain_vs_close)} vs the close` : undefined}
          />
        </div>
      ) : null}

      {data && data.days.length ? (
        <>
          <Card
            title="If you closed every trade at this time…"
            sub={`Average across ${data.days.length} day${data.days.length === 1 ? "" : "s"} (${data.basis === "net" ? "after" : "before"} charges${data.qty ? `, every trade as ${data.qty.toLocaleString("en-IN")} shares` : ""}${data.stocks_picked.length ? `, ${data.stocks_picked.join(", ")} only` : ""}). Hover for the details; click a time to see that moment.`}
            legend={
              <Legend
                items={[
                  { name: "Average", color: C.avg },
                  { name: "Median", color: C.median, dash: true },
                  { name: "Worst to best day", color: C.band, box: true },
                  { name: "Where the days closed (average)", color: C.close, dash: true },
                ]}
              />
            }
          >
            <TimeChart
              x={x}
              height={320}
              series={[
                { name: "Average", color: C.avg, values: avgCurve, width: 2.5 },
                { name: "Median", color: C.median, values: times.map((r) => r.median), dash: "5 4" },
              ]}
              band={{ lo: times.map((r) => r.min), hi: times.map((r) => r.max) }}
              refY={best ? { value: best.avg_close, label: `close ${money(best.avg_close)}`, color: C.close } : undefined}
              markX={best ? { at: best.by_avg, label: `best ${best.by_avg}` } : undefined}
              tip={(i) => {
                const r = times[i];
                return (
                  <div className="space-y-0.5">
                    <TipRow name="Average" v={r.avg} color={C.avg} />
                    <TipRow name="Median" v={r.median} color={C.median} />
                    <TipRow name="Best day" v={r.max} />
                    <TipRow name="Worst day" v={r.min} />
                    <TipRow name="All days added" v={r.sum} />
                    <TipRow name="vs holding to close" v={r.vs_close} />
                    <div className="text-slate-400">
                      Up on {r.pos_pct}% of days · beats the close on {r.beat_close_pct}%
                    </div>
                  </div>
                );
              }}
            />
            {best ? (
              <p className="mt-2 text-sm text-slate-300">
                Closing everything at <b className="text-profit">{best.by_avg}</b> would have made {money(best.avg_at_best)} a day on
                average ({money(best.sum_at_best)} over all the days) against {money(best.avg_close)} a day by holding to the close,
                and beaten the close on {best.beat_close_pct}% of days. By the median (one big day counts less) the best time is{" "}
                <b>{best.by_median}</b> ({money(best.median_at_best)}).
              </p>
            ) : null}
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Each day's P&L through the day" sub="Grey: every day. Click a day in the chart on the right or in the table to follow it.">
              <TimeChart
                x={x}
                series={[
                  ...data.days.map((d) => ({ name: d.label, color: C.faint, values: d.curve, faint: true })),
                  { name: "Average", color: C.avg, values: avgCurve, width: 2.5 },
                  ...(picked ? [{ name: picked.label, color: C.close, values: picked.curve, width: 2.5 }] : []),
                ]}
              />
              <Legend items={[{ name: "Average", color: C.avg }, ...(picked ? [{ name: picked.label, color: C.close }] : [])]} />
            </Card>
            <Card title="Day high, low and close" sub="Green dot = the day's high, red dot = its low, amber bar = where it closed. Click a day to follow it.">
              <DayRanges days={data.days} picked={pickedDay} onPick={(l) => setPickedDay((p) => (p === l ? null : l))} />
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card title="When the day's high came" sub="Days whose highest P&L fell in each half hour.">
              <Bars items={data.high_when.map((h) => ({ key: h.bucket, value: h.days }))} color={C.profit} tip={(i) => <span>{data.high_when[i].days} day(s)</span>} />
            </Card>
            <Card title="When the day's low came" sub="Days whose lowest P&L fell in each half hour.">
              <Bars items={data.low_when.map((h) => ({ key: h.bucket, value: h.days }))} color={C.loss} tip={(i) => <span>{data.low_when[i].days} day(s)</span>} />
            </Card>
            <Card title="Closing then beats holding to the close" sub="% of days the P&L at that time was above where the day ended.">
              <Bars
                items={times.map((r) => ({ key: r.t, value: r.beat_close_pct }))}
                color={(v) => (v >= 50 ? C.avg : C.faint)}
                maxY={100}
                fmt={(v) => `${Math.round(v)}%`}
                tip={(i) => (
                  <span>
                    {times[i].beat_close_pct}% of days · {money(times[i].vs_close)} a day vs the close
                  </span>
                )}
              />
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-5">
            <Card className="xl:col-span-2" title="P&L at the close by stock" sub="Before charges. Click a stock to show only its P&L everywhere on the page.">
              <DivergingBars
                items={data.stocks.slice(0, 25).map((s) => ({ key: s.symbol, value: s.sum_close, note: `${s.days} day(s), best exit ${s.best_exit ?? "—"}` }))}
                picked={f.stocks}
                onPick={(k) => set("stocks", toggle(f.stocks, k))}
              />
              {data.stocks.length > 25 ? <p className="mt-1 text-xs text-slate-500">Top 25 of {data.stocks.length}; all are in the table.</p> : null}
            </Card>
            <Card className="xl:col-span-3" title="Stocks: high, low, close and their best exit time">
              <StockTable data={data} picked={f.stocks} onPick={(k) => set("stocks", toggle(f.stocks, k))} />
            </Card>
          </div>

          <Card title="Days" sub="Click a row to follow that day in the charts.">
            <DayTable data={data} picked={pickedDay} onPick={(l) => setPickedDay((p) => (p === l ? null : l))} />
          </Card>
        </>
      ) : null}
    </div>
  );
}

const btn =
  "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-border hover:bg-white/5 disabled:opacity-50";
const input = "min-h-9 rounded-md border border-border bg-surface2 px-2 text-sm text-slate-100";

function Filters({
  f,
  set,
  toggle,
  opts,
  allStocks,
}: {
  f: Filters;
  set: <K extends keyof Filters>(k: K, v: Filters[K]) => void;
  toggle: <T>(list: T[], v: T) => T[];
  opts: PnlDashOptions | null;
  allStocks: string[];
}) {
  const [stockText, setStockText] = useState("");
  const [showRuns, setShowRuns] = useState(false);
  const customQty = (q: number) => (q && !QTY_PICKS.includes(q) ? String(q) : "");
  const [qtyText, setQtyText] = useState(customQty(f.qty));
  useEffect(() => setQtyText(customQty(f.qty)), [f.qty]);
  const runs = opts?.runs ?? [];
  const allIds = runs.map((r) => r.run_id);
  const allPicked = allIds.length > 0 && allIds.every((id) => f.runs.includes(id));
  const finishedIds = runs.filter((r) => (r.status ?? "").toUpperCase() !== "RUNNING").map((r) => r.run_id);
  const applyQty = (v: string) => {
    const n = Math.round(Number(v.replace(/[^0-9]/g, "")));
    set("qty", Number.isFinite(n) && n > 0 ? Math.min(n, 1_000_000) : 0);
  };
  const runLabel = (id: number) => {
    const r = runs.find((x) => x.run_id === id);
    return r ? `#${r.run_id} · Bot ${r.bot} · ${r.first === r.last ? r.first : `${r.first} → ${r.last}`} · ${r.days}d` : `#${id}`;
  };
  const addStock = (v: string) => {
    const s = v.trim().toUpperCase();
    if (s && !f.stocks.includes(s)) set("stocks", [...f.stocks, s]);
    setStockText("");
  };
  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-3 shadow-card">
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-300">
        <Field label="Read">
          <div className="inline-flex overflow-hidden rounded-md ring-1 ring-inset ring-border">
            {(["replay", "market"] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => set("source", s)}
                aria-pressed={f.source === s}
                className={clsx("min-h-9 px-3 font-semibold", f.source === s ? "bg-accentSky/15 text-accentSky" : "text-slate-300 hover:bg-white/5")}
              >
                {s === "replay" ? "Backtest runs" : "Market days"}
              </button>
            ))}
          </div>
        </Field>

        {f.source === "replay" ? (
          <Field label="Runs">
            <div className="relative">
              <button type="button" onClick={() => setShowRuns((v) => !v)} className={clsx(input, "w-full min-w-[220px] max-w-[320px] text-left")}>
                {f.runs.length ? (f.runs.length === 1 ? runLabel(f.runs[0]) : allPicked ? `All ${f.runs.length} runs` : `${f.runs.length} runs`) : "Latest run"}
              </button>
              {showRuns ? (
                <div className="absolute z-20 mt-1 max-h-80 w-[min(360px,calc(100vw-48px))] overflow-auto rounded-md border border-border bg-surface p-1 shadow-card">
                  {runs.length ? (
                    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-border bg-surface px-2 pb-1">
                      <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm font-semibold text-slate-100">
                        <input
                          type="checkbox"
                          checked={allPicked}
                          onChange={() => set("runs", allPicked ? [] : allIds)}
                          className="accent-[#10B981]"
                        />
                        Select all ({runs.length})
                      </label>
                      {finishedIds.length && finishedIds.length < runs.length ? (
                        <button type="button" className="min-h-8 text-xs text-accentSky" onClick={() => set("runs", finishedIds)}>
                          Finished only
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {runs.length ? (
                    runs.map((r) => (
                      <label key={r.run_id} className="flex min-h-9 cursor-pointer items-center gap-2 rounded px-2 text-sm text-slate-200 hover:bg-white/5">
                        <input type="checkbox" checked={f.runs.includes(r.run_id)} onChange={() => set("runs", toggle(f.runs, r.run_id))} className="accent-[#10B981]" />
                        <span className="truncate">{runLabel(r.run_id)}</span>
                        {r.status ? <span className="ml-auto text-xs text-slate-500">{r.status.toLowerCase()}</span> : null}
                      </label>
                    ))
                  ) : (
                    <p className="px-2 py-2 text-sm text-slate-500">No backtest has a P&amp;L log yet.</p>
                  )}
                  <div className="flex justify-between border-t border-border px-2 pt-1">
                    <button type="button" className="min-h-8 text-xs text-accentSky" onClick={() => set("runs", [])}>
                      Latest only
                    </button>
                    <button type="button" className="min-h-8 text-xs text-accentSky" onClick={() => setShowRuns(false)}>
                      Done
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </Field>
        ) : (
          <>
            <Field label="Bots">
              <div className="flex flex-wrap gap-1">
                {(opts?.desks ?? ["1", "2", "3", "4", "5", "research"]).map((d) => (
                  <Chip key={d} on={f.desks.includes(d)} onClick={() => set("desks", toggle(f.desks, d).length ? toggle(f.desks, d) : f.desks)}>
                    {DESK_LABEL[d] ?? d}
                  </Chip>
                ))}
              </div>
            </Field>
            <Field label="From">
              <input type="date" value={f.start} onChange={(e) => set("start", e.target.value)} className={input} />
            </Field>
            <Field label="To">
              <input type="date" value={f.end} onChange={(e) => set("end", e.target.value)} className={input} />
            </Field>
          </>
        )}

        <Field label="P&L">
          <select value={f.basis} onChange={(e) => set("basis", e.target.value as "gross" | "net")} className={input}>
            <option value="gross">Before charges</option>
            <option value="net">After charges</option>
          </select>
        </Field>
        <Field label="Qty per trade">
          <div className="flex flex-wrap items-center gap-1">
            <Chip on={!f.qty} onClick={() => set("qty", 0)}>
              As traded
            </Chip>
            {QTY_PICKS.map((q) => (
              <Chip key={q} on={f.qty === q} onClick={() => set("qty", q)}>
                {q.toLocaleString("en-IN")}
              </Chip>
            ))}
            <input
              inputMode="numeric"
              value={qtyText}
              placeholder="Other…"
              aria-label="Shares per trade"
              onChange={(e) => setQtyText(e.target.value.replace(/[^0-9]/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && applyQty(qtyText)}
              onBlur={() => qtyText && applyQty(qtyText)}
              className={clsx(input, "w-24")}
            />
          </div>
        </Field>
        <Field label="Every">
          <select value={f.step} onChange={(e) => set("step", Number(e.target.value))} className={input}>
            {STEPS.map((s) => (
              <option key={s} value={s}>
                {s} min
              </option>
            ))}
          </select>
        </Field>
        <Field label="Weekdays">
          <div className="flex gap-1">
            {WEEKDAYS.map((d, i) => (
              <Chip key={d} on={f.weekdays.includes(i)} onClick={() => set("weekdays", toggle(f.weekdays, i))}>
                {d}
              </Chip>
            ))}
          </div>
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
        <span className="text-slate-400">Stocks:</span>
        {f.stocks.length ? (
          f.stocks.map((s) => (
            <span key={s} className="inline-flex items-center gap-1 rounded-full bg-accentSky/15 px-2 py-1 font-semibold text-accentSky">
              {s}
              <button type="button" aria-label={`Remove ${s}`} onClick={() => set("stocks", f.stocks.filter((x) => x !== s))}>
                <X size={12} />
              </button>
            </span>
          ))
        ) : (
          <span className="text-slate-500">all</span>
        )}
        <input
          list="pnl-dash-stocks"
          value={stockText}
          placeholder="Add a stock…"
          onChange={(e) => setStockText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addStock(stockText)}
          onBlur={() => stockText && addStock(stockText)}
          className={clsx(input, "w-40")}
        />
        <datalist id="pnl-dash-stocks">
          {allStocks.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        {f.stocks.length ? (
          <button type="button" className="text-accentSky" onClick={() => set("stocks", [])}>
            Clear
          </button>
        ) : null}
        {f.stocks.length || f.qty ? (
          <span className="text-slate-500">
            ({f.qty ? `every trade restated as ${f.qty.toLocaleString("en-IN")} shares; ` : ""}stock and per-qty P&amp;L is always before charges)
          </span>
        ) : null}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 text-[11px] text-slate-400">{label}</div>
      {children}
    </div>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={clsx(
        "min-h-9 rounded-md px-2.5 font-semibold ring-1 ring-inset",
        on ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-400 ring-border hover:bg-white/5"
      )}
    >
      {children}
    </button>
  );
}

function Tile({ label, value, sub, tone, accent }: { label: string; value: string; sub?: string; tone?: number; accent?: boolean }) {
  return (
    <div className={clsx("rounded-xl border bg-surface p-3 shadow-card", accent ? "border-profit/50" : "border-border")}>
      <div className="text-xs text-slate-400">{label}</div>
      <div className={clsx("mt-1 font-mono text-xl font-semibold tabular-nums", accent ? "text-profit" : cls(tone))}>{value}</div>
      {sub ? <div className="mt-0.5 text-xs text-slate-500">{sub}</div> : null}
    </div>
  );
}

function Card({ title, sub, legend, children, className }: { title: string; sub?: string; legend?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("min-w-0 rounded-xl border border-border bg-surface p-3 shadow-card", className)}>
      <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
      {sub ? <p className="mt-0.5 text-xs text-slate-400">{sub}</p> : null}
      {legend ? <div className="mt-2">{legend}</div> : null}
      <div className="mt-2">{children}</div>
    </section>
  );
}

function TipRow({ name, v, color }: { name: string; v: number; color?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="inline-flex items-center gap-1.5">
        {color ? <span className="h-2 w-2 rounded-full" style={{ background: color }} /> : null}
        {name}
      </span>
      <span className={clsx("font-mono tabular-nums", cls(v))}>{money(v)}</span>
    </div>
  );
}

type DayKey = "label" | "high" | "high_at" | "low" | "low_at" | "close" | "giveback";

function DayTable({ data, picked, onPick }: { data: Dash; picked: string | null; onPick: (l: string) => void }) {
  const { sorted, sort, onSort } = useSort<Dash["days"][number], DayKey>(data.days, (r, k) => r[k]);
  const cols: [DayKey, string, boolean][] = [
    ["label", "Day", true],
    ["high", "High", false],
    ["high_at", "High at", true],
    ["low", "Low", false],
    ["low_at", "Low at", true],
    ["close", "Close", false],
    ["giveback", "Given back", false],
  ];
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-surface text-left text-xs text-slate-400">
          <tr>
            {cols.map(([k, label, text]) => (
              <SortTh key={k} k={k} label={label} sort={sort} onSort={onSort} text={text} num={!text} />
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((d) => (
            <tr
              key={d.key}
              onClick={() => onPick(d.label)}
              className={clsx("cursor-pointer border-t border-border hover:bg-white/5", picked === d.label && "bg-amber-500/10")}
            >
              <td className="py-1.5 font-semibold text-slate-200">{d.label}</td>
              <td className={clsx("py-1.5 text-right font-mono tabular-nums", cls(d.high))}>{money(d.high)}</td>
              <td className="py-1.5 tabular-nums text-slate-300">{d.high_at}</td>
              <td className={clsx("py-1.5 text-right font-mono tabular-nums", cls(d.low))}>{money(d.low)}</td>
              <td className="py-1.5 tabular-nums text-slate-300">{d.low_at}</td>
              <td className={clsx("py-1.5 text-right font-mono font-semibold tabular-nums", cls(d.close))}>{money(d.close)}</td>
              <td className="py-1.5 text-right font-mono tabular-nums text-slate-300">{money(d.giveback)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type StockKey = "symbol" | "days" | "sum_close" | "avg_high" | "avg_low" | "win_days" | "best_exit" | "gain_vs_close";

function StockTable({ data, picked, onPick }: { data: Dash; picked: string[]; onPick: (s: string) => void }) {
  const { sorted, sort, onSort } = useSort<Dash["stocks"][number], StockKey>(data.stocks, (r, k) => r[k]);
  const rows = useMemo(() => sorted, [sorted]);
  const cols: [StockKey, string, boolean][] = [
    ["symbol", "Stock", true],
    ["days", "Days", false],
    ["sum_close", "P&L at close", false],
    ["avg_high", "Avg high", false],
    ["avg_low", "Avg low", false],
    ["win_days", "Up days", false],
    ["best_exit", "Best exit", true],
    ["gain_vs_close", "Edge vs close", false],
  ];
  if (!rows.length) return <p className="text-sm text-slate-500">No stock P&amp;L in these days.</p>;
  return (
    <div className="max-h-[520px] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-surface text-left text-xs text-slate-400">
          <tr>
            {cols.map(([k, label, text]) => (
              <SortTh key={k} k={k} label={label} sort={sort} onSort={onSort} text={text} num={!text} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr
              key={s.symbol}
              onClick={() => onPick(s.symbol)}
              className={clsx("cursor-pointer border-t border-border hover:bg-white/5", picked.includes(s.symbol) && "bg-sky-500/10")}
            >
              <td className="py-1.5 font-semibold text-slate-200">{s.symbol}</td>
              <td className="py-1.5 text-right tabular-nums text-slate-300">{s.days}</td>
              <td className={clsx("py-1.5 text-right font-mono font-semibold tabular-nums", cls(s.sum_close))}>{money(s.sum_close)}</td>
              <td className={clsx("py-1.5 text-right font-mono tabular-nums", cls(s.avg_high))}>{money(s.avg_high)}</td>
              <td className={clsx("py-1.5 text-right font-mono tabular-nums", cls(s.avg_low))}>{money(s.avg_low)}</td>
              <td className="py-1.5 text-right tabular-nums text-slate-300">
                {s.win_days}/{s.days}
              </td>
              <td className="py-1.5 tabular-nums text-slate-300">{s.best_exit ?? "—"}</td>
              <td className={clsx("py-1.5 text-right font-mono tabular-nums", cls(s.gain_vs_close))}>{money(s.gain_vs_close)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
