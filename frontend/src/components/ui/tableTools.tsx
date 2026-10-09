"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Search, X } from "lucide-react";

/** How long a typed or slid number waits before it is applied (so a refetching filter is not hit on every tick). */
const COMMIT_MS = 350;

/**
 * A number filter you can type into or slide: a text box and a slider that move
 * together. Typing accepts any number (even beyond the slider's ends); an empty
 * or half-typed box applies nothing until it is a number again. `onChange` is
 * called once the value settles, not on every keystroke or slider tick.
 */
export function NumberFilter({
  label,
  value,
  onChange,
  min,
  max,
  step,
  prefix = "",
  suffix = "",
  hint,
  className = "w-44",
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  prefix?: string;
  suffix?: string;
  hint?: string;
  className?: string;
}) {
  const id = useId();
  const [draft, setDraft] = useState(String(value));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const last = useRef(value);

  // Follow the value when it is changed from outside (a reset, a saved filter).
  useEffect(() => {
    if (value !== last.current) {
      last.current = value;
      setDraft(String(value));
    }
  }, [value]);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const settle = (text: string) => {
    setDraft(text);
    const n = Number(text);
    if (text.trim() === "" || !Number.isFinite(n)) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      last.current = n;
      onChange(n);
    }, COMMIT_MS);
  };

  const n = Number(draft);
  const shown = Number.isFinite(n) && draft.trim() !== "" ? Math.min(max, Math.max(min, n)) : value;
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-xs text-slate-400">
        {label}
      </label>
      <div className="flex items-center gap-1.5">
        {prefix ? <span className="text-xs text-slate-500">{prefix}</span> : null}
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={draft}
          onChange={(e) => settle(e.target.value)}
          onBlur={() => {
            // Apply what was typed at once; an empty or invalid box goes back to the applied value.
            const typed = Number(draft);
            if (draft.trim() !== "" && Number.isFinite(typed)) {
              if (timer.current) clearTimeout(timer.current);
              last.current = typed;
              onChange(typed);
            } else {
              setDraft(String(last.current));
            }
          }}
          className="min-h-9 w-20 rounded border border-slate-700 bg-base px-1.5 py-1 text-xs tabular-nums text-slate-200"
        />
        {suffix ? <span className="text-xs text-slate-500">{suffix}</span> : null}
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={shown}
        onChange={(e) => settle(e.target.value)}
        aria-label={`${label} slider`}
        className="mt-1 w-full accent-sky-500"
      />
      {hint ? <p className="mt-0.5 text-[11px] text-slate-600">{hint}</p> : null}
    </div>
  );
}

/** A small "find a stock" box for a table. */
export function TextFilter({
  value,
  onChange,
  label = "Find a stock",
  placeholder = "Symbol or name",
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      <span className="relative">
        <Search size={13} aria-hidden className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          className="min-h-9 w-40 rounded border border-slate-700 bg-base py-1 pl-7 pr-7 text-xs text-slate-200"
        />
        {value ? (
          <button
            type="button"
            onClick={() => onChange("")}
            aria-label="Clear the stock filter"
            className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-slate-100"
          >
            <X size={12} />
          </button>
        ) : null}
      </span>
    </label>
  );
}

/** True when every word typed appears in one of the fields (case-insensitive). Blank matches all. */
export function matchesText(query: string, ...fields: (string | null | undefined)[]): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = fields.filter(Boolean).join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}
