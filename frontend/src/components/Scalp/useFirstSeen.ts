"use client";

import { useEffect, useMemo, useState } from "react";
import {
  smaApi,
  type CrossScanMinutes,
  type ScanRunDetail,
} from "@/lib/smaApi";

// Saved runs never change, so each is downloaded once per page load.
const runCache = new Map<number, ScanRunDetail>();

const istDay = (iso: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

/**
 * When each stock first appeared in a scan card's list (Trend quality or Cross scan) with its current
 * filters: the earliest of today's saved scans (same candle size, up to the scan on
 * screen) in which the stock passes `passes`. A stock in the list now that no saved
 * scan had is dated to the scan on screen. Read-only: it only reads the scan history.
 */
export function useFirstSeen<R extends { symbol: string }>(
  asOf: string | null | undefined,
  minutes: CrossScanMinutes | undefined,
  rows: R[],
  passes: (r: R) => boolean,
  kind: "chop" | "cross" = "chop",
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
        const list = await smaApi.scanHistory(istDay(asOf), kind);
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
  }, [asOf, minutes, kind]);

  return useMemo(() => {
    const first = new Map<string, string>();
    const ordered = [...runs].sort(
      (a, b) => Date.parse(a.as_of) - Date.parse(b.as_of),
    );
    for (const run of ordered) {
      for (const row of run.rows as unknown as R[]) {
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
