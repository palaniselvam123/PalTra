"use client";

import { useCallback, useEffect, useState } from "react";
import { SettingsCard } from "@/components/Settings/SettingsCard";
import { smaApi, type HandoffReturn as State } from "@/lib/smaApi";
import { NewTag } from "@/components/ui/NewTag";

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

  const save = async (on: boolean, at?: string, mode?: State["mode"]) => {
    setBusy(true);
    setNote(null);
    try {
      const next = await smaApi.handoffReturnSave(on, at, mode ?? view?.mode);
      setView(next);
      setNote(
        !on
          ? "Saved: handed-off stocks stay where they were moved."
          : next.mode === "BACK"
            ? "Saved: handed-off stocks go back to the bot they came from after the close."
            : "Saved: handed-off stocks are unarmed from Bot 2 / Bot 3 after the close."
      );
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const now = async () => {
    const what = view?.mode === "BACK" ? "send it back to the bot it came from" : "unarm it from the bot it was handed to";
    if (!window.confirm(`For every handed-off stock that is not in a trade: ${what} now?`)) return;
    setBusy(true);
    try {
      const out = await smaApi.handoffReturnNow();
      setView(out);
      setNote(out.lines.length ? out.lines.join(" · ") : "Nothing to do.");
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
      title="Hand-offs: at the end of the day"
      tier="advanced"
      status={view ? { on: Boolean(view.on) } : null}
      summary="A stock that a bot moved to Bot 2 or Bot 3 during the day is unarmed after the close (or, if you pick it, sent back to the bot it started on). Only the lists change; no order is placed."
      help={
        <>
          A recovery or losing V-turn moves a stock from its bot to Bot 2, and a breakout moves it on to Bot 3. Nothing brings
          it back, so the first bot&apos;s Trade list only shrinks. With this on, after the close every handed-off stock goes
          back to the bot it first came from (Bot 1 → Bot 2 → Bot 3 goes back to Bot 1) and waits for its next cross there,
          or, with &quot;Unarm them&quot;, is taken off Bot 2 / Bot 3 and put on no bot, so the next day starts only with what
          is armed fresh. A stock still in a trade waits. Only the Trade lists change; no order is placed.
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
          After the close
        </label>
        <label className="flex items-center gap-1.5">
          <select
            value={view?.mode ?? "UNARM"}
            disabled={!view || busy}
            onChange={(e) => void save(Boolean(view?.on), view?.at, e.target.value as State["mode"])}
            className="min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100"
            aria-label="What happens to handed-off stocks"
          >
            <option value="UNARM">unarm them from Bot 2 / Bot 3</option>
            <option value="BACK">send them back to the bot they came from</option>
          </select>
          <NewTag id="handoff-eod-unarm" />
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
          {view?.mode === "BACK" ? "Send back now" : "Unarm now"}
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {moved.length
          ? `Handed off now: ${moved.map(([name, r]) => `${name} on Bot ${r.on} (from Bot ${r.origin})`).join(", ")}`
          : "No handed-off stock is waiting."}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        Backtests (new runs): a stock that ends a replayed day on Bot 2 / Bot 3 is unarmed too, so it is not traded on the
        following days (an auto-arm or pick run can arm it again from that day&apos;s own scans). Only with this switch on and
        &quot;send them back&quot; does each replayed day start with it back on the replayed bot.
      </p>
      {note ? <p className="mt-2 text-xs text-emerald-300">{note}</p> : null}
    </SettingsCard>
  );
}
