"use client";

import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { api, type NewsAlertRow, type NewsFeedItem, type NewsImpact, type NewsWatchSettings, type NewsWatchStock } from "@/lib/api";
import { toneClass, toneWord, urgentAction } from "@/lib/newsTone";

const EVERY = [5, 10, 15, 20, 30, 60];
const INPUT = "min-h-9 rounded-md border border-white/15 bg-black/30 px-2 text-sm text-slate-100";

/**
 * News alerts (news_impact.py): the desk reads the news of every stock armed or held on Bots 1-4 (plus any
 * typed here), scores each headline positive / negative (AI with your OpenAI key, else a rule list), and alerts
 * with a pop-up and Telegram; a critical headline against a stock you hold also rings the phone-call channel.
 * Information only: nothing is traded from it.
 */
export function NewsAlerts() {
  const [cfg, setCfg] = useState<NewsWatchSettings | null>(null);
  const [extra, setExtra] = useState("");
  const [watch, setWatch] = useState<NewsWatchStock[]>([]);
  const [recent, setRecent] = useState<NewsAlertRow[]>([]);
  const [feed, setFeed] = useState<NewsFeedItem[]>([]);
  const [feedHigh, setFeedHigh] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((fill: boolean) => {
    api
      .newsSettings()
      .then((v) => {
        setCfg(v);
        if (fill) setExtra(v.extra_symbols.join(", "));
      })
      .catch(() => undefined);
    api
      .newsWatch()
      .then((w) => setWatch(w.stocks))
      .catch(() => undefined);
    api
      .newsAlerts(0, 10)
      .then((r) => setRecent(r.alerts))
      .catch(() => undefined);
    api
      .newsFeed(150)
      .then((r) => setFeed(r.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(true);
    const id = setInterval(() => load(false), 30_000);
    return () => clearInterval(id);
  }, [load]);

  const save = async (patch: Partial<NewsWatchSettings>) => {
    setBusy(true);
    try {
      const v = await api.saveNewsSettings(patch);
      setCfg((c) => ({ ...(c ?? v), ...v }));
      setNote("Saved.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const act = async (what: "test" | "run") => {
    setBusy(true);
    setNote(what === "run" ? "Reading the news of every watched stock…" : null);
    try {
      if (what === "test") {
        const r = await api.newsTest();
        setNote(`Test pop-up saved (it shows within 30 s). Telegram: ${r.telegram ? "sent" : r.skipped || "not sent"}.`);
      } else {
        const r = await api.newsRunNow();
        setNote(r.alerts.length ? `${r.alerts.length} new alert(s).` : "Checked: no new news with enough impact.");
      }
      load(false);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  const box = (key: keyof NewsWatchSettings, label: string, title?: string) => (
    <label className="flex min-h-9 items-center gap-2 text-sm text-slate-300" title={title}>
      <input
        type="checkbox"
        checked={Boolean(cfg?.[key])}
        disabled={!cfg || busy}
        onChange={(e) => save({ [key]: e.target.checked } as Partial<NewsWatchSettings>)}
        className="accent-[#10B981]"
      />
      {label}
    </label>
  );

  const st = cfg?.status;
  return (
    <section id="news-alerts" aria-label="News alerts" className="terminal-dark mb-4 scroll-mt-20 rounded-xl border border-white/10 p-4">
      <h2 className="text-base font-semibold text-slate-100">News alerts (pop-up / Telegram / phone call)</h2>
      <p className="mt-1 text-xs text-slate-400">
        Reads the news (NSE filings and Google News) of every stock armed or held on Bots 1–4, plus the ones you add,
        and judges each headline positive or negative{" "}
        {cfg?.ai_key ? "with AI (your OpenAI key)" : "with a rule list (save an OpenAI key under AI Trading Expert for an AI read)"}.
        New headlines with enough impact pop up on screen and go to Telegram with a suggestion (keep, sell / exit, buy,
        avoid). A critical headline against a stock you hold (fraud, raids, bans, defaults, top-management exits, big
        result misses…) also rings the phone-call channel (market hours, one call per 5 minutes). Information only, not
        advice: nothing is traded from it. Checked on trading days 07:30–20:00 IST.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        {box("enabled", "On")}
        {box("popup", "Pop-up")}
        {box("telegram", "Telegram")}
        {box("call_critical", "Phone call for critical news")}
        {box(
          "all_fno",
          "All F&O stocks (NSE filings)",
          "One NSE request a pass reads every company's latest filings. F&O stocks you don't hold or arm pop up for HIGH or critical news only; no AI, no phone call."
        )}
        {cfg?.all_fno ? box("fno_telegram", "Telegram for all-F&O alerts too") : null}
        {box("use_ai", "Use AI", cfg?.ai_key ? undefined : "No OpenAI key saved: the rule list is used")}
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Every
          <select
            value={cfg?.every_min ?? 15}
            disabled={!cfg || busy}
            onChange={(e) => save({ every_min: Number(e.target.value) })}
            className={INPUT}
          >
            {EVERY.map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Alert from
          <select
            value={cfg?.min_impact ?? "MEDIUM"}
            disabled={!cfg || busy}
            onChange={(e) => save({ min_impact: e.target.value as NewsImpact })}
            className={INPUT}
          >
            <option value="LOW">Any impact</option>
            <option value="MEDIUM">Medium impact and up</option>
            <option value="HIGH">High impact only</option>
          </select>
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex min-w-[260px] flex-1 flex-col gap-1 text-xs text-slate-400">
          Also watch (comma-separated NSE symbols)
          <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. RELIANCE, TCS" className={INPUT} />
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            save({ extra_symbols: extra.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean) })
          }
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10 disabled:opacity-50"
        >
          Save list
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => act("run")}
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
        >
          Check the news now
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => act("test")}
          title="A sample pop-up and Telegram message (no phone call)"
          className="min-h-9 rounded-md px-3 text-xs font-semibold text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
        >
          Send a test
        </button>
        {note ? <span className="text-xs text-slate-400">{note}</span> : null}
      </div>
      {st ? (
        <p className="mt-2 text-[11px] text-slate-500">
          Watching {st.watched} stock(s){st.last_run ? ` · last check ${st.last_run.slice(11, 16)}` : ""} · {st.alerts_sent} alert(s) since the
          app started{st.last_error ? ` · last problem: ${st.last_error}` : ""}
          {cfg?.all_fno
            ? ` · all F&O: ${st.fno_last_run ? `${st.fno_items ?? 0} filing(s), read ${st.fno_last_run.slice(11, 16)}` : "not read yet"}${
                st.fno_error ? ` (problem: ${st.fno_error})` : ""
              }`
            : ""}
        </p>
      ) : null}

      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Watched stocks</h3>
          {watch.length ? (
            <ul className="mt-1 divide-y divide-white/5 text-xs">
              {watch.map((w) => (
                <li key={w.symbol} className="flex flex-wrap items-center gap-2 py-1.5">
                  <span className="w-28 font-semibold text-slate-100">{w.symbol}</span>
                  <span className="text-slate-500">
                    {w.held.length ? `held: ${w.held.map((h) => `${h.side} ${h.qty} (${h.name})`).join(", ")}` : w.armed.length ? `armed: ${w.armed.join(", ")}` : "added"}
                  </span>
                  {w.latest && w.latest.count === 0 ? (
                    <span className="text-slate-500">
                      {Object.keys(w.latest.errors ?? {}).length >= 2 ? "news sources not reachable" : "no recent news"}
                    </span>
                  ) : w.latest ? (
                    <>
                      <span className={clsx("rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset", toneClass(w.latest.overall.sentiment))}>
                        {toneWord(w.latest.overall.sentiment)}
                      </span>
                      <span className={clsx("font-semibold", urgentAction(w.latest.action.code) ? "text-rose-300" : "text-slate-300")}>
                        {w.latest.action.label}
                      </span>
                    </>
                  ) : (
                    <span className="text-slate-600">not read yet</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs text-slate-500">No armed, held or added stocks yet.</p>
          )}
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Recent alerts</h3>
          {recent.length ? (
            <ul className="mt-1 divide-y divide-white/5 text-xs">
              {recent.map((a) => (
                <li key={a.id} className="py-1.5">
                  <span className="text-slate-500">{a.created_at?.slice(5, 16).replace("T", " ")}</span>{" "}
                  <span className="font-semibold text-slate-100">{a.symbol}</span>{" "}
                  <span className={clsx("rounded px-1 text-[10px] font-semibold ring-1 ring-inset", toneClass(a.sentiment))}>
                    {a.critical ? "⚠ " : ""}
                    {toneWord(a.sentiment)}
                  </span>{" "}
                  <span className="text-slate-300">{a.title}</span>
                  {a.action_label ? <span className="text-slate-400"> → {a.action_label}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs text-slate-500">No news alerts yet.</p>
          )}
        </div>
      </div>

      {cfg?.all_fno ? (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">All F&amp;O news feed (NSE filings, last 3 days)</h3>
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <input type="checkbox" checked={feedHigh} onChange={(e) => setFeedHigh(e.target.checked)} className="accent-[#10B981]" />
              High impact only
            </label>
          </div>
          {(() => {
            const rows = feed.filter((f) => !feedHigh || f.impact === "HIGH" || f.critical);
            return rows.length ? (
              <ul className="mt-1 max-h-96 divide-y divide-white/5 overflow-y-auto text-xs">
                {rows.map((f) => (
                  <li key={`${f.symbol}|${f.url}|${f.title}`} className="flex flex-wrap items-baseline gap-2 py-1.5">
                    <span className="text-slate-500">{f.at ? f.at.slice(5, 16).replace("T", " ") : "—"}</span>
                    <span className="w-24 font-semibold text-slate-100">
                      {f.symbol}
                      {f.watched ? <span className="ml-1 text-[10px] font-normal text-sky-300">watched</span> : null}
                    </span>
                    <span className={clsx("rounded px-1 text-[10px] font-semibold ring-1 ring-inset", toneClass(f.sentiment))}>
                      {f.critical ? "⚠ " : ""}
                      {toneWord(f.sentiment)} · {f.impact.toLowerCase()}
                    </span>
                    <a href={f.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 text-slate-300 hover:underline">
                      {f.title}
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-slate-500">
                {feed.length ? "Nothing with high impact." : "No filings read yet: the next check fills this (or press Check the news now)."}
              </p>
            );
          })()}
        </div>
      ) : null}
    </section>
  );
}
