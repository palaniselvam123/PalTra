"use client";

import { useEffect, useMemo, useState } from "react";
import {
  smaApi,
  type ChopScanRow,
  type CrossScanMinutes,
  type ScanRunDetail,
} from "@/lib/smaApi";

// Saved runs never change, so each is downloaded once per page load.
const runCache = new Map<number, ScanRunDetail>();

const istDay = (iso: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

/**
 * When each stock first appeared in the Trend quality list with the card's current
 * filters: the earliest of today's saved scans (same candle size, up to the scan on
 * screen) in which the stock passes `passes`. A stock in the list now that no saved
 * scan had is dated to the scan on screen. Read-only: it only reads the scan history.
 */
export function useFirstSeen(
  asOf: string | null | undefined,
  minutes: CrossScanMinutes | undefined,
  rows: ChopScanRow[],
  passes: (r: ChopScanRow) => boolean,
): Map<string, string> {
  const [runs, setRuns] = useState<ScanRunDetail[]>([]);

  useEffect(() => {
    if (!asOf || !minutes) {
      setRuns([]);
      return;
    }
    let live = true;
    const until = Date.parse(asOf);
    (async () => {
      try {
        const list = await smaApi.scanHistory(istDay(asOf), "chop");
        const wanted = list.runs.filter(
          (r) => r.minutes === minutes && Date.parse(r.as_of) <= until,
        );
        const out: ScanRunDetail[] = [];
        for (const r of wanted) {
          let detail = runCache.get(r.id);
          if (!detail) {
            detail = await smaApi.scanHistoryRun(r.id);
            runCache.set(r.id, detail);
          }
          out.push(detail);
          if (!live) return;
        }
        if (live) setRuns(out);
      } catch {
        if (live) setRuns([]); // no history on this server: every stock dates to the scan on screen
      }
    })();
    return () => {
      live = false;
    };
  }, [asOf, minutes]);

  return useMemo(() => {
    const first = new Map<string, string>();
    const ordered = [...runs].sort(
      (a, b) => Date.parse(a.as_of) - Date.parse(b.as_of),
    );
    for (const run of ordered) {
      for (const row of run.rows as ChopScanRow[]) {
        if (!first.has(row.symbol) && passes(row))
          first.set(row.symbol, run.as_of);
      }
    }
    if (asOf)
      for (const row of rows)
        if (!first.has(row.symbol) && passes(row)) first.set(row.symbol, asOf);
    return first;
  }, [runs, rows, passes, asOf]);
}
