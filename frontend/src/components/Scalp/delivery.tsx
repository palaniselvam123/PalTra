"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, type DeliveryRow } from "@/lib/api";

/** Delivery % of the stocks on screen, from NSE's end-of-day file (the latest published session).
 * Read once per set of stocks; the server keeps each day's file, so this is cheap. */
export function useDelivery(symbols: string[]) {
  const key = [...new Set(symbols.map((s) => s.toUpperCase()))].sort().join(",");
  const [rows, setRows] = useState<Map<string, DeliveryRow>>(new Map());
  const [day, setDay] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!key) return;
    let live = true;
    const t = setTimeout(() => {
      api
        .scalpDelivery(key.split(","))
        .then((r) => {
          if (!live) return;
          setRows(new Map(Object.entries(r.rows)));
          setDay(r.day);
          setError(r.day ? null : r.errors.join("; ") || "No delivery data");
        })
        .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load delivery %"));
    }, 400);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [key]);
  return { rows, day, error };
}

const fmtDay = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
const shares = (n: number | null) => (n == null ? "—" : n.toLocaleString("en-IN"));

/** A one-line note under the table title: which session the delivery % is from. */
export function DeliveryNote({ day, error }: { day: string | null; error: string | null }) {
  if (error) return <p className="text-[11px] text-amber-400">Delivery %: {error}</p>;
  if (!day) return null;
  return (
    <p className="text-[11px] text-slate-500">
      Deliv % = shares taken for delivery on {fmtDay(day)} (NSE end of day; today's comes about 6 pm). The rest was squared off the
      same day (intraday).
    </p>
  );
}

export function DeliveryHead() {
  return (
    <>
      <th className="pb-2 text-right font-medium" title="Delivery % on NSE's latest published day (the rest was intraday)">
        Deliv %
      </th>
      <th className="pb-2 text-right font-medium" title="Delivery % against its average of the sessions before">
        vs usual
      </th>
    </>
  );
}

export function DeliveryCells({ d }: { d: DeliveryRow | null | undefined }) {
  const title = d
    ? [
        `Traded ${shares(d.traded)} · delivered ${shares(d.delivered)} · intraday ${shares(d.intraday)}`,
        ...d.history.map((h) => `${fmtDay(h.day)}: ${h.pct == null ? "—" : `${h.pct.toFixed(1)}%`}`),
      ].join("\n")
    : "No delivery data for this stock";
  return (
    <>
      <td
        className={clsx(
          "py-1.5 pr-2 text-right font-mono",
          d?.pct == null ? "text-slate-500" : d.pct >= 60 ? "font-semibold text-accentSky" : d.pct < 25 ? "text-amber-400" : "text-slate-200"
        )}
        title={title}
      >
        {d?.pct == null ? "—" : `${d.pct.toFixed(1)}%`}
      </td>
      <td
        className={clsx(
          "py-1.5 pr-2 text-right font-mono",
          d?.vs_avg == null ? "text-slate-500" : d.vs_avg >= 0 ? "text-profit" : "text-loss"
        )}
        title={d?.avg_pct == null ? undefined : `Average before: ${d.avg_pct.toFixed(1)}%`}
      >
        {d?.vs_avg == null ? "—" : `${d.vs_avg > 0 ? "+" : ""}${d.vs_avg.toFixed(1)}`}
      </td>
    </>
  );
}
