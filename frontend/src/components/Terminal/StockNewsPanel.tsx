"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, ExternalLink, Loader2, RefreshCw, Sparkles, X } from "lucide-react";
import { api, type NewsAnalysis } from "@/lib/api";
import { directionClass, toneClass, toneWord, urgentAction } from "@/lib/newsTone";

const SOURCE_NAMES: Record<string, string> = { nse: "NSE announcements", google_news: "Google News" };

function ago(at: string | null): string {
  if (!at) return "";
  const mins = Math.round((Date.now() - Date.parse(at)) / 60000);
  if (!Number.isFinite(mins)) return "";
  if (mins < 60) return `${Math.max(mins, 0)}m ago`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)}h ago`;
  return new Date(at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
}

/**
 * News for one stock: NSE's own company announcements and Google News headlines, newest first.
 * Groww's trading API has no news, so these two public sources stand in. Each headline is read as
 * positive / negative (AI when an OpenAI key is saved, else the rule list) and the stock gets an overall
 * view and a suggestion for your position. Read-only: nothing is traded from it.
 */
export function StockNewsPanel({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [data, setData] = useState<NewsAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [only, setOnly] = useState<"all" | "announcement" | "news">("all");
  // A past day's news (read as of that day's close); "" = today's live news.
  const [date, setDate] = useState("");
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

  const load = useCallback(
    (force: boolean) => {
      if (!symbol) return;
      setLoading(true);
      setError(null);
      api
        .newsImpact(symbol, force, date || null)
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load the news"))
        .finally(() => setLoading(false));
    },
    [symbol, date]
  );

  useEffect(() => {
    setData(null);
    load(false);
  }, [load]);

  const items = (data?.items ?? []).filter((i) => only === "all" || i.kind === only);
  return (
    <div role="region" aria-label={`${symbol} news`} className="border-t border-white/10 bg-[#151921] px-3 py-3 sm:px-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">
          News · <span className="text-amber-300">{symbol}</span>
          {data?.name ? <span className="ml-1 font-normal text-slate-400">{data.name}</span> : null}
        </h3>
        <span className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-slate-400" title="News of a past day, read as of that day's 15:30 close">
            Date
            <input
              type="date"
              value={date}
              max={today}
              onChange={(e) => setDate(e.target.value && e.target.value < today ? e.target.value : "")}
              className="min-h-8 rounded-md border border-white/15 bg-black/30 px-1 text-xs text-slate-100"
            />
          </label>
          {(["all", "announcement", "news"] as const).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={only === k}
              onClick={() => setOnly(k)}
              className={clsx(
                "min-h-8 rounded-md px-2 text-xs ring-1 ring-inset",
                only === k ? "bg-sky-400/15 font-semibold text-accentSky ring-sky-400/40" : "text-slate-300 ring-white/15 hover:bg-white/5"
              )}
            >
              {k === "all" ? "All" : k === "announcement" ? "NSE filings" : "Headlines"}
            </button>
          ))}
          <button
            type="button"
            onClick={() => load(true)}
            disabled={loading}
            aria-label="Refresh news"
            className="flex min-h-8 min-w-8 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
          >
            {loading ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <RefreshCw size={13} aria-hidden />}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close news"
            className="flex min-h-8 min-w-8 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            <X size={14} aria-hidden />
          </button>
        </span>
      </div>
      {error ? <p className="mt-2 text-xs text-amber-300">{error}</p> : null}
      {data && Object.keys(data.errors).length ? (
        <p className="mt-2 text-xs text-slate-400">
          Not available right now:{" "}
          {Object.entries(data.errors)
            .map(([k, v]) => `${SOURCE_NAMES[k] ?? k} (${v})`)
            .join(", ")}
          .
        </p>
      ) : null}
      {loading && !data ? <p className="mt-2 text-xs text-slate-400">Loading and reading the news…</p> : null}
      {data && data.items.length ? <Verdict data={data} /> : null}
      {data && !items.length && !loading && Object.keys(data.errors).length < 2 ? <p className="mt-2 text-xs text-slate-400">{date ? `No news found around ${date}.` : "No news in the last few days."}</p> : null}
      <ul className="mt-2 max-h-80 divide-y divide-white/5 overflow-y-auto">
        {items.map((item) => (
          <li key={`${item.url}-${item.title}`} className="py-2">
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="group flex items-start gap-2 text-sm">
              <span
                className={clsx(
                  "mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset",
                  item.kind === "announcement" ? "text-amber-200 ring-amber-400/40" : "text-sky-200 ring-sky-400/30"
                )}
              >
                {item.kind === "announcement" ? "NSE" : "News"}
              </span>
              <span className="min-w-0 flex-1">
                <span
                  title={item.why}
                  className={clsx(
                    "mr-1.5 inline-block rounded px-1.5 py-px align-middle text-[10px] font-semibold ring-1 ring-inset",
                    toneClass(item.sentiment)
                  )}
                >
                  {item.critical ? "⚠ " : ""}
                  {toneWord(item.sentiment)}
                  {item.sentiment !== "NEUTRAL" ? ` · ${item.impact.toLowerCase()}` : ""}
                </span>
                <span className="text-slate-100 group-hover:text-accentSky group-hover:underline">{item.title}</span>
                <ExternalLink size={11} aria-hidden className="ml-1 inline opacity-40 group-hover:opacity-100" />
                {item.detail ? <span className="mt-0.5 block line-clamp-2 text-xs text-slate-400">{item.detail}</span> : null}
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  {item.source}
                  {item.at ? ` · ${ago(item.at)}` : ""}
                  {item.sentiment !== "NEUTRAL" && item.why ? ` · ${item.why}` : ""}
                </span>
              </span>
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-slate-500">
        Groww&apos;s trading API has no news, so this shows NSE&apos;s own company announcements and Google News headlines.
        The positive / negative read and the suggestion are information only, not investment advice: the bot does not
        read them and nothing is traded from them.
      </p>
    </div>
  );
}

/** The stock's overall news view and what it suggests for your position. */
function Verdict({ data }: { data: NewsAnalysis }) {
  const o = data.overall;
  const urgent = urgentAction(data.action.code);
  return (
    <div
      className={clsx(
        "mt-2 rounded-lg border px-3 py-2",
        urgent ? "border-rose-400/50 bg-rose-500/10" : "border-white/10 bg-white/[0.03]"
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={clsx("rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset", toneClass(o.sentiment))}>
          {o.critical ? "⚠ " : ""}
          {toneWord(o.sentiment)} news · {o.impact.toLowerCase()} impact
        </span>
        <span className="font-semibold text-slate-100">
          {urgent ? <AlertTriangle size={13} className="mr-1 inline text-rose-300" aria-hidden /> : null}
          {data.action.label}
        </span>
        <span className="text-xs text-slate-400">
          {data.side ? `for your ${data.side.toLowerCase()} position` : "you hold none"}
        </span>
        <span className="ml-auto flex items-center gap-1 text-[11px] text-slate-500" title={data.ai_error ?? undefined}>
          {data.method === "ai" ? <Sparkles size={11} aria-hidden /> : null}
          {data.method === "ai" ? "AI read" : data.ai_error ? "Rule-based read (AI not used)" : "Rule-based read"}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-300">{data.action.why}</p>
      {data.plan ? <PlanRows plan={data.plan} /> : null}
      {data.day ? <p className="mt-1 text-[11px] text-slate-500">News of {data.day}, read as of that day&apos;s 15:30 close.</p> : null}
      {o.summary ? <p className="mt-1 text-xs text-slate-400">{o.summary}</p> : null}
    </div>
  );
}

/** What the news says in every case: a new entry either way, and an open long or short. */
export function PlanRows({ plan }: { plan: NonNullable<NewsAnalysis["plan"]> }) {
  const rows: [string, (typeof plan)["flat"]][] = [
    ["New entry", plan.flat],
    ["If you are long", plan.long],
    ["If you are short", plan.short],
  ];
  return (
    <dl className="mt-1.5 grid gap-x-3 gap-y-0.5 text-xs sm:grid-cols-[auto_1fr]">
      {rows.map(([label, a]) => (
        <div key={label} className="contents">
          <dt className="text-slate-500">{label}</dt>
          <dd className={clsx("font-semibold", directionClass(a.code))} title={a.why}>
            {a.label}
          </dd>
        </div>
      ))}
    </dl>
  );
}
