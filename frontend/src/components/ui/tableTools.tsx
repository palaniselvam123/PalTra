"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import clsx from "clsx";
import { Search, X } from "lucide-react";

/** How long a typed or slid number waits before it is applied (so a refetching filter is not hit on every tick). */
const COMMIT_MS = 350;

/**
 * The toolbar that holds the filter cards above a table. Lays its children out
 * as a grid of equal-width cells on narrow screens and a wrapping flex row on
 * wide ones, so the labels and inputs line up. Pass a `status` for the "No scan
 * yet" / "Scanning…" label; it sits on its own row below the filters so it
 * never fights for space.
 */
export function FilterRow({ children, status }: { children: ReactNode; status?: ReactNode }) {
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-end gap-2.5">{children}</div>
      {status ? <div className="mt-1.5 text-[11px] text-slate-500">{status}</div> : null}
    </div>
  );
}

/** A pill-shaped on/off checkbox. Reads cleaner next to the number cards than a bare checkbox. */
export function Toggle({
  label,
  checked,
  onChange,
  title,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      title={title}
      onClick={() => onChange(!checked)}
      className={clsx(
        "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
        checked
          ? "border-accentSky/40 bg-accentSky/15 text-accentSky"
          : "border-slate-700 bg-base text-slate-300 hover:bg-white/5"
      )}
    >
      <span
        aria-hidden
        className={clsx(
          "inline-block h-3.5 w-3.5 rounded-full border transition-colors",
          checked ? "border-accentSky bg-accentSky" : "border-slate-500"
        )}
      />
      {label}
    </button>
  );
}

/**
 * A number filter you can type into or slide: a text box and a slider that move
 * together. Typing accepts any number (even beyond the slider's ends); an empty
 * or half-typed box applies nothing until it is a number again. `onChange` is
 * called once the value settles, not on every keystroke or slider tick.
 *
 * The card is a fixed width so a row of filters lines up visually: the label
 * sits on top, then the value with its unit (36px tall), and the slider below.
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
  className = "w-40",
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
      <label htmlFor={id} className="mb-1 block whitespace-nowrap text-[11px] font-medium text-slate-400">
        {label}
      </label>
      <div className="flex min-h-9 items-stretch rounded-md border border-slate-700 bg-base focus-within:border-slate-500">
        {prefix ? <span className="flex items-center pl-2 text-xs text-slate-500">{prefix}</span> : null}
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
          className="w-full bg-transparent px-2 py-1 text-sm tabular-nums text-slate-100 outline-none"
        />
        {suffix ? <span className="flex items-center pr-2 text-xs text-slate-500">{suffix}</span> : null}
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={shown}
        onChange={(e) => settle(e.target.value)}
        aria-label={`${label} slider`}
        className="mt-1.5 w-full accent-sky-500"
      />
      {hint ? <p className="mt-0.5 text-[11px] text-slate-600">{hint}</p> : null}
    </div>
  );
}

/** A select wrapped as a filter card so it lines up with the number cards. */
export function SelectFilter<T extends string | number>({
  label,
  value,
  onChange,
  options,
  className = "w-40",
}: {
  label: string;
  value: T;
  onChange: (next: T) => void;
  options: { value: T; label: string }[];
  className?: string;
}) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block whitespace-nowrap text-[11px] font-medium text-slate-400">
        {label}
      </label>
      <select
        id={id}
        value={value as string | number}
        onChange={(e) => {
          const raw = e.target.value;
          const next = (typeof value === "number" ? Number(raw) : raw) as T;
          onChange(next);
        }}
        className="min-h-9 w-full rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
      >
        {options.map((o) => (
          <option key={String(o.value)} value={o.value as string | number}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** A small "find a stock" box for a table. */
export function TextFilter({
  value,
  onChange,
  label = "Find a stock",
  placeholder = "Symbol or name",
  className = "w-44",
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
  placeholder?: string;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block whitespace-nowrap text-[11px] font-medium text-slate-400">
        {label}
      </label>
      <div className="relative">
        <Search size={13} aria-hidden className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          id={id}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          className="min-h-9 w-full rounded-md border border-slate-700 bg-base py-1 pl-7 pr-7 text-sm text-slate-100 outline-none focus:border-slate-500"
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
      </div>
    </div>
  );
}

/** True when every word typed appears in one of the fields (case-insensitive). Blank matches all. */
export function matchesText(query: string, ...fields: (string | null | undefined)[]): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = fields.filter(Boolean).join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}
