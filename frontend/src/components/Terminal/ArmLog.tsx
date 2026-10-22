"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Loader2, RefreshCw, X } from "lucide-react";
import { smaApi, type ArmLogDay } from "@/lib/smaApi";

const todayIst = () => new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);

const ACTION: Record<string, { label: string; tone: string }> = {
  ARM: { label: "Armed", tone: "text-emerald-300" },
  ARMED: { label: "Armed (at start)", tone: "text-emerald-300" },
  UNARM: { label: "Unarmed", tone: "text-rose-300" },
};

/** When each stock was armed on / taken off this desk's Trade list, for one day (arm_log.py). Read-only. */
export function ArmLog({ desk, deskName, onClose }: { desk: string; deskName?: string; onClose: () => void }) {
  const [day, setDay] = useState(todayIst);
  const [data, setData] = useState<ArmLogDay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    smaApi
      .armLog(desk, day)
      .then((got) => {
        setData(got);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Arm log did not load"))
      .finally(() => setLoading(false));
  }, [desk, day]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows = data?.rows ?? [];
  const stocks = data?.stocks ?? [];

  return createPortal(
    <div
      className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Arm log"
        className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl"
      >
        <div className="mb-1 flex items-center gap-2">
          <h2 className="mr-auto font-semibold text-slate-100">
            Arm log{deskName ? <span className="font-normal text-slate-400"> · {deskName}</span> : null}
          </h2>
          <button type="button" aria-label="Refresh" onClick={load} className="rounded p-1 text-slate-400 hover:bg-white/5">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
          </button>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          Every time a stock was armed on, or taken off, this desk&apos;s Trade list, from any page. &quot;Replay as armed&quot;
          in the replay bar uses these times, so each stock trades only while it was armed live.
        </p>
        <label className="mb-3 flex items-center gap-2 text-xs text-slate-400">
          Day
          <input
            type="date"
            value={day}
            onChange={(e) => e.target.value && setDay(e.target.value)}
            className="h-8 rounded-md border border-border bg-surface px-1.5 font-mono text-xs text-slate-200 [color-scheme:dark]"
          />
        </label>
        {error && <p className="mb-2 text-xs text-rose-300">{error}</p>}

        {stocks.length > 0 && (
          <div className="mb-4">
            <div className="mb-1 text-xs uppercase tracking-wider text-slate-400">Armed during the day</div>
            <ul className="grid gap-1 text-xs sm:grid-cols-2">
              {stocks.map((s) => (
                <li key={s.symbol} className="rounded-md border border-border px-2 py-1.5">
                  <span className="font-semibold text-amber-300">{s.symbol}</span>{" "}
                  <span className="font-mono text-slate-300">
                    {s.spans.map((p) => `${p.from.slice(0, 5)} → ${p.to === "00:00:00" ? "close" : p.to.slice(0, 5)}`).join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {rows.length === 0 ? (
          <p className="py-6 text-center text-xs text-slate-400">
            Nothing was armed or taken off on this day. The log starts from the day this was added.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-left text-slate-400">
              <tr className="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                <th>Time</th>
                <th>Stock</th>
                <th>What</th>
                <th>From</th>
                <th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-white/5 [&_td]:px-2 [&_td]:py-1.5">
                  <td className="font-mono text-slate-200">{r.at.slice(11, 19)}</td>
                  <td className="font-semibold text-amber-300">{r.symbol}</td>
                  <td className={clsx(ACTION[r.action]?.tone ?? "text-slate-300")}>{ACTION[r.action]?.label ?? r.action}</td>
                  <td className="text-slate-400">{r.source ?? "—"}</td>
                  <td className={r.mode === "LIVE" ? "font-semibold text-rose-300" : "text-slate-400"}>{r.mode ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>,
    document.body
  );
}
