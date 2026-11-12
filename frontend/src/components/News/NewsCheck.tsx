"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Download, Loader2, Play } from "lucide-react";
import { api, type NewsCheck, type NewsCheckRow } from "@/lib/api";
import { saveFile } from "@/lib/chartTable";
import { lateText, toneClass, toneWord } from "@/lib/newsTone";
import { GrowwLink } from "@/components/ui/growwLink";
import { DEFAULT_KEYWORDS, keywordMatcher, keywordStats, parseKeywords } from "@/lib/newsKeywords";

const KEYWORDS_KEY = "news.check.keywords";
const MAX_RANGE_DAYS = 31;

function shiftDay(day: string, by: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
}

/** The date `n` weekdays before `day` (so "last 5 days" means 5 trading days). */
function weekdaysBack(day: string, n: number): string {
  let d = day;
  let left = n - 1;
  while (left > 0) {
    d = shiftDay(d, -1);
    const wd = new Date(`${d}T12:00:00Z`).getUTCDay();
    if (wd !== 0 && wd !== 6) left -= 1;
  }
  return d;
}

const card = "terminal-dark rounded-xl border border-white/10 p-4";
const field = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

const VERDICT: Record<NewsCheckRow["verdict"], { label: string; cls: string; tip: string }> = {
  WITH: { label: "Moved with it", cls: "text-emerald-300 ring-emerald-400/40", tip: "Closed at least the minimum move the news' way" },
  AGAINST: { label: "Went against", cls: "text-rose-300 ring-rose-400/40", tip: "Closed at least the minimum move the other way" },
  FADED: { label: "Moved, then faded", cls: "text-amber-300 ring-amber-400/40", tip: "Got the minimum move the news' way, then gave it back" },
  NO_EFFECT: { label: "No effect", cls: "text-slate-300 ring-white/15", tip: "Never moved the minimum the news' way, nor closed that far against it" },
  NEUTRAL: { label: "Neutral news", cls: "text-slate-400 ring-white/10", tip: "Neutral headline: only the size of the move is shown" },
  AFTER_CLOSE: { label: "After the close", cls: "text-slate-400 ring-white/10", tip: "Came out after 15:30: check it on the next day" },
  PENDING: { label: "Too new", cls: "text-slate-400 ring-white/10", tip: "No candle after it yet" },
  NO_DATA: { label: "No candles", cls: "text-slate-500 ring-white/10", tip: "Groww sent no candles for it" },
};

function todayIst(): string {
  return new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 10);
}

function pct(v: number | null | undefined, signed = true): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function tone(v: number | null | undefined): string {
  return v == null ? "text-slate-500" : v > 0 ? "text-emerald-300" : v < 0 ? "text-rose-300" : "text-slate-300";
}

function clock(at: string | null): string {
  if (!at) return "—";
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? at
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

/**
 * "Did it move?": each headline of a day against the stock's 1-minute candles of that day, and a summary per
 * positive / negative × high / medium / low impact (news_validation.py). After the close it reads the whole
 * session; in market hours the move so far. Information only: nothing is traded.
 */
export function NewsCheckPanel() {
  const [date, setDate] = useState(todayIst());
  const [endDate, setEndDate] = useState(todayIst());
  const [keywordText, setKeywordText] = useState(DEFAULT_KEYWORDS.join(", "));
  const [keyword, setKeyword] = useState<string | null>(null);
  const [alerts, setAlerts] = useState(true);
  const [armed, setArmed] = useState(false);
  const [symbols, setSymbols] = useState("");
  const [minMove, setMinMove] = useState("0.5");
  const [data, setData] = useState<NewsCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState("ALL");
  const [imp, setImp] = useState("ALL");
  const [verdict, setVerdict] = useState("ALL");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEYWORDS_KEY);
      if (saved) setKeywordText(saved);
    } catch {
      /* no browser storage: keep the usual words */
    }
  }, []);
  const saveKeywords = (text: string) => {
    setKeywordText(text);
    try {
      localStorage.setItem(KEYWORDS_KEY, text);
    } catch {
      /* not kept: fine */
    }
  };

  const span = Math.round((Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${date}T00:00:00Z`)) / 86_400_000) + 1;
  const rangeError = endDate < date ? "The To date is before the From date." : span > MAX_RANGE_DAYS ? `Pick at most ${MAX_RANGE_DAYS} days at a time.` : null;

  const run = async () => {
    if (rangeError) {
      setError(rangeError);
      return;
    }
    setBusy(true);
    setError(null);
    setKeyword(null);
    try {
      const names = Array.from(new Set(symbols.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)));
      const move = Math.min(10, Math.max(0.05, Number(minMove) || 0.5));
      setData(await api.newsCheck({ date, end: endDate, symbols: names, alerts, armed, minMove: move }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not check the news");
    } finally {
      setBusy(false);
    }
  };

  const keywords = useMemo(() => parseKeywords(keywordText), [keywordText]);
  const kwStats = useMemo(() => keywordStats(data?.rows ?? [], keywords), [data, keywords]);
  const rows = useMemo(() => {
    const re = keyword ? keywordMatcher(keyword) : null;
    return (data?.rows ?? []).filter(
      (r) =>
        (sent === "ALL" || r.sentiment === sent) &&
        (imp === "ALL" || r.impact === imp) &&
        (verdict === "ALL" || r.verdict === verdict) &&
        (!re || re.test(r.title || ""))
    );
  }, [data, sent, imp, verdict, keyword]);
  const ranged = !!data && !!data.end && data.end !== data.day;

  const csv = () => {
    if (!data) return;
    const head = ["Time", "Stock", "Sentiment", "Impact", "Verdict", "Gap %", "15m %", "30m %", "60m %", "Close %", "Best with %", "Worst against %", "Peak after min", "Headline", "From"];
    const q = (s: string) => `"${s.replace(/"/g, '""')}"`;
    const lines = rows.map((r) =>
      [
        clock(r.at), r.symbol, r.sentiment, r.impact, r.verdict, r.gap_pct ?? "", r.move_15m ?? "", r.move_30m ?? "", r.move_60m ?? "",
        r.move_close ?? "", r.best_with ?? "", r.worst_against ?? "", r.peak_after_min ?? "", q(r.title || ""), r.from,
      ].join(",")
    );
    saveFile(`news-check-${data.day}${ranged ? `-to-${data.end}` : ""}${keyword ? `-${keyword.split("|")[0].replace(/\W+/g, "_")}` : ""}.csv`, [head.join(","), ...lines].join("\n"), "text/csv;charset=utf-8");
  };

  return (
    <div className="space-y-4">
      <section className={card} aria-label="Did the news move the stock">
        <h2 className="text-base font-semibold text-slate-100">Did the news move the stock?</h2>
        <p className="mt-1 text-xs text-slate-400">
          Each headline of the day is set against the stock&apos;s 1-minute candles: the move 15, 30 and 60 minutes after it and
          at the close, the best and worst move, and how long the best move took. News out before 09:15 is measured from the
          previous close, so the opening gap counts. Positive news is judged on the move up, negative on the move down. Run it
          after 15:30 for the whole day, or in market hours for the move so far. Pick a From / To range (up to {MAX_RANGE_DAYS}{" "}
          days) to read many days at once; each stock&apos;s candles are downloaded once for the range. Information only: nothing
          is traded.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            From
            <input
              type="date"
              value={date}
              max={todayIst()}
              onChange={(e) => {
                setDate(e.target.value);
                if (e.target.value > endDate) setEndDate(e.target.value);
              }}
              className={field}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            To
            <input type="date" value={endDate} min={date} max={todayIst()} onChange={(e) => setEndDate(e.target.value)} className={field} />
          </label>
          <div className="flex flex-col gap-1 text-xs text-slate-400">
            Quick pick
            <div className="flex gap-1">
              {[
                ["Today", 1],
                ["5 days", 5],
                ["10 days", 10],
                ["20 days", 20],
              ].map(([label, n]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    const to = todayIst();
                    setEndDate(to);
                    setDate(weekdaysBack(to, Number(n)));
                  }}
                  className="min-h-9 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={alerts} onChange={(e) => setAlerts(e.target.checked)} className="accent-amber-400" />
            News alerts sent on those days
          </label>
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={armed} onChange={(e) => setArmed(e.target.checked)} className="accent-amber-400" />
            Armed / held stocks&apos; news
          </label>
          <label className="flex min-w-[200px] flex-1 flex-col gap-1 text-xs text-slate-400">
            Also these stocks (optional)
            <input value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="KFINTECH, DABUR" className={field} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400" title="A move this big (in %) counts as the news moving the stock">
            Counts as a move (%)
            <input inputMode="decimal" value={minMove} onChange={(e) => setMinMove(e.target.value)} className={clsx(field, "w-20")} />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void run()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-amber-500/15 px-3 text-xs font-semibold text-amber-200 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/25 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} {busy ? "Checking…" : "Check"}
          </button>
        </div>
        {rangeError ? <p className="mt-2 text-xs text-rose-300">{rangeError}</p> : null}
        {busy ? (
          <p className="mt-2 text-xs text-slate-400">
            Reading the news and downloading each stock&apos;s candles{span > 1 ? ` for ${span} days; a long range can take several minutes` : "; this can take a minute"}.
          </p>
        ) : null}
        {error ? <p className="mt-2 text-xs text-rose-300">{error}</p> : null}
        {data ? (
          <p className="mt-2 text-xs text-slate-400">
            {data.so_far ? <b className="text-amber-300">Market open: moves so far, as of {clock(data.as_of)}. </b> : null}
            {data.rows.length} headlines on {data.stocks} stocks
            {ranged ? `, ${data.day} to ${data.end} (${data.days} trading days)` : ""}. A move counts from {data.min_move}%.
            {data.skipped_stocks.length ? ` Not checked (over 40 stocks on a day): ${data.skipped_stocks.join(", ")}.` : ""}
          </p>
        ) : null}
      </section>

      {data && data.summary.length ? (
        <section className={clsx(card, "overflow-x-auto")} aria-label="Summary by sentiment and impact">
          <h3 className="mb-2 text-sm font-semibold text-slate-100">How each kind of news moved prices</h3>
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-slate-400">
              <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                <th>News</th>
                <th className="text-right">Headlines</th>
                <th className="text-right" title="Closed at least the minimum move the news' way">Moved with it</th>
                <th className="text-right">Against</th>
                <th className="text-right">Faded</th>
                <th className="text-right">No effect</th>
                <th className="text-right" title="Average move the news' way 30 minutes after it">Avg 30 min</th>
                <th className="text-right" title="Average move the news' way at the close (or so far)">Avg to close</th>
                <th className="text-right" title="Average best move the news' way after it">Avg best</th>
                <th className="text-right" title="Average worst move against the news after it">Avg worst</th>
                <th className="text-right" title="Average minutes from the news to its best move">Best after</th>
              </tr>
            </thead>
            <tbody>
              {data.summary.map((g) => (
                <tr key={g.group} className={clsx("border-t border-white/5 [&_td]:px-2 [&_td]:py-1.5", !g.impact && "font-semibold")}>
                  <td className={clsx(g.sentiment ? toneClass(g.sentiment).split(" ")[0] : "text-slate-200")}>{g.group}</td>
                  <td className="text-right font-mono text-slate-200">{g.headlines}</td>
                  <td className="text-right font-mono text-emerald-300">
                    {g.with} {g.hit_rate != null ? <span className="text-slate-400">({g.hit_rate}%)</span> : null}
                  </td>
                  <td className="text-right font-mono text-rose-300">{g.against}</td>
                  <td className="text-right font-mono text-amber-300">{g.faded}</td>
                  <td className="text-right font-mono text-slate-300">{g.no_effect}</td>
                  <td className={clsx("text-right font-mono", tone(g.avg_signed_30m))}>{pct(g.avg_signed_30m)}</td>
                  <td className={clsx("text-right font-mono", tone(g.avg_signed_close))}>{pct(g.avg_signed_close)}</td>
                  <td className="text-right font-mono text-emerald-300/80">{pct(g.avg_best)}</td>
                  <td className="text-right font-mono text-rose-300/80">{pct(g.avg_worst, false)}</td>
                  <td className="text-right font-mono text-slate-300">{g.avg_peak_min != null ? `${Math.round(g.avg_peak_min)} min` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-slate-500">
            Moves are signed the news&apos; way: +1% on negative news means the price fell 1%. Headlines after the close, too new or
            without candles are listed below but not counted.
          </p>
        </section>
      ) : null}

      {data ? (
        <section className={clsx(card, "overflow-x-auto")} aria-label="Which words move the price">
          <h3 className="text-sm font-semibold text-slate-100">Which words move the price?</h3>
          <p className="mt-1 text-xs text-slate-400">
            Every headline whose title holds a word, and how far the price went after it, in plain % (up is +, down is −, not
            signed by the news). Separate words with commas; <b className="text-slate-300">a|b</b> counts either word as one
            idea. Changing the words needs no new check. Click a word to list only its headlines below.
          </p>
          <div className="mt-2 flex flex-wrap items-start gap-2">
            <textarea
              value={keywordText}
              onChange={(e) => saveKeywords(e.target.value)}
              rows={2}
              aria-label="Keywords"
              className={clsx(field, "min-w-[260px] flex-1 py-1.5 font-mono text-xs")}
            />
            <button
              type="button"
              onClick={() => saveKeywords(DEFAULT_KEYWORDS.join(", "))}
              className="min-h-9 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
            >
              Usual words
            </button>
          </div>
          {kwStats.some((k) => k.headlines) ? (
            <table className="mt-3 w-full min-w-[980px] text-xs">
              <thead className="text-left text-slate-400">
                <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                  <th>Word</th>
                  <th className="text-right" title="Headlines holding the word, and how many stocks">Headlines</th>
                  <th className="text-right" title="Positive / negative headlines among them">+ / −</th>
                  <th className="text-right" title="Average move 15 minutes after the headline">15 min</th>
                  <th className="text-right">30 min</th>
                  <th className="text-right">60 min</th>
                  <th className="text-right" title="Average move from the headline to the close (or so far)">To close</th>
                  <th className="text-right" title="Average size of the move to the close, either way">Size</th>
                  <th className="text-right" title="How often the price closed higher than at the headline">Closed up</th>
                  <th className="text-right" title="Average highest point after the headline">Avg high</th>
                  <th className="text-right" title="Average lowest point after the headline">Avg low</th>
                  <th className="text-right" title="Positive / negative headlines only: how often the price closed at least the minimum move the news' way">News was right</th>
                </tr>
              </thead>
              <tbody>
                {kwStats
                  .filter((k) => k.headlines)
                  .map((k) => (
                    <tr
                      key={k.keyword}
                      onClick={() => setKeyword(keyword === k.keyword ? null : k.keyword)}
                      className={clsx(
                        "cursor-pointer border-t border-white/5 hover:bg-white/5 [&_td]:px-2 [&_td]:py-1.5",
                        keyword === k.keyword && "bg-amber-500/10"
                      )}
                    >
                      <td className="font-mono text-slate-100">{k.keyword.split("|").join(" / ")}</td>
                      <td className="text-right font-mono text-slate-200">
                        {k.headlines} <span className="text-slate-500">({k.stocks})</span>
                      </td>
                      <td className="text-right font-mono">
                        <span className="text-emerald-300">{k.positive}</span> / <span className="text-rose-300">{k.negative}</span>
                      </td>
                      <td className={clsx("text-right font-mono", tone(k.avg15))}>{pct(k.avg15)}</td>
                      <td className={clsx("text-right font-mono", tone(k.avg30))}>{pct(k.avg30)}</td>
                      <td className={clsx("text-right font-mono", tone(k.avg60))}>{pct(k.avg60)}</td>
                      <td className={clsx("text-right font-mono font-semibold", tone(k.avgClose))}>{pct(k.avgClose)}</td>
                      <td className="text-right font-mono text-slate-200">{pct(k.avgAbsClose, false)}</td>
                      <td className="text-right font-mono text-slate-300">{k.upClosePct != null ? `${k.upClosePct}%` : "—"}</td>
                      <td className="text-right font-mono text-emerald-300/80">{pct(k.avgBestUp)}</td>
                      <td className="text-right font-mono text-rose-300/80">{pct(k.avgWorstDown)}</td>
                      <td className="text-right font-mono text-slate-300">{k.withPct != null ? `${k.withPct}%` : "—"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          ) : (
            <p className="mt-2 text-xs text-slate-400">No headline holds these words. Try other words or a longer range.</p>
          )}
          {kwStats.length && kwStats.some((k) => k.headlines && k.measured < 5) ? (
            <p className="mt-2 text-[11px] text-slate-500">A word with fewer than 5 measured headlines says little yet: pick a longer range.</p>
          ) : null}
          {kwStats.some((k) => !k.headlines) ? (
            <p className="mt-1 text-[11px] text-slate-500">
              Not found: {kwStats.filter((k) => !k.headlines).map((k) => k.keyword.split("|")[0]).join(", ")}.
            </p>
          ) : null}
        </section>
      ) : null}

      {data ? (
        <section className={clsx(card, "overflow-x-auto")} aria-label="Each headline">
          <div className="mb-2 flex flex-wrap items-end gap-2">
            <h3 className="mr-auto text-sm font-semibold text-slate-100">
              Each headline
              {keyword ? (
                <button type="button" onClick={() => setKeyword(null)} className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-normal text-amber-200 ring-1 ring-inset ring-amber-400/40">
                  word: {keyword.split("|").join(" / ")} ✕
                </button>
              ) : null}
            </h3>
            <select value={sent} onChange={(e) => setSent(e.target.value)} className={field} aria-label="Sentiment">
              <option value="ALL">All news</option>
              <option value="POSITIVE">Positive</option>
              <option value="NEGATIVE">Negative</option>
              <option value="NEUTRAL">Neutral</option>
            </select>
            <select value={imp} onChange={(e) => setImp(e.target.value)} className={field} aria-label="Impact">
              <option value="ALL">Any impact</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
            <select value={verdict} onChange={(e) => setVerdict(e.target.value)} className={field} aria-label="Result">
              <option value="ALL">Any result</option>
              {Object.entries(VERDICT).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
            <button type="button" onClick={csv} className="inline-flex min-h-9 items-center gap-1 rounded-md px-3 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
              <Download size={13} /> CSV
            </button>
          </div>
          {rows.length ? (
            <table className="w-full min-w-[1000px] text-xs">
              <thead className="text-left text-slate-400">
                <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                  <th>Time</th>
                  <th>Stock</th>
                  <th>News</th>
                  <th>Result</th>
                  <th className="text-right" title="Opening gap on the previous close (news before 09:15)">Gap</th>
                  <th className="text-right">15 min</th>
                  <th className="text-right">30 min</th>
                  <th className="text-right">60 min</th>
                  <th className="text-right">To close</th>
                  <th className="text-right" title="Best move the news' way after it, and minutes to it">Best</th>
                  <th className="text-right" title="Worst move against the news after it">Worst</th>
                  <th>Headline</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const v = VERDICT[r.verdict] ?? VERDICT.NO_DATA;
                  return (
                    <tr key={`${r.symbol}-${r.at}-${i}`} className="border-t border-white/5 align-top [&_td]:px-2 [&_td]:py-1.5">
                      <td className="whitespace-nowrap font-mono text-slate-300">
                        {clock(r.at)}
                        {r.timing === "BEFORE_OPEN" ? <div className="text-[10px] text-slate-500">before open</div> : null}
                        {r.story_at ? (
                          <div className="text-[10px] text-slate-500" title="Measured from the company's NSE results filing; the write-up came later">
                            NSE filing · story {lateText(r.late_min)} later
                          </div>
                        ) : null}
                      </td>
                      <td className="font-semibold">
                        <GrowwLink symbol={r.symbol} />
                      </td>
                      <td className="whitespace-nowrap">
                        <span className={clsx("rounded px-1.5 py-0.5 ring-1 ring-inset", toneClass(r.sentiment))}>
                          {toneWord(r.sentiment)} · {r.impact ? r.impact[0] + r.impact.slice(1).toLowerCase() : "—"}
                        </span>
                      </td>
                      <td className="whitespace-nowrap">
                        <span title={r.error || v.tip} className={clsx("rounded px-1.5 py-0.5 ring-1 ring-inset", v.cls)}>
                          {v.label}
                          {r.so_far && ["WITH", "AGAINST", "FADED", "NO_EFFECT"].includes(r.verdict) ? " (so far)" : ""}
                        </span>
                      </td>
                      <td className={clsx("text-right font-mono", tone(r.gap_pct))}>{pct(r.gap_pct)}</td>
                      <td className={clsx("text-right font-mono", tone(r.move_15m))}>{pct(r.move_15m)}</td>
                      <td className={clsx("text-right font-mono", tone(r.move_30m))}>{pct(r.move_30m)}</td>
                      <td className={clsx("text-right font-mono", tone(r.move_60m))}>{pct(r.move_60m)}</td>
                      <td className={clsx("text-right font-mono", tone(r.move_close))}>{pct(r.move_close)}</td>
                      <td className="whitespace-nowrap text-right font-mono text-emerald-300/80">
                        {r.best_with != null ? `${pct(r.best_with)} in ${r.peak_after_min ?? 0}m` : r.range_pct != null ? `range ${pct(r.range_pct, false)}` : "—"}
                      </td>
                      <td className="text-right font-mono text-rose-300/80">{r.worst_against != null ? pct(-r.worst_against) : "—"}</td>
                      <td className="max-w-[360px] text-slate-300">
                        {r.url ? (
                          <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline">
                            {r.title}
                          </a>
                        ) : (
                          r.title
                        )}
                        <div className="text-[10px] text-slate-500">
                          {r.source || ""}
                          {r.from === "alert" ? " · alerted" : ""}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="text-xs text-slate-400">No headlines {data.rows.length ? "match these filters" : "for that day: tick the armed stocks or type symbols"}.</p>
          )}
          <p className="mt-2 text-[11px] text-slate-500">{data.disclaimer}</p>
        </section>
      ) : null}
    </div>
  );
}
