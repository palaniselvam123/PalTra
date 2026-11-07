"use client";

import { useCallback, useEffect, useState } from "react";
import { SettingsCard } from "@/components/Settings/SettingsCard";
import { smaApi, type HandoffReturn as State } from "@/lib/smaApi";

const TIMES = ["15:35", "15:45", "16:00", "17:00", "18:00", "20:00"];

/**
 * Send handed-off stocks back (handoff_chain.py): a recovery / V-turn hand-off moves a stock to Bot 2 and a breakout
 * moves it on to Bot 3, so the first bot's Trade list only shrinks. With this on, after the close each handed-off
 * stock goes back to the bot it first came from, to wait for its next cross there. Arming only: no order.
 */
export function HandoffReturn() {
  const [view, setView] = useState<State | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    smaApi
      .handoffReturn()
      .then(setView)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const poll = setInterval(load, 30000);
    return () => clearInterval(poll);
  }, [load]);

  const save = async (on: boolean, at?: string) => {
    setBusy(true);
    setNote(null);
    try {
      setView(await smaApi.handoffReturnSave(on, at));
      setNote(on ? "Saved: handed-off stocks go back after the close." : "Saved: handed-off stocks stay where they were moved.");
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const now = async () => {
    if (!window.confirm("Send every handed-off stock that is not in a trade back to the bot it came from now?")) return;
    setBusy(true);
    try {
      const out = await smaApi.handoffReturnNow();
      setView(out);
      setNote(out.lines.length ? out.lines.join(" · ") : "Nothing to send back.");
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Could not send back");
    } finally {
      setBusy(false);
    }
  };

  const moved = Object.entries(view?.moved ?? {});
  return (
    <SettingsCard
      id="handoff-return"
      title="Hand-offs: send stocks back at the end of the day"
      tier="advanced"
      status={view ? { on: Boolean(view.on) } : null}
      summary="A stock that a bot moved to Bot 2 or Bot 3 during the day goes back to the bot it started on after the close, so the first bot's stock list does not shrink for good. Only the lists change; no order is placed."
      help={
        <>
          A recovery or losing V-turn moves a stock from its bot to Bot 2, and a breakout moves it on to Bot 3. Nothing brings
          it back, so the first bot&apos;s Trade list only shrinks. With this on, after the close every handed-off stock goes
          back to the bot it first came from (Bot 1 → Bot 2 → Bot 3 goes back to Bot 1) and waits for its next cross there.
          A stock still in a trade waits. Only the Trade lists change; no order is placed.
        </>
      }
    >
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-200">
        <label className="flex min-h-9 items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(view?.on)}
            disabled={!view || busy}
            onChange={(e) => void save(e.target.checked, view?.at)}
            className="accent-emerald-400"
          />
          Send handed-off stocks back after the close
        </label>
        <label className="flex items-center gap-1.5">
          at
          <select
            value={view?.at ?? "15:35"}
            disabled={!view || busy}
            onChange={(e) => void save(Boolean(view?.on), e.target.value)}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
          >
            {(TIMES.includes(view?.at ?? "15:35") ? TIMES : [view?.at ?? "15:35", ...TIMES]).map((t) => (
              <option key={t} value={t}>
                {t} IST
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || !moved.length}
          onClick={() => void now()}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-200 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Send back now
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {moved.length
          ? `Handed off now: ${moved.map(([name, r]) => `${name} on Bot ${r.on} (from Bot ${r.origin})`).join(", ")}`
          : "No handed-off stock is waiting to go back."}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        Backtests: the replay bar&apos;s &quot;Play hand-offs&quot; keeps a handed-off stock in the run on Bot 2&apos;s / Bot
        3&apos;s settings; with this switch on, each replayed day starts with every stock back on the replayed bot.
      </p>
      {note ? <p className="mt-2 text-xs text-emerald-300">{note}</p> : null}
    </SettingsCard>
  );
}
