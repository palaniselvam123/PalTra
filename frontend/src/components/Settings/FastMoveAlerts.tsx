"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { smaApi, type FastMoveSettings } from "@/lib/smaApi";

const input = "min-h-9 w-24 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

/**
 * Fast-move alerts (fast_move.py): a Telegram (and a phone call if ticked) when a stock's price moves fast.
 * Armed / held stocks are watched every 5 s on the bots' own prices; all scanned stocks after each Trend quality pass.
 * Alerts only: nothing is traded.
 */
export function FastMoveAlerts() {
  const [view, setView] = useState<FastMoveSettings | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .fastMoves()
      .then((v) => {
        setView(v);
        if (fill)
          setForm({
            on: v.on,
            pct: String(v.pct),
            minutes: String(v.minutes),
            scan_on: v.scan_on,
            scan_speed: String(v.scan_speed),
            cooldown_min: String(v.cooldown_min),
            call: v.call,
          });
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), 15000);
    return () => clearInterval(poll);
  }, [load]);

  const set = (k: string, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));
  const num = (k: string, fb: number) => {
    const n = Number(form[k]);
    return Number.isFinite(n) && n > 0 ? n : fb;
  };

  const save = async () => {
    setBusy(true);
    try {
      const v = await smaApi.saveFastMoves({
        on: Boolean(form.on),
        pct: Math.min(20, Math.max(0.05, num("pct", 0.5))),
        minutes: Math.min(30, Math.max(1, Math.round(num("minutes", 3)))),
        scan_on: Boolean(form.scan_on),
        scan_speed: Math.min(5, Math.max(0.01, num("scan_speed", 0.1))),
        cooldown_min: Math.min(240, Math.max(1, Math.round(num("cooldown_min", 15)))),
        call: Boolean(form.call),
      });
      setView(v);
      setForm((f) => ({ ...f, pct: String(v.pct), minutes: String(v.minutes), scan_speed: String(v.scan_speed), cooldown_min: String(v.cooldown_min) }));
      setNote(v.on || v.scan_on ? "Saved. Alerts go to your trade Telegram channel while NSE is open." : "Saved: off.");
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const recent = view?.recent ?? [];
  return (
    <section id="fast-moves" aria-label="Fast-move alerts" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="text-base font-semibold text-slate-100">Fast-move alerts (Telegram / phone call)</h2>
      <p className="mt-1 text-xs text-slate-400">
        Tells you when a stock&apos;s price is changing quickly, either way. Each stock alerts at most once in the cool-down,
        while NSE is open, through your trade alert channel (the call uses the phone-call channel: market hours, at most one
        every 5 minutes). Alerts only: nothing is traded.
      </p>
      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={Boolean(form.on)} onChange={(e) => set("on", e.target.checked)} className="accent-[#10B981]" />
            Armed and held stocks (Bots 1–4), checked every 5 s
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Moved at least (%)
            <input inputMode="decimal" value={String(form.pct ?? "")} onChange={(e) => set("pct", e.target.value)} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Within (minutes)
            <input inputMode="numeric" value={String(form.minutes ?? "")} onChange={(e) => set("minutes", e.target.value)} className={input} />
          </label>
          {view && view.watches_armed === false ? (
            <span className="text-xs text-amber-300">This server runs no bots: use the desk&apos;s Settings for this one.</span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={Boolean(form.scan_on)} onChange={(e) => set("scan_on", e.target.checked)} className="accent-[#10B981]" />
            All stocks in the Trend quality scan (after each pass, manual or auto re-scan)
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Speed at least (%/min, last 10 min)
            <input inputMode="decimal" value={String(form.scan_speed ?? "")} onChange={(e) => set("scan_speed", e.target.value)} className={input} />
          </label>
          {view && view.scans_enabled === false ? (
            <span className="text-xs text-amber-300">Scans are off on this server: tick it on the scanner server&apos;s Settings.</span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Same stock again after (minutes)
            <input inputMode="numeric" value={String(form.cooldown_min ?? "")} onChange={(e) => set("cooldown_min", e.target.value)} className={input} />
          </label>
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={Boolean(form.call)} onChange={(e) => set("call", e.target.checked)} className="accent-[#10B981]" />
            Phone call too
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={save}
            className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
          >
            Save
          </button>
          {note ? <span className="text-xs text-slate-400">{note}</span> : null}
        </div>
      </div>
      {recent.length ? (
        <div className="mt-3">
          <h3 className="text-xs font-semibold text-slate-300">Last alerts</h3>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
            {recent.slice(0, 10).map((r, i) => {
              const move = r.move_pct ?? r.speed ?? 0;
              return (
                <li key={`${r.at}-${r.symbol}-${i}`}>
                  <span className="font-mono">{r.at.slice(11, 16)}</span>{" "}
                  <span className={clsx("font-semibold", move >= 0 ? "text-emerald-300" : "text-rose-300")}>
                    {move >= 0 ? "▲" : "▼"} {r.symbol}
                  </span>{" "}
                  {r.source === "scan"
                    ? `${(r.speed ?? 0).toFixed(3)}%/min in the scan`
                    : `${(r.move_pct ?? 0).toFixed(2)}% in ${Math.max(1, Math.round((r.secs ?? 60) / 60))} min (${r.from} → ${r.price})`}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
