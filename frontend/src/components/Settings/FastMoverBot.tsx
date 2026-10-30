"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { ApiError, smaApi, type FastBotRule, type FastBotSettings } from "@/lib/smaApi";

const input = "min-h-9 w-24 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
const SOURCES = [1, 2, 3, 4] as const;
const RULES: { value: FastBotRule; label: string }[] = [
  { value: "MOVE", label: "% move within N minutes" },
  { value: "SPEED", label: "Speed (% per minute)" },
  { value: "EITHER", label: "Either one" },
];

/**
 * Fast-mover hand-off (fast_bot.py): a stock Bots 1-4 hold that runs fast in its trade's favour is cut there and
 * Bot 5 opens the same side at once with its own (bigger) quantity, no entry checks. Bot 5's own stop and exits
 * then run it, and the stock stays on Bot 5 for the day. Turning it on while Bot 5 is LIVE asks first.
 */
export function FastMoverBot() {
  const [view, setView] = useState<FastBotSettings | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [sources, setSources] = useState<number[]>([...SOURCES]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .fastBot()
      .then((v) => {
        setView(v);
        if (fill) {
          setForm({
            on: v.on,
            rule: v.rule,
            pct: String(v.pct),
            minutes: String(v.minutes),
            speed: String(v.speed),
            speed_minutes: String(v.speed_minutes),
            max_day: String(v.max_day),
            min_profit_pct: String(v.min_profit_pct ?? 0.3),
          });
          setSources(v.sources);
        }
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
  const live = view?.target_mode === "LIVE";

  const save = async () => {
    const on = Boolean(form.on);
    let confirm = false;
    if (on && live) {
      confirm = window.confirm(
        `Bot 5 is LIVE. Fast movers will be cut on their bot and Bot 5 will place real Groww orders at once with its own quantity (${view?.target_qty ?? "?"}), without the entry checks. Turn it on?`
      );
      if (!confirm) return;
    }
    setBusy(true);
    try {
      const v = await smaApi.saveFastBot({
        on,
        rule: (form.rule as FastBotRule) || "MOVE",
        pct: Math.min(10, Math.max(0.05, num("pct", 0.5))),
        minutes: Math.min(30, Math.max(1, Math.round(num("minutes", 3)))),
        speed: Math.min(2, Math.max(0.01, num("speed", 0.15))),
        speed_minutes: Math.min(30, Math.max(1, Math.round(num("speed_minutes", 5)))),
        max_day: Math.min(100, Math.max(1, Math.round(num("max_day", 10)))),
        min_profit_pct: (() => {
          const n = Number(form.min_profit_pct);
          return Number.isFinite(n) ? Math.min(10, Math.max(0, n)) : 0.3;
        })(),
        sources,
        confirm_live: confirm,
      });
      setView(v);
      load(true);
      setNote(v.on ? "Saved. Checked every 5 s while NSE is open; Bot 5 must be running." : "Saved: off.");
    } catch (err: unknown) {
      setNote(err instanceof ApiError || err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const rule = (form.rule as FastBotRule) || "MOVE";
  const recent = view?.recent ?? [];
  return (
    <section id="fast-mover-bot" aria-label="Fast movers to Bot 5" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold text-slate-100">
        Fast movers → Bot 5
        {view ? (
          <span
            className={clsx(
              "rounded px-1.5 py-0.5 text-[11px] font-semibold",
              live ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/15 text-emerald-300"
            )}
          >
            Bot 5 {view.target_mode} · qty {view.target_qty ?? "?"} · {view.target_status ?? "not running"}
          </span>
        ) : null}
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        When a stock one of the ticked bots holds moves fast in that trade&apos;s favour, its trade there is closed and Bot 5
        opens the same side at once with Bot 5&apos;s own quantity, stop and exits. No SMA cross, VWAP, RSI or other entry
        check is read. Only a trade already in profit by the set % moves: a losing trade stays on its bot, however fast
        the price bounces. The stock stays on Bot 5 for the rest of the day. Set Bot 5&apos;s quantity in Settings → SMA strategy
        (Bot 5 tab), and press Start on Bot 5. Keep both bots in the same mode: a LIVE Bot 5 never takes a practice
        (PAPER) trade, a LIVE trade is never cut for a PAPER Bot 5, and in LIVE the move waits until Groww shows no shares
        on the old bot. Replays of the ticked bots play it too (Bot 5's size and settings for the rest of that day), so
        you can check it on past days.
      </p>
      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={Boolean(form.on)} onChange={(e) => set("on", e.target.checked)} className="accent-[#10B981]" />
            On
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Fast means
            <select value={rule} onChange={(e) => set("rule", e.target.value)} className={clsx(input, "w-56")}>
              {RULES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="flex flex-col gap-1 text-xs text-slate-400">
            <legend className="mb-1">Take from</legend>
            <div className="flex gap-3">
              {SOURCES.map((n) => (
                <label key={n} className="flex min-h-9 items-center gap-1.5 text-sm text-slate-300">
                  <input
                    type="checkbox"
                    checked={sources.includes(n)}
                    onChange={(e) =>
                      setSources((cur) => (e.target.checked ? [...cur, n].sort() : cur.filter((b) => b !== n)))
                    }
                    className="accent-[#10B981]"
                  />
                  Bot {n}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className={clsx("flex flex-col gap-1 text-xs text-slate-400", rule === "SPEED" && "opacity-50")}>
            Moved its way at least (%)
            <input inputMode="decimal" value={String(form.pct ?? "")} onChange={(e) => set("pct", e.target.value)} className={input} />
          </label>
          <label className={clsx("flex flex-col gap-1 text-xs text-slate-400", rule === "SPEED" && "opacity-50")}>
            Within (minutes)
            <input inputMode="numeric" value={String(form.minutes ?? "")} onChange={(e) => set("minutes", e.target.value)} className={input} />
          </label>
          <label className={clsx("flex flex-col gap-1 text-xs text-slate-400", rule === "MOVE" && "opacity-50")}>
            Speed its way at least (%/min)
            <input inputMode="decimal" value={String(form.speed ?? "")} onChange={(e) => set("speed", e.target.value)} className={input} />
          </label>
          <label className={clsx("flex flex-col gap-1 text-xs text-slate-400", rule === "MOVE" && "opacity-50")}>
            Over the last (minutes)
            <input inputMode="numeric" value={String(form.speed_minutes ?? "")} onChange={(e) => set("speed_minutes", e.target.value)} className={input} />
          </label>
          <label
            className="flex flex-col gap-1 text-xs text-slate-400"
            title="A trade still in loss, or not yet this far in profit, stays on its bot even when the price is moving fast. 0 = any trade."
          >
            Only if the trade is up at least (%)
            <input inputMode="decimal" value={String(form.min_profit_pct ?? "")} onChange={(e) => set("min_profit_pct", e.target.value)} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            At most per day
            <input inputMode="numeric" value={String(form.max_day ?? "")} onChange={(e) => set("max_day", e.target.value)} className={input} />
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
          <h3 className="text-xs font-semibold text-slate-300">
            Today: {view?.moved_today?.length ?? 0} moved
          </h3>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
            {recent.slice(0, 10).map((r, i) => (
              <li key={`${r.at}-${r.symbol}-${i}`}>
                <span className="font-mono">{r.at.slice(11, 16)}</span>{" "}
                <span className={clsx("font-semibold", r.side === "SHORT" ? "text-rose-300" : "text-emerald-300")}>{r.symbol}</span>{" "}
                from Bot {r.from_bot} ({r.why}): {r.result}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
