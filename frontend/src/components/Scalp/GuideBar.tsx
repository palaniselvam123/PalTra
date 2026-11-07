"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { BookOpen, HelpCircle, Search, X } from "lucide-react";
import clsx from "clsx";
import { allPlainWords, plainWords, type PlainWord } from "@/lib/plainWords";

/**
 * The row under a Scalp tab's heading for a beginner: "Guide (PDF)" opens that tab's lesson on the Learn page, and
 * "What do these words mean?" opens the plain-words panel with the terms the tab uses (lib/plainWords.ts). Display only.
 */
export function GuideBar({
  lesson,
  terms,
  className,
  note,
}: {
  /** The lesson id on the Learn page (lib/lessons.ts). */
  lesson?: string;
  /** Keys into PLAIN_WORDS, in the order to show them. */
  terms: readonly string[];
  className?: string;
  /** One short line for a beginner, shown before the buttons. */
  note?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={clsx("flex flex-wrap items-center gap-2 text-xs", className)}>
      {note ? <span className="text-slate-400">{note}</span> : null}
      {lesson ? (
        <Link
          href={`/learn/?lesson=${encodeURIComponent(lesson)}`}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2.5 font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
        >
          <BookOpen size={13} aria-hidden /> Guide (PDF)
        </Link>
      ) : null}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2.5 font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10"
      >
        <HelpCircle size={13} aria-hidden /> What do these words mean?
      </button>
      {open ? <PlainWordsPanel terms={terms} onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

/** The plain-words panel: the tab's own terms first, every term on request, with a search box. */
export function PlainWordsPanel({ terms, onClose }: { terms: readonly string[]; onClose: () => void }) {
  const [all, setAll] = useState(false);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const words = useMemo(() => {
    const base: PlainWord[] = all ? allPlainWords() : plainWords(terms);
    const q = query.trim().toLowerCase();
    if (!q) return base;
    return base.filter((w) => `${w.term} ${w.means} ${w.example ?? ""} ${w.why ?? ""}`.toLowerCase().includes(q));
  }, [all, terms, query]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="What these words mean" className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-surface shadow-xl">
        <div className="flex items-center gap-2 border-b border-white/10 p-3">
          <HelpCircle size={16} className="text-amber-300" aria-hidden />
          <h2 className="text-sm font-semibold text-slate-100">What these words mean</h2>
          <span className="text-xs text-slate-400">plain words, one example each</span>
          <button type="button" aria-label="Close" onClick={onClose} className="ml-auto rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-3 py-2 text-xs">
          <label className="relative flex-1">
            <Search size={13} aria-hidden className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a word…"
              aria-label="Search the words"
              className="min-h-8 w-full rounded-md border border-slate-700 bg-base py-1 pl-7 pr-2 text-sm text-slate-100"
            />
          </label>
          <button
            type="button"
            onClick={() => setAll((v) => !v)}
            aria-pressed={all}
            className={clsx(
              "min-h-8 rounded-md px-2.5 font-semibold ring-1 ring-inset",
              all ? "bg-sky-500/15 text-sky-300 ring-sky-400/40" : "text-slate-300 ring-slate-700 hover:bg-white/5"
            )}
          >
            {all ? "Only this tab's words" : "All words"}
          </button>
        </div>
        <div className="overflow-y-auto p-3">
          {words.length === 0 ? <p className="text-sm text-slate-400">No word matches.</p> : null}
          <dl className="space-y-3">
            {words.map((w) => (
              <div key={w.term} className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                <dt className="text-sm font-semibold text-slate-100">{w.term}</dt>
                <dd className="mt-1 text-sm text-slate-300">{w.means}</dd>
                {w.example ? (
                  <dd className="mt-1 rounded bg-sky-500/10 px-2 py-1 text-xs text-sky-200">
                    <span className="font-semibold">Example: </span>
                    {w.example}
                  </dd>
                ) : null}
                {w.why ? (
                  <dd className="mt-1 text-xs text-slate-400">
                    <span className="font-semibold text-slate-300">Why it matters: </span>
                    {w.why}
                  </dd>
                ) : null}
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  );
}
