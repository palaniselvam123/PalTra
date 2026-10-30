"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { RotateCcw, Settings2, X } from "lucide-react";
import {
  LINE_NAMES,
  LINE_PATTERNS,
  LINE_WIDTHS,
  type ChartStyle,
  type LineKey,
  type LineLook,
  type Pattern,
  type Show,
  type Width,
} from "@/lib/chartStyle";

const SELECT =
  "min-h-9 rounded-md border border-white/15 bg-black/30 px-1.5 text-xs text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400";
const COLOR = "h-8 w-9 cursor-pointer rounded border border-white/15 bg-transparent";

/** A preview of a line in its colour, thickness and pattern. */
function Swatch({ look, color }: { look: LineLook; color: string }) {
  const dash = look.style === 0 ? "solid" : look.style === 1 || look.style === 4 ? "dotted" : "dashed";
  return <span aria-hidden className="block w-6" style={{ borderTop: `${look.width + 1}px ${dash} ${color}` }} />;
}

/** Auto / Show / Hide as one switch, like Groww's indicator toggles. */
function ShowSwitch({ name, value, auto, onChange }: { name: string; value: Show; auto: string | null; onChange: (next: Show) => void }) {
  const choices: { value: Show; label: string; title: string }[] = [
    ...(auto ? [{ value: "auto" as Show, label: "Auto", title: `Drawn ${auto}` }] : []),
    { value: "on", label: "Show", title: "Always drawn" },
    { value: "off", label: "Hide", title: "Never drawn" },
  ];
  return (
    <div role="radiogroup" aria-label={`${name}: show`} className="flex shrink-0 rounded-md bg-black/30 p-0.5 ring-1 ring-inset ring-white/10">
      {choices.map((c) => (
        <button
          key={c.value}
          type="button"
          role="radio"
          aria-checked={value === c.value}
          title={c.title}
          onClick={() => onChange(c.value)}
          className={clsx(
            "min-h-8 rounded px-2 text-xs font-semibold",
            value === c.value ? "bg-sky-400/20 text-sky-100 ring-1 ring-inset ring-sky-400/40" : "text-slate-400 hover:text-slate-100"
          )}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Indicator settings, like Groww's: a switch per line, with its colour, thickness and
 * pattern behind the gear. Floats over the chart (a bottom sheet on phones) so the
 * candles and their dates stay where they are. Display only; saved in this browser.
 */
export function ChartIndicators({
  style,
  onChange,
  onReset,
  onClose,
  stAvailable,
  floating = true,
}: {
  style: ChartStyle;
  onChange: (next: ChartStyle) => void;
  onReset: () => void;
  onClose: () => void;
  /** The Supertrend line is worked out by the server only while its check or exit is on. */
  stAvailable: boolean;
  /** False draws it as a plain block (used while the chart itself is hidden). */
  floating?: boolean;
}) {
  const [open, setOpen] = useState<LineKey | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const setLine = (key: LineKey, patch: Partial<LineLook>) => onChange({ ...style, [key]: { ...style[key], ...patch } });

  // Escape closes the panel (before the chart's own Escape leaves full screen); a click outside it too.
  useEffect(() => {
    if (!floating) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      onClose();
    };
    const onDown = (e: PointerEvent) => {
      const el = ref.current;
      if (!el || el.contains(e.target as Node)) return;
      // The Indicators button toggles it itself.
      if ((e.target as HTMLElement).closest?.("[data-indicators-toggle]")) return;
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [floating, onClose]);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Chart indicators"
      className={clsx(
        "flex flex-col border-white/10 bg-[#151921] text-slate-200",
        floating
          ? "fixed inset-x-0 bottom-0 z-40 max-h-[75vh] rounded-t-2xl border shadow-2xl sm:absolute sm:inset-auto sm:bottom-2 sm:right-2 sm:top-2 sm:z-20 sm:w-[360px] sm:max-h-none sm:rounded-xl"
          : "border-t"
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
        <h3 className="text-sm font-semibold text-slate-100">Indicators</h3>
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onReset}
            className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            <RotateCcw size={13} aria-hidden /> Reset
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close indicators"
            className="flex min-h-9 min-w-9 items-center justify-center rounded-md text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            <X size={14} aria-hidden />
          </button>
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-1">
        <ul className="divide-y divide-white/5">
          {(Object.keys(LINE_NAMES) as LineKey[]).map((key) => {
            const look = style[key];
            const meta = LINE_NAMES[key];
            const always = key === "sma9" || key === "sma21";
            const off = look.show === "off";
            const editing = open === key;
            return (
              <li key={key} className="py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="flex w-6 shrink-0 items-center justify-center">
                    <Swatch look={look} color={look.color} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={clsx("block truncate font-semibold", off ? "text-slate-500" : "text-slate-100")}>{meta.name}</span>
                    {look.show === "auto" ? (
                      <span className="line-clamp-2 text-slate-500">
                        {key === "st" && !stAvailable ? "needs its check or exit on" : meta.auto}
                      </span>
                    ) : null}
                  </span>
                  <ShowSwitch name={meta.name} value={look.show} auto={always ? null : meta.auto} onChange={(show) => setLine(key, { show })} />
                  <button
                    type="button"
                    onClick={() => setOpen(editing ? null : key)}
                    aria-expanded={editing}
                    aria-label={`${meta.name}: colour and line style`}
                    title="Colour, thickness and pattern"
                    className={clsx(
                      "flex min-h-8 min-w-8 shrink-0 items-center justify-center rounded-md ring-1 ring-inset",
                      editing ? "bg-sky-400/15 text-sky-100 ring-sky-400/40" : "text-slate-400 ring-white/10 hover:bg-white/5 hover:text-slate-100"
                    )}
                  >
                    <Settings2 size={14} aria-hidden />
                  </button>
                </div>
                {editing ? (
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 pl-8 text-slate-300">
                    <label className="flex items-center gap-1.5">
                      {meta.color2 ? "First" : "Colour"}
                      <input
                        type="color"
                        aria-label={`${meta.name}: colour`}
                        value={look.color}
                        onChange={(e) => setLine(key, { color: e.target.value })}
                        className={COLOR}
                      />
                    </label>
                    {look.color2 ? (
                      <label className="flex items-center gap-1.5">
                        <span className="capitalize">{meta.color2}</span>
                        <input
                          type="color"
                          aria-label={`${meta.name}: ${meta.color2} colour`}
                          value={look.color2}
                          onChange={(e) => setLine(key, { color2: e.target.value })}
                          className={COLOR}
                        />
                      </label>
                    ) : null}
                    <select
                      aria-label={`${meta.name}: thickness`}
                      value={look.width}
                      onChange={(e) => setLine(key, { width: Number(e.target.value) as Width })}
                      className={SELECT}
                    >
                      {LINE_WIDTHS.map((w) => (
                        <option key={w} value={w}>
                          {w}px
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label={`${meta.name}: line pattern`}
                      value={look.style}
                      onChange={(e) => setLine(key, { style: Number(e.target.value) as Pattern })}
                      className={SELECT}
                    >
                      {LINE_PATTERNS.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    {look.color2 ? <Swatch look={look} color={look.color2} /> : null}
                  </div>
                ) : null}
              </li>
            );
          })}
          <li className="flex flex-wrap items-center gap-3 py-2 text-xs text-slate-300">
            <span className="w-6" aria-hidden />
            <span className="font-semibold text-slate-100">Candles</span>
            <label className="flex items-center gap-1.5">
              Up
              <input
                type="color"
                aria-label="Candles: up colour"
                value={style.candleUp}
                onChange={(e) => onChange({ ...style, candleUp: e.target.value })}
                className={COLOR}
              />
            </label>
            <label className="flex items-center gap-1.5">
              Down
              <input
                type="color"
                aria-label="Candles: down colour"
                value={style.candleDown}
                onChange={(e) => onChange({ ...style, candleDown: e.target.value })}
                className={COLOR}
              />
            </label>
          </li>
        </ul>
      </div>
      <p className="border-t border-white/10 px-3 py-2 text-xs text-slate-500">
        Display only: the bot trades the same whatever is drawn. Saved in this browser.
      </p>
    </div>
  );
}
