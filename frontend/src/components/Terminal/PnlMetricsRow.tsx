"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { inr, type SmaState } from "@/lib/smaApi";
import { Skeleton, pnlTone } from "./ui";

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${inr(value)}`;
}

export function PnlMetricsRow({ state }: { state: SmaState | null }) {
  const k = state?.kpis;
  const b = k?.charge_breakdown;
  const [open, setOpen] = useState(false);
  const chargesRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (chargesRef.current && !chargesRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const book = state?.mode === "LIVE" ? "Live book" : state?.mode === "RESEARCH" ? "Research book" : "Paper book";
  const trades = k?.trades ?? 0;
  // Wins counted before charges, like the P&L shown (older servers: after charges).
  const wins = k?.gross_wins ?? k?.wins ?? 0;
  const losses = Math.max(0, trades - wins);
  const winPct = trades > 0 ? (wins / trades) * 100 : 0;

  return (
    <section aria-label={`Today's results, ${book.toLowerCase()}`} className="grid grid-cols-2 gap-2 sm:gap-3 xl:grid-cols-3">
      <Card label="MA-cross P&L" hint="If filled at the cross">
        {k ? <span className={pnlTone(k.theoretical_gross)}>{signed(k.theoretical_gross)}</span> : null}
      </Card>
      <div ref={chargesRef} className="relative">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="h-full w-full text-left"
        >
          <Card label="Charges" hint={open ? "Tap to hide breakdown" : "Not taken off the P&L · tap for breakdown"}>
            {k ? <span className="text-amber-300">{inr(k.total_charges)}</span> : null}
          </Card>
        </button>
        {open && b && (
          <ul className="absolute left-0 right-0 top-full z-20 mt-1 rounded-lg border border-white/15 bg-[#0B0E14] p-3 text-sm text-slate-200 shadow-xl">
            <Li k="Brokerage" v={b.brokerage} />
            <Li k="STT (sell)" v={b.stt} />
            <Li k="NSE txn" v={b.exchange_charge} />
            <Li k="SEBI" v={b.sebi_fee} />
            <Li k="Stamp duty" v={b.stamp_duty} />
            <Li k="GST 18%" v={b.gst} />
          </ul>
        )}
      </div>
      <Card
        label="P&L"
        hint="Before charges, with real fill lag"
        strong
        extra={
          k ? (
          <div className="mt-2">
            <div
              className="flex h-1.5 w-full overflow-hidden rounded-full bg-white/10"
              role="img"
              aria-label={`Win rate ${winPct.toFixed(0)} percent: ${wins} wins, ${losses} losses`}
            >
              {trades > 0 && (
                <>
                  <span className="h-full bg-emerald-400" style={{ width: `${winPct}%` }} />
                  <span className="h-full bg-rose-400" style={{ width: `${100 - winPct}%` }} />
                </>
              )}
            </div>
            <div className="mt-1 flex justify-between text-xs text-slate-300">
              <span>
                <span className="text-emerald-300">{wins} W</span> · <span className="text-rose-300">{losses} L</span>
              </span>
              <span>{trades > 0 ? `${winPct.toFixed(0)}% win` : "No trades yet"}</span>
            </div>
          </div>
          ) : null
        }
      >
        {k ? <span className={pnlTone(k.actual_gross)}>{signed(k.actual_gross)}</span> : null}
      </Card>
    </section>
  );
}

function Card({
  label,
  hint,
  strong,
  extra,
  children,
}: {
  label: string;
  hint: string;
  strong?: boolean;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={clsx(
        "h-full rounded-xl border bg-[#151921] p-3 sm:p-4",
        strong ? "border-white/20" : "border-white/10"
      )}
    >
      <div className="truncate text-xs font-medium uppercase leading-4 tracking-wider text-slate-400">{label}</div>
      <div className="mt-1 whitespace-nowrap font-mono text-xl font-semibold leading-9 tabular-nums sm:text-[28px]">
        {children ?? <Skeleton className="mt-1 h-7 w-28" />}
      </div>
      {extra}
      <div className="mt-1 text-xs text-slate-400">{hint}</div>
    </div>
  );
}

function Li({ k, v }: { k: string; v: number }) {
  return (
    <li className="flex justify-between gap-4 py-0.5">
      <span>{k}</span>
      <span className="font-mono">{inr(v)}</span>
    </li>
  );
}
