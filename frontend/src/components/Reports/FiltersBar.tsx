"use client";

import clsx from "clsx";
import { Download, RotateCcw, Search } from "lucide-react";
import type { ReportFilters } from "@/lib/api";

type Options = {
  symbols: string[];
  sources: string[];
  strategies: string[];
  exit_reasons: string[];
  date_min: string | null;
  date_max: string | null;
};

const PRESETS: { label: string; days: number | null }[] = [
  { label: "Today", days: 0 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: null },
];

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const inputClass =
  "bg-base border border-border rounded-md px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-bot/60";

export function FiltersBar({
  filters,
  options,
  onChange,
  onReset,
  csvUrl,
}: {
  filters: ReportFilters;
  options: Options | null;
  onChange: (next: ReportFilters) => void;
  onReset: () => void;
  csvUrl: string;
}) {
  const set = (patch: Partial<ReportFilters>) => onChange({ ...filters, ...patch });

  const applyPreset = (days: number | null) => {
    if (days === null) set({ date_from: undefined, date_to: undefined });
    else set({ date_from: isoDaysAgo(days), date_to: undefined });
  };

  const activePreset = (days: number | null) => {
    if (days === null) return !filters.date_from && !filters.date_to;
    return filters.date_from === isoDaysAgo(days) && !filters.date_to;
  };

  return (
    <div className="rounded-card border border-border bg-surface p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center rounded-md border border-border overflow-hidden text-[11px]">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => applyPreset(p.days)}
              className={clsx(
                "px-2.5 py-1.5 transition",
                activePreset(p.days) ? "bg-bot/20 text-bot" : "text-slate-400 hover:text-slate-200"
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        <label className="text-[11px] text-slate-500 flex items-center gap-1.5">
          From
          <input
            type="date"
            value={filters.date_from ?? ""}
            min={options?.date_min ?? undefined}
            max={options?.date_max ?? undefined}
            onChange={(e) => set({ date_from: e.target.value || undefined })}
            className={inputClass}
          />
        </label>
        <label className="text-[11px] text-slate-500 flex items-center gap-1.5">
          To
          <input
            type="date"
            value={filters.date_to ?? ""}
            min={options?.date_min ?? undefined}
            max={options?.date_max ?? undefined}
            onChange={(e) => set({ date_to: e.target.value || undefined })}
            className={inputClass}
          />
        </label>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-[11px] text-slate-300 hover:bg-white/5 transition"
          >
            <RotateCcw size={12} /> Reset
          </button>
          <a
            href={csvUrl}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-bot/20 text-bot text-[11px] font-medium hover:bg-bot/30 transition"
          >
            <Download size={12} /> Export CSV
          </a>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={filters.symbols ?? ""}
          onChange={(e) => set({ symbols: e.target.value || undefined })}
          className={inputClass}
        >
          <option value="">All symbols</option>
          {options?.symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <select value={filters.side ?? ""} onChange={(e) => set({ side: e.target.value || undefined })} className={inputClass}>
          <option value="">Both sides</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>

        <select
          value={filters.source ?? ""}
          onChange={(e) => set({ source: e.target.value || undefined })}
          className={inputClass}
        >
          <option value="">Any source</option>
          {options?.sources.map((s) => (
            <option key={s} value={s}>
              {s === "BOT" ? "BOT (automated)" : "MANUAL"}
            </option>
          ))}
        </select>

        <select
          value={filters.status ?? ""}
          onChange={(e) => set({ status: e.target.value || undefined })}
          className={inputClass}
        >
          <option value="">Open + closed</option>
          <option value="CLOSED">Closed only</option>
          <option value="OPEN">Open only</option>
        </select>

        <select
          value={filters.outcome ?? ""}
          onChange={(e) => set({ outcome: e.target.value || undefined })}
          className={inputClass}
        >
          <option value="">Any outcome</option>
          <option value="WIN">Wins</option>
          <option value="LOSS">Losses</option>
          <option value="BREAKEVEN">Breakeven</option>
          <option value="VOID">Voided</option>
        </select>

        {options && options.strategies.length > 1 && (
          <select
            value={filters.strategy ?? ""}
            onChange={(e) => set({ strategy: e.target.value || undefined })}
            className={inputClass}
          >
            <option value="">Any strategy</option>
            {options.strategies.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}

        <div className="relative flex-1 min-w-[160px]">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-600" />
          <input
            type="text"
            placeholder="Search symbol, source, exit reason…"
            value={filters.search ?? ""}
            onChange={(e) => set({ search: e.target.value || undefined })}
            className={`${inputClass} w-full pl-6`}
          />
        </div>
      </div>
    </div>
  );
}
