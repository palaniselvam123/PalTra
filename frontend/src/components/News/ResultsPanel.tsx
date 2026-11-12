"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ExternalLink, Loader2, RefreshCw, Search } from "lucide-react";
import { api, type ResultCalendar, type ResultFiled, type ResultQuarter, type ResultStock } from "@/lib/api";
import { toneClass, toneWord } from "@/lib/newsTone";
import { GrowwLink } from "@/components/ui/growwLink";

const card = "terminal-dark rounded-xl border border-white/10 p-4";
const field = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
const btn = "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset disabled:opacity-50";

type Scope = "all" | "fno" | "other";
type Row = { symbol: string; company: string; fno: boolean; filed_at: string | null; date?: string };

function dayChip(iso: string, today: string): { top: string; mid: string } {
  const d = new Date(`${iso}T00:00:00+05:30`);
  const top = iso === today ? "Today" : d.toLocaleDateString("en-IN", { weekday: "short", timeZone: "Asia/Kolkata" });
  return { top, mid: d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" }) };
}

function clock(at: string | null): string {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

function crore(v: number | null | undefined): string {
  return v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function pct(v: number | null | undefined, unit = "%"): string {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}${unit}`;
}

function pctClass(v: number | null | undefined): string {
  return v == null ? "text-slate-500" : v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-300";
}

function istToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/** The verdict chip: Positive / Negative / Mixed and the impact. */
export function ResultChip({ r }: { r: ResultStock | undefined }) {
  if (!r) return <span className="text-slate-600">—</span>;
  const a = r.analysis;
  if (r.error && !r.quarters.length) return <span className="text-xs text-amber-300" title={r.error}>not read</span>;
  if (a.sentiment === "NO_DATA") return <span className="text-xs text-slate-500" title={a.why}>no data</span>;
  return (
    <span className={clsx("whitespace-nowrap rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(a.sentiment))} title={`Score ${a.score}`}>
      {toneWord(a.sentiment)}
      {a.impact && a.sentiment !== "MIXED" && a.sentiment !== "NEUTRAL" ? ` · ${a.impact.toLowerCase()}` : ""}
    </span>
  );
}

/**
 * Quarterly results (News page tab): the results calendar per day (NSE's board meetings), NSE's latest results filings,
 * and for any company its last quarters as filed and whether the newest one is good or bad (YoY / QoQ growth of sales and
 * profit, the margin change, a turnaround), scored by backend results.analyze. Information only: nothing here trades;
 * Arm only puts a stock on a bot's Trade list.
 */
export function ResultsPanel({ onArm, onNews }: { onArm: (s: string) => void; onNews: (s: string) => void }) {
  const today = istToday();
  const [cal, setCal] = useState<ResultCalendar | null>(null);
  const [filed, setFiled] = useState<ResultFiled[] | null>(null);
  const [filedError, setFiledError] = useState<string | null>(null);
  const [day, setDay] = useState<string>("filed");
  const [scope, setScope] = useState<Scope>("fno");
  const [query, setQuery] = useState("");
  const [read, setRead] = useState<Record<string, ResultStock>>({});
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<Record<string, unknown> | null>(null);
  const [sortBy, setSortBy] = useState<"list" | "score">("list");

  const loadLists = async () => {
    setError(null);
    const [c, f] = await Promise.allSettled([api.resultsCalendar(7), api.resultsFiled(300)]);
    if (c.status === "fulfilled") setCal(c.value);
    else setError(String(c.reason));
    if (f.status === "fulfilled") {
      setFiled(f.value.rows);
      setFiledError(f.value.error);
    } else {
      setFiled([]);
      setFiledError(String(f.reason));
    }
  };
  useEffect(() => {
    void loadLists();
  }, []);

  const filedBy = useMemo(() => new Map((filed ?? []).map((f) => [f.symbol, f])), [filed]);

  const rows: Row[] = useMemo(() => {
    let base: Row[];
    if (day === "filed") {
      base = (filed ?? []).map((f) => ({ symbol: f.symbol, company: f.company, fno: f.fno, filed_at: f.filed_at }));
    } else {
      const d = cal?.days.find((x) => x.date === day);
      base = (d?.rows ?? []).map((r) => ({ symbol: r.symbol, company: r.company, fno: r.fno, filed_at: filedBy.get(r.symbol)?.filed_at ?? null, date: r.date }));
    }
    const seen = new Set<string>();
    const q = query.trim().toUpperCase();
    const out = base.filter((r) => {
      if (seen.has(r.symbol)) return false;
      seen.add(r.symbol);
      if (scope === "fno" && !r.fno) return false;
      if (scope === "other" && r.fno) return false;
      return !q || r.symbol.includes(q) || r.company.toUpperCase().includes(q);
    });
    if (sortBy === "score") {
      const s = (r: Row) => read[r.symbol]?.analysis.score;
      out.sort((a, b) => (s(b) ?? -9) - (s(a) ?? -9));
    }
    return out;
  }, [day, filed, cal, filedBy, scope, query, sortBy, read]);

  const counts = (list: { fno: boolean }[]) => ({ all: list.length, fno: list.filter((r) => r.fno).length });
  const scopeCounts = counts(
    day === "filed" ? (filed ?? []) : (cal?.days.find((x) => x.date === day)?.rows ?? [])
  );

  const readOne = async (symbol: string, force = false) => {
    setBusy(symbol);
    try {
      const r = await api.resultsStock(symbol, force);
      setRead((m) => ({ ...m, [symbol]: r }));
      setOpenRow(symbol);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const readShown = async () => {
    const want = rows.filter((r) => day !== "filed" || r.filed_at).map((r) => r.symbol).filter((s) => !read[s]).slice(0, 40);
    if (!want.length) return;
    setBusy("__all");
    try {
      const got = await api.resultsAnalyze(want);
      setRead((m) => ({ ...m, ...Object.fromEntries(got.rows.map((r) => [r.symbol, r])) }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const tally = useMemo(() => {
    const got = rows.map((r) => read[r.symbol]).filter((r): r is ResultStock => !!r && r.analysis.sentiment !== "NO_DATA");
    const n = (s: string) => got.filter((r) => r.analysis.sentiment === s).length;
    return { read: got.length, pos: n("POSITIVE"), neg: n("NEGATIVE"), mixed: n("MIXED") + n("NEUTRAL") };
  }, [rows, read]);

  return (
    <section className={clsx(card, "space-y-3")}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-slate-100">Quarterly results</h2>
          <p className="text-xs text-slate-400">
            Companies file results with NSE first; this reads the numbers they filed and says whether the quarter is good or bad: sales,
            profit before tax and net profit against the same quarter last year and the quarter before, the net margin, and a loss turned
            into a profit (or back). Information only.
          </p>
        </div>
        <button type="button" onClick={() => void loadLists()} className={clsx(btn, "text-slate-200 ring-white/15 hover:bg-white/5")}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Results day">
        <button
          type="button"
          role="tab"
          aria-selected={day === "filed"}
          onClick={() => setDay("filed")}
          className={clsx("min-w-[96px] rounded-lg border px-3 py-2 text-center", day === "filed" ? "border-emerald-400/60 bg-emerald-500/10" : "border-white/10 hover:bg-white/5")}
        >
          <div className="text-[10px] uppercase tracking-wide text-slate-400">Just filed</div>
          <div className="text-sm font-semibold text-slate-100">on NSE</div>
          <div className="text-[11px] text-emerald-300">{filed ? `${filed.length} results` : "…"}</div>
        </button>
        {(cal?.days ?? []).map((d) => {
          const c = dayChip(d.date, today);
          return (
            <button
              key={d.date}
              type="button"
              role="tab"
              aria-selected={day === d.date}
              disabled={!d.count}
              onClick={() => setDay(d.date)}
              className={clsx(
                "min-w-[86px] rounded-lg border px-3 py-2 text-center disabled:opacity-40",
                day === d.date ? "border-emerald-400/60 bg-emerald-500/10" : "border-white/10 hover:bg-white/5"
              )}
            >
              <div className="text-[10px] uppercase tracking-wide text-slate-400">{c.top}</div>
              <div className="text-sm font-semibold text-slate-100">{c.mid}</div>
              <div className="text-[11px] text-emerald-300">{d.count ? `${d.count} · ${d.fno} F&O` : "—"}</div>
            </button>
          );
        })}
        {cal === null && !error ? <Loader2 size={16} className="mt-3 animate-spin text-slate-500" /> : null}
      </div>
      {cal?.error ? <p className="text-xs text-amber-300">Calendar: {cal.error}</p> : null}
      {filedError ? <p className="text-xs text-amber-300">Filings: {filedError}</p> : null}
      {error ? <p className="text-xs text-amber-300">{error}</p> : null}

      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        {(["fno", "all", "other"] as Scope[]).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setScope(s)}
            aria-pressed={scope === s}
            className={clsx("min-h-8 rounded-full px-3 ring-1 ring-inset", scope === s ? "bg-emerald-500/15 text-slate-100 ring-emerald-400/60" : "ring-white/15 hover:bg-white/5")}
          >
            {s === "fno" ? `F&O stocks ${scopeCounts.fno}` : s === "all" ? `All stocks ${scopeCounts.all}` : `Non-F&O ${scopeCounts.all - scopeCounts.fno}`}
          </button>
        ))}
        <label className="relative">
          <Search size={13} className="pointer-events-none absolute left-2 top-2.5 text-slate-500" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company or symbol" className={clsx(field, "pl-7")} aria-label="Search company or symbol" />
        </label>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value === "score" ? "score" : "list")} className={field} aria-label="Sort">
          <option value="list">{day === "filed" ? "Newest filed first" : "F&O first, A-Z"}</option>
          <option value="score">Best results first</option>
        </select>
        <button
          type="button"
          onClick={() => void readShown()}
          disabled={busy !== null || !rows.length}
          className={clsx(btn, "ml-auto bg-emerald-500/15 text-emerald-100 ring-emerald-400/50 hover:bg-emerald-500/25")}
        >
          {busy === "__all" ? <Loader2 size={13} className="animate-spin" /> : null}
          Read the results shown (up to 40{day === "filed" ? "" : ", filed ones"})
        </button>
      </div>
      {tally.read ? (
        <p className="text-xs text-slate-300" aria-live="polite">
          Read {tally.read}: <b className="text-emerald-300">{tally.pos} positive</b> · <b className="text-rose-300">{tally.neg} negative</b> ·{" "}
          <b className="text-amber-300">{tally.mixed} mixed</b>
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-1.5 pr-2 font-medium">Stock</th>
              <th className="py-1.5 pr-2 font-medium">Company</th>
              <th className="py-1.5 pr-2 font-medium">Filed</th>
              <th className="py-1.5 pr-2 font-medium">Result</th>
              <th className="py-1.5 pr-2 font-medium">Why</th>
              <th className="py-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const got = read[r.symbol];
              return (
                <Fragment key={r.symbol}>
                  <tr className="border-t border-white/5 align-top">
                    <td className="whitespace-nowrap py-1.5 pr-2 font-semibold text-slate-100">
                      <GrowwLink symbol={r.symbol} />
                      {r.fno ? <span className="ml-1 rounded bg-emerald-500/15 px-1 text-[9px] font-semibold text-emerald-300">F&amp;O</span> : null}
                    </td>
                    <td className="max-w-[240px] truncate py-1.5 pr-2 text-xs text-slate-300">{r.company}</td>
                    <td className="whitespace-nowrap py-1.5 pr-2 text-xs text-slate-400">{r.filed_at ? clock(r.filed_at) : day === "filed" ? "—" : "not yet"}</td>
                    <td className="py-1.5 pr-2"><ResultChip r={got} /></td>
                    <td className="max-w-[360px] py-1.5 pr-2 text-xs text-slate-300">{got ? (got.error && !got.quarters.length ? got.error : got.analysis.why) : ""}</td>
                    <td className="whitespace-nowrap py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => (got && openRow !== r.symbol ? setOpenRow(r.symbol) : got ? setOpenRow(null) : void readOne(r.symbol))}
                        disabled={busy === r.symbol}
                        className="min-h-8 rounded-md px-2 text-xs font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
                      >
                        {busy === r.symbol ? <Loader2 size={12} className="inline animate-spin" /> : got ? (openRow === r.symbol ? "Hide" : "Numbers") : "Read"}
                      </button>
                      <button type="button" onClick={() => onNews(r.symbol)} className="ml-1 min-h-8 rounded-md px-2 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10">News</button>
                      <button type="button" onClick={() => onArm(r.symbol)} className="ml-1 min-h-8 rounded-md px-2 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">Arm</button>
                    </td>
                  </tr>
                  {got && openRow === r.symbol ? (
                    <tr>
                      <td colSpan={6} className="pb-3">
                        <ResultDetail r={got} onReload={() => void readOne(r.symbol, true)} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {!rows.length && (filed !== null || cal !== null) ? (
          <p className="py-3 text-sm text-slate-400">No companies here{scope === "fno" ? " among the F&O stocks" : ""}.</p>
        ) : null}
      </div>

      <details className="text-xs text-slate-400">
        <summary className="cursor-pointer">Check what NSE answers (for spotting a renamed field)</summary>
        <button
          type="button"
          onClick={() => {
            setProbe(null);
            void api.resultsProbe(openRow ?? "ALKEM").then(setProbe).catch((e) => setProbe({ error: String(e) }));
          }}
          className={clsx(btn, "mt-2 text-slate-200 ring-white/15 hover:bg-white/5")}
        >
          Check NSE ({openRow ?? "ALKEM"})
        </button>
        {probe ? <pre className="mt-2 max-h-96 overflow-auto rounded bg-black/40 p-2 text-[10px] text-slate-300">{JSON.stringify(probe, null, 2)}</pre> : null}
      </details>
    </section>
  );
}

const LINES: { key: keyof ResultQuarter; label: string }[] = [
  { key: "revenue", label: "Revenue from operations" },
  { key: "total_income", label: "Total income" },
  { key: "pbt", label: "Profit before tax" },
  { key: "net_profit", label: "Net profit" },
  { key: "eps", label: "Basic EPS (₹)" },
];

function margin(q: ResultQuarter): number | null {
  return q.net_profit != null && q.revenue ? (q.net_profit / q.revenue) * 100 : null;
}

/** One company's quarters as filed (newest first) with the YoY / QoQ growth and the verdict. */
export function ResultDetail({ r, onReload }: { r: ResultStock; onReload?: () => void }) {
  const a = r.analysis;
  const g = a.growth;
  const qs = r.quarters.slice(0, 6);
  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <ResultChip r={r} />
        <span className="font-semibold text-slate-100">{a.quarter ?? ""}</span>
        <span className="text-slate-300">{a.why}</span>
        {onReload ? (
          <button type="button" onClick={onReload} className="ml-auto rounded px-2 py-1 text-slate-400 ring-1 ring-inset ring-white/15 hover:bg-white/5">
            Read again
          </button>
        ) : null}
      </div>
      {g ? (
        <div className="flex flex-wrap gap-3 text-slate-400">
          <span>Sales YoY <b className={pctClass(g.revenue_yoy)}>{pct(g.revenue_yoy)}</b></span>
          <span>Net profit YoY <b className={pctClass(g.profit_yoy)}>{pct(g.profit_yoy)}</b></span>
          <span>PBT YoY <b className={pctClass(g.pbt_yoy)}>{pct(g.pbt_yoy)}</b></span>
          <span>EPS YoY <b className={pctClass(g.eps_yoy)}>{pct(g.eps_yoy)}</b></span>
          <span>Sales QoQ <b className={pctClass(g.revenue_qoq)}>{pct(g.revenue_qoq)}</b></span>
          <span>Net profit QoQ <b className={pctClass(g.profit_qoq)}>{pct(g.profit_qoq)}</b></span>
          <span>Net margin <b className="text-slate-200">{g.margin == null ? "—" : `${g.margin.toFixed(1)}%`}</b> (<b className={pctClass(g.margin_yoy_pts)}>{pct(g.margin_yoy_pts, " pts")}</b> YoY)</span>
        </div>
      ) : null}
      {qs.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-right tabular-nums">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-2 text-left font-medium">₹ crore</th>
                {qs.map((q) => (
                  <th key={q.end} className="py-1 pr-2 font-medium">{q.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {LINES.map((l) => (
                <tr key={l.key} className="border-t border-white/5">
                  <td className="py-1 pr-2 text-left text-slate-300">{l.label}</td>
                  {qs.map((q) => (
                    <td key={q.end} className="py-1 pr-2 text-slate-200">{crore(q[l.key] as number | null)}</td>
                  ))}
                </tr>
              ))}
              <tr className="border-t border-white/5">
                <td className="py-1 pr-2 text-left text-slate-300">Net margin</td>
                {qs.map((q) => {
                  const m = margin(q);
                  return <td key={q.end} className="py-1 pr-2 text-slate-200">{m == null ? "—" : `${m.toFixed(1)}%`}</td>;
                })}
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="text-slate-500">
        As filed with NSE ({qs[0]?.consolidated === false ? "standalone" : qs[0]?.consolidated ? "consolidated" : "as NSE gives it"}
        {qs[0]?.audited === false ? ", unaudited" : qs[0]?.audited ? ", audited" : ""}).{" "}
        <a href={`https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(r.symbol)}`} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">
          NSE page <ExternalLink size={10} className="inline" />
        </a>
        {r.error ? <span className="ml-2 text-amber-300">{r.error}</span> : null}
      </p>
    </div>
  );
}
