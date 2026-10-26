"use client";

import clsx from "clsx";
import { AlertTriangle, ExternalLink, Newspaper, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type NewsAlertRow } from "@/lib/api";
import { toneClass, toneWord, urgentAction } from "@/lib/newsTone";

const POLL_MS = 30_000;
const SEEN_KEY = "news.alerts.seen";
const FIRST_LOOK_MIN = 30; // on a browser's first visit, show alerts from the last half hour only
const MAX_SHOWN = 3;
const HIDE_MS = 25_000;

function readSeen(): number | null {
  try {
    const v = Number(localStorage.getItem(SEEN_KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function writeSeen(id: number) {
  try {
    localStorage.setItem(SEEN_KEY, String(id));
  } catch {
    /* private window: this tab only */
  }
}

/**
 * News alert pop-ups on every page: the desk's news watcher (news_impact.py) saves an alert when a
 * stock you hold or have armed gets news with real impact. Critical ones (against a held position)
 * stay until dismissed; others clear after a while. Also a desktop notification when the browser
 * allows it. Information only: nothing is traded from these.
 */
export function NewsAlertPopups() {
  const [shown, setShown] = useState<NewsAlertRow[]>([]);
  const last = useRef<number | null>(null);

  const dismiss = useCallback((id: number) => setShown((prev) => prev.filter((a) => a.id !== id)), []);

  const poll = useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    try {
      const seen = last.current ?? readSeen();
      const { alerts } = await api.newsAlerts(seen ?? 0, 20);
      if (!alerts.length) return;
      const newest = Math.max(...alerts.map((a) => a.id));
      const cutoff = Date.now() - FIRST_LOOK_MIN * 60_000;
      const fresh = alerts
        .filter((a) => a.popup)
        .filter((a) => seen != null || (a.created_at ? Date.parse(a.created_at) >= cutoff : false))
        .sort((a, b) => Number(b.critical) - Number(a.critical) || b.id - a.id)
        .slice(0, MAX_SHOWN);
      last.current = newest;
      writeSeen(newest);
      if (!fresh.length) return;
      setShown((prev) => [...fresh, ...prev.filter((p) => !fresh.some((f) => f.id === p.id))].slice(0, MAX_SHOWN));
      for (const a of fresh) {
        if (!a.critical) window.setTimeout(() => dismiss(a.id), HIDE_MS);
        if ("Notification" in window && Notification.permission === "granted") {
          try {
            new Notification(`${a.critical ? "⚠ Critical news" : "News"}: ${a.symbol} · ${toneWord(a.sentiment)}`, {
              body: `${a.title}\n${a.action_label ?? ""}`,
              tag: `news-${a.symbol}`,
              requireInteraction: a.critical,
            });
          } catch {
            /* the pop-up is already on screen */
          }
        }
      }
    } catch {
      /* signed out or the desk is restarting: try again next poll */
    }
  }, [dismiss]);

  useEffect(() => {
    poll();
    const id = window.setInterval(poll, POLL_MS);
    const onShow = () => document.visibilityState === "visible" && poll();
    document.addEventListener("visibilitychange", onShow);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [poll]);

  if (!shown.length) return null;
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed left-1/2 top-16 z-[60] flex w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2"
    >
      {shown.map((a) => (
        <div
          key={a.id}
          role={a.critical ? "alert" : "status"}
          className={clsx(
            "pointer-events-auto overflow-hidden rounded-lg border bg-surface shadow-2xl",
            a.critical ? "border-rose-500/70" : a.sentiment === "POSITIVE" ? "border-emerald-500/50" : "border-amber-400/50"
          )}
        >
          <div className="flex items-start gap-2 px-3 py-2.5">
            {a.critical ? (
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-rose-400" aria-hidden />
            ) : (
              <Newspaper size={16} className="mt-0.5 shrink-0 text-sky-400" aria-hidden />
            )}
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="font-semibold text-slate-100">
                  {a.critical ? "Critical news" : a.scope === "fno" ? "F&O news" : "News"} · {a.symbol}
                </span>
                {a.price ? <span className="font-mono text-slate-300">₹{a.price.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> : null}
                <span className={clsx("rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(a.sentiment))}>
                  {toneWord(a.sentiment)} · {a.impact.toLowerCase()}
                </span>
              </div>
              {a.items?.length ? (
                <ul className="space-y-0.5">
                  {a.items.slice(0, 2).map((it) => (
                    <li key={it.url || it.title} className="text-xs text-slate-200">
                      {it.url ? (
                        <a href={it.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                          {it.title}
                          <ExternalLink size={10} className="ml-1 inline opacity-50" aria-hidden />
                        </a>
                      ) : (
                        it.title
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-slate-200">{a.title}</p>
              )}
              {a.held?.length ? (
                <p className="text-[11px] text-slate-400">
                  You hold: {a.held.map((h) => `${h.side} ${h.qty} on ${h.name} (${h.mode})`).join("; ")}
                </p>
              ) : a.armed?.length ? (
                <p className="text-[11px] text-slate-400">Armed on {a.armed.join(", ")}</p>
              ) : null}
              {a.action_label ? (
                <p className={clsx("text-xs font-semibold", urgentAction(a.action) ? "text-rose-300" : "text-slate-100")}>
                  What to do: {a.action_label}
                  {a.action_why ? <span className="font-normal text-slate-400"> · {a.action_why}</span> : null}
                </p>
              ) : null}
              <p className="text-[10px] text-slate-500">
                {a.method === "ai" ? "AI read" : "Rule-based read"} · not advice, nothing was traded
              </p>
            </div>
            <button
              type="button"
              onClick={() => dismiss(a.id)}
              aria-label="Dismiss news alert"
              className="flex min-h-8 min-w-8 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-white/5 hover:text-slate-100"
            >
              <X size={14} aria-hidden />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
