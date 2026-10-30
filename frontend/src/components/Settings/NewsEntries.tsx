"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { smaApi, type BotSummary, type NewsEntrySettings } from "@/lib/smaApi";

const IMPACTS: { value: NewsEntrySettings["impact"]; label: string }[] = [
  { value: "HIGH", label: "High impact only" },
  { value: "MEDIUM", label: "Medium and high" },
  { value: "LOW", label: "Low, medium and high" },
];

/**
 * News entries (news_entry.py): the picked bot enters at once on a strong, fresh headline (good news buys,
 * bad news shorts) with no SMA cross, candle pattern or entry filter. Its own stop, target and exits then
 * manage the trade. PAPER and LIVE; a LIVE bot asks first.
 */
export function NewsEntries() {
  const [view, setView] = useState<NewsEntrySettings | null>(null);
  const [bots, setBots] = useState<BotSummary[]>([]);
  const [bot, setBot] = useState(0);
  const [impact, setImpact] = useState<NewsEntrySettings["impact"]>("HIGH");
  const [scope, setScope] = useState<NewsEntrySettings["scope"]>("FNO");
  const [age, setAge] = useState("15");
  const [perDay, setPerDay] = useState("5");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((fill: boolean) => {
    smaApi
      .newsEntry()
      .then((v) => {
        setView(v);
        if (fill) {
          setBot(v.bot);
          setImpact(v.impact);
          setScope(v.scope);
          setAge(String(v.max_age_min));
          setPerDay(String(v.max_day));
        }
      })
      .catch(() => undefined);
    smaApi.bots().then(setBots).catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const poll = setInterval(() => load(false), 15000);
    return () => clearInterval(poll);
  }, [load]);

  const picked = bots.find((b) => b.bot === bot);
  const live = picked?.mode === "LIVE";

  const save = async () => {
    const body: NewsEntrySettings = {
      bot,
      impact,
      scope,
      max_age_min: Math.min(240, Math.max(1, Math.round(Number(age) || 15))),
      max_day: Math.min(50, Math.max(1, Math.round(Number(perDay) || 5))),
    };
    if (
      bot &&
      live &&
      !window.confirm(
        `${picked?.name ?? `Bot ${bot}`} is LIVE. News entries will place REAL Groww orders the moment a headline qualifies, ` +
          `with no SMA cross or pattern check${scope === "FNO" ? ", on any F&O stock" : ""}. Turn on?`
      )
    )
      return;
    setBusy(true);
    try {
      const v = await smaApi.saveNewsEntry(body, live);
      setView(v);
      setAge(String(v.max_age_min));
      setPerDay(String(v.max_day));
      setNote(v.bot ? `Saved. Bot ${v.bot} (${v.mode ?? "PAPER"}) takes news entries while it is running.` : "Saved: off.");
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const field = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";
  const recent = view?.recent ?? [];
  return (
    <section id="news-entries" aria-label="News entries" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="text-base font-semibold text-slate-100">
        News entries: trade at once on a news alert
        {view?.bot ? (
          <span
            className={clsx(
              "ml-2 rounded px-1.5 py-0.5 text-[11px] font-semibold",
              view.mode === "LIVE" ? "bg-rose-500/20 text-rose-200" : "bg-emerald-500/15 text-emerald-200"
            )}
          >
            Bot {view.bot} · {view.mode ?? "PAPER"}
          </span>
        ) : null}
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        When a fresh headline qualifies, the bot you pick orders straight away: good news buys, bad news shorts. No SMA cross,
        candle pattern or entry filter is checked. From then on that bot&apos;s own settings run the trade: its stop loss /
        trailing stop / target, the SMA cross or candle-colour exit, the other exits and the square-off. Market hours, the
        entry cut-off, the bot&apos;s trade cap and loss limit still apply, and only a running bot trades. NSE filings for every
        company are read once a minute (09:15–15:30); Google News headlines of armed / held stocks on the News alerts interval.
        Each headline trades once. Headlines are scored by rules (or AI when on) and can be misread, so try it on a PAPER bot
        first.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Bot
          <select value={bot} onChange={(e) => setBot(Number(e.target.value))} className={field}>
            <option value={0}>Off</option>
            {[1, 2, 3, 4, 5].map((n) => {
              const b = bots.find((x) => x.bot === n);
              return (
                <option key={n} value={n}>
                  {b ? `${b.name} (${b.mode})` : `Bot ${n}`}
                </option>
              );
            })}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          News strength
          <select value={impact} onChange={(e) => setImpact(e.target.value as NewsEntrySettings["impact"])} className={field}>
            {IMPACTS.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Stocks
          <select value={scope} onChange={(e) => setScope(e.target.value === "ARMED" ? "ARMED" : "FNO")} className={field}>
            <option value="FNO">Any F&amp;O stock (armed as &quot;News&quot;)</option>
            <option value="ARMED">Only stocks armed on that bot</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Headline at most (min old)
          <input inputMode="numeric" value={age} onChange={(e) => setAge(e.target.value)} className={clsx(field, "w-24")} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          News trades a day
          <input inputMode="numeric" value={perDay} onChange={(e) => setPerDay(e.target.value)} className={clsx(field, "w-20")} />
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
      {bot && live ? (
        <p role="alert" className="mt-2 text-xs text-rose-300">
          {picked?.name ?? `Bot ${bot}`} is LIVE: news entries place real Groww orders.
        </p>
      ) : null}
      {recent.length ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-2 font-medium">Time</th>
                <th className="py-1 pr-2 font-medium">Stock</th>
                <th className="py-1 pr-2 font-medium">Side</th>
                <th className="py-1 pr-2 font-medium">Headline</th>
                <th className="py-1 font-medium">What happened</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r, i) => (
                <tr key={`${r.at}-${r.symbol}-${i}`} className="border-t border-white/5 text-slate-300">
                  <td className="py-1 pr-2 font-mono">{r.at.slice(11, 16)}</td>
                  <td className="py-1 pr-2 font-semibold">{r.symbol}</td>
                  <td className={clsx("py-1 pr-2 font-semibold", r.side === "BUY" ? "text-emerald-300" : "text-rose-300")}>{r.side}</td>
                  <td className="max-w-[340px] truncate py-1 pr-2" title={r.title}>
                    {r.url ? (
                      <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline">
                        {r.title}
                      </a>
                    ) : (
                      r.title
                    )}
                  </td>
                  <td className={clsx("py-1", r.result.startsWith("not traded") ? "text-slate-400" : "text-sky-200")}>{r.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
