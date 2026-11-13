"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { BellRing } from "lucide-react";
import { smaApi, type ScalpAlertCheck, type ScalpAlerts } from "@/lib/smaApi";

/**
 * The Scalp page's two Telegram alerts (backend/scalp_alerts.py): a strong up / down trend with 6-7 checks passed
 * (after each Trend quality scan) and buyers or sellers holding most of the waiting orders (after each Most active
 * scan). Alerts only: nothing is armed or ordered.
 */
export function ScalpAlertsCard() {
  const [view, setView] = useState<ScalpAlerts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    smaApi
      .scalpAlerts()
      .then(setView)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the alert settings"));
  }, []);

  const save = async (patch: Partial<Omit<ScalpAlerts, "recent" | "max_names" | "auto_arm">>) => {
    setBusy(true);
    setError(null);
    try {
      setView(await smaApi.saveScalpAlerts(patch));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const box = "rounded-lg border p-3";
  const select = "h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-200";
  const time = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

  return (
    <section className="mb-4 rounded-xl border border-slate-800 bg-card p-4">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <BellRing size={15} className="text-bot" /> Telegram alerts and auto-arm: strong trend, and buyers or sellers in control
      </h2>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        Each stock alerts at most once a day per alert, during market hours only, to the Telegram set up on Settings. Information
        only: nothing is ordered. Tick &quot;Also arm them&quot; to put the same stocks on a bot&apos;s Trade list by themselves (a PAPER bot
        only; the bot then trades them on its own next signal). They ride on the scans, so keep the scan running (its Auto scan /
        Auto re-scan switch). Test the auto-arm on past days in the Backtest tab, &quot;Auto-arm replay&quot;.
      </p>
      {error ? <p className="mb-2 text-xs font-semibold text-amber-300">{error}</p> : null}
      {!view ? (
        <p className="text-xs text-slate-500">{error ? "" : "Loading…"}</p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <div className={clsx(box, view.strong_on ? "border-emerald-400/40 bg-emerald-400/[0.04]" : "border-slate-800")}>
            <label className="flex min-h-9 items-center gap-2 text-sm font-semibold text-slate-100">
              <input type="checkbox" checked={view.strong_on} disabled={busy} onChange={(e) => void save({ strong_on: e.target.checked })} className="accent-[#10B981]" />
              Strong up or down trend
              <span className={clsx("ml-auto text-xs font-normal", view.strong_on ? "text-emerald-300" : "text-slate-500")}>{view.strong_on ? "On" : "Off"}</span>
            </label>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              After each <b>Trend quality</b> scan: a stock that passes the strong-trend checklist. The message gives the price, how
              many of the 7 checks passed and which one it missed.
            </p>
            <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-slate-400">
              <label className="flex flex-col gap-1">
                Checks that must pass
                <select className={select} value={view.strong_min_checks} disabled={busy} onChange={(e) => void save({ strong_min_checks: Number(e.target.value) })}>
                  <option value={6}>6 of 7 (or all 7)</option>
                  <option value={7}>All 7 only</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                Direction
                <select className={select} value={view.strong_side} disabled={busy} onChange={(e) => void save({ strong_side: e.target.value as ScalpAlerts["strong_side"] })}>
                  <option value="BOTH">Up and down</option>
                  <option value="UP">Up only</option>
                  <option value="DOWN">Down only</option>
                </select>
              </label>
            </div>
            <ArmRow
              label="Trend quality"
              on={view.arm_strong_on}
              bot={view.arm_strong_bot}
              busy={busy}
              today={view.auto_arm?.today.strong ?? []}
              onChange={(on, bot) => void save({ arm_strong_on: on, arm_strong_bot: bot })}
            />
            <LastCheck row={view.last?.strong} scan="Trend quality" />
          </div>
          <div className={clsx(box, view.pressure_on ? "border-emerald-400/40 bg-emerald-400/[0.04]" : "border-slate-800")}>
            <label className="flex min-h-9 items-center gap-2 text-sm font-semibold text-slate-100">
              <input type="checkbox" checked={view.pressure_on} disabled={busy} onChange={(e) => void save({ pressure_on: e.target.checked })} className="accent-[#10B981]" />
              Buyers or sellers in control
              <span className={clsx("ml-auto text-xs font-normal", view.pressure_on ? "text-emerald-300" : "text-slate-500")}>{view.pressure_on ? "On" : "Off"}</span>
            </label>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              After each <b>Most active</b> scan: a stock where buyers (or sellers) hold at least this share of the orders waiting in
              the order book. The message gives the price, the share, the day&apos;s move and volume vs usual.
            </p>
            <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-slate-400">
              <label className="flex flex-col gap-1">
                Share at least (type any number, 50-95)
                <span className="flex items-center gap-1">
                  <input
                    key={view.pressure_pct}
                    type="number"
                    min={50}
                    max={95}
                    step={1}
                    defaultValue={view.pressure_pct}
                    disabled={busy}
                    onBlur={(e) => {
                      const v = Math.min(95, Math.max(50, Number(e.target.value) || 70));
                      if (v !== view.pressure_pct) void save({ pressure_pct: v });
                    }}
                    className={clsx(select, "w-20")}
                  />
                  %
                </span>
              </label>
              <label className="flex flex-col gap-1">
                Who
                <select className={select} value={view.pressure_side} disabled={busy} onChange={(e) => void save({ pressure_side: e.target.value as ScalpAlerts["pressure_side"] })}>
                  <option value="BOTH">Buyers or sellers</option>
                  <option value="BUY">Buyers only</option>
                  <option value="SELL">Sellers only</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                Money traded at least (₹ crore, 0 = any)
                <input
                  type="number"
                  min={0}
                  step={10}
                  defaultValue={view.pressure_min_value_cr}
                  disabled={busy}
                  onBlur={(e) => {
                    const v = Math.max(0, Number(e.target.value) || 0);
                    if (v !== view.pressure_min_value_cr) void save({ pressure_min_value_cr: v });
                  }}
                  className={clsx(select, "w-28")}
                />
              </label>
            </div>
            <ArmRow
              label="Most active"
              on={view.arm_pressure_on}
              bot={view.arm_pressure_bot}
              busy={busy}
              today={view.auto_arm?.today.pressure ?? []}
              onChange={(on, bot) => void save({ arm_pressure_on: on, arm_pressure_bot: bot })}
            />
            <LastCheck row={view.last?.pressure} scan="Most active" />
          </div>
          {view.arm_strong_on || view.arm_pressure_on ? (
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400 lg:col-span-2">
              Auto-arm at most
              <input
                key={view.arm_max_day}
                type="number"
                min={1}
                max={50}
                defaultValue={view.arm_max_day}
                disabled={busy}
                onBlur={(e) => {
                  const v = Math.min(50, Math.max(1, Math.round(Number(e.target.value)) || 10));
                  if (v !== view.arm_max_day) void save({ arm_max_day: v });
                }}
                className={clsx(select, "w-16")}
              />
              stocks a day from each scan. A stock already on the bot&apos;s list stays as it is; nothing is ever taken off.
            </div>
          ) : null}
          {view.auto_arm?.recent.length ? (
            <div className="lg:col-span-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Auto-arm since the server started</p>
              <ul className="mt-1 space-y-1 text-xs">
                {view.auto_arm.recent.map((r, i) => (
                  <li key={`${r.at}-${i}`} className="text-slate-300">
                    <span className="tabular-nums text-slate-500">{time(r.at)}</span> {r.source === "strong" ? "Trend quality" : "Most active"} →{" "}
                    {r.bot_name ?? `Bot ${r.bot}`}:{" "}
                    {r.error ? <span className="text-amber-300">{r.error}</span> : <b>{r.armed.join(", ") || "nothing new"}</b>}
                    {r.skipped.length ? <span className="text-slate-500"> · not armed: {r.skipped.map((x) => `${x.symbol} (${x.why})`).join("; ")}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {view.recent.length ? (
            <div className="lg:col-span-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Alerts (kept through restarts)</p>
              <ul className="mt-1 flex flex-wrap gap-1.5 text-xs">
                {view.recent.map((r, i) => (
                  <li key={`${r.at}-${r.symbol}-${i}`} className="rounded-md border border-slate-800 px-2 py-1 text-slate-300">
                    <span className="tabular-nums text-slate-500">{time(r.at)}</span> <b>{r.symbol}</b>{" "}
                    {r.kind === "strong" ? `${r.side === "UP" ? "▲" : "▼"} ${r.passed} of ${r.of} checks` : `${r.side === "BUY" ? "buyers" : "sellers"} ${r.pct?.toFixed(0)}%`}
                    {r.price != null ? ` · ₹${r.price.toFixed(2)}` : ""}
                    {r.failed ? <span className="text-amber-300"> · not sent: {r.failed}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

/** What the last scan in market hours found for this alert and whether Telegram took it (scalp_alerts.last). */
function LastCheck({ row, scan }: { row?: ScalpAlertCheck; scan: string }) {
  if (!row) {
    return (
      <p className="mt-2 text-xs text-slate-500">
        No {scan} scan has finished in market hours on this server since this was added. The alert only reads scans made here while
        NSE is open.
      </p>
    );
  }
  const at = new Date(row.at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const tail = !row.on
    ? "the alert was off, so nothing was sent"
    : row.error
      ? `Telegram did NOT take it: ${row.error}`
      : row.new
        ? `${row.new} new sent to Telegram`
        : row.matched
          ? "all of them already sent earlier today"
          : "nothing to send";
  return (
    <p className={clsx("mt-2 text-xs", row.error ? "font-semibold text-amber-300" : "text-slate-400")}>
      Last {scan} scan {at}: {row.rows} stocks read, {row.matched} met the thresholds · {tail}.
    </p>
  );
}

/** "Also arm them on Bot N" under an alert (backend auto_arm.py). PAPER bots only: a LIVE bot is refused when the scan arrives. */
function ArmRow({
  label,
  on,
  bot,
  busy,
  today,
  onChange,
}: {
  label: string;
  on: boolean;
  bot: number;
  busy: boolean;
  today: string[];
  onChange: (on: boolean, bot: number) => void;
}) {
  return (
    <div className={clsx("mt-3 rounded-md border px-2 py-2 text-xs", on ? "border-sky-400/40 bg-sky-400/[0.05]" : "border-slate-800")}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex min-h-9 items-center gap-2 font-semibold text-slate-200">
          <input type="checkbox" checked={on} disabled={busy} onChange={(e) => onChange(e.target.checked, bot)} className="accent-[#38BDF8]" />
          Also arm them on
        </label>
        <select
          value={bot}
          disabled={busy}
          onChange={(e) => onChange(on, Number(e.target.value))}
          aria-label={`Bot to arm from ${label}`}
          className="h-9 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-200"
        >
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>
              Bot {n}
            </option>
          ))}
        </select>
        <span className={on ? "text-sky-300" : "text-slate-500"}>{on ? "On" : "Off"}</span>
      </div>
      <p className="mt-1 text-slate-400">
        Same thresholds as the alert above (the alert itself can be on or off). After each {label} scan in market hours, each new
        stock goes on that bot&apos;s Trade list, once a day. <b>PAPER bots only</b>: a LIVE bot is never armed by itself.
      </p>
      {on && today.length ? <p className="mt-1 text-slate-300">Armed today: {today.join(", ")}</p> : null}
    </div>
  );
}
