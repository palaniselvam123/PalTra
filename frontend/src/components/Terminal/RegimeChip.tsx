"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { smaApi, type MarketRegimeReading } from "@/lib/smaApi";

const SHORT: Record<string, string> = {
  TREND_DAY: "Trend",
  SELECTIVE_STOCK_SPECIFIC: "Selective",
  SIDEWAYS_CHOP: "Chop",
};

/**
 * Today's regime on the terminal header. Hidden until a reading exists.
 * The switch on Settings decides whether it changes order size.
 */
export function RegimeChip() {
  const [reading, setReading] = useState<MarketRegimeReading | null>(null);
  const [applies, setApplies] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = () => {
      smaApi
        .marketRegime()
        .then((view) => {
          if (stop) return;
          setReading(view.reading?.regime ? view.reading : null);
          setApplies(view.applies);
        })
        .catch(() => undefined);
    };
    load();
    const poll = setInterval(load, 20000);
    return () => {
      stop = true;
      clearInterval(poll);
    };
  }, []);

  if (!reading?.regime) return null;
  const label = SHORT[reading.regime] || reading.regime;
  return (
    <a
      href="/settings/#market-regime"
      title={applies ? "This regime is sizing trend entries" : "Saved reading. The switch is off, so size is unchanged"}
      className={clsx(
        "inline-flex h-7 items-center rounded-full px-2 text-[11px] font-semibold ring-1 ring-inset",
        !applies && "text-slate-400 ring-white/15",
        applies && reading.regime === "TREND_DAY" && "text-emerald-300 ring-emerald-400/40",
        applies && reading.regime === "SELECTIVE_STOCK_SPECIFIC" && "text-amber-300 ring-amber-400/40",
        applies && reading.regime === "SIDEWAYS_CHOP" && "text-slate-300 ring-white/20"
      )}
    >
      {label} {reading.score}/{reading.max_score}
      {applies ? "" : " off"}
    </a>
  );
}
