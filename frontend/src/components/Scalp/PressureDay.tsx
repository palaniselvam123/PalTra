"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowDown, ArrowUp, ArrowUpDown, Loader2, Search } from "lucide-react";
import { api, type PressureDay, type PressureDayRow } from "@/lib/api";
import { GrowwLink } from "@/components/ui/growwLink";

/**
 * Day list of the buyers / sellers idea (backend/pressure_study.reached): every stock that reached the typed share
 * of buyers or sellers at any saved Most active scan of a day, when it first did, and whether the real price then
 * moved the typed % its way. Read-only: saved scans only, no Groww call, no order.
 */
const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => ((v ?? 0) > 0 ? "text-emerald-300" : (v ?? 0) < 0 ? "text-rose-300" : "text-slate-300");

type SortKey = "day" | "symbol" | "side" | "first_at" | "max_share" | "price" | "moved" | "best" | "worst" | "last";

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
    return [...kept].sort((a, b) => {
      const va = sortValue(a, sort.key);
      const vb = sortValue(b, sort.key);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return c * sort.dir;
    });
  }, [data, side, show, timeFrom, timeTo, priceMin, priceMax, sort]);

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
          {rows.length ? (
            <div className="max-h-[28rem] overflow-auto rounded-lg border border-slate-800">
              <table className="w-full min-w-[860px] text-xs">
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
                    <SortTh k="first_at" title="The first saved scan of the day in which it reached your %">
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
                      <td className={td}>{r.first_at}</td>
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
