"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, X } from "lucide-react";
import { inr, smaApi, type ReviewItem, type SmaState } from "@/lib/smaApi";
import { ConfirmDialog } from "./ConfirmDialog";

const DOT: Record<string, string> = { GREEN: "bg-emerald-400", RED: "bg-rose-400", DOJI: "bg-slate-400" };

function clock(ts: number | undefined, plusMinutes = 0): string {
  if (ts == null) return "—";
  const d = new Date((ts + plusMinutes * 60 + 5.5 * 3600) * 1000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

/**
 * The 1-minute human review: the bot's candle says its SMA lines are too close
 * to call, so it shows the last closed 1-minute candles and asks. EXIT closes
 * the reviewed trade; WAIT and no answer leave it open (the strategy carries on).
 */
export function ReviewCards({ state, onChanged }: { state: SmaState | null; onChanged: () => void }) {
  const pending = (state?.reviews ?? []).filter((r) => r.status === "PENDING");
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ReviewItem | null>(null);
  const seen = useRef<Set<number>>(new Set());
  const live = state?.mode === "LIVE";

  // A browser notification for each new review, when the browser already allows them.
  useEffect(() => {
    for (const r of pending) {
      if (seen.current.has(r.id)) continue;
      seen.current.add(r.id);
      try {
        if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.hidden) {
          new Notification(`${r.symbol} — ${r.candle_minutes}-min SMA review`, { body: r.message, tag: `review-${r.id}` });
        }
      } catch {
        /* notifications are a convenience */
      }
    }
  }, [pending]);

  const answer = async (r: ReviewItem, action: "exit" | "wait") => {
    setBusy(r.id);
    setMsg(null);
    try {
      const out = await smaApi.answerReview(r.id, action);
      setMsg(out.result);
      onChanged();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "The answer did not reach the bot");
    } finally {
      setBusy(null);
    }
  };

  const shown = pending.filter((r) => !hidden.has(r.id));
  if (!shown.length && !msg) return null;

  return (
    <div className="border-t border-amber-400/40 bg-amber-400/[0.08] px-3 py-2 sm:px-4" aria-live="polite">
      {shown.map((r) => {
        const five = r.five_min ?? {};
        const one = r.one_min ?? {};
        const fast = five.sma_fast_len ?? 9;
        const slow = five.sma_slow_len ?? 21;
        const candles = (one.candles ?? []).slice(-5);
        return (
          <section key={r.id} role="alertdialog" aria-label={`${r.symbol} SMA review`} className="mb-2 last:mb-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="flex w-full min-w-0 items-center gap-2 text-sm sm:w-auto sm:flex-1">
                <AlertTriangle size={16} aria-hidden className="shrink-0 text-amber-300" />
                <span className="min-w-0">
                  <b className="text-amber-200">{r.symbol}</b>{" "}
                  <span className="text-slate-200">
                    {r.direction} {r.qty ?? ""} {r.entry_price != null ? `@ ${inr(r.entry_price)}` : ""}
                  </span>{" "}
                  <span className="text-slate-300">
                    · {r.candle_minutes}-min gap <b className="font-mono tabular-nums">{(five.gap_pct ?? 0) >= 0 ? "+" : ""}{num(five.gap_pct, 3)}%</b>{" "}
                    {five.crossed ? "crossed" : "narrowing"} — uncertain
                  </span>
                  {r.mode === "REPLAY" ? <span className="ml-1 text-xs text-slate-400">(replay paused · Play = no answer)</span> : null}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => (live ? setConfirm(r) : void answer(r, "exit"))}
                  className="inline-flex h-11 items-center rounded-md bg-rose-600 px-4 text-sm font-semibold text-white hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
                >
                  EXIT
                </button>
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => void answer(r, "wait")}
                  className="inline-flex h-11 items-center rounded-md px-4 text-sm font-semibold text-slate-100 ring-1 ring-inset ring-slate-400/60 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
                >
                  WAIT
                </button>
                <button
                  type="button"
                  aria-label={`Hide the ${r.symbol} review (no answer keeps the trade open)`}
                  title="Hide — no answer keeps the trade open"
                  onClick={() => setHidden((h) => new Set(h).add(r.id))}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-md text-slate-400 hover:bg-white/5"
                >
                  <X size={16} aria-hidden />
                </button>
              </span>
            </div>
            <details className="mt-1 text-xs text-slate-300">
              <summary className="cursor-pointer select-none text-slate-400 hover:text-slate-200">1-minute read-out</summary>
              <div className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-2">
                <p>
                  {r.candle_minutes}-min candle closed {clock(five.candle_ts, r.candle_minutes)}: SMA{fast}{" "}
                  <span className="font-mono">{num(five.sma_fast)}</span> · SMA{slow} <span className="font-mono">{num(five.sma_slow)}</span> · band ±
                  {five.band_pct ?? "—"}%
                </p>
                {candles.length ? (
                  <p className="flex flex-wrap items-center gap-1">
                    1-min to {clock(one.candle_ts, 1)}:
                    {candles.map((c) => (
                      <span key={c.ts} className="inline-flex items-center gap-1 font-mono">
                        <span aria-label={c.colour.toLowerCase()} className={clsx("inline-block h-2 w-2 rounded-full", DOT[c.colour])} />
                        {num(c.close)}
                      </span>
                    ))}
                  </p>
                ) : (
                  <p>Not enough closed 1-minute candles yet.</p>
                )}
                {candles.length ? (
                  <>
                    <p>
                      SMA{fast} {one.fast_slope} · SMA{slow} {one.slow_slope} · gap {num(one.gap_pct, 3)}% {one.gap_trend}
                    </p>
                    <p>
                      Price {one.vs_fast ?? "—"} SMA{fast}, {one.vs_slow ?? "—"} SMA{slow} · {one.vs_vwap ?? "—"} VWAP{" "}
                      <span className="font-mono">{num(one.vwap)}</span>
                      {one.rsi14 != null ? ` · RSI ${one.rsi14.toFixed(0)}` : ""}
                      {one.volume_ratio != null ? ` · vol ${one.volume_ratio.toFixed(1)}× avg` : ""}
                    </p>
                    <p className="text-slate-400">
                      Highs / lows:{" "}
                      {candles.map((c) => `${num(c.high)}/${num(c.low)}`).join(" · ")}
                    </p>
                  </>
                ) : null}
              </div>
            </details>
          </section>
        );
      })}
      {msg ? (
        <p role="status" className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-200">
          {msg}
          <button type="button" onClick={() => setMsg(null)} className="rounded px-2 py-1 text-slate-400 hover:bg-white/5">
            OK
          </button>
        </p>
      ) : null}
      <ConfirmDialog
        open={confirm != null}
        title={`Exit ${confirm?.symbol ?? ""} with LIVE money?`}
        confirmLabel="Exit now"
        requireTyping={false}
        busy={busy != null}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const r = confirm;
          setConfirm(null);
          if (r) void answer(r, "exit");
        }}
      >
        A real exit order goes to Groww for this {confirm?.direction} {confirm?.symbol} trade (the stop is cancelled first). If it has
        already closed, nothing is sent.
      </ConfirmDialog>
    </div>
  );
}
