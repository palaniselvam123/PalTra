"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2, Sparkles } from "lucide-react";
import { api, type ActiveStock, type NewsFeedItem } from "@/lib/api";
import type { ChopScanRow } from "@/lib/smaApi";
import { GrowwLink } from "@/components/ui/growwLink";

/**
 * "Best to trade now" on the Most active card: one short list that joins what three tabs say about a stock.
 * Busy (Most active: volume vs usual), trending or sideways (Trend quality, read on these stocks) and news
 * (NSE filings still valid). Display only: reads what the page already has, plus the news feed; no order.
 */

const BUSY_RVOL = 1.5;

type Verdict = "GOOD" | "OK" | "SIDEWAYS" | "UNREAD";

type Pick = {
  row: ActiveStock;
  verdict: Verdict;
  side: "UP" | "DOWN" | null;
  why: string[];
  news: NewsFeedItem | null;
  rank: number;
};

function judge(row: ActiveStock, tq: ChopScanRow | undefined, news: NewsFeedItem | null): Pick {
  const why: string[] = [];
  const busy = (row.rvol ?? 0) >= BUSY_RVOL;
  if (row.rvol != null) why.push(`Volume ${row.rvol.toFixed(1)}× usual`);
  let side: Pick["side"] = row.bias === "LONG" ? "UP" : row.bias === "SHORT" ? "DOWN" : null;
  let verdict: Verdict = "UNREAD";
  let rank = row.rvol ?? 0;
  if (tq) {
    const crosses = `${tq.crosses_today} cross${tq.crosses_today === 1 ? "" : "es"} today`;
    if (tq.trend) {
      side = tq.trend === "STRONG_BULLISH" ? "UP" : "DOWN";
      why.push(`Strong ${side === "UP" ? "up" : "down"}trend`);
      verdict = busy ? "GOOD" : "OK";
      rank += 10;
    } else if (tq.score === "TRENDING") {
      why.push(`Trending · ${crosses}`);
      verdict = busy ? "GOOD" : "OK";
      rank += 5;
    } else if (tq.score === "CHOPPY") {
      why.push(`Sideways · ${crosses}`);
      verdict = "SIDEWAYS";
    } else {
      why.push(`Mixed · ${crosses}`);
      verdict = "OK";
    }
  }
  if (news) {
    const against = (news.sentiment === "NEGATIVE" && side === "UP") || (news.sentiment === "POSITIVE" && side === "DOWN");
    why.push(`News: ${news.sentiment.toLowerCase()} (${news.impact.toLowerCase()})${against ? ", against the move" : ""}`);
    if (!against && news.sentiment !== "NEUTRAL") rank += 2;
  }
  return { row, verdict, side, why, news, rank };
}

export function BestNow({
  rows,
  tq,
  reading,
  onRead,
  armed,
  arming,
  onArm,
}: {
  /** The stocks the card's filters show. */
  rows: ActiveStock[];
  /** Trend quality rows read for them (empty until "Read trend quality" has run). */
  tq: Map<string, ChopScanRow>;
  reading: boolean;
  onRead: () => void;
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
}) {
  const [news, setNews] = useState<Map<string, NewsFeedItem>>(new Map());
  useEffect(() => {
    let live = true;
    api
      .newsFeed(200, true)
      .then((r) => {
        if (!live) return;
        const map = new Map<string, NewsFeedItem>();
        // Newest first: keep each stock's strongest still-valid filing.
        const weight = { HIGH: 3, MEDIUM: 2, LOW: 1 } as const;
        for (const item of r.items) {
          if (item.impact === "LOW" || item.sentiment === "NEUTRAL") continue;
          const have = map.get(item.symbol);
          if (!have || weight[item.impact] > weight[have.impact]) map.set(item.symbol, item);
        }
        setNews(map);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  if (!rows.length) return null;
  const picks = rows.map((r) => judge(r, tq.get(r.symbol), news.get(r.symbol) ?? null));
  const read = tq.size > 0;
  const good = picks.filter((p) => p.verdict === "GOOD").sort((a, b) => b.rank - a.rank).slice(0, 8);
  const sideways = picks.filter((p) => p.verdict === "SIDEWAYS");
  const withNews = picks.filter((p) => p.news && p.verdict !== "GOOD").slice(0, 6);

  return (
    <div className="mb-3 rounded-lg border border-emerald-400/30 bg-emerald-400/[0.04] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles size={14} aria-hidden className="text-emerald-300" />
        <h3 className="text-sm font-semibold text-slate-100">Best to trade now</h3>
        <span className="text-xs text-slate-400">Busy + trending + news, in one list. Sideways stocks are named so you can skip them.</span>
        <button
          type="button"
          onClick={onRead}
          disabled={reading}
          title="Reads whether each stock on this list is trending or sideways (the Trend quality scan on just these stocks). Read-only."
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md bg-emerald-500/20 px-3 text-xs font-semibold text-emerald-100 ring-1 ring-inset ring-emerald-400/50 hover:bg-emerald-500/30 disabled:opacity-50"
        >
          {reading ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Sparkles size={13} aria-hidden />}
          {read ? "Refresh" : "Find the best stocks now"}
        </button>
      </div>
      {!read ? (
        <p className="mt-2 text-xs leading-relaxed text-slate-300">
          Press <b>Find the best stocks now</b>. It checks each busy stock below for a clean trend (about a minute) and shows the ones worth
          trading and the sideways ones to avoid. Nothing is armed or ordered.
        </p>
      ) : (
        <>
          <p className="mt-2 text-[11px] uppercase tracking-wide text-emerald-300">
            Good to trade · busy (volume at least {BUSY_RVOL}× usual) and trending{good.length ? "" : ": none right now"}
          </p>
          {good.length ? (
            <ul className="mt-1 grid gap-1.5 md:grid-cols-2">
              {good.map((p) => {
                const on = armed.get(p.row.symbol);
                return (
                  <li key={p.row.symbol} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-white/10 bg-white/[0.02] px-2.5 py-1.5 text-xs">
                    <GrowwLink symbol={p.row.symbol} className="font-semibold text-slate-100" />
                    {p.side ? (
                      <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold", p.side === "UP" ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300")}>
                        {p.side === "UP" ? "▲ Up" : "▼ Down"}
                      </span>
                    ) : null}
                    <span className="min-w-0 flex-1 text-slate-300">{p.why.join(" · ")}</span>
                    {on?.length ? (
                      <span className="text-[11px] text-slate-400">Armed: {on.join(", ")}</span>
                    ) : (
                      <button
                        type="button"
                        disabled={arming === p.row.symbol}
                        onClick={() => onArm(p.row.symbol)}
                        className="inline-flex min-h-8 items-center rounded-md px-2.5 text-[11px] font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/50 hover:bg-sky-500/10 disabled:opacity-50"
                      >
                        Arm
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {sideways.length ? (
            <p className="mt-2 text-xs leading-relaxed text-slate-300">
              <b className="text-amber-300">Sideways, skip for now ({sideways.length}):</b>{" "}
              {sideways
                .slice(0, 20)
                .map((p) => p.row.symbol)
                .join(", ")}
              {sideways.length > 20 ? ` and ${sideways.length - 20} more` : ""}. These kept crossing back and forth today; the SMA cross tends to lose on them.
            </p>
          ) : null}
          {withNews.length ? (
            <p className="mt-1 text-xs leading-relaxed text-slate-300">
              <b className="text-slate-200">Also has news:</b> {withNews.map((p) => `${p.row.symbol} (${p.news?.sentiment.toLowerCase()})`).join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
