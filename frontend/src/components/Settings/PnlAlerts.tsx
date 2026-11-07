"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { SettingsCard } from "@/components/Settings/SettingsCard";
import { inr, smaApi, type PnlAlertSettings } from "@/lib/smaApi";

/**
 * All-bots P&L alerts (pnl_alerts.py): a Telegram message and / or a phone call when today's P&L of
 * bots 1-4 together reaches the profit level or falls to minus the loss level. Once per level per day.
 * Alerts only: nothing is squared off (the all-bots day target / stop loss above does that).
 */
export function PnlAlerts() {
  const [view, setView] = useState<PnlAlertSettings | null>(null);
  const [profit, setProfit] = useState("");
  const [loss, setLoss] = useState("");
  const [telegram, setTelegram] = useState(true);
  const [call, setCall] = useState(true);
  const [basis, setBasis] = useState<"GROSS" | "NET">("GROSS");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .pnlAlerts()
      .then((v) => {
        setView(v);
        if (fill) {
          setProfit(String(v.profit || 0));
          setLoss(String(v.loss || 0));
          setTelegram(v.telegram);
          setCall(v.call);
          setBasis(v.basis);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), 10000);
    return () => clearInterval(poll);
  }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      const p = Math.max(0, Number(profit) || 0);
      const l = Math.max(0, Number(loss) || 0);
      const v = await smaApi.savePnlAlerts({ profit: p, loss: l, telegram, call, basis });
      setView(v);
      setProfit(String(v.profit));
      setLoss(String(v.loss));
      setNote(
        !(p || l)
          ? "Saved: off."
          : !(telegram || call)
            ? "Saved, but tick Telegram or Phone call or nothing is sent."
            : "Saved. Checked every 5 s while NSE is open; each level alerts once a day at its value, and a level you change alerts again."
      );
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    try {
      const out = await smaApi.testPnlAlerts();
      setNote(Object.entries(out).map(([k, v]) => `${k === "call" ? "Call" : "Telegram"}: ${v}`).join(" · "));
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Test failed");
    } finally {
      setBusy(false);
    }
  };

  const total = view?.total ?? 0;
  const sent = Object.entries(view?.sent ?? {});
  return (
    <SettingsCard
      id="pnl-alerts"
      title="All bots: P&L alerts (Telegram / phone call)"
      tier="start"
      status={view ? { on: Number(view.profit) > 0 || Number(view.loss) > 0 } : null}
      summary="A Telegram message or a phone call when today's total profit or loss across the bots reaches a level you set. Alerts only: nothing is closed. Change a level and it alerts again at the new level."
      help={
        <>
        Tells you when today&apos;s P&amp;L of Bots 1–4 together (closed trades plus open positions, each bot&apos;s own PAPER or LIVE
        book) reaches the profit level or falls to minus the loss level. Each level alerts once a day at its value, while NSE is open; change a level and the new one alerts again when the total reaches it.
        Alerts only: nothing is squared off (use the day target / stop loss above for that). Telegram uses your trade alert
        channel; the call uses the phone-call channel (market hours, at most one call every 5 minutes). 0 = off.
        </>
      }
    >
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Alert at profit (₹)
          <input
            inputMode="decimal"
            value={profit}
            onChange={(e) => setProfit(e.target.value)}
            className="min-h-9 w-36 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Alert at loss (₹)
          <input
            inputMode="decimal"
            value={loss}
            onChange={(e) => setLoss(e.target.value)}
            className="min-h-9 w-36 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          P&amp;L
          <select
            value={basis}
            onChange={(e) => setBasis(e.target.value === "NET" ? "NET" : "GROSS")}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          >
            <option value="GROSS">Before charges (as on screen)</option>
            <option value="NET">After charges (net)</option>
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={telegram} onChange={(e) => setTelegram(e.target.checked)} className="accent-[#10B981]" />
          Telegram
        </label>
        <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={call} onChange={(e) => setCall(e.target.checked)} className="accent-[#10B981]" />
          Phone call
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={save}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={test}
          title="Sends a test message and rings now, using the saved Telegram / Phone call ticks"
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
        >
          Send a test
        </button>
        {view ? (
          <span className="text-sm text-slate-300">
            Today, all bots:{" "}
            <span className={clsx("font-mono font-semibold", total > 0 ? "text-emerald-300" : total < 0 ? "text-rose-300" : "text-slate-200")}>
              {inr(total)}
            </span>
          </span>
        ) : null}
        {note ? <span className="text-xs text-slate-400">{note}</span> : null}
      </div>
      {sent.length ? (
        <p role="status" className="mt-2 text-xs text-slate-300">
          Alerted today:{" "}
          {sent
            .map(([kind, s]) => `${kind === "profit" ? "profit" : "loss"} level at ${s.at.slice(11, 16)} (P&L ${inr(s.total)})`)
            .join("; ")}
        </p>
      ) : null}
    </SettingsCard>
  );
}
