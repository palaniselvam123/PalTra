"use client";

import clsx from "clsx";
import { ExternalLink, Loader2, Newspaper } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type NewsDayReport, type NewsDayRow } from "@/lib/api";
import { directionClass, timingClass, toneClass, toneWord } from "@/lib/newsTone";
import { GrowwLink } from "@/components/ui/growwLink";
import { useArming } from "@/components/Scalp/useArming";

const INPUT = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

function istToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

function yesterday(): string {
  const d = new Date(`${istToday()}T12:00:00+05:30`);
  d.setDate(d.getDate() - 1);
  return d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/**
 * News on a past date (news_impact.day_report): every stock armed or held on Bots 1-4 (or the ones typed),
 * that day's NSE filings and headlines read as of its 15:30 close, and what they suggest for a new entry
 * either way (buy or short) and for an open long or short. Information only: nothing is traded from it.
 */
export function NewsByDay() {
  const [date, setDate] = useState(yesterday());
  const [symbols, setSymbols] = useState("");
  const [report, setReport] = useState<NewsDayReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [filling, setFilling] = useState(false);
  // Arm a stock on an SMA bot from here (same prompt as the Scalp page; arming only adds it to the Trade list).
  const { armed, arming, armNote, arm, loadDesks, dialogs } = useArming();
  useEffect(() => loadDesks(), [loadDesks]);

  /** Every F&O stock with medium / high NSE filings in the chosen day's window, from the all-F&O news feed (max 40). */
  const fillFromFeed = async () => {
    setFilling(true);
    setError(null);
    try {
      const { items } = await api.newsFeed(300);
      const end = new Date(`${date}T15:30:00+05:30`).getTime();
      const start = end - 2.5 * 86_400_000;
      const strength = new Map<string, number>();
      for (const it of items) {
        const t = it.at ? new Date(it.at).getTime() : NaN;
        if (!(t >= start && t <= end) || (it.impact === "LOW" && !it.critical)) continue;
        const w = (it.critical ? 3 : it.impact === "HIGH" ? 2 : 1) + (strength.get(it.symbol) ?? 0);
        strength.set(it.symbol, w);
      }
      const picked = [...strength.entries()].sort((a, b) => b[1] - a[1]).slice(0, 40).map(([s]) => s);
      if (!picked.length) setError("No F&O stock had medium or high NSE news in that window (the feed keeps 3 days).");
      setSymbols(picked.join(", "));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read the F&O news feed");
    } finally {
      setFilling(false);
    }
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const list = symbols.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      setReport(await api.newsDay(date, list.length ? list : undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="news-by-day" aria-label="News on a date" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="flex items-center gap-2 text-base font-semibold text-slate-100">
        <Newspaper size={16} aria-hidden /> News on a date: what to do, long or short
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        Pick a day to read the news of every stock armed or held on Bots 1–4 (or type symbols): NSE filings and
        headlines from the two days before up to that day&apos;s 15:30 close. For each stock you get the news read and
        what it suggests for a new entry (buy, short, or no bias) and for an open long or short position. Information
        only, not advice: nothing is traded from it.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Date
          <input type="date" value={date} max={istToday()} onChange={(e) => setDate(e.target.value)} className={INPUT} />
        </label>
        <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-xs text-slate-400">
          Stocks (empty = every armed / held stock)
          <input value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="e.g. AMBUJACEM, TCS" className={INPUT} />
        </label>
        <button
          type="button"
          disabled={busy || !date}
          onClick={run}
          className="flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : null}
          {busy ? "Reading the news…" : "Get news"}
        </button>
        <button
          type="button"
          disabled={filling || busy}
          onClick={fillFromFeed}
          title="Fill the stocks box with every F&O stock that had medium or high NSE news in that day's window (from the all-F&O news feed, up to 40), then press Get news"
          className="flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
        >
          {filling ? <Loader2 size={13} className="animate-spin" aria-hidden /> : null}
          F&amp;O stocks with news
        </button>
      </div>
      {armNote ? <div className="mt-2 text-xs">{armNote}</div> : null}
      {error ? <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p> : null}
      {report ? (
        <div className="mt-3 overflow-x-auto">
          <p className="mb-1 text-[11px] text-slate-500">
            {report.day}: news from {report.window.from.slice(0, 10)} to {report.window.to.slice(0, 16).replace("T", " ")} IST ·{" "}
            {report.stocks.length} stock(s), strongest news first.
          </p>
          <table className="w-full min-w-[720px] text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1 pr-2 font-medium">Stock</th>
                <th className="py-1 pr-2 font-medium">News</th>
                <th className="py-1 pr-2 font-medium" title="When to buy while flat: now (strong, fresh news), later (on a dip / the bot's buy signal), or not">Buy?</th>
                <th className="py-1 pr-2 font-medium" title="When to short while flat: now, later (on a bounce / the bot's short signal), or not">Short?</th>
                <th className="py-1 pr-2 font-medium">New entry</th>
                <th className="py-1 pr-2 font-medium">If long</th>
                <th className="py-1 pr-2 font-medium">If short</th>
                <th className="py-1 pr-2 font-medium">Headlines</th>
                <th className="py-1 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {report.stocks.map((r) => (
                <Row
                  key={r.symbol}
                  r={r}
                  open={open === r.symbol}
                  onToggle={() => setOpen(open === r.symbol ? null : r.symbol)}
                  armedOn={armed.get(r.symbol.toUpperCase()) ?? []}
                  arming={arming === r.symbol}
                  onArm={() => arm(r.symbol, "News")}
                />
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-slate-500">
            {report.disclaimer} Arm only puts the stock on a bot&apos;s Trade list: the bot orders on its own next signal.
          </p>
        </div>
      ) : null}
      {dialogs}
    </section>
  );
}

function Row({
  r,
  open,
  onToggle,
  armedOn,
  arming,
  onArm,
}: {
  r: NewsDayRow;
  open: boolean;
  onToggle: () => void;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  const o = r.overall;
  // Both sources failed: there is nothing to judge, so no suggestion is shown.
  const noData = !r.count && Object.keys(r.errors ?? {}).length >= 2;
  const p = noData ? undefined : r.plan;
  return (
    <>
      <tr className="align-top">
        <td className="py-1.5 pr-2">
          <GrowwLink symbol={r.symbol} className="font-semibold text-slate-100" />
          {armedOn.length || r.armed?.length ? (
            <span className="block text-[10px] text-slate-500">{(armedOn.length ? armedOn : r.armed).join(", ")}</span>
          ) : null}
        </td>
        <td className="py-1.5 pr-2">
          {r.error ? (
            <span className="text-rose-300">{r.error}</span>
          ) : noData ? (
            <span className="text-amber-300">No data: news sources not reachable</span>
          ) : o ? (
            <span className={clsx("rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(o.sentiment))}>
              {o.critical ? "⚠ " : ""}
              {toneWord(o.sentiment)} · {o.impact.toLowerCase()}
            </span>
          ) : null}
        </td>
        {(["buy", "short"] as const).map((k) => {
          const t = noData ? undefined : r.timing?.[k];
          return (
            <td key={k} className={clsx("py-1.5 pr-2 font-semibold", timingClass(t?.code))} title={t?.why}>
              {t?.label ?? "—"}
            </td>
          );
        })}
        {(["flat", "long", "short"] as const).map((k) => (
          <td key={k} className={clsx("py-1.5 pr-2 font-semibold", directionClass(p?.[k].code))} title={p?.[k].why}>
            {p?.[k].label ?? "—"}
          </td>
        ))}
        <td className="py-1.5">
          {r.count ? (
            <button type="button" onClick={onToggle} className="text-sky-300 hover:underline">
              {open ? "Hide" : `${r.count} item(s)`}
            </button>
          ) : r.error ? null : (
            <span className="text-slate-500">
              {r.errors && Object.keys(r.errors).length >= 2 ? "sources not reachable" : "no news"}
            </span>
          )}
        </td>
        <td className="py-1.5 text-right">
          <button
            type="button"
            onClick={onArm}
            disabled={arming}
            title="Put this stock on an SMA bot's Trade list (asks which bot, quantity and stop first). It orders on the bot's next signal, not now."
            className="min-h-7 rounded-md px-2 text-[11px] font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
          >
            {arming ? "…" : armedOn.length ? "Arm again" : "Arm"}
          </button>
        </td>
      </tr>
      {open && r.items?.length ? (
        <tr>
          <td colSpan={9} className="pb-2">
            <p className="mb-1 text-[11px] text-slate-400">{p?.flat.why}</p>
            {r.timing ? (
              <p className="mb-1 text-[11px] text-slate-400">
                Buy: {r.timing.buy.why} · Short: {r.timing.short.why}
              </p>
            ) : null}
            <ul className="space-y-1">
              {r.items.map((it) => (
                <li key={`${it.url}-${it.title}`}>
                  <span className={clsx("mr-1 rounded px-1 text-[10px] font-semibold ring-1 ring-inset", toneClass(it.sentiment))}>
                    {it.critical ? "⚠ " : ""}
                    {toneWord(it.sentiment)}
                  </span>
                  <a href={it.url} target="_blank" rel="noopener noreferrer" className="text-slate-200 hover:underline">
                    {it.title}
                    <ExternalLink size={10} aria-hidden className="ml-1 inline opacity-50" />
                  </a>
                  <span className="text-slate-500">
                    {" "}
                    · {it.source}
                    {it.at ? ` · ${it.at.slice(0, 16).replace("T", " ")}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
    </>
  );
}
