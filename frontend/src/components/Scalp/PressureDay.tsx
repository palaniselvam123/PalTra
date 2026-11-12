"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowDown, ArrowUp, ArrowUpDown, CalendarClock, Loader2, Newspaper, Search } from "lucide-react";
import { api, type NewsDayRow, type PressureDay, type PressureDayRow } from "@/lib/api";
import { smaApi, type HoldTestResult, type HoldTestRow } from "@/lib/smaApi";
import { GrowwLink } from "@/components/ui/growwLink";

/**
 * Day list of the buyers / sellers idea (backend/pressure_study.reached): every stock that reached the typed share
 * of buyers or sellers at any saved Most active scan of a day, when it first did, and whether the real price then
 * moved the typed % its way. Read-only: saved scans only, no Groww call, no order.
 */
const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-emerald-300" : (v ?? 0) < 0 ? "text-rose-300" : "text-slate-300");

type SortKey = "day" | "symbol" | "side" | "first_at" | "max_share" | "price" | "moved" | "best" | "worst" | "last" | "news" | "hold";

const newsKey = (r: { day: string; symbol: string }) => `${r.day}|${r.symbol}`;
const holdKey = (r: { day: string; symbol: string; side: string }) => `${r.day}|${r.symbol}|${r.side}`;

/** The news score signed the share's way: + = the news points where the buyers / sellers point. */
function newsWith(r: PressureDayRow, n: NewsDayRow | undefined): number | null {
  const score = n?.overall?.score;
  if (score == null || !n?.count) return null;
  return r.side === "BUY" ? score : -score;
}

/** "fits" / "against" / "no news" / "neutral" for a row, from the day's overall news view. */
function newsFit(r: PressureDayRow, n: NewsDayRow | undefined): "fits" | "against" | "neutral" | "none" | null {
  if (!n) return null;
  if (n.error || !n.count) return "none";
  const s = n.overall?.sentiment;
  if (s === "POSITIVE") return r.side === "BUY" ? "fits" : "against";
  if (s === "NEGATIVE") return r.side === "SELL" ? "fits" : "against";
  return "neutral";
}

/** "HH:MM" IST and the IST date of an ISO time. */
function istParts(iso: string | null | undefined): { day: string; time: string } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const ist = new Date(d.getTime() + 330 * 60000);
  return { day: ist.toISOString().slice(0, 10), time: ist.toISOString().slice(11, 16) };
}

/** The value a column sorts on; null always goes last. "Moved?" sorts Yes (soonest first), then No, then no later scan. */
function sortValue(r: PressureDayRow, key: SortKey): number | string | null {
  switch (key) {
    case "day":
      return r.day;
    case "symbol":
      return r.symbol;
    case "side":
      return `${r.side}-${(100 - r.share_pct).toFixed(2).padStart(6, "0")}`;
    case "first_at":
      return `${r.day} ${r.first_at}`;
    case "max_share":
      return r.max_share_pct;
    case "price":
      return r.price;
    case "moved":
      return r.moved == null ? null : r.moved ? r.minutes_to_move ?? 0 : 100000;
    case "best":
      return r.best_pct;
    case "worst":
      return r.worst_pct;
    case "last":
      return r.last_pct;
    default:
      return null;
  }
}

/** "HH:MM" → minutes after midnight, or null when blank / not a time. */
function toMinutes(t: string): number | null {
  const m = /^(\d{1,2}):(\d{2})/.exec(t.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

export function PressureDayView() {
  const [share, setShare] = useState(75);
  const [move, setMove] = useState(0.5);
  const [day, setDay] = useState("");
  const [side, setSide] = useState<"ALL" | "BUY" | "SELL">("ALL");
  const [show, setShow] = useState<"ALL" | "YES" | "NO">("ALL");
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 } | null>(null);
  const [news, setNews] = useState<Record<string, NewsDayRow>>({});
  const [newsBusy, setNewsBusy] = useState(false);
  const [newsErr, setNewsErr] = useState<string | null>(null);
  const [newsAi, setNewsAi] = useState(false);
  const [holdDays, setHoldDays] = useState(5);
  const [holdPct, setHoldPct] = useState(1);
  const [hold, setHold] = useState<HoldTestResult | null>(null);
  const [holdBusy, setHoldBusy] = useState(false);
  const [holdErr, setHoldErr] = useState<string | null>(null);
  const [data, setData] = useState<PressureDay | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (d: string) => {
      setBusy(true);
      setError(null);
      try {
        const got = await api.scalpPressureDay({ days: 20, pct: share, move_pct: move, day: d === "ALL" ? undefined : d || undefined });
        setData(got);
        // First open: show the newest day that has saved scans.
        if (!d && got.all_days.length) {
          setDay(got.all_days[0]);
          setData(await api.scalpPressureDay({ days: 20, pct: share, move_pct: move, day: got.all_days[0] }));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not read the saved scans");
      } finally {
        setBusy(false);
      }
    },
    [share, move]
  );

  useEffect(() => {
    void load("");
    // Run once on open; afterwards only when Scan the day is pressed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = useMemo(() => {
    const from = toMinutes(timeFrom);
    const to = toMinutes(timeTo);
    const lo = priceMin.trim() ? Number(priceMin) : null;
    const hi = priceMax.trim() ? Number(priceMax) : null;
    const kept = (data?.rows ?? []).filter((r) => {
      if (side !== "ALL" && r.side !== side) return false;
      if (show !== "ALL" && (show === "YES" ? r.moved !== true : r.moved !== false)) return false;
      const at = toMinutes(r.first_at);
      if (from != null && at != null && at < from) return false;
      if (to != null && at != null && at > to) return false;
      if (lo != null && Number.isFinite(lo) && lo > 0 && r.price < lo) return false;
      if (hi != null && Number.isFinite(hi) && hi > 0 && r.price > hi) return false;
      return true;
    });
    if (!sort) return kept;
    const holdBy = new Map((hold?.rows ?? []).map((h) => [holdKey(h), h]));
    const value = (r: PressureDayRow) => {
      if (sort.key === "news") return newsWith(r, news[newsKey(r)]);
      if (sort.key === "hold") {
        const h = holdBy.get(holdKey(r));
        return !h || h.error ? null : h.reached ? h.day_no : 1000 - (h.best_pct ?? 0);
      }
      return sortValue(r, sort.key);
    };
    return [...kept].sort((a, b) => {
      const va = value(a);
      const vb = value(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return c * sort.dir;
    });
  }, [data, side, show, timeFrom, timeTo, priceMin, priceMax, sort, news, hold]);

  const holdMap = useMemo(() => new Map((hold?.rows ?? []).map((h) => [holdKey(h), h])), [hold]);

  /** Read each shown stock's news on its day (desk news reader; rules, or AI when ticked). Up to 40 stocks a day. */
  const readNews = async () => {
    setNewsBusy(true);
    setNewsErr(null);
    try {
      const byDay = new Map<string, string[]>();
      for (const r of rows) {
        const list = byDay.get(r.day) ?? [];
        if (!list.includes(r.symbol) && list.length < 40) list.push(r.symbol);
        byDay.set(r.day, list);
      }
      const got: Record<string, NewsDayRow> = {};
      for (const [d, syms] of byDay) {
        const rep = await api.newsDay(d, syms, newsAi);
        for (const n of rep.stocks) got[`${d}|${n.symbol}`] = n;
      }
      setNews((cur) => ({ ...cur, ...got }));
    } catch (e) {
      setNewsErr(e instanceof Error ? e.message : "Could not read the news");
    } finally {
      setNewsBusy(false);
    }
  };

  /** Follow each shown stock from the price then for the same day plus N trading days (hold_test.py). Up to 60. */
  const runHold = async () => {
    setHoldBusy(true);
    setHoldErr(null);
    try {
      const picked = rows.slice(0, 60).map((r) => ({ symbol: r.symbol, day: r.day, at: r.first_at, side: r.side, price: r.price }));
      setHold(await smaApi.holdTest({ rows: picked, days: holdDays, target_pct: holdPct }));
    } catch (e) {
      setHoldErr(e instanceof Error ? e.message : "Could not run the hold test");
    } finally {
      setHoldBusy(false);
    }
  };

  // How the shown rows did, split by whether that day's news pointed the same way as the buyers / sellers.
  const newsSplit = useMemo(() => {
    const groups: Record<"fits" | "against" | "neutral" | "none", { n: number; moved: number }> = {
      fits: { n: 0, moved: 0 },
      against: { n: 0, moved: 0 },
      neutral: { n: 0, moved: 0 },
      none: { n: 0, moved: 0 },
    };
    let read = 0;
    for (const r of rows) {
      const fit = newsFit(r, news[newsKey(r)]);
      if (!fit || r.moved == null) continue;
      read += 1;
      groups[fit].n += 1;
      if (r.moved) groups[fit].moved += 1;
    }
    return read ? groups : null;
  }, [rows, news]);

  // How the rows on screen did (the cards above count the whole day).
  const shownMoved = rows.filter((r) => r.moved === true).length;
  const shownJudged = rows.filter((r) => r.moved != null).length;
  const filtered = Boolean(timeFrom || timeTo || priceMin || priceMax || side !== "ALL" || show !== "ALL");

  /** Click a header: first click sorts the natural way (A-Z, earliest, biggest move), the second flips it, the third clears. */
  const toggleSort = (key: SortKey, first: 1 | -1) =>
    setSort((cur) => (cur?.key !== key ? { key, dir: first } : cur.dir === first ? { key, dir: (first * -1) as 1 | -1 } : null));
  const SortTh = ({ k, first = 1, left = false, title, children }: { k: SortKey; first?: 1 | -1; left?: boolean; title?: string; children: React.ReactNode }) => {
    const on = sort?.key === k;
    const Icon = !on ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
    return (
      <th className={clsx("px-2 py-1.5 font-medium", left ? "text-left" : "text-right")} title={title}>
        <button
          type="button"
          onClick={() => toggleSort(k, first)}
          className={clsx("inline-flex items-center gap-1 hover:text-slate-100", on && "text-emerald-300", !left && "flex-row-reverse")}
        >
          {children}
          <Icon size={11} className={on ? "" : "opacity-40"} />
        </button>
      </th>
    );
  };

  const select = "h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-200";
  const td = "px-2 py-1.5 text-right tabular-nums";

  return (
    <div className="space-y-3 rounded-lg border border-emerald-400/25 bg-emerald-400/[0.03] p-3">
      <div className="text-xs leading-relaxed text-slate-300">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm font-semibold text-slate-100">
          <span>Which stocks reached it that day, and did the price then move?</span>
          <a href="/learn/?lesson=buyers-sellers" className="text-xs font-normal text-emerald-300 underline hover:text-emerald-200">
            How to read this (guide)
          </a>
        </p>
        <p className="mt-1">
          Type your <b>buyers / sellers %</b> and the <b>price move %</b> you expect. Every saved scan of the day is checked: each stock that
          reached your % is listed with the time it first did, and whether the real price then moved your % its way (up after buyers, down
          after sellers) before the day&apos;s last scan. The top line says how often your assumption was correct.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-xs text-slate-400">
        <label className="flex flex-col gap-1">
          Buyers / sellers at least
          <span className="flex items-center gap-1">
            <input type="number" min={50} max={99} step={1} value={share} onChange={(e) => setShare(Math.min(99, Math.max(50, Number(e.target.value) || 75)))} className={clsx(select, "w-20")} />%
          </span>
        </label>
        <label className="flex flex-col gap-1">
          Price must move at least
          <span className="flex items-center gap-1">
            <input type="number" min={0.05} max={20} step={0.05} value={move} onChange={(e) => setMove(Math.min(20, Math.max(0.05, Number(e.target.value) || 0.5)))} className={clsx(select, "w-20")} />%
          </span>
        </label>
        <label className="flex flex-col gap-1">
          Day
          <select className={select} value={day} onChange={(e) => setDay(e.target.value)}>
            {(data?.all_days ?? []).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
            <option value="ALL">All saved days (last 20)</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void load(day || "ALL")}
          disabled={busy}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-emerald-500/20 px-3 text-xs font-semibold text-emerald-100 ring-1 ring-inset ring-emerald-400/50 hover:bg-emerald-500/30 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />} Scan the day
        </button>
        {data ? (
          <span className="text-slate-500">
            {data.days.length} day{data.days.length === 1 ? "" : "s"} · {data.days.reduce((a, d) => a + d.scans, 0)} saved scans · {data.total_rows} stocks reached {data.pct}%
          </span>
        ) : null}
      </div>
      {error ? <p className="text-xs font-semibold text-amber-300">{error}</p> : null}
      {data && data.all_days.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-400">
          No saved Most active scans on this server yet. Keep the card&apos;s &quot;Auto scan&quot; on during market hours, then come back.
        </p>
      ) : null}
      {data && data.all_days.length > 0 ? (
        <>
          <ul className="space-y-1 text-sm leading-relaxed text-slate-100">
            {data.answer.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="grid gap-2 sm:grid-cols-3">
            {data.stats.map((s) => (
              <div key={s.side} className="rounded-lg border border-slate-800 p-2.5">
                <p className="text-xs font-semibold text-slate-300">{s.label}</p>
                <p className={clsx("mt-0.5 text-2xl font-semibold tabular-nums", s.correct_pct == null ? "text-slate-500" : s.correct_pct >= 60 ? "text-emerald-300" : s.correct_pct >= 45 ? "text-amber-300" : "text-rose-300")}>
                  {s.correct_pct == null ? "—" : `${s.correct_pct.toFixed(0)}% correct`}
                </p>
                <p className="text-xs text-slate-400">
                  {s.moved} of {s.stocks} moved {data.move_pct}%{s.avg_minutes != null ? ` · about ${s.avg_minutes} min` : ""}
                </p>
                <p className="text-xs text-slate-500">
                  Best move on average {signed(s.avg_best_pct)} · ended the right side {s.ended_right_pct == null ? "—" : `${s.ended_right_pct.toFixed(0)}%`}
                </p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <label className="flex items-center gap-1.5">
              Who
              <select className={select} value={side} onChange={(e) => setSide(e.target.value as typeof side)}>
                <option value="ALL">Buyers and sellers</option>
                <option value="BUY">Buyers only</option>
                <option value="SELL">Sellers only</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">
              Show
              <select className={select} value={show} onChange={(e) => setShow(e.target.value as typeof show)}>
                <option value="ALL">All</option>
                <option value="YES">Price moved (correct)</option>
                <option value="NO">Price did not move (wrong)</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5" title="When the stock first reached your % (IST)">
              First reached from
              <input type="time" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} className={clsx(select, "w-28")} />
              to
              <input type="time" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} className={clsx(select, "w-28")} />
            </label>
            <label className="flex items-center gap-1.5" title="The price (LTP) at the scan that first reached your %">
              Price ₹ from
              <input type="number" min={0} step={1} inputMode="decimal" placeholder="any" value={priceMin} onChange={(e) => setPriceMin(e.target.value)} className={clsx(select, "w-24")} />
              to
              <input type="number" min={0} step={1} inputMode="decimal" placeholder="any" value={priceMax} onChange={(e) => setPriceMax(e.target.value)} className={clsx(select, "w-24")} />
            </label>
            {filtered || sort ? (
              <button
                type="button"
                onClick={() => {
                  setSide("ALL");
                  setShow("ALL");
                  setTimeFrom("");
                  setTimeTo("");
                  setPriceMin("");
                  setPriceMax("");
                  setSort(null);
                }}
                className="min-h-9 rounded-md px-2 text-xs text-slate-400 underline hover:text-slate-200"
              >
                Clear filters and sort
              </button>
            ) : null}
            <span>
              {rows.length} shown
              {filtered && shownJudged ? (
                <>
                  {" "}
                  · <b className={shownMoved / shownJudged >= 0.6 ? "text-emerald-300" : shownMoved / shownJudged >= 0.45 ? "text-amber-300" : "text-rose-300"}>
                    {Math.round((shownMoved / shownJudged) * 100)}% correct
                  </b>{" "}
                  ({shownMoved} of {shownJudged} moved)
                </>
              ) : null}
            </span>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="space-y-2 rounded-lg border border-slate-800 p-2.5 text-xs text-slate-400">
              <p className="text-sm font-semibold text-slate-100">Which news was behind it?</p>
              <p>
                Reads each shown stock&apos;s news that day (NSE filings and news headlines, up to 40 stocks a day) and says whether it points the
                same way as the buyers / sellers. A headline marked <i>after</i> came out after the share was first reached, so it did not cause it.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void readNews()}
                  disabled={newsBusy || !rows.length}
                  className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/15 px-3 text-xs font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/25 disabled:opacity-50"
                >
                  {newsBusy ? <Loader2 size={13} className="animate-spin" /> : <Newspaper size={13} />} Read the news ({Math.min(rows.length, 40)} stocks)
                </button>
                <label className="flex items-center gap-1.5" title="Score the headlines with the saved OpenAI key (slower, costs a little); off = the built-in rules">
                  <input type="checkbox" checked={newsAi} onChange={(e) => setNewsAi(e.target.checked)} /> Read with AI
                </label>
              </div>
              {newsErr ? <p className="font-semibold text-amber-300">{newsErr}</p> : null}
              {newsSplit ? (
                <ul className="space-y-0.5 text-slate-300">
                  {(["fits", "against", "neutral", "none"] as const).map((k) =>
                    newsSplit[k].n ? (
                      <li key={k}>
                        {k === "fits" ? "News the same way" : k === "against" ? "News the other way" : k === "neutral" ? "News, no clear side" : "No news found"}:{" "}
                        <b>{newsSplit[k].moved}</b> of {newsSplit[k].n} moved {data.move_pct}% ({Math.round((newsSplit[k].moved / newsSplit[k].n) * 100)}%)
                      </li>
                    ) : null
                  )}
                </ul>
              ) : null}
            </div>
            <div className="space-y-2 rounded-lg border border-slate-800 p-2.5 text-xs text-slate-400">
              <p className="text-sm font-semibold text-slate-100">If I keep it for N days, when does it reach my profit?</p>
              <p>
                From the price then, each shown stock (up to 60) is followed on its real 1-minute candles for the same day and the next N trading
                days: the day it first touched your profit %, and how far it went against you first. Before charges.
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1">
                  Keep for
                  <span className="flex items-center gap-1">
                    <input type="number" min={1} max={20} step={1} value={holdDays} onChange={(e) => setHoldDays(Math.min(20, Math.max(1, Math.round(Number(e.target.value) || 5))))} className={clsx(select, "w-16")} />
                    trading days
                  </span>
                </label>
                <label className="flex flex-col gap-1">
                  Profit
                  <span className="flex items-center gap-1">
                    <input type="number" min={0.05} max={50} step={0.1} value={holdPct} onChange={(e) => setHoldPct(Math.min(50, Math.max(0.05, Number(e.target.value) || 1)))} className={clsx(select, "w-20")} />%
                  </span>
                </label>
                <button
                  type="button"
                  onClick={() => void runHold()}
                  disabled={holdBusy || !rows.length}
                  className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-violet-500/15 px-3 text-xs font-semibold text-violet-100 ring-1 ring-inset ring-violet-400/40 hover:bg-violet-500/30 disabled:opacity-50"
                >
                  {holdBusy ? <Loader2 size={13} className="animate-spin" /> : <CalendarClock size={13} />} Test holding
                </button>
              </div>
              {holdErr ? <p className="font-semibold text-amber-300">{holdErr}</p> : null}
              {hold ? (
                <>
                  <ul className="space-y-0.5 text-slate-200">
                    {hold.answer.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  <div className="flex flex-wrap gap-1.5">
                    {hold.by_day.map((b) => (
                      <span key={b.day_no} className="rounded border border-slate-700 px-1.5 py-0.5 tabular-nums text-slate-300" title={`${b.count} reached it on this day`}>
                        {b.day_no === 0 ? "Same day" : `By day ${b.day_no}`}: <b>{b.by_then_pct == null ? "—" : `${b.by_then_pct.toFixed(0)}%`}</b>
                      </span>
                    ))}
                  </div>
                  {hold.errors.length ? <p className="text-amber-300">Not read: {hold.errors.map((e) => e.symbol).join(", ")}</p> : null}
                </>
              ) : null}
            </div>
          </div>
          {rows.length ? (
            <div className="max-h-[28rem] overflow-auto rounded-lg border border-slate-800">
              <table className="w-full min-w-[1180px] text-xs">
                <thead className="sticky top-0 bg-card text-slate-400">
                  <tr>
                    {data.day ? null : (
                      <SortTh k="day" first={-1} left>
                        Day
                      </SortTh>
                    )}
                    <SortTh k="symbol" left>
                      Stock
                    </SortTh>
                    <SortTh k="side" left title="Buyers first (highest share first), then sellers">
                      Who
                    </SortTh>
                    <SortTh k="first_at" title="When its order book was first read at or above your % (the stock's own read time; older scans only kept the scan's start). Under it: the last read still under your %, so it crossed between the two">
                      First reached
                    </SortTh>
                    <SortTh k="max_share" first={-1} title="Highest share that day, and in how many scans it was at or above your %">
                      Highest · scans
                    </SortTh>
                    <SortTh k="price" first={-1}>
                      Price then
                    </SortTh>
                    <SortTh k="moved" left title="Did the real price move your % its way afterwards? Sorts Yes (soonest first), then No">
                      Moved {data.move_pct}%?
                    </SortTh>
                    <SortTh k="best" first={-1} title="The furthest the price went its way afterwards, and when">
                      Best after
                    </SortTh>
                    <SortTh k="worst" title="The furthest it went against (worst first)">
                      Worst after
                    </SortTh>
                    <SortTh k="last" first={-1} title="At the day's last saved scan">
                      At the end
                    </SortTh>
                    <SortTh k="news" first={-1} left title="That day's news (press Read the news). Sorts news that agrees with the buyers / sellers first">
                      News that day
                    </SortTh>
                    <SortTh k="hold" left title={`Days to reach +${holdPct}% holding up to ${holdDays} trading days (press Test holding). Sorts the quickest first`}>
                      Keep {hold?.days ?? holdDays}d: +{hold?.target_pct ?? holdPct}%
                    </SortTh>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.day}-${r.symbol}-${r.side}`} className="border-t border-slate-800/70 text-slate-200">
                      {data.day ? null : <td className="px-2 py-1.5">{r.day}</td>}
                      <td className="px-2 py-1.5">
                        <GrowwLink symbol={r.symbol} className="font-semibold" />
                      </td>
                      <td className="px-2 py-1.5">
                        <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", r.side === "BUY" ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300")}>
                          {r.side === "BUY" ? "Buyers" : "Sellers"} {r.share_pct.toFixed(0)}%
                        </span>
                      </td>
                      <td
                        className={td}
                        title={
                          r.below_at
                            ? `Seen at your % when its order book was read at ${r.first_at}; it was still under your % at the read before (${r.below_at}). It crossed somewhere in between: the order book is only read at each scan.`
                            : `Already at your % at the day's first read (${r.first_at}); when it got there before that is not known.`
                        }
                      >
                        {r.first_at}
                        <div className="text-[10px] text-slate-500">{r.below_at ? `under at ${r.below_at}` : "at first scan"}</div>
                      </td>
                      <td className={clsx(td, "text-slate-400")}>
                        {r.max_share_pct.toFixed(0)}% · {r.scans_at}
                      </td>
                      <td className={td}>₹{r.price.toFixed(2)}</td>
                      <td className="px-2 py-1.5">
                        {r.moved == null ? (
                          <span className="text-slate-500">no later scan</span>
                        ) : r.moved ? (
                          <span className="font-semibold text-emerald-300">
                            Yes · {r.moved_at} ({r.minutes_to_move} min)
                          </span>
                        ) : (
                          <span className="font-semibold text-rose-300">No</span>
                        )}
                      </td>
                      <td className={clsx(td, tone(r.best_pct))}>
                        {signed(r.best_pct)}
                        {r.best_at ? <span className="ml-1 text-slate-500">{r.best_at}</span> : null}
                      </td>
                      <td className={clsx(td, tone(r.worst_pct))}>{signed(r.worst_pct)}</td>
                      <td className={clsx(td, tone(r.last_pct))}>{signed(r.last_pct)}</td>
                      <td className="max-w-[22rem] px-2 py-1.5">
                        <NewsCell row={r} news={news[newsKey(r)]} />
                      </td>
                      <td className="px-2 py-1.5">
                        <HoldCell h={holdMap.get(holdKey(r))} days={hold?.days ?? holdDays} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="text-xs leading-relaxed text-slate-500">
            Moves are signed your idea&apos;s way: green = up after buyers or down after sellers. Prices are the real last traded prices in the
            saved scans, read every few minutes, so a move between two scans shows at the next scan. The % is of orders waiting in the order
            book. Before charges.
          </p>
        </>
      ) : null}
    </div>
  );
}

const SENT_TONE: Record<string, string> = {
  POSITIVE: "bg-emerald-500/15 text-emerald-300",
  NEGATIVE: "bg-rose-500/15 text-rose-300",
  MIXED: "bg-amber-500/15 text-amber-300",
  NEUTRAL: "bg-slate-700/50 text-slate-300",
};

/** That day's news for one row: the overall view, whether it fits the buyers / sellers, and the strongest headline. */
function NewsCell({ row, news }: { row: PressureDayRow; news: NewsDayRow | undefined }) {
  if (!news) return <span className="text-slate-600">—</span>;
  if (news.error) return <span className="text-amber-300" title={news.error}>could not read</span>;
  if (!news.count || !news.items?.length) return <span className="text-slate-500">no news found</span>;
  const fit = newsFit(row, news);
  const top = news.items[0];
  const when = istParts(top.at);
  const after = when != null && when.day === row.day && when.time > row.first_at;
  const all = news.items
    .map((it) => {
      const w = istParts(it.at);
      return `${w ? `${w.day} ${w.time}` : ""} · ${it.sentiment} ${it.impact} · ${it.title}${it.why ? ` (${it.why})` : ""}`;
    })
    .join("\n");
  const sent = news.overall?.sentiment ?? "NEUTRAL";
  return (
    <div className="space-y-0.5" title={all}>
      <div className="flex flex-wrap items-center gap-1">
        <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", SENT_TONE[sent] ?? SENT_TONE.NEUTRAL)}>
          {sent.toLowerCase()} · {(news.overall?.impact ?? "LOW").toLowerCase()}
        </span>
        {fit === "fits" ? (
          <span className="text-[10px] font-semibold text-emerald-300">same way as the {row.side === "BUY" ? "buyers" : "sellers"}</span>
        ) : fit === "against" ? (
          <span className="text-[10px] font-semibold text-rose-300">against the {row.side === "BUY" ? "buyers" : "sellers"}</span>
        ) : null}
        {news.items.length > 1 ? <span className="text-[10px] text-slate-500">+{news.items.length - 1} more</span> : null}
      </div>
      <a href={top.url || undefined} target="_blank" rel="noreferrer" className="line-clamp-2 text-slate-300 hover:text-slate-100 hover:underline">
        {when ? <span className="mr-1 text-slate-500">{when.day === row.day ? when.time : `${when.day.slice(5)} ${when.time}`}</span> : null}
        {after ? <span className="mr-1 rounded bg-slate-700/60 px-1 text-[10px] text-slate-300">after</span> : null}
        {top.title}
      </a>
    </div>
  );
}

/** Days to the profit % for one row (hold_test.follow). */
function HoldCell({ h, days }: { h: HoldTestRow | undefined; days: number }) {
  if (!h) return <span className="text-slate-600">—</span>;
  if (h.error) return <span className="text-slate-500">{h.error}</span>;
  const worst = h.worst_before_pct != null ? <span className="ml-1 text-rose-300/80">worst {signed(h.worst_before_pct)}</span> : null;
  if (h.reached) {
    return (
      <span className="whitespace-nowrap tabular-nums">
        <b className="text-emerald-300">{h.day_no === 0 ? "Same day" : `Day ${h.day_no}`}</b>
        <span className="ml-1 text-slate-400">
          {h.day_no === 0 ? h.hit_at : `${(h.on ?? "").slice(5)} ${h.hit_at}`}
        </span>
        {worst}
      </span>
    );
  }
  return (
    <span className="whitespace-nowrap tabular-nums">
      <b className="text-rose-300">{h.sessions < days ? `Not yet (${h.sessions} of ${days} days so far)` : `Not in ${days} days`}</b>
      <span className="ml-1 text-slate-400">best {signed(h.best_pct)}</span>
      {worst}
    </span>
  );
}
