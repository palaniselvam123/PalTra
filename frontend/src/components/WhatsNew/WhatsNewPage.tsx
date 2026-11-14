"use client";

import clsx from "clsx";
import { ExternalLink, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AREAS, FEATURES, NEW_DAY_CHOICES, PR_URL, isNew, type Feature, type FeatureArea } from "@/lib/features";
import { markSeen, useNewDays, useToday } from "@/components/WhatsNew/useWhatsNew";

/**
 * What's new: every feature of Trend quality, Most active, the SMA terminal and Settings (lib/features.ts), newest
 * first, with the day it was added, a link to it, what it is for and its pull request. "New" = added in the last N
 * days (picked here, kept in this browser); the same rule marks it New next to its control in the app. Opening this
 * page marks the new ones as seen (the menu's count). Display only.
 */
const AREA_LABEL: Record<FeatureArea, string> = Object.fromEntries(AREAS.map((a) => [a.id, a.label])) as Record<FeatureArea, string>;

const AREA_TONE: Record<FeatureArea, string> = {
  trend: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  active: "bg-amber-500/15 text-amber-300 ring-amber-400/30",
  terminal: "bg-sky-500/15 text-sky-300 ring-sky-400/30",
  settings: "bg-violet-500/15 text-violet-300 ring-violet-400/30",
};

function longDay(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function matches(f: Feature, q: string): boolean {
  if (!q) return true;
  const text = `${f.title} ${f.purpose} ${f.where} ${AREA_LABEL[f.area]} ${f.pr ? `#${f.pr}` : ""}`.toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => text.includes(word));
}

export function WhatsNewPage() {
  const [days, setDays] = useNewDays();
  const today = useToday();
  const [area, setArea] = useState<FeatureArea | "all">("all");
  const [onlyNew, setOnlyNew] = useState(false);
  const [query, setQuery] = useState("");
  const [lit, setLit] = useState<string | null>(null);
  const seenOnce = useRef(false);

  const fresh = (f: Feature) => (today ? isNew(f, days, today) : false);
  const newOnes = useMemo(() => (today ? FEATURES.filter((f) => isNew(f, days, today)) : []), [today, days]);

  // Opening the page marks the new ones as seen (the menu's count goes back to 0).
  useEffect(() => {
    if (!today || seenOnce.current) return;
    seenOnce.current = true;
    markSeen(newOnes.map((f) => f.id));
  }, [today, newOnes]);

  // A link to one feature (#f-<id>, from a "New" tag) shows it with everything else, lit up.
  useEffect(() => {
    const id = window.location.hash.replace(/^#f-/, "");
    if (!id || !FEATURES.some((f) => f.id === id)) return;
    setLit(id);
    requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById(`f-${id}`)?.scrollIntoView({ block: "center" })));
    const t = setTimeout(() => setLit(null), 4000);
    return () => clearTimeout(t);
  }, []);

  const shown = useMemo(
    () =>
      FEATURES.filter((f) => (area === "all" || f.area === area) && (!onlyNew || fresh(f)) && matches(f, query.trim())).sort(
        (a, b) => b.added.localeCompare(a.added) || (b.pr ?? 0) - (a.pr ?? 0)
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [area, onlyNew, query, today, days]
  );
  const byDay = useMemo(() => {
    const out: [string, Feature[]][] = [];
    for (const f of shown) {
      const last = out[out.length - 1];
      if (last && last[0] === f.added) last[1].push(f);
      else out.push([f.added, [f]]);
    }
    return out;
  }, [shown]);

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
          <Sparkles size={20} className="text-amber-300" aria-hidden /> What&apos;s new
        </h1>
        <p className="max-w-3xl text-sm text-slate-400">
          Every feature of Trend quality, Most active, the SMA terminal and Settings, newest first: when it was added, what
          it is for, and a link straight to it. A feature added in the last {days} day{days === 1 ? "" : "s"} is marked{" "}
          <span className="rounded-full bg-[#f59e0b] px-1.5 text-[10px] font-bold uppercase text-[#1a1203]">New</span> here and
          next to its setting in the app.
        </p>
      </header>

      <section aria-label="Features by area" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {AREAS.map((a) => {
          const all = FEATURES.filter((f) => f.area === a.id);
          const recent = all.filter(fresh).length;
          const on = area === a.id;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => setArea(on ? "all" : a.id)}
              aria-pressed={on}
              className={clsx(
                "rounded-xl border p-3 text-left transition-colors",
                on ? "border-accentSky/60 bg-accentSky/10" : "border-border bg-surface hover:bg-white/[0.04]"
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-100">{a.label}</span>
                {recent ? (
                  <span className="rounded-full bg-[#f59e0b] px-1.5 text-[10px] font-bold uppercase leading-4 text-[#1a1203]">{recent} new</span>
                ) : null}
              </span>
              <span className="mt-1 block text-xs text-slate-400">{a.hint}</span>
              <span className="mt-1 block text-xs text-slate-500">{all.length} features</span>
            </button>
          );
        })}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-0 flex-1 sm:max-w-sm">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search features (e.g. stop, colour, alert, #419)"
            aria-label="Search features"
            className="min-h-9 w-full rounded-md border border-border bg-surface pl-8 pr-2 text-sm text-slate-100"
          />
        </label>
        <div role="group" aria-label="Area" className="inline-flex flex-wrap overflow-hidden rounded-md ring-1 ring-inset ring-border">
          {(["all", ...AREAS.map((a) => a.id)] as const).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setArea(id)}
              aria-pressed={area === id}
              className={clsx(
                "min-h-9 px-3 text-xs font-semibold",
                area === id ? "bg-accentSky/15 text-accentSky" : "text-slate-300 hover:bg-white/5"
              )}
            >
              {id === "all" ? "All" : AREA_LABEL[id]}
            </button>
          ))}
        </div>
        <label className="flex min-h-9 items-center gap-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} className="accent-amber-500" />
          New only
        </label>
        <label className="flex items-center gap-1.5 text-xs text-slate-300">
          New = added in the last
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="min-h-9 rounded-md border border-border bg-surface px-2 text-sm text-slate-100"
          >
            {NEW_DAY_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n} day{n === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <span className="text-xs text-slate-500" role="status">
          {shown.length} of {FEATURES.length} features · {newOnes.length} new
        </span>
      </div>

      {byDay.length === 0 ? (
        <p className="rounded-xl border border-border bg-surface p-4 text-sm text-slate-400">
          {onlyNew ? `Nothing added in the last ${days} day${days === 1 ? "" : "s"}${query ? " that matches" : ""}.` : "No feature matches."}
        </p>
      ) : (
        <ol className="space-y-5">
          {byDay.map(([day, items]) => (
            <li key={day}>
              <h2 className="sticky top-0 z-[1] -mx-1 mb-2 flex items-baseline gap-2 bg-base/95 px-1 py-1 text-sm font-semibold text-slate-200 backdrop-blur">
                {longDay(day)}
                <span className="text-xs font-normal text-slate-500">
                  {items.length} feature{items.length === 1 ? "" : "s"}
                </span>
              </h2>
              <ul className="grid gap-2 lg:grid-cols-2">
                {items.map((f) => (
                  <li
                    key={f.id}
                    id={`f-${f.id}`}
                    className={clsx(
                      "scroll-mt-24 rounded-xl border bg-surface p-3 transition-shadow",
                      lit === f.id ? "border-amber-400 shadow-[0_0_0_3px_rgba(245,158,11,0.35)]" : "border-border"
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      {fresh(f) ? (
                        <span className="rounded-full bg-[#f59e0b] px-1.5 text-[10px] font-bold uppercase leading-4 text-[#1a1203]">New</span>
                      ) : null}
                      <span className={clsx("rounded-full px-1.5 text-[10px] font-semibold leading-4 ring-1 ring-inset", AREA_TONE[f.area])}>
                        {AREA_LABEL[f.area]}
                      </span>
                      <Link href={f.href} prefetch={false} className="text-sm font-semibold text-slate-100 underline-offset-2 hover:underline">
                        {f.title}
                      </Link>
                    </div>
                    <p className="mt-1 text-sm text-slate-300">{f.purpose}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                      <Link href={f.href} prefetch={false} className="text-accentSky underline-offset-2 hover:underline">
                        Open: {f.where}
                      </Link>
                      <span>Added {longDay(f.added)}</span>
                      {f.pr ? (
                        <a
                          href={`${PR_URL}${f.pr}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 underline-offset-2 hover:underline"
                          title="The change on GitHub"
                        >
                          PR #{f.pr} <ExternalLink size={11} aria-hidden />
                        </a>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
