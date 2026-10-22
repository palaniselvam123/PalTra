"use client";

import clsx from "clsx";
import { RotateCcw, X } from "lucide-react";
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

/** A preview of a line in its colour, thickness and pattern. */
function Swatch({ look, color }: { look: LineLook; color: string }) {
  const dash = look.style === 0 ? "solid" : look.style === 1 || look.style === 4 ? "dotted" : "dashed";
  return <span aria-hidden className="block w-8" style={{ borderTop: `${look.width + 1}px ${dash} ${color}` }} />;
}

/**
 * Indicator settings, like Groww's: show / hide each line and pick its colour, thickness and
 * pattern. Display only; saved in this browser for every SMA chart.
 */
export function ChartIndicators({
  style,
  onChange,
  onReset,
  onClose,
  stAvailable,
}: {
  style: ChartStyle;
  onChange: (next: ChartStyle) => void;
  onReset: () => void;
  onClose: () => void;
  /** The Supertrend line is worked out by the server only while its check or exit is on. */
  stAvailable: boolean;
}) {
  const setLine = (key: LineKey, patch: Partial<LineLook>) => onChange({ ...style, [key]: { ...style[key], ...patch } });
  return (
    <div role="region" aria-label="Chart indicators" className="border-t border-white/10 bg-[#151921] px-3 py-3 sm:px-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">Indicators</h3>
        <span className="flex items-center gap-2">
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
      <p className="mt-1 text-xs text-slate-400">
        Display only: the bot trades the same whatever is drawn. &quot;Auto&quot; shows a line only while your strategy uses it.
        Saved in this browser.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-300">
        <span className="font-semibold text-slate-200">Candles</span>
        <label className="flex items-center gap-1.5">
          Up
          <input
            type="color"
            value={style.candleUp}
            onChange={(e) => onChange({ ...style, candleUp: e.target.value })}
            className="h-8 w-10 cursor-pointer rounded border border-white/15 bg-transparent"
          />
        </label>
        <label className="flex items-center gap-1.5">
          Down
          <input
            type="color"
            value={style.candleDown}
            onChange={(e) => onChange({ ...style, candleDown: e.target.value })}
            className="h-8 w-10 cursor-pointer rounded border border-white/15 bg-transparent"
          />
        </label>
      </div>
      <ul className="mt-2 divide-y divide-white/5">
        {(Object.keys(LINE_NAMES) as LineKey[]).map((key) => {
          const look = style[key];
          const meta = LINE_NAMES[key];
          const always = key === "sma9" || key === "sma21";
          const off = look.show === "off";
          return (
            <li key={key} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2 text-xs text-slate-300">
              <span className={clsx("w-36 shrink-0 font-semibold", off ? "text-slate-500" : "text-slate-100")}>
                {meta.name}
                {key === "st" && !stAvailable && look.show !== "off" ? (
                  <span className="block font-normal text-slate-500">needs its check or exit on</span>
                ) : null}
              </span>
              <select
                aria-label={`${meta.name}: show`}
                value={look.show}
                onChange={(e) => setLine(key, { show: e.target.value as Show })}
                className={SELECT}
              >
                {always ? null : <option value="auto">Auto ({meta.auto})</option>}
                <option value="on">Show</option>
                <option value="off">Hide</option>
              </select>
              <label className="flex items-center gap-1" title={meta.color2 ? "First line" : "Colour"}>
                <input
                  type="color"
                  aria-label={`${meta.name}: colour`}
                  value={look.color}
                  onChange={(e) => setLine(key, { color: e.target.value })}
                  className="h-8 w-10 cursor-pointer rounded border border-white/15 bg-transparent"
                />
              </label>
              {look.color2 ? (
                <label className="flex items-center gap-1">
                  <span className="text-slate-400">{meta.color2}</span>
                  <input
                    type="color"
                    aria-label={`${meta.name}: ${meta.color2} colour`}
                    value={look.color2}
                    onChange={(e) => setLine(key, { color2: e.target.value })}
                    className="h-8 w-10 cursor-pointer rounded border border-white/15 bg-transparent"
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
              <span className="flex items-center gap-1">
                <Swatch look={look} color={look.color} />
                {look.color2 ? <Swatch look={look} color={look.color2} /> : null}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
