"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Download, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { api, type ResultsCalendar, type ResultsRow } from "@/lib/api";
import { saveFile } from "@/lib/chartTable";
import { GrowwLink } from "@/components/ui/growwLink";

const card = "terminal-dark rounded-xl border border-white/10 p-4";
const field = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
const SCOPE_KEY = "news.results.scope";
const MAX_DAYS = 62;

type Scope = "fno" | "all" | "mine";
type Show = "ALL" | "UPCOMING" | "TODAY" | "FILED" | "IN_MARKET" | "AFTER_CLOSE";

function todayIst(): string {
  return new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 10);
}

function shift(day: string, by: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
}

function dayLabel(day: string): string {
  const d = new Date(`${day}T12:00:00Z`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });
}

function clock(at: string | undefined): string {
  if (!at) return "";
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? at
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

const STATUS: Record<ResultsRow["status"], { label: string; cls: string; tip: string }> = {
  UPCOMING: { label: "Due", cls: "text-sky-300 ring-sky-400/40", tip: "The board meets on this day to approve the results" },
  TODAY: { label: "Due today", cls: "text-amber-300 ring-amber-400/50", tip: "Due today; NSE has no results filing from it yet" },
  FILED: { label: "Filed", cls: "text-emerald-300 ring-emerald-400/40", tip: "The results were filed with NSE" },
  NOT_SEEN: { label: "No filing seen", cls: "text-slate-400 ring-white/15", tip: "The date passed but NSE's answer had no results filing from it" },
};

const SESSION: Record<NonNullable<ResultsRow["session"]>, { label: string; cls: string }> = {
  BEFORE_OPEN: { label: "before the open", cls: "text-sky-300" },
  IN_MARKET: { label: "in market hours", cls: "text-amber-300" },
  AFTER_CLOSE: { label: "after the close", cls: "text-violet-300" },
};

/**
 * Results calendar (the News page's tab; desk `results_calendar.py`): NSE's own event calendar of board meetings called
 * to approve financial results, joined with the exact time each company filed them, so a late write-up is never mistaken
 * for the news. Filed in market hours = the market reacted that day; after the close = the next session. Information
 * only: Arm only puts a stock on a bot's Trade list.
 */
export function ResultsCalendarPanel({ onOpen, onArm }: { onOpen: (s: string) => void; onArm: (s: string) => void }) {
  const today = todayIst();
  const [from, setFrom] = useState(shift(today, -7));
  const [to, setTo] = useState(shift(today, 21));
  const [scope, setScope] = useState<Scope>("fno");
  const [show, setShow] = useState<Show>("ALL");
  const [search, setSearch] = useState("");
  const [data, setData] = useState<ResultsCalendar | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(SCOPE_KEY);
      if (saved === "fno" || saved === "all" || saved === "mine") setScope(saved);
    } catch {
      /* no browser storage */
    }
  }, []);
  const pickScope = (s: Scope) => {
    setScope(s);
    try {
      localStorage.setItem(SCOPE_KEY, s);
    } catch {
      /* not kept */
    }
  };

  const span = Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
  const rangeError = to < from ? "The To date is before the From date." : span > MAX_DAYS ? `Pick at most ${MAX_DAYS} days at a time.` : null;

  const load = useCallback(
    async (force = false) => {
      if (rangeError) return;
      setBusy(true);
      setError(null);
      try {
        setData(await api.resultsCalendar({ from, to, scope, force }));
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Could not read NSE's results calendar");
      } finally {
        setBusy(false);
      }
    },
    [from, to, scope, rangeError]
  );
  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return (data?.rows ?? []).filter((r) => {
      if (q && !r.symbol.includes(q) && !(r.company ?? "").toUpperCase().includes(q)) return false;
      if (show === "ALL") return true;
      if (show === "IN_MARKET" || show === "AFTER_CLOSE") return r.status === "FILED" && (show === "IN_MARKET" ? r.session === "IN_MARKET" : r.session !== "IN_MARKET");
      return r.status === show;
    });
  }, [data, search, show]);

  const byDay = useMemo(() => {
    const out = new Map<string, ResultsRow[]>();
    for (const r of rows) out.set(r.meeting_date, [...(out.get(r.meeting_date) ?? []), r]);
    return Array.from(out.entries());
  }, [rows]);

  const counts = useMemo(() => {
    const all = data?.rows ?? [];
    return {
      due: all.filter((r) => r.status === "UPCOMING").length,
      today: all.filter((r) => r.status === "TODAY" || (r.status === "FILED" && r.meeting_date === today)).length,
      filed: all.filter((r) => r.status === "FILED").length,
      inMarket: all.filter((r) => r.session === "IN_MARKET").length,
    };
  }, [data, today]);

  const csv = () => {
    if (!data) return;
    const q = (s: string | null | undefined) => `"${(s ?? "").replace(/"/g, '""')}"`;
    const head = ["Date", "Stock", "Company", "Status", "Filed at", "When", "Market sees it on", "Purpose", "Filing"];
    const lines = rows.map((r) =>
      [r.meeting_date, r.symbol, q(r.company), r.status, r.filed_at ?? "", r.session ?? "", r.trades_on ?? "", q(r.purpose), q(r.filing_url)].join(",")
    );
    saveFile(`results-calendar-${data.from}-to-${data.to}.csv`, [head.join(","), ...lines].join("\n"), "text/csv;charset=utf-8");
  };

  return (
    <div className="space-y-4">
      <section className={card} aria-label="Results calendar">
        <h2 className="text-base font-semibold text-slate-100">Results calendar (from NSE)</h2>
        <p className="mt-1 text-xs text-slate-400">
          Every board meeting called to approve financial results, from NSE&apos;s own event calendar, and for the days that have
          passed the exact time each company filed them with NSE. That filing time is when the news really came out: a story
          on a website days later is old news. Filed <b className="text-amber-300">in market hours</b> = the price could react
          that day; <b className="text-violet-300">after the close</b> = the next session. Information only: nothing is traded.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            From
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={field} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            To
            <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className={field} />
          </label>
          <div className="flex flex-col gap-1 text-xs text-slate-400">
            Quick pick
            <div className="flex gap-1">
              {(
                [
                  ["Today", 0, 0],
                  ["Next 7 days", 0, 7],
                  ["Last 7 days", -7, 0],
                  ["Next 2 weeks", 0, 14],
                ] as const
              ).map(([label, a, b]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    setFrom(shift(today, a));
                    setTo(shift(today, b));
                  }}
                  className="min-h-9 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Stocks
            <select value={scope} onChange={(e) => pickScope(e.target.value as Scope)} className={field}>
              <option value="fno">F&amp;O stocks</option>
              <option value="mine">My stocks (bots + watch lists)</option>
              <option value="all">Every listed stock</option>
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !!rangeError}
            onClick={() => void load(true)}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-amber-500/15 px-3 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/25 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Refresh from NSE
          </button>
        </div>
        {rangeError ? <p className="mt-2 text-xs text-rose-300">{rangeError}</p> : null}
        {error ? <p className="mt-2 text-xs text-rose-300">{error}</p> : null}
        {data && Object.keys(data.errors).length ? (
          <p className="mt-2 text-xs text-amber-300">
            NSE {Object.entries(data.errors).map(([k, v]) => `${k === "calendar" ? "calendar" : "filings"}: ${v}`).join(" · ")}
          </p>
        ) : null}
        {data ? (
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-md bg-sky-500/10 px-2 py-1 text-sky-200">{counts.due} due</span>
            <span className="rounded-md bg-amber-500/10 px-2 py-1 text-amber-200">{counts.today} today</span>
            <span className="rounded-md bg-emerald-500/10 px-2 py-1 text-emerald-200">{counts.filed} filed</span>
            <span className="rounded-md bg-white/5 px-2 py-1 text-slate-300">{counts.inMarket} filed in market hours</span>
            <span className="ml-auto text-slate-500">Read from NSE {clock(data.fetched_at)}</span>
          </div>
        ) : null}
      </section>

      <section className={clsx(card, "overflow-x-auto")} aria-label="Results by day">
        <div className="mb-2 flex flex-wrap items-end gap-2">
          <select value={show} onChange={(e) => setShow(e.target.value as Show)} className={field} aria-label="Show">
            <option value="ALL">All</option>
            <option value="UPCOMING">Due (not yet)</option>
            <option value="TODAY">Due today, not filed yet</option>
            <option value="FILED">Filed</option>
            <option value="IN_MARKET">Filed in market hours</option>
            <option value="AFTER_CLOSE">Filed outside market hours</option>
          </select>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search stock or company" className={clsx(field, "w-56")} />
          <span className="text-xs text-slate-400">{rows.length} shown</span>
          <button type="button" onClick={csv} disabled={!rows.length} className="ml-auto inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50">
            <Download size={13} /> CSV
          </button>
        </div>
        {busy && !data ? (
          <p className="text-sm text-slate-400">
            <Loader2 size={14} className="mr-1 inline animate-spin" />
            Reading NSE&apos;s calendar and filings…
          </p>
        ) : !rows.length ? (
          <p className="text-sm text-slate-400">{data ? "No results meetings match." : ""}</p>
        ) : (
          <table className="w-full min-w-[900px] text-xs">
            <thead className="text-left text-slate-400">
              <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                <th>Stock</th>
                <th>Company</th>
                <th>Status</th>
                <th title="When the results were filed with NSE: the moment the news came out">Filed with NSE</th>
                <th title="The first session whose prices can carry the results">Market sees it</th>
                <th>Filing</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {byDay.map(([day, list]) => (
                <DayRows key={day} day={day} today={today} rows={list} onOpen={onOpen} onArm={onArm} />
              ))}
            </tbody>
          </table>
        )}
        {data ? <p className="mt-2 text-[11px] text-slate-500">{data.disclaimer}</p> : null}
      </section>
    </div>
  );
}

function DayRows({
  day,
  today,
  rows,
  onOpen,
  onArm,
}: {
  day: string;
  today: string;
  rows: ResultsRow[];
  onOpen: (s: string) => void;
  onArm: (s: string) => void;
}) {
  return (
    <>
      <tr className="border-t border-white/10 bg-white/[0.03]">
        <td colSpan={7} className={clsx("px-2 py-1.5 text-xs font-semibold", day === today ? "text-amber-300" : "text-slate-200")}>
          {dayLabel(day)}
          {day === today ? " · today" : ""}
          <span className="ml-2 font-normal text-slate-500">{rows.length} stock{rows.length === 1 ? "" : "s"}</span>
        </td>
      </tr>
      {rows.map((r) => {
        const st = STATUS[r.status];
        const ses = r.session ? SESSION[r.session] : null;
        return (
          <tr key={`${r.symbol}-${r.meeting_date}-${r.filed_at ?? ""}`} className="border-t border-white/5 align-top [&_td]:px-2 [&_td]:py-1.5">
            <td className="whitespace-nowrap font-semibold">
              <GrowwLink symbol={r.symbol} />
              {r.armed ? <span className="ml-1 rounded bg-emerald-500/15 px-1 text-[10px] font-normal text-emerald-300">armed</span> : null}
            </td>
            <td className="max-w-[260px] text-slate-300" title={r.desc ?? r.purpose}>
              {r.company ?? "—"}
              {r.from_filing ? <div className="text-[10px] text-slate-500">results filed; no meeting in this range</div> : null}
            </td>
            <td className="whitespace-nowrap">
              <span title={st.tip} className={clsx("rounded px-1.5 py-0.5 ring-1 ring-inset", st.cls)}>
                {st.label}
              </span>
            </td>
            <td className="whitespace-nowrap font-mono text-slate-200">
              {r.filed_at ? clock(r.filed_at) : "—"}
              {ses ? <div className={clsx("text-[10px] font-sans", ses.cls)}>{ses.label}</div> : null}
            </td>
            <td className="whitespace-nowrap text-slate-300">{r.trades_on ? dayLabel(r.trades_on) : "—"}</td>
            <td className="max-w-[220px]">
              {r.filing_url ? (
                <a href={r.filing_url} target="_blank" rel="noreferrer" className="text-slate-300 hover:underline">
                  {r.filing_title || "Filing"} <ExternalLink size={10} className="inline opacity-50" />
                </a>
              ) : (
                <span className="text-slate-500">—</span>
              )}
              {r.filings && r.filings > 1 ? <div className="text-[10px] text-slate-500">{r.filings} filings that day</div> : null}
            </td>
            <td className="whitespace-nowrap text-right">
              <button type="button" onClick={() => onOpen(r.symbol)} className="min-h-8 rounded-md px-2 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10">
                News
              </button>
              <button type="button" onClick={() => onArm(r.symbol)} className="ml-1 min-h-8 rounded-md px-2 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/40 hover:bg-emerald-500/10">
                Arm
              </button>
            </td>
          </tr>
        );
      })}
    </>
  );
}
