"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { Brain, ExternalLink, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { api, type AiStatus, type ExpertView } from "@/lib/api";

const STANCE_STYLES: Record<string, string> = {
  BULLISH: "bg-profit/15 text-profit border-profit/30",
  BEARISH: "bg-loss/15 text-loss border-loss/30",
  NEUTRAL: "bg-slate-700/40 text-slate-300 border-border",
};

function sentimentColor(score: number): string {
  if (score > 0.25) return "text-profit";
  if (score < -0.25) return "text-loss";
  return "text-slate-300";
}

function Bullets({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">{title}</div>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-xs text-slate-300 flex gap-1.5 leading-relaxed">
            <span className={clsx("shrink-0", tone)}>•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AiExpertPanel({ symbols, activeSymbol }: { symbols: string[]; activeSymbol?: string }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [symbol, setSymbol] = useState<string>("");
  const [view, setView] = useState<ExpertView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cached, setCached] = useState(false);

  useEffect(() => {
    api.getAiStatus().then(setStatus).catch(() => {});
  }, []);

  // Follow the dashboard's selected symbol until the user picks their own.
  useEffect(() => {
    if (!symbol && activeSymbol) setSymbol(activeSymbol);
  }, [activeSymbol, symbol]);

  // A view already in the backend cache is free to show.
  useEffect(() => {
    if (!symbol) return;
    setView(null);
    setError(null);
    api
      .getCachedView(symbol)
      .then((d) => {
        if (d.view) {
          setView(d.view);
          setCached(true);
        }
      })
      .catch(() => {});
  }, [symbol]);

  const ask = async (force: boolean) => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.analyzeSymbol(symbol, force);
      setView(res.view);
      setCached(res.cached);
      api.getAiStatus().then(setStatus).catch(() => {});
    } catch (e: any) {
      setError(e.message ?? "The expert could not be reached");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <Brain size={14} className="text-bot" />
        <span className="text-sm font-medium text-slate-200">AI Trading Expert</span>
        {status?.gate_enabled && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-bot/15 text-bot ml-auto">GATING BOT ENTRIES</span>
        )}
      </div>

      {status && !status.configured ? (
        <div className="px-4 py-5 text-center space-y-2">
          <p className="text-xs text-slate-500">
            No OpenAI key saved. The expert reads news, results, analyst actions, sector moves and macro for a
            symbol — the parameters the ORB engine cannot see.
          </p>
          <Link
            href="/settings"
            className="inline-block px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition"
          >
            Add a key in Settings
          </Link>
        </div>
      ) : (
        <div className="p-4 space-y-3">
          <div className="flex items-center gap-2">
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 flex-1 min-w-0"
            >
              {symbols.length === 0 && <option value="">Waiting for quotes…</option>}
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              onClick={() => ask(false)}
              disabled={loading || !symbol}
              className="px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition flex items-center gap-1.5 disabled:opacity-40 shrink-0"
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              Ask
            </button>
            {view && (
              <button
                onClick={() => ask(true)}
                disabled={loading}
                title="Force a fresh analysis, ignoring the cached view"
                className="px-2 py-1.5 rounded-md border border-border text-slate-400 hover:text-slate-200 transition shrink-0 disabled:opacity-40"
              >
                <RefreshCw size={13} />
              </button>
            )}
          </div>

          {loading && (
            <div className="text-[11px] text-slate-500">
              {status?.web_search_enabled
                ? "Searching current news and building the view — this usually takes 30–60 seconds."
                : "Building the view…"}
            </div>
          )}

          {error && <div className="text-xs text-loss">{error}</div>}

          {view && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={clsx(
                    "text-xs font-semibold px-2 py-1 rounded border",
                    STANCE_STYLES[view.stance] ?? STANCE_STYLES.NEUTRAL
                  )}
                >
                  {view.stance}
                </span>
                <div className="flex-1 min-w-[120px]">
                  <div className="flex items-center justify-between text-[10px] text-slate-500 mb-0.5">
                    <span>conviction</span>
                    <span className="font-mono text-slate-300">{view.conviction}/100</span>
                  </div>
                  <div className="h-1.5 bg-border rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        "h-full rounded-full",
                        view.conviction >= 75 ? "bg-profit" : view.conviction >= 50 ? "bg-bot" : "bg-slate-500"
                      )}
                      style={{ width: `${view.conviction}%` }}
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3 text-[11px]">
                <span className="text-slate-500">
                  Sentiment{" "}
                  <span className={clsx("font-medium", sentimentColor(view.sentiment_score))}>
                    {view.sentiment_label.replace("_", " ")}
                  </span>{" "}
                  <span className="font-mono text-slate-600">({view.sentiment_score.toFixed(2)})</span>
                </span>
                {status?.gate_enabled && (
                  <span className={view.conviction >= (status.min_conviction ?? 0) ? "text-profit" : "text-loss"}>
                    {view.conviction >= (status.min_conviction ?? 0) ? "clears" : "below"} the {status.min_conviction}
                    -conviction gate
                  </span>
                )}
              </div>

              <p className="text-xs text-slate-300 leading-relaxed">{view.thesis}</p>

              <Bullets title="Catalysts" items={view.catalysts} tone="text-profit" />
              <Bullets title="Risks" items={view.risks} tone="text-loss" />

              {view.invalidation && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">Invalidation</div>
                  <p className="text-xs text-slate-400 leading-relaxed">{view.invalidation}</p>
                </div>
              )}

              {view.sources.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                    Sources ({view.sources.length})
                  </div>
                  <div className="space-y-0.5">
                    {view.sources.slice(0, 6).map((s) => (
                      <a
                        key={s.url}
                        href={s.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-[11px] text-bot hover:underline flex items-center gap-1"
                      >
                        <ExternalLink size={9} className="shrink-0" />
                        <span className="truncate">{s.title}</span>
                      </a>
                    ))}
                  </div>
                </div>
              )}

              {view.recency_note && (
                <div className="text-[11px] text-amber-400/80 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-1.5">
                  {view.recency_note}
                </div>
              )}

              <div className="text-[10px] text-slate-600 border-t border-border pt-2">
                {view.model}
                {view.web_search_used ? " · web search used" : " · no web search (training data only)"}
                {view.latency_ms > 0 && ` · ${(view.latency_ms / 1000).toFixed(1)}s`}
                {cached && ` · cached view, ${view.age_sec}s old`}
                {" · analysis, not investment advice"}
              </div>
            </div>
          )}

          {!view && !loading && !error && (
            <p className="text-[11px] text-slate-500">
              Ask for a sentiment-driven read on {symbol || "a symbol"}: news flow, results and guidance, analyst
              actions, sector read-across, flows and macro. No chart analysis — the ORB engine handles price.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
