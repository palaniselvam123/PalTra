"use client";

import clsx from "clsx";
import { BookOpen, CheckCircle2, Circle, Download, ExternalLink, Search, Settings2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { LESSONS, type Lesson } from "@/lib/lessons";

/**
 * Learn: the app's own guides (lib/lessons.ts), read inside the portal. A list of lessons with a topic filter and
 * search, each opening in a reader with the guide embedded, download / new-tab buttons, a link to the setting it
 * explains, and "Mark as done" (kept in this browser). Display only.
 */
const DONE_KEY = "learn.done";
const OPEN_KEY = "learn.open";

function readDone(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DONE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function LearnPage() {
  const [done, setDone] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [topic, setTopic] = useState<string>("All");
  const [openId, setOpenId] = useState<string>(LESSONS[0]?.id ?? "");

  useEffect(() => {
    setDone(readDone());
    try {
      const fromUrl = new URLSearchParams(window.location.search).get("lesson");
      const saved = localStorage.getItem(OPEN_KEY);
      const pick = [fromUrl, saved].find((id) => id && LESSONS.some((l) => l.id === id));
      if (pick) setOpenId(pick);
    } catch {
      /* private window */
    }
  }, []);

  const choose = (id: string) => {
    setOpenId(id);
    try {
      localStorage.setItem(OPEN_KEY, id);
    } catch {
      /* private window */
    }
  };
  const toggleDone = (id: string) => {
    const next = done.includes(id) ? done.filter((d) => d !== id) : [...done, id];
    setDone(next);
    try {
      localStorage.setItem(DONE_KEY, JSON.stringify(next));
    } catch {
      /* private window */
    }
  };

  const topics = useMemo(() => ["All", ...Array.from(new Set(LESSONS.map((l) => l.topic)))], []);
  const shown = LESSONS.filter((l) => {
    if (topic !== "All" && l.topic !== topic) return false;
    const q = query.trim().toLowerCase();
    return !q || `${l.title} ${l.summary} ${l.points.join(" ")}`.toLowerCase().includes(q);
  });
  const lesson = LESSONS.find((l) => l.id === openId) ?? shown[0] ?? null;
  const doneCount = LESSONS.filter((l) => done.includes(l.id)).length;

  return (
    <div className="terminal-dark grid gap-4 lg:grid-cols-[320px_1fr]">
      <aside className="space-y-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
            <BookOpen className="h-5 w-5 text-sky-300" aria-hidden /> Learn
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            Guides to the bots and their settings, with pictures. {doneCount} of {LESSONS.length} done.
          </p>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10" aria-hidden>
            <div className="h-full bg-emerald-400" style={{ width: `${LESSONS.length ? (doneCount / LESSONS.length) * 100 : 0}%` }} />
          </div>
        </div>
        <label className="flex items-center gap-2 rounded-md border border-white/15 bg-black/30 px-2">
          <Search className="h-4 w-4 text-slate-500" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search guides"
            aria-label="Search guides"
            className="h-9 w-full bg-transparent text-sm text-slate-100 outline-none"
          />
        </label>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Topic">
          {topics.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTopic(t)}
              aria-pressed={topic === t}
              className={clsx(
                "min-h-8 rounded-full px-3 text-xs font-semibold ring-1 ring-inset",
                topic === t ? "bg-sky-400/20 text-sky-100 ring-sky-400/50" : "text-slate-300 ring-white/15 hover:bg-white/5"
              )}
            >
              {t}
            </button>
          ))}
        </div>
        <ul className="space-y-2">
          {shown.map((l) => (
            <li key={l.id}>
              <LessonCard lesson={l} active={lesson?.id === l.id} done={done.includes(l.id)} onOpen={() => choose(l.id)} />
            </li>
          ))}
          {shown.length === 0 ? <li className="text-xs text-slate-500">No guide matches.</li> : null}
        </ul>
      </aside>
      {lesson ? <Reader lesson={lesson} done={done.includes(lesson.id)} onDone={() => toggleDone(lesson.id)} /> : null}
    </div>
  );
}

function LessonCard({ lesson, active, done, onOpen }: { lesson: Lesson; active: boolean; done: boolean; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={active ? "true" : undefined}
      className={clsx(
        "w-full rounded-lg border p-3 text-left transition-colors",
        active ? "border-sky-400/50 bg-sky-500/10" : "border-white/10 bg-white/[0.02] hover:bg-white/5"
      )}
    >
      <span className="flex items-start gap-2">
        {done ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-label="Done" />
        ) : (
          <Circle className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-label="Not done" />
        )}
        <span>
          <span className="block text-sm font-semibold text-slate-100">{lesson.title}</span>
          <span className="mt-0.5 block text-[11px] text-slate-400">
            {lesson.topic} · {lesson.pages} pages · {lesson.minutes} min
          </span>
        </span>
      </span>
    </button>
  );
}

function Reader({ lesson, done, onDone }: { lesson: Lesson; done: boolean; onDone: () => void }) {
  return (
    <section aria-label={lesson.title} className="min-w-0 rounded-lg border border-white/10 bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-100">{lesson.title}</h2>
          <p className="mt-1 max-w-3xl text-sm text-slate-300">{lesson.summary}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onDone}
            className={clsx(
              "inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold ring-1 ring-inset",
              done ? "bg-emerald-500/15 text-emerald-200 ring-emerald-400/40" : "text-slate-200 ring-white/15 hover:bg-white/5"
            )}
          >
            <CheckCircle2 className="h-4 w-4" aria-hidden /> {done ? "Done" : "Mark as done"}
          </button>
          <a
            href={lesson.file}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            <ExternalLink className="h-4 w-4" aria-hidden /> Open in a new tab
          </a>
          <a
            href={lesson.file}
            download
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            <Download className="h-4 w-4" aria-hidden /> Download PDF
          </a>
          {lesson.open ? (
            <Link
              href={lesson.open.href}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-sky-500/20 px-3 text-xs font-semibold text-sky-100 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/30"
            >
              <Settings2 className="h-4 w-4" aria-hidden /> {lesson.open.label}
            </Link>
          ) : null}
        </div>
      </div>
      <ul className="mt-3 grid gap-1.5 text-xs text-slate-300 sm:grid-cols-2">
        {lesson.points.map((p) => (
          <li key={p} className="flex gap-2 rounded-md bg-black/20 px-2 py-1.5">
            <span className="text-sky-300" aria-hidden>
              •
            </span>
            {p}
          </li>
        ))}
      </ul>
      <object
        data={`${lesson.file}#view=FitH`}
        type="application/pdf"
        aria-label={`${lesson.title} (PDF)`}
        className="mt-3 h-[78vh] min-h-[520px] w-full rounded-md border border-white/10 bg-white"
      >
        <p className="p-4 text-sm text-slate-700">
          This browser cannot show the PDF here.{" "}
          <a href={lesson.file} target="_blank" rel="noreferrer" className="font-semibold text-sky-700 underline">
            Open it in a new tab
          </a>
          .
        </p>
      </object>
    </section>
  );
}
