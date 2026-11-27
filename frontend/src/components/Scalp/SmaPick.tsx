"use client";

import { useEffect, useId, useState } from "react";
import { DESK_SMA, SMA_MAX, SMA_MIN, SMA_PRESETS, smaError, type ScanSma } from "@/lib/scanSizes";

const LABEL = "mb-1 block h-4 whitespace-nowrap text-[11px] font-medium leading-4 text-slate-400"; // tableTools LABEL
const BOX = "min-h-9 w-16 rounded-md border border-slate-700 bg-surface2 px-2 text-sm tabular-nums text-slate-100";

/**
 * Which moving averages a scan reads: Bot 1's own, 9 / 21, 20 / 50, 50 / 200, or two typed numbers.
 * Only a pair that can be scanned is handed up. Display and request only; no bot setting is changed.
 */
export function SmaPick({ value, onChange, desk }: { value: ScanSma; onChange: (next: ScanSma) => void; desk?: ScanSma | null }) {
  const id = useId();
  const preset = SMA_PRESETS.find((p) => p.fast === value.fast && p.slow === value.slow);
  const [custom, setCustom] = useState(false);
  const [fast, setFast] = useState(String(value.fast || 20));
  const [slow, setSlow] = useState(String(value.slow || 50));
  // A pair that is not a preset (loaded from the server, or typed before) shows in the boxes.
  useEffect(() => {
    if (value.fast && !preset) {
      setCustom(true);
      setFast(String(value.fast));
      setSlow(String(value.slow));
    }
  }, [value.fast, value.slow, preset]);
  const picked = custom ? "custom" : value.fast ? `${value.fast}/${value.slow}` : "desk";
  const problem = custom ? smaError(Number(fast), Number(slow)) : null;
  const typed = (f: string, s: string) => {
    setFast(f);
    setSlow(s);
    if (!smaError(Number(f), Number(s))) onChange({ fast: Number(f), slow: Number(s) });
  };
  return (
    <div>
      <label htmlFor={id} className={LABEL}>
        Moving averages (SMA)
      </label>
      <div className="flex min-h-9 flex-wrap items-center gap-2">
        <select
          id={id}
          value={picked}
          title="The two moving averages the scan reads. Bot 1's own = the SMA periods in Bot 1's settings. Custom = type your own two numbers."
          onChange={(e) => {
            const v = e.target.value;
            if (v === "custom") {
              setCustom(true);
              if (!smaError(Number(fast), Number(slow))) onChange({ fast: Number(fast), slow: Number(slow) });
              return;
            }
            setCustom(false);
            if (v === "desk") return onChange(DESK_SMA);
            const [f, s] = v.split("/").map(Number);
            onChange({ fast: f, slow: s });
          }}
          className="min-h-9 w-44 rounded-md border border-slate-700 bg-surface2 px-2 text-sm text-slate-100"
        >
          <option value="desk">{desk?.fast ? `Bot 1's own (${desk.fast} / ${desk.slow})` : "Bot 1's own"}</option>
          {SMA_PRESETS.map((p) => (
            <option key={`${p.fast}/${p.slow}`} value={`${p.fast}/${p.slow}`}>
              {p.fast} / {p.slow}
            </option>
          ))}
          <option value="custom">Custom numbers…</option>
        </select>
        {custom ? (
          <>
            <input
              type="number"
              inputMode="numeric"
              aria-label="Fast SMA period"
              min={SMA_MIN}
              max={SMA_MAX}
              step={1}
              value={fast}
              onChange={(e) => typed(e.target.value, slow)}
              className={BOX}
            />
            <span className="text-xs text-slate-400">/</span>
            <input
              type="number"
              inputMode="numeric"
              aria-label="Slow SMA period"
              min={SMA_MIN}
              max={SMA_MAX}
              step={1}
              value={slow}
              onChange={(e) => typed(fast, e.target.value)}
              className={BOX}
            />
          </>
        ) : null}
      </div>
      {problem ? (
        <p role="alert" className="mt-1 text-xs text-amber-400">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
