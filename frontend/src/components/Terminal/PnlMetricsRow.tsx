"use client";

import { useState } from "react";
import { inr, type SmaState } from "@/lib/smaApi";
import clsx from "clsx";

export function PnlMetricsRow({ state }: { state: SmaState | null }) {
  const k = state?.kpis;
  const b = k?.charge_breakdown;
  const [open, setOpen] = useState(false);
  const cap = state?.max_trades ?? 15;

  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Card label="Theoretical MA-cross gross" value={state ? (k?.theoretical_gross ?? 0) : null} hint="Signal-candle price vs exit" />
      <Card label="Actual candle-fill gross" value={state ? (k?.actual_gross ?? 0) : null} hint="Includes fill lag vs the cross" />
      <div className="relative rounded-xl border border-white/5 bg-[#151921] p-4">
        <button className="text-left" onClick={() => setOpen((v) => !v)}>
          <div className="text-[11px] uppercase tracking-[0.14em] text-slate-500">Brokerage & statutory taxes</div>
          <div className="mt-1 font-mono text-xl text-[#F59E0B]">{state ? inr(k?.total_charges ?? 0) : "—"}</div>
          <div className="mt-1 text-[11px] text-slate-500">Groww · STT · NSE · stamp · GST</div>
        </button>
        {open && b && (
          <ul className="absolute left-0 right-0 top-full z-20 mt-1 rounded-lg border border-white/10 bg-[#0B0E14] p-3 text-xs text-slate-300 shadow-xl">
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
        label="Realistic net P&L"
        value={state ? (k?.net ?? 0) : null}
        hint={
          state
            ? `Win rate ${(k?.win_rate ?? 0).toFixed(0)}% · ${k?.trades ?? 0} taken, limit ${cap}`
            : "Terminal totals did not load"
        }
      />
    </section>
  );
}

function Card({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  const up = (value ?? 0) >= 0;
  return (
    <div className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{label}</div>
      <div className={clsx("mt-1 font-mono text-xl", value == null ? "text-slate-500" : up ? "text-[#10B981]" : "text-[#F43F5E]")}>
        {value == null ? "—" : inr(value)}
      </div>
      <div className="mt-1 text-[11px] text-slate-500">{hint}</div>
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
